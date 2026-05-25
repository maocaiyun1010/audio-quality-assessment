# -*- coding: utf-8 -*-
"""
日志初始化：与业务逻辑分离，输出可交付的运行日志文件。

控制台 + UTF-8 滚动文件；调用方在进程入口执行一次 ``setup_app_logging``。
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional


def setup_app_logging(
    log_dir: Path,
    *,
    name: str = "speaker_eval",
    level: int = logging.INFO,
    file_prefix: str = "run",
) -> logging.Logger:
    """
    配置根 logger：控制台 StreamHandler + 按次运行文件（由调用方决定何时 Rotating）。

    返回命名 logger ``speaker_eval``，业务代码使用 ``logging.getLogger("speaker_eval")``。
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(level)
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(level)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    from datetime import datetime

    log_file = log_dir / f"{file_prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(level)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    logger.debug("日志初始化完成，文件: %s", log_file)
    return logger


def get_logger(sub: Optional[str] = None) -> logging.Logger:
    base = "speaker_eval"
    return logging.getLogger(f"{base}.{sub}" if sub else base)
