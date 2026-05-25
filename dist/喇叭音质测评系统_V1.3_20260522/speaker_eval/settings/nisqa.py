# -*- coding: utf-8 -*-
"""NISQA 本地客观音质评测（可选，默认关闭）。"""
from __future__ import annotations

import os
from pathlib import Path

from speaker_eval.settings.paths import BASE_DIR

NISQA_ENABLED: bool = os.environ.get("SPEAKER_NISQA_ENABLED", "").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)

# 权重目录：默认 ``<项目>/models/nisqa/``，内含 ``nisqa.tar``
NISQA_MODEL_DIR: Path = Path(
    os.environ.get("SPEAKER_NISQA_MODEL_DIR", str(BASE_DIR / "models" / "nisqa"))
).resolve()

NISQA_WEIGHTS_FILE: str = (
    os.environ.get("SPEAKER_NISQA_WEIGHTS", "nisqa.tar").strip() or "nisqa.tar"
)

# skip：推理失败仅记日志，不中断 Dify 评分；fail：任一条 NISQA 失败则整段评分返回错误
NISQA_ON_FAILURE: str = (
    os.environ.get("SPEAKER_NISQA_ON_FAILURE", "skip").strip().lower() or "skip"
)

# transmitted | tts
NISQA_MODEL_KIND: str = (
    os.environ.get("SPEAKER_NISQA_MODEL_KIND", "transmitted").strip().lower()
    or "transmitted"
)

NISQA_PREDICT_BATCH_SIZE: int = max(
    1, int(os.environ.get("SPEAKER_NISQA_BS", "1") or "1")
)
