# -*- coding: utf-8 -*-
"""
整曲播放 vs 固定时长：与 ``sync_capture``、``dual_device_full_recorder`` 共用，避免两处逻辑漂移。

- ``SPEAKER_PLAY_FULL_TRACK`` 为真且能解析源文件时长 → 有效段 = 文件时长（整曲）。
- 否则 → 有效段 = ``configured_seconds``（Web 滑块或 ``PER_TRACK_PLAY_SECONDS``）。
"""
from __future__ import annotations

import os
import wave
from pathlib import Path
from typing import Callable, Optional

import soundfile as sf


def is_full_track_play_enabled() -> bool:
    v = (os.getenv("SPEAKER_PLAY_FULL_TRACK", "") or "").strip().lower()
    return v in {"1", "true", "yes", "on"}


def full_track_end_pad_seconds() -> float:
    """
    整曲模式下在探测时长末尾追加的缓冲（秒），补偿播放器起播/解码延迟，避免尾音被截断。

    可通过 ``SPEAKER_FULL_TRACK_END_PAD_SEC`` 调整（默认 0.35，范围 0～2）。
    """
    if not is_full_track_play_enabled():
        return 0.0
    raw = (os.getenv("SPEAKER_FULL_TRACK_END_PAD_SEC", "0.35") or "").strip()
    try:
        return max(0.0, min(2.0, float(raw)))
    except ValueError:
        return 0.35


def probe_source_duration_seconds(src: Path) -> float | None:
    """返回音源文件时长（秒）；无法识别时返回 None。"""
    try:
        info = sf.info(str(src))
        d = float(getattr(info, "duration", 0.0) or 0.0)
        if d > 0.01:
            return d
    except Exception:
        pass
    if src.suffix.lower() == ".wav":
        try:
            with wave.open(str(src), "rb") as wf:
                fr = float(wf.getframerate() or 0)
                n = float(wf.getnframes() or 0)
            if fr > 0 and n > 0:
                return n / fr
        except Exception:
            pass
    return None


def effective_play_seconds(
    *,
    source_path: Path | None,
    configured_seconds: float,
    log: Optional[Callable[[str], None]] = None,
) -> float:
    """
    计算单条音源「有效播放/录制」秒数。

    Parameters
    ----------
    source_path
        本地音源路径；整曲模式用于探测时长。
    configured_seconds
        固定时长模式下的秒数（须 >0）；来自界面滑块或 ``PER_TRACK_PLAY_SECONDS``。
    """
    log = log or (lambda _m: None)
    configured = max(0.5, float(configured_seconds))
    if not is_full_track_play_enabled():
        return configured
    if source_path is None:
        log("⚠️ 已开启完整音频播放，但缺少源文件路径；回退为时长配置。")
        return configured
    dur = probe_source_duration_seconds(source_path)
    if dur is None:
        log(
            f"⚠️ 无法识别音频时长（{source_path.name}）；回退为时长配置 {configured:.2f}s。"
        )
        return configured
    use_sec = max(0.5, float(dur))
    pad = full_track_end_pad_seconds()
    if pad > 0:
        use_sec += pad
        log(
            f"🎵 完整音频模式：{source_path.name} 文件 {dur:.2f}s + 尾缓冲 {pad:.2f}s "
            f"→ 有效段 {use_sec:.2f}s（忽略时长滑块）"
        )
    else:
        log(f"🎵 完整音频模式：{source_path.name} 时长 {use_sec:.2f}s（忽略时长滑块）")
    return use_sec
