# -*- coding: utf-8 -*-
"""
向后兼容层：配置已迁移至 ``speaker_eval.settings``。

新代码请使用: ``from speaker_eval.settings import BASE_DIR, ...``

录音采样率唯一源为工程根目录 ``recording_config.py``（经 ``speaker_eval.settings`` 导出，当前 48000 Hz）。
"""
from __future__ import annotations

from speaker_eval.settings import *  # noqa: F403
from markdown_report import DIMENSION_KEYS

# Web UI（streamlit）默认值，与 Dify 五维一致
EVAL_METRICS = list(DIMENSION_KEYS)
DUT_SERIAL = ""
REF_SERIAL = ""
GAIN_DB = int(round(float(OMNIMIC_GAIN_DB)))  # noqa: F405
DURATION = int(round(float(PER_TRACK_PLAY_SECONDS)))  # noqa: F405
