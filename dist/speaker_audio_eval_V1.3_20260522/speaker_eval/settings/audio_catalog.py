# -*- coding: utf-8 -*-
"""音源目录扫描与输出目录创建（纯路径/扫描逻辑）。"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from speaker_eval.settings.paths import (
    ANALYSIS_DIR,
    ASSETS_AUDIO_DIR,
    AUDIO_EXTENSIONS,
    LOG_DIR,
    RECORDED_DIR,
    REPORT_DIR,
)


def _natural_sort_key(name: str) -> list:
    parts = re.split(r"(\d+)", name)
    key: list = []
    for p in parts:
        if p.isdigit():
            key.append(int(p))
        else:
            key.append(p.lower())
    return key


def _is_audio_file(path: Path) -> bool:
    return (
        path.is_file()
        and path.suffix.lower() in AUDIO_EXTENSIONS
        and not path.name.startswith(".")
    )


def _load_selected_tracks_from_env() -> set[str] | None:
    """
    从环境变量读取用户在 Web UI 勾选的音源列表。
    返回 None 代表不过滤；返回空集合代表显式选择为空。
    """
    raw = (os.getenv("SPEAKER_SELECTED_TRACKS_JSON") or "").strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
        if not isinstance(data, list):
            return None
        selected = set()
        for item in data:
            s = str(item or "").strip().replace("\\", "/")
            if s:
                selected.add(s)
        return selected
    except Exception:
        return None


def discover_standard_tracks(
    assets_dir: Path | None = None,
    *,
    apply_env_filter: bool = True,
) -> list[tuple[str, str]]:
    """
    扫描 assets/test_audio，返回 [(分组名, posix 相对路径), ...]。
    """
    root = (assets_dir or ASSETS_AUDIO_DIR).resolve()
    if not root.is_dir():
        return []

    entries: list[tuple[str, str]] = []

    subdirs = sorted(
        [p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")],
        key=lambda p: _natural_sort_key(p.name),
    )
    subdir_has_audio = False
    for d in subdirs:
        files = sorted(
            [p for p in d.iterdir() if _is_audio_file(p)],
            key=lambda p: _natural_sort_key(p.name),
        )
        if files:
            subdir_has_audio = True
        for f in files:
            entries.append((d.name, f.relative_to(root).as_posix()))

    if not subdir_has_audio:
        root_files = sorted(
            [p for p in root.iterdir() if _is_audio_file(p)],
            key=lambda p: _natural_sort_key(p.name),
        )
        for f in root_files:
            entries.append(("根目录", f.relative_to(root).as_posix()))

    if not apply_env_filter:
        return entries

    selected = _load_selected_tracks_from_env()
    if selected is None:
        return entries
    return [(g, rel) for g, rel in entries if rel in selected]


def ensure_output_dirs() -> None:
    for p in (RECORDED_DIR, ANALYSIS_DIR, REPORT_DIR, LOG_DIR):
        p.mkdir(parents=True, exist_ok=True)
