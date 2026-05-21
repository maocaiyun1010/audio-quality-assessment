# -*- coding: utf-8 -*-
"""录音后端名称解析（无硬件 I/O）。"""
from __future__ import annotations

import os


def get_record_tool(explicit: str | None = None) -> str:
    if explicit is not None and str(explicit).strip():
        t = str(explicit).strip().lower()
        if t in ("sounddevice", "sd", "mic", "local"):
            return "sounddevice"
        if t in ("omnimic", "omni", "omni_mic", "professional"):
            return "omnimic"
        return "sounddevice"
    t = os.environ.get("SPEAKER_RECORD_TOOL", "sounddevice").strip().lower()
    if t in ("omnimic", "omni", "omni_mic", "professional"):
        return "omnimic"
    return "sounddevice"
