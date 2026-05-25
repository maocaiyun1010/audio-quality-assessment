# -*- coding: utf-8 -*-
"""
同步采集：每条音源在 **同一时段** 内完成「设备播放 + PC 麦克风录制」。
播放列表由 config.discover_standard_tracks() 根据 assets/test_audio 自动生成。

多设备：对 `adb devices` 中每一台依次、按「同一音源 → 各设备轮流外放+本机录音」
完成采集，便于后续 **刺激比较法**（同刺激下多路录音对比）评分。
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence

import numpy as np

import adbutils
from adbutils import AdbDevice

from adb_audio_playback import wake_and_trigger_playback
from config import (
    ASSETS_AUDIO_DIR,
    DEVICE_REMOTE_DIR,
    PER_TRACK_PLAY_SECONDS,
    POST_TAIL_SECONDS,
    PRE_ROLL_SECONDS,
    RECORD_CHANNELS,
    RECORDED_DIR,
    SAMPLE_RATE,
    discover_standard_tracks,
    ensure_output_dirs,
)
from speaker_eval.adapters.adb import list_connected_adb_devices
from speaker_eval.adapters.audio import acquire_recording_buffer, get_record_tool
from speaker_eval.adapters.audio.recording_plan import effective_play_seconds
from speaker_eval.adapters.audio.wav_capture_write import write_standard_capture_wav


def _force_stop_known_music_packages(device: adbutils.AdbDevice) -> None:
    """对常见音乐/媒体 App 执行 force-stop（VIEW 打开的播放器未必在此列，需配合媒体键）。"""
    pkgs = [
        "com.android.music",
        "com.google.android.music",
        "com.google.android.apps.youtube.music",
        "com.miui.player",
        "com.huawei.music",
        "com.sec.android.app.music",
        "com.heytap.music",
        "com.coloros.music",
        "com.oplus.music",
        "com.realme.music",
        "com.vivo.music",
        "com.meizu.media.music",
        "com.tencent.qqmusic",
        "com.netease.cloudmusic",
    ]
    for pkg in pkgs:
        try:
            device.shell(f"am force-stop {pkg}", timeout=10)
        except Exception:
            pass


def halt_device_audio_playback(
    device: adbutils.AdbDevice,
    log: Optional[Callable[[str], None]] = None,
    *,
    reason: str = "",
) -> None:
    """
    尽量停止当前外放：先全局媒体键 / media_session，再 force-stop 常见音乐包。

    解决 ``am start VIEW`` 打开**非**上述包名播放器时，整曲播完仍不停止的问题。
    """
    log = log or (lambda _m: None)
    suffix = f"（{reason}）" if reason else ""
    # 媒体键：多数系统默认「音频」类 Activity 会响应
    for keycode in ("86", "85", "127", "79"):  # STOP, PLAY_PAUSE, PAUSE, HEADSETHOOK
        try:
            device.shell(f"input keyevent {keycode}", timeout=5)
        except Exception:
            pass
        time.sleep(0.08)
    try:
        device.shell("cmd media_session dispatch pause", timeout=5)
    except Exception:
        pass
    try:
        device.shell("cmd media_session dispatch stop", timeout=5)
    except Exception:
        pass
    time.sleep(0.15)
    _force_stop_known_music_packages(device)
    log(f"[采集] 已尝试停止设备外放{suffix}（媒体键 + media_session + 常见音乐包）")


def _safe_filename_stem(rel_posix: str) -> str:
    """录制文件名用：相对路径转合法文件名片段。"""
    s = rel_posix.replace("\\", "/").replace("/", "_")
    for ch in '<>:"|?*':
        s = s.replace(ch, "_")
    return s or "track"


@dataclass
class TrackCaptureResult:
    group: str
    filename: str  # 相对路径 posix，便于报告与追溯
    local_wav: Path
    ok: bool
    message: str = ""
    device_serial: str = ""
    device_slot: str = ""


def capture_one_track(
    device: AdbDevice,
    remote_basename: str,
    out_wav: Path,
    log: Optional[Callable[[str], None]] = None,
    record_tool: str | None = None,
    source_path: Path | None = None,
) -> tuple[bool, str]:
    log = log or (lambda _m: None)

    play_seconds = effective_play_seconds(
        source_path=source_path,
        configured_seconds=float(PER_TRACK_PLAY_SECONDS),
        log=log,
    )
    total_seconds = PRE_ROLL_SECONDS + play_seconds + POST_TAIL_SECONDS
    frames = int(total_seconds * SAMPLE_RATE)
    error_box: list[Optional[BaseException]] = [None]

    def playback_thread() -> None:
        try:
            time.sleep(PRE_ROLL_SECONDS)
            ok_pb, msg_pb = wake_and_trigger_playback(device, remote_basename, log)
            if not ok_pb:
                raise RuntimeError(msg_pb or "设备播放触发失败")
            # 默认播放器常按**整文件**播放；在「有效内容段」结束时主动停外放，尾段 POST_TAIL 仍继续录环境
            time.sleep(play_seconds)
            halt_device_audio_playback(
                device, log, reason=f"有效段 {play_seconds:.1f}s 结束"
            )
        except BaseException as exc:  # noqa: BLE001
            error_box[0] = exc

    th = threading.Thread(target=playback_thread, daemon=True)
    th.start()

    try:
        log(
            f"开始录音: {out_wav.name} (frames={frames}, 原生 {SAMPLE_RATE} Hz，无需重采样, "
            f"工具={get_record_tool(record_tool)})"
        )
        recording = acquire_recording_buffer(
            total_seconds, frames, log=log, tool=record_tool
        )
    except Exception as exc:
        error_box[0] = exc
        return False, f"本机录音失败: {exc}"

    th.join(timeout=5.0)

    if error_box[0] is not None:
        return False, f"设备播放触发失败: {error_box[0]}"

    pre_i = int(PRE_ROLL_SECONDS * SAMPLE_RATE)
    win = int(play_seconds * SAMPLE_RATE)
    end_i = min(pre_i + win, recording.shape[0])
    trimmed = recording[pre_i:end_i]
    if trimmed.shape[0] < win:
        pad = win - trimmed.shape[0]
        trimmed = np.pad(trimmed, ((0, pad), (0, 0)), mode="constant")

    out_wav.parent.mkdir(parents=True, exist_ok=True)
    try:
        dur = trimmed.shape[0] / float(SAMPLE_RATE)
        rms = float(np.sqrt(np.mean(np.square(trimmed, dtype=np.float64))))
        log(
            f"有效段时长 {dur:.2f}s (配置 PER_TRACK={float(PER_TRACK_PLAY_SECONDS):.2f}s), "
            f"RMS={rms:.6f}"
        )
        if rms < 1e-4:
            log(
                "[WARN] 波形能量极低，可能未录到喇叭声：请提高设备音量、麦克风靠近声源，"
                "或设置环境变量 SPEAKER_INPUT_DEVICE 为正确设备索引（见 python self_test.py 麦克风列表）。"
            )
        write_standard_capture_wav(out_wav, trimmed)
    except Exception as exc:
        return False, f"写文件失败: {exc}"

    halt_device_audio_playback(device, log, reason="本条录音结束，再次兜底停止")
    time.sleep(0.4)
    return True, "ok"


def _build_push_plan(
    tracks: list[tuple[str, str]],
) -> list[tuple[str, str, str]]:
    """(分组, 相对路径, 设备端文件名 track_001.ext)。"""
    plan: list[tuple[str, str, str]] = []
    for i, (group, rel) in enumerate(tracks, start=1):
        ext = Path(rel).suffix.lower()
        plan.append((group, rel, f"track_{i:03d}{ext}"))
    return plan


def push_all_tracks(
    device_serial: str,
    plan: list[tuple[str, str, str]],
    log: Optional[Callable[[str], None]] = None,
) -> tuple[bool, str]:
    log = log or (lambda _m: None)
    try:
        d = adbutils.adb.device(device_serial)
    except Exception as exc:
        return False, f"连接 ADB 设备失败: {exc}"

    try:
        d.shell(f"mkdir -p {DEVICE_REMOTE_DIR}", timeout=30)
    except Exception as exc:
        return False, f"创建设备目录失败: {exc}"

    for group, rel, remote_base in plan:
        src = ASSETS_AUDIO_DIR / rel
        if not src.is_file():
            return False, f"缺少音源文件: {src}（分组={group}）"
        remote = f"{DEVICE_REMOTE_DIR}/{remote_base}"
        try:
            log(f"推送: {rel} -> {remote_base}")
            d.push(str(src), remote)
        except Exception as exc:
            return False, f"推送失败 {rel}: {exc}"

    return True, "ok"


def run_multi_device_capture(
    device_serials: Sequence[str],
    session_tag: str,
    log: Optional[Callable[[str], None]] = None,
    device_role_labels: Optional[Sequence[str]] = None,
    record_tool: str | None = None,
) -> tuple[list[TrackCaptureResult], str]:
    """
    多设备采集：每台设备先推送全量音源；再按音源序号对每台设备依次「播放+本机录音」。
    录制文件名含 device_slot（d01、d02…），与 playlist.json 中 devices 一致。

    ``device_role_labels``：与 ``device_serials`` 等长时写入清单 ``devices[].label``（如被测/对比），
    供评分与报告展示；不传则 ``label`` 为 ``设备-d01`` 形式。

    ``record_tool``：``sounddevice``（本机默认）或 ``omnimic``（OmniMic 专业：硬件优先或外部 exe）；
    不设则读环境变量 ``SPEAKER_RECORD_TOOL``。
    """
    log = log or (lambda _m: None)
    ensure_output_dirs()

    serials = [s.strip() for s in device_serials if s and str(s).strip()]
    if not serials:
        return [], "未指定任何 ADB 设备序列号"

    log(
        f"录制参数: 每条有效段={float(PER_TRACK_PLAY_SECONDS):.2f}s "
        f"(预滚 {float(PRE_ROLL_SECONDS):.2f}s + 尾 {float(POST_TAIL_SECONDS):.2f}s)"
    )
    log(f"录音工具: {get_record_tool(record_tool)}")

    tracks = discover_standard_tracks()
    if not tracks:
        return (
            [],
            f"未发现音源：请将 wav/mp3 等放入 {ASSETS_AUDIO_DIR}（可用子文件夹分组）",
        )

    plan = _build_push_plan(tracks)
    safe_tag = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in session_tag)[:64]
    device_slots = [f"d{i:02d}" for i in range(1, len(serials) + 1)]

    labels_seq = list(device_role_labels) if device_role_labels is not None else []
    devices_meta: list[dict[str, str]] = []
    for i, (ser, slot) in enumerate(zip(serials, device_slots)):
        if i < len(labels_seq) and str(labels_seq[i]).strip():
            lab = str(labels_seq[i]).strip()
        else:
            lab = f"设备-{slot}"
        devices_meta.append({"slot": slot, "serial": ser, "label": lab})

    playlist_payload = {
        "version": 2,
        "mode": "multi_device_stimulus_compare",
        "session_tag": session_tag,
        "safe_tag": safe_tag,
        "devices": devices_meta,
        "items": [
            {"index": i, "group": g, "source": r, "device_remote": rem}
            for i, (g, r, rem) in enumerate(plan, start=1)
        ],
    }
    try:
        pl_path = RECORDED_DIR / f"{safe_tag}_playlist.json"
        pl_path.parent.mkdir(parents=True, exist_ok=True)
        pl_path.write_text(
            json.dumps(playlist_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        log(f"已写入播放清单: {pl_path}（设备数={len(serials)}）")
    except Exception as exc:
        return [], f"写入播放清单失败: {exc}"

    for ser, slot in zip(serials, device_slots):
        ok, msg = push_all_tracks(ser, plan, log=log)
        if not ok:
            return [], f"设备 {ser} ({slot}) 推送失败: {msg}"

    results: list[TrackCaptureResult] = []
    n_tracks = len(plan)

    for idx, (group, rel, remote_base) in enumerate(plan, start=1):
        stem_safe = _safe_filename_stem(rel)
        for ser, slot in zip(serials, device_slots):
            out_name = f"{safe_tag}_{idx:02d}_{slot}_{group}_{stem_safe}.wav"
            out_path = RECORDED_DIR / out_name
            log(
                f"[音源 {idx}/{n_tracks}] [{slot}] 设备 {ser} | {group} / {rel} -> {out_path.name}"
            )
            try:
                device = adbutils.adb.device(ser)
            except Exception as exc:
                results.append(
                    TrackCaptureResult(
                        group=group,
                        filename=rel,
                        local_wav=out_path,
                        ok=False,
                        message=f"连接失败: {exc}",
                        device_serial=ser,
                        device_slot=slot,
                    )
                )
                log(f"连接失败，跳过: {exc}")
                continue

            ok_cap, cap_msg = capture_one_track(
                device,
                remote_base,
                out_path,
                log=log,
                record_tool=record_tool,
                source_path=ASSETS_AUDIO_DIR / rel,
            )
            results.append(
                TrackCaptureResult(
                    group=group,
                    filename=rel,
                    local_wav=out_path,
                    ok=ok_cap,
                    message=cap_msg,
                    device_serial=ser,
                    device_slot=slot,
                )
            )
            if not ok_cap:
                log(f"本条失败: {cap_msg}，继续…")

    return results, "completed"


def run_full_capture(
    device_serial: str,
    session_tag: str,
    log: Optional[Callable[[str], None]] = None,
    record_tool: str | None = None,
) -> tuple[list[TrackCaptureResult], str]:
    """单设备入口，等价于 run_multi_device_capture([device_serial], …)。"""
    return run_multi_device_capture(
        [device_serial], session_tag, log=log, record_tool=record_tool
    )


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("用法: python sync_capture.py <设备序列号[,序列号2...]> <会话标签>")
        print("示例: python sync_capture.py emulator-5554 EVT1")
        print("  多台: python sync_capture.py SN1,SN2 EVT1")
        sys.exit(1)

    raw_serials, tag = sys.argv[1], sys.argv[2]
    serials = [x.strip() for x in raw_serials.split(",") if x.strip()]

    def _print(s: str) -> None:
        print(s, flush=True)

    items, summary = run_multi_device_capture(serials, tag, log=_print)
    print("==== 结果汇总 ====")
    print(summary)
    for it in items:
        print(it.group, it.filename, "OK" if it.ok else "FAIL", it.message, it.local_wav)
