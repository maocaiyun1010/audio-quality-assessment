# -*- coding: utf-8 -*-
"""本机 sounddevice 采集（PortAudio）。"""
from __future__ import annotations

from typing import Callable

import sounddevice as sd

from speaker_eval.adapters.audio.device_query import resolve_sounddevice_input_index
from speaker_eval.adapters.audio.portaudio_record import rec_with_samplerate_fallback


def acquire_sounddevice_buffer(
    frames: int,
    log: Callable[[str], None],
    *,
    total_seconds: float | None = None,
) -> np.ndarray:
    _ = total_seconds
    rec_dev = resolve_sounddevice_input_index(log=log)
    if rec_dev is not None:
        try:
            di = sd.query_devices(rec_dev)
            log(
                f"录音后端=sounddevice/本机 (frames={frames}, "
                f"输入设备#{rec_dev}: {di.get('name', '?')})"
            )
        except Exception:
            log(f"录音后端=sounddevice/本机 (frames={frames}, 输入设备#{rec_dev})")
    else:
        try:
            di = sd.query_devices(kind="input")
            log(
                f"录音后端=sounddevice/本机 (frames={frames}, "
                f"默认输入: {di.get('name', '?')})"
            )
        except Exception:
            log(f"录音后端=sounddevice/本机 (frames={frames})")

    return rec_with_samplerate_fallback(frames, log, device=rec_dev)
