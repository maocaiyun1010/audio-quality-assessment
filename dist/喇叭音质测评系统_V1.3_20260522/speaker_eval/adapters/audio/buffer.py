# -*- coding: utf-8 -*-
"""录音缓冲统一入口：按工具分发至 sounddevice / OmniMic。"""
from __future__ import annotations

import os
from typing import Callable, Optional

import numpy as np

from speaker_eval.adapters.audio.omnimic_cli import acquire_via_cli
from speaker_eval.adapters.audio.omnimic_portaudio import acquire_via_portaudio
from speaker_eval.adapters.audio.sounddevice_capture import acquire_sounddevice_buffer
from speaker_eval.adapters.audio.tooling import get_record_tool


def acquire_recording_buffer(
    total_seconds: float,
    frames: int,
    log: Optional[Callable[[str], None]] = None,
    *,
    tool: str | None = None,
) -> np.ndarray:
    log = log or (lambda _m: None)
    which = get_record_tool(tool)
    if which == "omnimic":
        exe = os.environ.get("SPEAKER_OMNIMIC_EXE", "").strip().strip('"')
        if exe:
            return acquire_via_cli(total_seconds, frames, log)
        return acquire_via_portaudio(frames, log)
    return acquire_sounddevice_buffer(frames, log, total_seconds=total_seconds)
