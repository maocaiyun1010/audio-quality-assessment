# -*- coding: utf-8 -*-
"""
Web UI 实时步骤日志：子进程通过环境变量 ``SPEAKER_WEB_UI_LIVE_LOG`` 指向 JSONL 文件，
各行一条 JSON：ts / step / title / detail。
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any


def append_live_step(step: str, title: str, detail: str = "") -> None:
    """追加一行步骤记录；未设置路径时静默跳过。"""
    path = (os.environ.get("SPEAKER_WEB_UI_LIVE_LOG") or "").strip()
    if not path:
        return
    try:
        rec: dict[str, Any] = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "step": step,
            "title": title,
            "detail": (detail or "")[:2000],
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
    except Exception:
        pass


def append_live_scoring_detail(detail: str) -> None:
    """更新「评分计算」步骤的细粒度说明（上传完成 / 等待模型等）。"""
    append_live_step("scoring", "评分计算", detail)
