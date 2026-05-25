# -*- coding: utf-8 -*-
"""麦克风录音适配公共 API。"""
from speaker_eval.adapters.audio.buffer import acquire_recording_buffer
from speaker_eval.adapters.audio.device_query import (
    find_omnimic_input_device,
    find_omnimic_input_device_meta,
)
from speaker_eval.adapters.audio.tooling import get_record_tool

__all__ = [
    "acquire_recording_buffer",
    "find_omnimic_input_device",
    "find_omnimic_input_device_meta",
    "get_record_tool",
]
