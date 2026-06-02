# -*- coding: utf-8 -*-
"""
双设备单麦对比模式 - 完整录制流程（参考常规模式）

核心特性：
- 扫描音源文件（assets/test_audio）
- 推送音源到被测设备A和对比设备B
- 按「节目-设备」循环：播放 + PC录音
- 两段音频都录制完成后，读清单，按节目合并送Dify评分
- 解析模型JSON，生成报告
- 保留音频预览功能
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import numpy as np

# (音源展示名, 被测A的 wav, 对比B的 wav)
PairedAudioRow = tuple[str, str, str]

import adbutils
from adbutils import AdbDevice

from config import (
    ASSETS_AUDIO_DIR,
    DEVICE_REMOTE_DIR,
    POST_TAIL_SECONDS,
    PRE_ROLL_SECONDS,
    RECORDED_DIR,
    SAMPLE_RATE,
    discover_standard_tracks,
    ensure_output_dirs,
)
from adb_audio_playback import clean_shell_text, trigger_android_audio_playback
from speaker_eval.adapters.audio import acquire_recording_buffer
from speaker_eval.adapters.audio.recording_plan import effective_play_seconds, is_full_track_play_enabled
from speaker_eval.adapters.audio.tooling import get_record_tool
from speaker_eval.adapters.audio.wav_capture_write import write_standard_capture_wav
from sync_capture import halt_device_audio_playback


def _halt_playback_quick(
    device: adbutils.AdbDevice,
    log: Optional[Callable[[str], None]] = None,
    *,
    reason: str = "",
) -> None:
    """
    轻量停播：仅媒体键 + media_session，不做 force-stop 全包扫描。
    用于缩短切歌间隔和尾段抑制重播。
    """
    log = log or (lambda _m: None)
    suffix = f"（{reason}）" if reason else ""
    for keycode in ("127", "86"):  # PAUSE, STOP
        try:
            device.shell(f"input keyevent {keycode}", timeout=3)
        except Exception:
            pass
        time.sleep(0.04)
    try:
        device.shell("cmd media_session dispatch pause", timeout=3)
    except Exception:
        pass
    try:
        device.shell("cmd media_session dispatch stop", timeout=3)
    except Exception:
        pass
    log(f"[采集] 轻量停播{suffix}")


def load_paired_audios_from_dual_playlist_path(playlist_path: str | Path) -> list[PairedAudioRow]:
    """
    从已保存的 ``*_dual_playlist.json``（version 2 / dual_device_single_mic_comparison）读取配对 WAV，
    用于跳过重新采集、直接走「手动开始测评」。

    要求 JSON 内 devices[0]=d01、devices[1]=d02 的 results 按 track_index 对齐，且 local_wav 均存在。
    """
    p = Path(playlist_path)
    if not p.is_file():
        raise FileNotFoundError(f"找不到播放清单：{p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"清单不是合法 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("清单根节点应为 JSON 对象")

    devices = list(data.get("devices") or [])
    if len(devices) < 2:
        raise ValueError("清单中 devices 少于 2 台，无法配对 A/B")

    def _pick(slot: str) -> dict:
        for d in devices:
            if str(d.get("slot") or "") == slot:
                return d
        return {}

    a_dev = _pick("d01") or devices[0]
    b_dev = _pick("d02") or devices[1]
    ra = sorted(
        list(a_dev.get("results") or []),
        key=lambda r: int(r.get("track_index") or 0),
    )
    rb = sorted(
        list(b_dev.get("results") or []),
        key=lambda r: int(r.get("track_index") or 0),
    )
    if not ra or not rb:
        raise ValueError("清单中某台设备无 results 条目")
    if len(ra) != len(rb):
        raise ValueError(f"A/B 音源条数不一致（{len(ra)} vs {len(rb)}）")

    try:
        from config import RECORDED_DIR  # 延迟导入，避免循环依赖
    except Exception:
        RECORDED_DIR = None  # type: ignore[assignment]

    def _resolve_wav_path(raw: str) -> Path:
        """
        兼容历史清单中的绝对路径：若原路径不存在，尝试用文件名在
        1) 清单同目录
        2) 当前项目 RECORDED_DIR
        中重定位。
        """
        cand = Path(str(raw or "").strip())
        if cand.is_file():
            return cand
        name = cand.name
        if name:
            alt1 = p.parent / name
            if alt1.is_file():
                return alt1
            if RECORDED_DIR is not None:
                try:
                    alt2 = Path(RECORDED_DIR) / name
                    if alt2.is_file():
                        return alt2
                except Exception:
                    pass
        return cand

    out: list[PairedAudioRow] = []
    missing: list[str] = []
    for x, y in zip(ra, rb):
        if not (x.get("ok") and y.get("ok")):
            continue
        tix = int(x.get("track_index") or 0)
        if int(y.get("track_index") or 0) != tix:
            raise ValueError(f"track_index 不对齐：A={tix} B={y.get('track_index')}")
        group = str(x.get("group") or "").strip()
        fn = str(x.get("filename") or "").strip()
        track_name = f"{group}_{fn}" if group or fn else f"track_{tix:02d}"
        pa = str(x.get("local_wav") or "").strip()
        pb = str(y.get("local_wav") or "").strip()
        if not pa or not pb:
            missing.append(f"track {tix} 缺少 local_wav")
            continue
        pqa = _resolve_wav_path(pa)
        pqb = _resolve_wav_path(pb)
        if not pqa.is_file():
            missing.append(str(pqa))
        if not pqb.is_file():
            missing.append(str(pqb))
        if pqa.is_file() and pqb.is_file():
            out.append((track_name, str(pqa), str(pqb)))

    if missing:
        raise FileNotFoundError("以下文件不存在或路径无效：\n" + "\n".join(missing[:12]))
    if not out:
        raise ValueError("没有可用的 ok=true 且文件存在的音源对")
    return out


class DualDeviceFullRecorder:
    """双设备单麦完整录制器（参考常规模式流程）"""

    def __init__(self, log: Optional[Callable[[str], None]] = None, mic_spec: str = ""):
        self.log = log or (lambda _m: None)
        self._mic_spec = mic_spec
        self._session_tag = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._safe_tag = "".join(
            c if c.isalnum() or c in ("-", "_") else "_" for c in self._session_tag
        )[:64]
        
        # 录制结果存储
        self._device_a_results: list[dict] = []
        self._device_b_results: list[dict] = []
        self._playlist_path: Optional[Path] = None
        
    @property
    def device_a_results(self) -> list[dict]:
        """被测设备A的录制结果列表"""
        return self._device_a_results
    
    @property
    def device_b_results(self) -> list[dict]:
        """对比设备B的录制结果列表"""
        return self._device_b_results
    
    @property
    def is_device_a_complete(self) -> bool:
        """被测设备A是否完成所有音源录制"""
        return len(self._device_a_results) > 0 and all(r.get("ok") for r in self._device_a_results)
    
    @property
    def is_device_b_complete(self) -> bool:
        """对比设备B是否完成所有音源录制"""
        return len(self._device_b_results) > 0 and all(r.get("ok") for r in self._device_b_results)
    
    @property
    def is_complete(self) -> bool:
        """两段设备是否都完成录制"""
        return self.is_device_a_complete and self.is_device_b_complete

    @property
    def device_a_has_partial(self) -> bool:
        """被测设备 A 已有进度但未全部成功（可续录）。"""
        return bool(self._device_a_results) and not self.is_device_a_complete

    @property
    def device_b_has_partial(self) -> bool:
        """对比设备 B 已有进度但未全部成功（可续录）。"""
        return bool(self._device_b_results) and not self.is_device_b_complete

    @staticmethod
    def _track_entry_ok(entry: dict | None) -> bool:
        if not entry or not entry.get("ok"):
            return False
        wav = str(entry.get("local_wav") or "").strip()
        return bool(wav) and Path(wav).is_file()

    @staticmethod
    def _index_existing_results(existing: list[dict]) -> dict[int, dict]:
        out: dict[int, dict] = {}
        for row in existing or []:
            try:
                out[int(row.get("track_index") or 0)] = row
            except (TypeError, ValueError):
                continue
        return out

    def _build_track_plan(self, tracks: list[tuple[str, str]]) -> list[tuple[str, str, str]]:
        plan: list[tuple[str, str, str]] = []
        for i, (group, rel) in enumerate(tracks, start=1):
            ext = Path(rel).suffix.lower()
            remote_base = f"track_{i:03d}{ext}"
            plan.append((group, rel, remote_base))
        return plan

    def _commit_side_results(
        self,
        side: str,
        results: list[dict],
        *,
        save_playlist: bool = True,
    ) -> None:
        if side == "A":
            self._device_a_results = list(results)
        else:
            self._device_b_results = list(results)
        if save_playlist:
            self._save_playlist()

    def _record_tracks_for_device(
        self,
        *,
        side: str,
        device: AdbDevice,
        device_serial: str,
        plan: list[tuple[str, str, str]],
        duration: float,
        existing_results: list[dict],
    ) -> tuple[bool, str]:
        """按 plan 录制；跳过已成功条目，失败时保留进度供续录。"""
        existing_by_idx = self._index_existing_results(existing_results)
        results: list[dict] = []
        n_plan = len(plan)
        role = "被测设备A" if side == "A" else "对比设备B"

        need_push: list[tuple[int, str, str, str]] = []
        for idx, (group, rel, remote_base) in enumerate(plan, start=1):
            if not self._track_entry_ok(existing_by_idx.get(idx)):
                need_push.append((idx, group, rel, remote_base))

        if need_push:
            self.log(f"📤 推送待录制音源到设备（{len(need_push)}/{n_plan} 条）…")
        else:
            self.log(f"📤 全部 {n_plan} 条已在设备侧，跳过推送")

        for idx, group, rel, remote_base in need_push:
            src = ASSETS_AUDIO_DIR / rel
            ok_push, msg_push = self._push_one_track_with_logs(
                device=device,
                src=src,
                remote_base=remote_base,
                group=group,
                rel=rel,
            )
            if not ok_push:
                self._commit_side_results(
                    side, results if results else list(existing_results)
                )
                return False, msg_push

        self.log("🎵 开始按节目录制…")
        for idx, (group, rel, remote_base) in enumerate(plan, start=1):
            prev = existing_by_idx.get(idx)
            if self._track_entry_ok(prev):
                self.log(f"\n[音源 {idx}/{n_plan}] {group} / {rel}")
                self.log(f"  ⏭️ 跳过已成功: {Path(str(prev.get('local_wav'))).name}")
                results.append(dict(prev))
                continue

            stem_safe = "".join(
                c if c.isalnum() or c in ("-", "_") else "_" for c in rel
            )[:50]
            if prev and str(prev.get("local_wav") or "").strip():
                out_path = Path(str(prev["local_wav"]))
            else:
                out_name = f"{self._safe_tag}_{side}_{idx:02d}_{group}_{stem_safe}.wav"
                out_path = RECORDED_DIR / out_name

            self.log(f"\n[音源 {idx}/{n_plan}] {group} / {rel}")

            ok, msg = self._record_one_track(
                device,
                remote_base,
                out_path,
                duration,
                source_path=ASSETS_AUDIO_DIR / rel,
            )

            result = {
                "track_index": idx,
                "group": group,
                "filename": rel,
                "local_wav": str(out_path),
                "ok": ok,
                "message": msg,
                "device": side,
                "device_serial": device_serial,
            }
            results.append(result)

            if ok:
                self.log(f"  ✅ 录制成功: {out_path.name}")
            else:
                self.log(f"  ❌ 录制失败: {msg}")
                self._commit_side_results(side, results)
                n_ok = sum(1 for r in results if r.get("ok"))
                return (
                    False,
                    f"音源 {idx}/{n_plan} 录制失败: {msg}（已成功 {n_ok}/{n_plan} 条，"
                    f"可点击续录【{role}】从本条重试）",
                )

            if idx < n_plan:
                time.sleep(0.2)

        self._commit_side_results(side, results)
        self.log(f"\n✅ 【{role}】所有音源录制完成！")
        return True, "ok"
    
    @property
    def playlist_path(self) -> Optional[Path]:
        """播放清单路径"""
        return self._playlist_path
    
    def _record_one_track(
        self,
        device: AdbDevice,
        remote_basename: str,
        out_wav: Path,
        duration: float = 30.0,
        source_path: Optional[Path] = None,
    ) -> tuple[bool, str]:
        """
        录制单个音源（播放 + PC录音）
        
        Args:
            remote_basename: 设备端文件名
            out_wav: 输出WAV文件路径
            duration: 录制时长（秒）
            
        Returns:
            (成功标志, 消息)
        """
        self.log(f"🎙️ 开始录制: {remote_basename} -> {out_wav.name}")
        
        effective_duration = effective_play_seconds(
            source_path=source_path,
            configured_seconds=float(duration),
            log=self.log,
        )
        full_mode = is_full_track_play_enabled()

        # 计算帧数
        total_seconds = PRE_ROLL_SECONDS + effective_duration + POST_TAIL_SECONDS
        frames = int(total_seconds * SAMPLE_RATE)
        
        try:
            # 应用麦克风配置（勿在函数内 import os，否则会遮蔽模块级 os，导致 record_thread 闭包报错）
            if self._mic_spec:
                os.environ["SPEAKER_INPUT_DEVICE"] = self._mic_spec
            
            # 录音（后台线程）
            import threading
            error_box: list[Optional[BaseException]] = [None]
            
            def record_thread():
                try:
                    recording = acquire_recording_buffer(
                        total_seconds,
                        frames,
                        log=self.log,
                        tool=get_record_tool(os.environ.get("SPEAKER_RECORD_TOOL")),
                    )
                    # 裁剪有效段（去掉预滚和尾缓冲）
                    pre_frames = int(PRE_ROLL_SECONDS * SAMPLE_RATE)
                    post_frames = int(POST_TAIL_SECONDS * SAMPLE_RATE)
                    trimmed = recording[pre_frames : len(recording) - post_frames]
                    rms = float(np.sqrt(np.mean(np.square(trimmed.astype(np.float64)))))
                    if rms < 1e-4:
                        self.log(
                            "[WARN] 波形能量极低（近似静音）：请确认侧栏「麦克风」为当前使用的输入设备、"
                            "增益是否过小，以及设备外放音量与摆位。"
                        )

                    # 写入WAV文件
                    out_wav.parent.mkdir(parents=True, exist_ok=True)
                    write_standard_capture_wav(str(out_wav), trimmed)
                    self.log(f"✅ 录制完成: {out_wav.name}")
                except BaseException as exc:
                    error_box[0] = exc
            
            th = threading.Thread(target=record_thread, daemon=True)
            th.start()
            
            # 等待预滚时间
            time.sleep(PRE_ROLL_SECONDS)
            
            # 【自动化】PC 端直接触发设备播放
            self.log(f"🎵 正在通过 ADB 触发播放: {remote_basename}...")
            play_error: list[str | None] = [None]
            try:
                device.shell("input keyevent KEYCODE_WAKEUP", timeout=5)
                device.shell("input keyevent KEYCODE_MENU", timeout=5)

                ok_play, msg_play = self._trigger_device_playback(device, remote_basename)
                if ok_play:
                    self.log(f"✅ {msg_play}")
                else:
                    play_error[0] = msg_play or "设备播放触发失败"
                    self.log(f"❌ {play_error[0]}")
            except Exception as play_err:
                play_error[0] = f"ADB 自动播放触发异常: {play_err}"
                self.log(f"❌ {play_error[0]}")

            # 等待有效播放段（整曲模式须播满探测时长+尾缓冲，不可提前停播）
            self.log(f"⏳ 录制进行中，持续 {effective_duration:.2f} 秒...")
            _release = clean_shell_text(device.shell("getprop ro.build.version.release", timeout=5))
            _sdk = clean_shell_text(device.shell("getprop ro.build.version.sdk", timeout=5))
            _platform = clean_shell_text(device.shell("getprop ro.board.platform", timeout=5))
            _is_mtk_android10 = ("mt" in _platform.lower()) and (_sdk == "29" or _release.startswith("10"))
            used_hard_stop = False
            time.sleep(max(0.0, float(effective_duration)))
            if full_mode and _is_mtk_android10:
                # 播满后再停：避免提前 1s 截断尾音；结束后立即硬停以防 MediaPlaybackActivity 自动重播
                halt_device_audio_playback(
                    device, self.log, reason="MTK Android10 完整播放结束防重播"
                )
                used_hard_stop = True
                for _ in range(2):
                    time.sleep(0.2)
                    _halt_playback_quick(device, self.log, reason="MTK Android10 尾段防重播抑制")

            # 录音线程为实时采集，总墙钟 ≈ total_seconds；分块 PortAudio 会略有余量。
            # 注意：``Thread.join()`` 在 Python 中始终返回 ``None``，不能用 ``if not th.join()`` 判断超时。
            join_timeout = float(total_seconds) + 60.0
            th.join(timeout=join_timeout)
            if th.is_alive():
                self.log(
                    f"[ERR] 录音线程在 {join_timeout:.0f}s 后仍在运行（is_alive=True）。"
                    "若为整曲长录音，请查看上方是否出现 PortAudio / 麦克风相关报错。"
                )
                return (
                    False,
                    f"录音线程超时（>{join_timeout:.0f}s）：请检查麦克风是否被占用、驱动是否正常，"
                    "或尝试设置环境变量 SPEAKER_RECORD_TOOL=sounddevice",
                )
            if used_hard_stop:
                _halt_playback_quick(device, self.log, reason="本条录制结束（快速兜底）")
            else:
                halt_device_audio_playback(device, self.log, reason="本条录制结束")
            
            if play_error[0]:
                return False, f"设备播放触发失败: {play_error[0]}"
            if error_box[0]:
                return False, f"录制失败: {error_box[0]}"
            
            if not out_wav.exists():
                return False, "未生成WAV文件"
            
            return True, "ok"
            
        except Exception as exc:
            return False, f"录制异常: {exc}"

    def _trigger_device_playback(self, device: AdbDevice, remote_basename: str) -> tuple[bool, str]:
        """委托 ``adb_audio_playback``，与常规采集共用 Android10 多策略播放逻辑。"""
        return trigger_android_audio_playback(device, remote_basename, self.log)

    def _push_one_track_with_logs(
        self,
        device: AdbDevice,
        src: Path,
        remote_base: str,
        group: str,
        rel: str,
    ) -> tuple[bool, str]:
        """
        推送单个音源并打印详细日志，便于排查设备端提示“播放列表为空”。
        """
        remote = f"{DEVICE_REMOTE_DIR}/{remote_base}"
        src_abs = src.resolve()
        if not src.is_file():
            return False, f"本地音源不存在: {src_abs}"

        try:
            local_size = src.stat().st_size
        except Exception:
            local_size = -1

        self.log(
            f"  📦 准备推送 | group={group} | rel={rel} | local={src_abs} | "
            f"size={local_size} bytes | remote={remote}"
        )
        t0 = time.time()
        try:
            device.shell(f"mkdir -p {DEVICE_REMOTE_DIR}", timeout=30)
            device.push(str(src_abs), remote)
            cost_ms = int((time.time() - t0) * 1000)
            self.log(f"  ✅ 推送完成: {rel} -> {remote_base} (耗时 {cost_ms} ms)")

            # 设备端校验：确认文件可见、大小可读
            check_cmd = (
                f"if [ -f '{remote}' ]; then "
                f"echo '__PUSH_OK__'; "
                f"ls -l '{remote}'; "
                f"else echo '__PUSH_MISSING__'; fi"
            )
            check_out = device.shell(check_cmd, timeout=15)
            check_out = (check_out or "").strip()
            if "__PUSH_OK__" in check_out:
                self.log(f"  🔎 设备端校验通过: {check_out}")
                return True, "ok"
            self.log(f"  ⚠️ 设备端校验异常输出: {check_out or '<empty>'}")
            return False, f"设备端未找到推送文件: {remote}"
        except Exception as exc:
            return False, f"推送失败 {rel}: {exc}"
    
    def record_device_a(
        self,
        device_serial: str,
        duration: float = 30.0,
    ) -> tuple[bool, str]:
        """
        第一步：录制【被测设备A】的所有音源
        
        Args:
            device_serial: 被测设备A的ADB序列号
            duration: 每个音源的录制时长（秒）
            
        Returns:
            (成功标志, 消息)
        """
        self.log("=" * 60)
        self.log("📱 第一步：录制【被测设备A】")
        self.log("=" * 60)
        
        ensure_output_dirs()

        if self.device_a_has_partial:
            n_ok = sum(1 for r in self._device_a_results if r.get("ok"))
            self.log(
                f"🔁 续录模式：已有 {n_ok}/{len(self._device_a_results)} 条成功，"
                "将跳过已成功音源并从失败处继续"
            )

        tracks = discover_standard_tracks()
        self.log(f"🔍 扫描音源目录: {ASSETS_AUDIO_DIR}")
        self.log(f"📋 发现音源数量: {len(tracks)}")
        if not tracks:
            if ASSETS_AUDIO_DIR.exists():
                files = list(ASSETS_AUDIO_DIR.rglob("*.mp3")) + list(
                    ASSETS_AUDIO_DIR.rglob("*.wav")
                )
                self.log(f"⚠️ 目录下实际存在的音频文件: {[f.name for f in files]}")
            return False, f"未发现音源：请将 wav/mp3 等放入 {ASSETS_AUDIO_DIR}"

        self.log(f"✅ 准备录制 {len(tracks)} 个音源文件")

        try:
            device = adbutils.adb.device(device_serial)
            self.log(f"✅ 已连接设备: {device_serial}")
        except Exception as exc:
            return False, f"连接设备失败: {exc}"

        plan = self._build_track_plan(tracks)
        return self._record_tracks_for_device(
            side="A",
            device=device,
            device_serial=device_serial,
            plan=plan,
            duration=duration,
            existing_results=list(self._device_a_results),
        )
    
    def record_device_b(
        self,
        device_serial: str,
        duration: float = 30.0,
    ) -> tuple[bool, str]:
        """
        第二步：录制【对比设备B】的所有音源
        
        Args:
            device_serial: 对比设备B的ADB序列号
            duration: 每个音源的录制时长（秒）
            
        Returns:
            (成功标志, 消息)
        """
        if not self._device_a_results:
            return False, "必须先录制【被测设备A】，才能录制【对比设备B】"
        if not self.is_device_a_complete:
            return False, (
                "【被测设备A】尚未全部录制成功，请先完成或续录设备 A，再录制对比设备 B"
            )

        self.log("=" * 60)
        self.log("📱 第二步：录制【对比设备B】")
        self.log("=" * 60)

        ensure_output_dirs()

        if self.device_b_has_partial:
            n_ok = sum(1 for r in self._device_b_results if r.get("ok"))
            self.log(
                f"🔁 续录模式：已有 {n_ok}/{len(self._device_b_results)} 条成功，"
                "将跳过已成功音源并从失败处继续"
            )

        tracks = discover_standard_tracks()
        if not tracks:
            return False, f"未发现音源：请将 wav/mp3 等放入 {ASSETS_AUDIO_DIR}"

        self.log(f"📋 发现 {len(tracks)} 个音源文件")

        try:
            device = adbutils.adb.device(device_serial)
            self.log(f"✅ 已连接设备: {device_serial}")
        except Exception as exc:
            return False, f"连接设备失败: {exc}"

        plan = self._build_track_plan(tracks)
        return self._record_tracks_for_device(
            side="B",
            device=device,
            device_serial=device_serial,
            plan=plan,
            duration=duration,
            existing_results=list(self._device_b_results),
        )
    
    def _save_playlist(self):
        """保存播放清单"""
        playlist_payload = {
            "version": 2,
            "mode": "dual_device_single_mic_comparison",
            "session_tag": self._session_tag,
            "safe_tag": self._safe_tag,
            "devices": [
                {
                    "slot": "d01",
                    "role": "被测设备A",
                    "results": self._device_a_results,
                },
                {
                    "slot": "d02",
                    "role": "对比设备B",
                    "results": self._device_b_results,
                },
            ],
        }
        
        try:
            self._playlist_path = RECORDED_DIR / f"{self._safe_tag}_dual_playlist.json"
            self._playlist_path.write_text(
                json.dumps(playlist_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self.log(f"📝 已写入播放清单: {self._playlist_path}")
        except Exception as exc:
            self.log(f"⚠️ 写入播放清单失败: {exc}")
    
    def get_paired_audio_paths(self) -> list[tuple[str, str, str]]:
        """
        获取配对的音频路径（用于刺激比较评分）
        
        Returns:
            [(音源名, 设备A路径, 设备B路径), ...]
        """
        if not self.is_complete:
            raise RuntimeError("两段设备未全部录制完成")
        
        paired = []
        seen_names: set[str] = set()
        for a_result, b_result in zip(self._device_a_results, self._device_b_results):
            if a_result["ok"] and b_result["ok"]:
                track_name = f"{a_result['group']}_{a_result['filename']}"
                if track_name in seen_names:
                    self.log(f"⚠️ 跳过重复音源键（已配对过）: {track_name}")
                    continue
                seen_names.add(track_name)
                paired.append((
                    track_name,
                    a_result["local_wav"],
                    b_result["local_wav"],
                ))
        
        return paired
    
    def detach_in_memory_results_keep_wav_files(self) -> None:
        """
        仅清空内存中的 A/B 录制结果列表，**不删除**磁盘上的 WAV。

        在从 ``*_dual_playlist.json`` 导入并准备「仅用已有录音评测」时调用，避免会话里仍残留
        旧 ``local_wav`` 路径；否则用户若再点「第一步：重新录制」，``clear_recordings()`` 会按
        列表逐条 ``unlink``，易与清单指向的同一批文件重叠，表现为「导入评测删了本地录音」。
        """
        self._device_a_results.clear()
        self._device_b_results.clear()
        self._playlist_path = None

    def clear_device_a_recordings(self) -> None:
        """仅清除被测设备 A 的录音与内存结果。"""
        for result in self._device_a_results:
            path = result.get("local_wav")
            if path and Path(path).is_file():
                try:
                    Path(path).unlink()
                    self.log(f"🗑️ 已删除：{path}")
                except Exception as e:
                    self.log(f"⚠️ 删除失败 {path}: {e}")
        self._device_a_results.clear()
        self._save_playlist()

    def clear_device_b_recordings(self) -> None:
        """仅清除对比设备 B 的录音与内存结果。"""
        for result in self._device_b_results:
            path = result.get("local_wav")
            if path and Path(path).is_file():
                try:
                    Path(path).unlink()
                    self.log(f"🗑️ 已删除：{path}")
                except Exception as e:
                    self.log(f"⚠️ 删除失败 {path}: {e}")
        self._device_b_results.clear()
        self._save_playlist()

    def clear_recordings(self) -> None:
        """清除已录制的音频文件（用于重新开始）"""
        for result in self._device_a_results + self._device_b_results:
            path = result.get("local_wav")
            if path and Path(path).is_file():
                try:
                    Path(path).unlink()
                    self.log(f"🗑️ 已删除：{path}")
                except Exception as e:
                    self.log(f"⚠️ 删除失败 {path}: {e}")
        
        self._device_a_results.clear()
        self._device_b_results.clear()
        self._playlist_path = None
        self._session_tag = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._safe_tag = "".join(
            c if c.isalnum() or c in ("-", "_") else "_" for c in self._session_tag
        )[:64]
