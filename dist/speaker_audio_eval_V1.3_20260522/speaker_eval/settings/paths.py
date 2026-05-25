# -*- coding: utf-8 -*-
"""路径与目录常量（仅从环境变量读取，无业务逻辑）。"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _default_repo_root() -> Path:
    """开发态为源码树项目根；frozen 可执行文件下为 exe 所在目录（便于 output/assets 与程序同发）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    # speaker_eval/settings/paths.py -> parents[2] == 项目根
    return Path(__file__).resolve().parents[2]


_REPO_ROOT: Path = _default_repo_root()

BASE_DIR: Path = Path(os.environ.get("SPEAKER_BASE_DIR", str(_REPO_ROOT))).resolve()

ASSETS_AUDIO_DIR: Path = BASE_DIR / "assets" / "test_audio"
OUTPUT_DIR: Path = BASE_DIR / "output"
RECORDED_DIR: Path = OUTPUT_DIR / "recorded"
ANALYSIS_DIR: Path = OUTPUT_DIR / "analysis"
REPORT_DIR: Path = OUTPUT_DIR / "reports"
LOG_DIR: Path = OUTPUT_DIR / "logs"

DEVICE_REMOTE_DIR: str = "/sdcard/speaker_ai_test"

AUDIO_EXTENSIONS: frozenset[str] = frozenset({".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aac"})
