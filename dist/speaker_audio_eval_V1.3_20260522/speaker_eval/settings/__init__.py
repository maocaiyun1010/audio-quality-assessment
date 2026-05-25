# -*- coding: utf-8 -*-
"""配置聚合导出（兼容原 ``from config import X`` 习惯，根目录 config 薄封装本包）。"""
from speaker_eval.settings.audio_catalog import discover_standard_tracks, ensure_output_dirs
from speaker_eval.settings.dify import (
    DIFY_API_KEY,
    DIFY_API_URL,
    DIFY_FILE_UPLOAD_URL,
    DIFY_USER,
)
from speaker_eval.settings.paths import (
    ANALYSIS_DIR,
    ASSETS_AUDIO_DIR,
    AUDIO_EXTENSIONS,
    BASE_DIR,
    DEVICE_REMOTE_DIR,
    LOG_DIR,
    OUTPUT_DIR,
    RECORDED_DIR,
    REPORT_DIR,
)
from speaker_eval.settings.recording import (
    INPUT_DEVICE_ID,
    INPUT_DEVICE_NAME_SUBSTR,
    OMNIMIC_GAIN_DB,
    PER_TRACK_PLAY_SECONDS,
    POST_TAIL_SECONDS,
    PRE_ROLL_SECONDS,
    RECORD_CHANNELS,
    SAMPLE_RATE,
)
from speaker_eval.settings.service import SERVICE_HOST, SERVICE_PORT

__all__ = [
    "ANALYSIS_DIR",
    "ASSETS_AUDIO_DIR",
    "AUDIO_EXTENSIONS",
    "BASE_DIR",
    "DEVICE_REMOTE_DIR",
    "LOG_DIR",
    "OUTPUT_DIR",
    "RECORDED_DIR",
    "REPORT_DIR",
    "DIFY_API_KEY",
    "DIFY_API_URL",
    "DIFY_FILE_UPLOAD_URL",
    "DIFY_USER",
    "SERVICE_HOST",
    "SERVICE_PORT",
    "SAMPLE_RATE",
    "RECORD_CHANNELS",
    "PER_TRACK_PLAY_SECONDS",
    "PRE_ROLL_SECONDS",
    "POST_TAIL_SECONDS",
    "OMNIMIC_GAIN_DB",
    "INPUT_DEVICE_ID",
    "INPUT_DEVICE_NAME_SUBSTR",
    "discover_standard_tracks",
    "ensure_output_dirs",
]
