# -*- coding: utf-8 -*-
"""
PortAudio / sounddevice 录音：工程统一 ``SAMPLE_RATE``（见 ``recording_config``）原生采集，不重采样。
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any, Callable

import numpy as np
import sounddevice as sd

from speaker_eval.adapters.audio.device_query import input_device_allows_wasapi_extra_settings
from speaker_eval.settings.recording import RECORD_CHANNELS, SAMPLE_RATE


def _max_frames_per_sd_rec() -> int:
    """
    超过该帧数时，不再使用单次 ``sd.rec(frames=全部)``（易触发 -9992），
    改为 **单次 ``InputStream`` + 分片 ``read``**（只开一次流，避免 WDM-KS 连续开流 -9999）。
    """
    raw = (os.environ.get("SPEAKER_SD_REC_MAX_FRAMES") or "").strip()
    if raw.isdigit() and int(raw) >= 2048:
        return int(raw)
    return int(SAMPLE_RATE * 60)


def _stream_open_retries() -> int:
    raw = (os.environ.get("SPEAKER_SD_STREAM_OPEN_RETRIES") or "").strip()
    if raw.isdigit():
        return max(1, min(12, int(raw)))
    return 6


def _retry_backoff_base_sec() -> float:
    raw = (os.environ.get("SPEAKER_SD_STREAM_RETRY_GAP_SEC") or "").strip()
    if raw:
        try:
            return max(0.05, float(raw))
        except ValueError:
            pass
    return 0.22


def _init_release_gap_sec() -> float:
    """``sd.stop()`` 之后短暂停顿，便于 WDM 驱动完成 teardown，再开新流。"""
    raw = (os.environ.get("SPEAKER_SD_INIT_RELEASE_GAP_SEC") or "").strip()
    if raw:
        try:
            return max(0.0, min(2.0, float(raw)))
        except ValueError:
            pass
    return 0.12


def release_sounddevice_host(log: Callable[[str], None] | None = None) -> None:
    """
    停止本进程内 sounddevice 管理的活动流并稍作等待，用于失败重试前或避免与下次开流竞态。

    在 Windows WDM-KS 上，异常中断后若不 ``stop``，再次 ``rec``/``InputStream`` 可能长时间阻塞或报 -9999。
    """
    lg = log or (lambda _m: None)
    try:
        sd.stop()
    except Exception as exc:
        lg(f"[WARN] sounddevice.stop() 释放主机流时异常（已忽略）：{exc}")
    gap = _init_release_gap_sec()
    if gap > 0:
        time.sleep(gap)


def _sd_wait_deadline_sec(frames: int) -> float:
    """``sd.wait()`` 最长阻塞时间（秒），防止驱动/宿主异常时无限等待。"""
    rec = max(0.05, int(frames) / float(SAMPLE_RATE))
    raw = (os.environ.get("SPEAKER_SD_WAIT_EXTRA_SEC") or "").strip()
    try:
        extra = float(raw) if raw else 10.0
    except ValueError:
        extra = 10.0
    extra = max(5.0, extra)
    cap_raw = (os.environ.get("SPEAKER_SD_WAIT_MAX_SEC") or "").strip()
    try:
        cap = float(cap_raw) if cap_raw else 7200.0
    except ValueError:
        cap = 7200.0
    cap = max(60.0, cap)
    return min(cap, max(30.0, rec + extra))


def _sd_wait_bounded(frames: int, log: Callable[[str], None]) -> None:
    done = threading.Event()
    box: list[BaseException | None] = [None]

    def _worker() -> None:
        try:
            sd.wait()
        except BaseException as exc:
            box[0] = exc
        finally:
            done.set()

    deadline = _sd_wait_deadline_sec(frames)
    th = threading.Thread(target=_worker, name="sounddevice_sd_wait", daemon=True)
    th.start()
    if done.wait(timeout=deadline):
        if box[0] is not None:
            raise box[0]
        return
    log(
        f"[WARN] sd.wait 超过 {deadline:.1f}s 仍未返回，调用 stop() 以解除可能的阻塞 "
        f"（可调大 SPEAKER_SD_WAIT_EXTRA_SEC / SPEAKER_SD_WAIT_MAX_SEC）"
    )
    try:
        sd.stop()
    except Exception as exc:
        log(f"[WARN] 解除阻塞时 stop() 异常（已忽略）：{exc}")
    th.join(timeout=min(8.0, max(2.0, deadline * 0.02)))
    if th.is_alive():
        log("[WARN] sd.wait 后台线程仍未结束，后续录音若异常请重试或重启进程")
    raise sd.PortAudioError(f"sd.wait 超时（>{deadline:.1f}s），已尝试 stop 释放")


def _read_block_frames() -> int:
    """``InputStream.read`` 单次帧数，须 ≤ ``blocksize``。"""
    raw = (os.environ.get("SPEAKER_SD_READ_BLOCK_FRAMES") or "").strip()
    if raw.isdigit():
        return max(256, min(65536, int(raw)))
    return min(8192, max(1024, SAMPLE_RATE // 25))


def _wasapi_shared_explicitly_enabled() -> bool:
    """默认关闭：部分 WDM-KS 对 ``WasapiSettings`` 的 IOCTL 会报 -9999；需要时再设环境变量为 1。"""
    return (os.environ.get("SPEAKER_SD_WASAPI_SHARED", "0") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _win_aggressive_wasapi_first() -> bool:
    """恢复「先 high latency + WASAPI」的旧顺序（更易在少数机器上成功，但在更多 WDM 上会 IOCTL 失败）。"""
    return (os.environ.get("SPEAKER_SD_WIN_AGGRESSIVE_WASAPI", "0") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _windows_stream_option_tiers(device: int | None = None) -> list[dict[str, Any]]:
    """
    Windows 下开流可选参数组合（按顺序尝试）。

    默认从最简（完全使用 PortAudio 宿主默认）开始，避免 ``prop_id=0`` 类 KSPROPERTY IOCTL 失败。
    非 WASAPI 设备（例如 MME）上不得附加 ``WasapiSettings``，否则可能开流失败。
    """
    minimal: dict[str, Any] = {}
    high_lat = {"latency": "high"}
    low_lat = {"latency": "low"}
    wasapi: dict[str, Any] = {}
    if _wasapi_shared_explicitly_enabled() and input_device_allows_wasapi_extra_settings(device):
        try:
            wasapi = {
                "latency": "high",
                "extra_settings": sd.WasapiSettings(
                    exclusive=False,
                    auto_convert=True,
                ),
            }
        except Exception:
            wasapi = {}

    if _win_aggressive_wasapi_first() and wasapi:
        tiers = [wasapi, high_lat, low_lat, minimal]
    else:
        tiers = [minimal, high_lat, low_lat]
        if wasapi:
            tiers.append(wasapi)
    # 去重：避免相邻完全相同的 dict（wasapi 空时）
    out: list[dict[str, Any]] = []
    for t in tiers:
        if t not in out:
            out.append(t)
    return out if out else [{}]


def _is_transient_portaudio_err(exc: BaseException) -> bool:
    s = str(exc).lower()
    markers = (
        "-9999",
        "9999",
        "unanticipated",
        "wdm",
        "wdm-ks",
        "ioctl",
        "gle =",
        "-9992",
        "insufficient memory",
        "device unavailable",
        "paerrorcode",
        "bad i/o structure",
        "sd.wait",
        "超时",
    )
    return any(m in s for m in markers)


def is_recording_host_transient_error(exc: BaseException) -> bool:
    """供外层会话重试判断是否值得在 ``release_sounddevice_host`` 后再试。"""
    return _is_transient_portaudio_err(exc)


def _inputstream_blocksize_fallbacks(primary: int) -> list[int]:
    """在 IOCTL 失败时尝试更保守的 blocksize（2 的幂常见更稳）。"""
    alts = (primary, 4096, 2048, 1024, 512)
    seen: set[int] = set()
    out: list[int] = []
    for b in alts:
        b = int(b)
        if b < 256 or b in seen:
            continue
        seen.add(b)
        out.append(b)
    return out if out else [1024]


def _rec_via_single_inputstream(
    frames: int,
    log: Callable[[str], None],
    *,
    device: int | None,
) -> np.ndarray:
    frames = int(frames)
    primary_block = _read_block_frames()
    retries = _stream_open_retries()
    base_gap = _retry_backoff_base_sec()
    last_exc: BaseException | None = None

    for bi, block in enumerate(_inputstream_blocksize_fallbacks(primary_block)):
        if bi > 0:
            release_sounddevice_host(log)
            time.sleep(0.15)
            log(f"PortAudio：InputStream 改用 blocksize={block}（缓解部分 WDM 驱动 IOCTL 失败）")

        base_kw: dict[str, Any] = {
            "samplerate": SAMPLE_RATE,
            "channels": RECORD_CHANNELS,
            "dtype": "float32",
            "blocksize": block,
        }
        if device is not None:
            base_kw["device"] = device

        tiers = _windows_stream_option_tiers(device) if os.name == "nt" else [{}]
        for ti, tier in enumerate(tiers):
            if ti > 0:
                release_sounddevice_host(log)
                time.sleep(0.1)
                keys = ", ".join(sorted(tier.keys())) if tier else "默认"
                log(f"PortAudio：InputStream 尝试参数层 {ti + 1}/{len(tiers)}（{keys}）")

            kw = {**base_kw, **tier}
            for attempt in range(retries):
                if attempt > 0:
                    release_sounddevice_host(log)
                    delay = min(2.5, base_gap * (1.55 ** (attempt - 1)))
                    time.sleep(delay)
                    log(
                        f"PortAudio：InputStream 同层重试 {attempt + 1}/{retries}（已等待 {delay:.2f}s）"
                    )
                try:
                    parts: list[np.ndarray] = []
                    got = 0
                    with sd.InputStream(**kw) as stream:
                        while got < frames:
                            need = frames - got
                            nread = min(need, block)
                            data, overflowed = stream.read(nread)
                            if overflowed:
                                log("[WARN] PortAudio 输入 buffer overflow（overflow）")
                            if data is None:
                                continue
                            arr = np.asarray(data, dtype=np.float32)
                            if arr.size == 0:
                                time.sleep(0.001)
                                continue
                            if arr.ndim == 1:
                                arr = arr.reshape(-1, 1)
                            parts.append(arr)
                            got += int(arr.shape[0])

                    if not parts:
                        raise sd.PortAudioError("InputStream 未读到任何采样")

                    out = np.concatenate(parts, axis=0)
                    if out.shape[0] < frames:
                        log(
                            f"[WARN] InputStream 仅采集到 {out.shape[0]}/{frames} 帧，"
                            "末尾以静音填充（请检查设备是否在采集全程保持外放）"
                        )
                        out = np.pad(
                            out,
                            ((0, frames - int(out.shape[0])), (0, 0)),
                            mode="constant",
                        )
                    elif out.shape[0] > frames:
                        out = out[:frames]
                    return out.astype(np.float32, copy=False)

                except sd.PortAudioError as exc:
                    last_exc = exc
                    transient = _is_transient_portaudio_err(exc)
                    release_sounddevice_host(log)
                    if not transient:
                        raise
                    if attempt + 1 >= retries:
                        break
                    log(f"[WARN] PortAudio InputStream 失败（将同层重试）：{exc}")
                except OSError as exc:
                    last_exc = exc
                    release_sounddevice_host(log)
                    if not _is_transient_portaudio_err(exc):
                        raise
                    if attempt + 1 >= retries:
                        break
                    log(f"[WARN] 录音 I/O 异常（将同层重试）：{exc}")

    if last_exc is not None:
        raise last_exc
    raise sd.PortAudioError("InputStream 开流失败（无可用异常信息）")


def _rec_once(
    frames: int,
    log: Callable[[str], None],
    *,
    device: int | None,
) -> np.ndarray:
    frames = int(frames)
    base_kw: dict[str, Any] = {
        "frames": frames,
        "samplerate": SAMPLE_RATE,
        "channels": RECORD_CHANNELS,
        "dtype": "float32",
    }
    if device is not None:
        base_kw["device"] = device

    retries = _stream_open_retries()
    base_gap = _retry_backoff_base_sec()
    last_exc: BaseException | None = None

    tiers = _windows_stream_option_tiers(device) if os.name == "nt" else [{}]
    for ti, tier in enumerate(tiers):
        if ti > 0:
            release_sounddevice_host(log)
            time.sleep(0.1)
            keys = ", ".join(sorted(tier.keys())) if tier else "默认"
            log(f"PortAudio：sd.rec 尝试参数层 {ti + 1}/{len(tiers)}（{keys}）")

        kw = {**base_kw, **tier}
        for attempt in range(retries):
            if attempt > 0:
                release_sounddevice_host(log)
                delay = min(2.5, base_gap * (1.55 ** (attempt - 1)))
                time.sleep(delay)
                log(
                    f"PortAudio：sd.rec 同层重试 {attempt + 1}/{retries}（已等待 {delay:.2f}s）"
                )
            try:
                recording = sd.rec(**kw)
                _sd_wait_bounded(frames, log)
                return recording.astype(np.float32, copy=False)
            except sd.PortAudioError as exc:
                last_exc = exc
                release_sounddevice_host(log)
                if not _is_transient_portaudio_err(exc):
                    raise
                if attempt + 1 >= retries:
                    break
                log(f"[WARN] PortAudio 开流失败（将同层重试）：{exc}")
            except OSError as exc:
                last_exc = exc
                release_sounddevice_host(log)
                if not _is_transient_portaudio_err(exc):
                    raise
                if attempt + 1 >= retries:
                    break
                log(f"[WARN] 录音 I/O 异常（将同层重试）：{exc}")

    if last_exc is not None:
        raise last_exc
    raise sd.PortAudioError("sd.rec 开流失败")


def _session_init_retries() -> int:
    """整段录音失败后的会话级重试次数（每次会先 ``stop`` 并短暂等待）。"""
    raw = (os.environ.get("SPEAKER_SD_SESSION_RETRIES") or "").strip()
    if raw.isdigit():
        return max(1, min(8, int(raw)))
    return 2


def _session_retry_gap_after_fail_sec() -> float:
    raw = (os.environ.get("SPEAKER_SD_SESSION_RETRY_GAP_SEC") or "").strip()
    if raw:
        try:
            return max(0.0, min(5.0, float(raw)))
        except ValueError:
            pass
    return 0.2


def rec_with_samplerate_fallback(
    frames: int,
    log: Callable[[str], None],
    *,
    device: int | None,
) -> np.ndarray:
    """
    以 ``SAMPLE_RATE`` 打开输入流，返回 ``(frames, RECORD_CHANNELS)`` float32。

    较短片段用 ``sd.rec``；超过 ``_max_frames_per_sd_rec()`` 时用 **单次 InputStream 分片 read**，
    兼顾 -9992 与 WDM-KS -9999。失败时对可恢复错误做 **会话级重试**（``stop`` + 等待后再整段重录）。
    """
    frames = int(frames)
    if frames < 1:
        frames = 1

    attempts = _session_init_retries()
    last_exc: BaseException | None = None
    for ai in range(attempts):
        if ai > 0:
            log(
                f"PortAudio：会话级重试 {ai + 1}/{attempts}（已 stop + 主机释放等待，整段重新采集）"
            )
            release_sounddevice_host(log)
            time.sleep(_session_retry_gap_after_fail_sec())
        try:
            return _rec_with_samplerate_fallback_once(frames, log, device=device)
        except (sd.PortAudioError, OSError) as exc:
            last_exc = exc
            if ai + 1 >= attempts or not _is_transient_portaudio_err(exc):
                raise
            log(f"[WARN] PortAudio 整段采集失败，将释放设备后重试会话：{exc}")
            release_sounddevice_host(log)

    if last_exc is not None:
        raise last_exc
    raise sd.PortAudioError("PortAudio 会话重试耗尽")


def _rec_with_samplerate_fallback_once(
    frames: int,
    log: Callable[[str], None],
    *,
    device: int | None,
) -> np.ndarray:
    cap = _max_frames_per_sd_rec()
    if frames <= cap:
        log(f"PortAudio：原生 {SAMPLE_RATE} Hz，sd.rec 单次采集（frames={frames}）")
        try:
            return _rec_once(frames, log, device=device)
        except (sd.PortAudioError, OSError) as exc:
            if os.name != "nt" or not _is_transient_portaudio_err(exc):
                raise
            log("[WARN] sd.rec 失败后改用单次 InputStream 同长度采集（Windows WDM 兼容路径）")
            release_sounddevice_host(log)
            return _rec_via_single_inputstream(frames, log, device=device)

    log(
        f"PortAudio：原生 {SAMPLE_RATE} Hz，总 frames={frames}（约 {frames / float(SAMPLE_RATE):.1f}s），"
        f"使用单次 InputStream + 分片 read（初始 blocksize≈{_read_block_frames()}），"
        "避免单次 sd.rec 过大触发 -9992，并避免多次开流触发 WDM-KS -9999"
    )
    return _rec_via_single_inputstream(frames, log, device=device)
