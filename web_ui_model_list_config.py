# -*- coding: utf-8 -*-
"""
侧栏「大模型 / 评测模型」候选项优先级：

1. **``web_ui_dify_api_keys_by_model.json``**（根目录）：若存在且至少有一条「模型名 → app key」映射，
   则列表**首先**展示这些模型名（按键名排序）；其后追加内置 + ``web_ui_custom_models.json`` 中、
   尚未出现在映射表里的名称（便于未配专钥的临时选项）。
2. 否则若 **``web_ui_model_list.json``** 存在且非空，则以该文件为准（数组或 ``{"models":[]}``）。
3. 否则返回 ``merged_builtin_and_custom``（内置 + 自定义）。
"""
from __future__ import annotations

import json
from pathlib import Path

MODEL_LIST_PATH = Path(__file__).resolve().parent / "web_ui_model_list.json"


def effective_model_choices(merged_builtin_and_custom: list[str]) -> list[str]:
    """
    若 ``web_ui_dify_api_keys_by_model.json`` 可解析且含专钥映射，则以其键名为侧栏主列表；
    否则若 ``web_ui_model_list.json`` 存在且解析出非空列表，则返回该列表；
    否则返回 ``merged_builtin_and_custom``。
    """
    try:
        from web_ui_dify_model_keys import load_model_api_key_map

        mp = load_model_api_key_map()
    except Exception:
        mp = {}
    if mp:
        seen: set[str] = set()
        out: list[str] = []
        for k in sorted(mp.keys(), key=lambda x: (str(x).lower(), str(x))):
            ks = str(k).strip()
            if not ks or ks in seen:
                continue
            seen.add(ks)
            out.append(ks)
        for x in merged_builtin_and_custom or []:
            t = str(x).strip()
            if t and t not in seen:
                seen.add(t)
                out.append(t)
        return out

    if not MODEL_LIST_PATH.is_file():
        return list(merged_builtin_and_custom or [])
    try:
        raw = json.loads(MODEL_LIST_PATH.read_text(encoding="utf-8"))
        arr: list | None = None
        if isinstance(raw, list):
            arr = raw
        elif isinstance(raw, dict):
            m = raw.get("models")
            if isinstance(m, list):
                arr = m
        if not arr:
            return list(merged_builtin_and_custom or [])
        seen: set[str] = set()
        out: list[str] = []
        for x in arr:
            t = str(x).strip()
            if not t or t in seen:
                continue
            seen.add(t)
            out.append(t)
        return out if out else list(merged_builtin_and_custom or [])
    except Exception:
        return list(merged_builtin_and_custom or [])
