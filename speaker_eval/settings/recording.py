# -*- coding: utf-8 -*-
"""录制相关标量配置（环境变量 → 常量，无 I/O）。"""
from __future__ import annotations

import os

from recording_config import SAMPLE_RATE

# 与 OmniMic 落盘约定一致：单声道；WAV 为 48kHz PCM_16（见 ``wav_capture_write.write_standard_capture_wav``）
RECORD_CHANNELS: int = 1
PER_TRACK_PLAY_SECONDS: float = float(os.environ.get("SPEAKER_PER_TRACK_SEC", "30.0"))
PRE_ROLL_SECONDS: float = float(os.environ.get("SPEAKER_PRE_ROLL_SEC", "0.4"))
POST_TAIL_SECONDS: float = float(os.environ.get("SPEAKER_POST_TAIL_SEC", "1.0"))

_og = os.environ.get("SPEAKER_OMNIMIC_GAIN_DB", "-6").strip()
try:
    OMNIMIC_GAIN_DB: float = float(_og)
except ValueError:
    OMNIMIC_GAIN_DB = 0

_SPEAKER_IN = os.environ.get("SPEAKER_INPUT_DEVICE", "").strip()
INPUT_DEVICE_ID: int | None = int(_SPEAKER_IN) if _SPEAKER_IN.isdigit() else None
INPUT_DEVICE_NAME_SUBSTR: str = (
    _SPEAKER_IN if _SPEAKER_IN and not _SPEAKER_IN.isdigit() else ""
)
