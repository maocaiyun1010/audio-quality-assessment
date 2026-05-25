# -*- coding: utf-8 -*-
"""
按「模型展示名」选用不同的 Dify API Key（可选）。

在项目根目录放置 ``web_ui_dify_api_keys_by_model.json``，格式为 JSON 对象，
**键**须与侧栏所选模型名称完全一致（含空格、大小写），**值**为该模型对应的 ``app-...`` API Key。

未出现在映射表中的模型继续使用侧栏「全局」DIFY_API_KEY（或环境变量中的值）。

安全提示：该 JSON 含密钥，勿提交到公开仓库；可加入 .gitignore。
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

_logger = logging.getLogger(__name__)

_KEYS_PATH = Path(__file__).resolve().parent / "web_ui_dify_api_keys_by_model.json"

_last_baseline: str | None = None


def _key_tail(key: str, n: int = 10) -> str:
    k = (key or "").strip()
    if not k:
        return "(空)"
    return k[-n:] if len(k) >= n else k


def emit_dify_key_audit(model_name: str, *, note: str = "") -> None:
    """
    确认当前 ``os.environ['DIFY_API_KEY']`` 是否与 ``web_ui_dify_api_keys_by_model.json``
    中该模型专钥一致；打印中文日志（仅密钥后缀，不打印完整 app key）。
    """
    m = (model_name or "").strip()
    mp = load_model_api_key_map()
    actual = (os.environ.get("DIFY_API_KEY") or "").strip()
    tail = _key_tail(actual)
    n_note = f" | {note}" if note else ""

    if not m:
        msg = f"[DifyKey确认] 未指定模型名；当前 app_key 长度={len(actual)} 后缀=…{tail}{n_note}"
        print(msg, flush=True)
        _logger.info("%s", msg)
        return

    expected = (mp.get(m) or "").strip()
    if expected:
        if actual == expected:
            msg = (
                f"[DifyKey确认] 模型 {m!r} 已使用映射表中专钥（与 web_ui_dify_api_keys_by_model.json 一致），"
                f"app_key 后缀 …{tail}{n_note}"
            )
        else:
            msg = (
                f"[DifyKey警告] 模型 {m!r} 在映射表中有专钥，但当前 DIFY_API_KEY 与表中值不一致。"
                f" 请检查父进程传入的 resolved 或基线 Key。当前后缀 …{tail}{n_note}"
            )
    else:
        msg = (
            f"[DifyKey确认] 模型 {m!r} 未在映射表中命中，使用全局/基线 app Key，"
            f"后缀 …{tail}{n_note}"
        )

    print(msg, flush=True)
    _logger.info("%s", msg)


def describe_current_key_for_model(model_name: str) -> str:
    """单行摘要供 Web UI「运行日志」展示（不含完整密钥）。"""
    m = (model_name or "").strip()
    mp = load_model_api_key_map()
    actual = (os.environ.get("DIFY_API_KEY") or "").strip()
    tail = _key_tail(actual)
    if not m:
        return f"app_key 长度={len(actual)} 后缀=…{tail}"
    expected = (mp.get(m) or "").strip()
    if expected:
        if actual == expected:
            return f"模型 {m!r} 已使用映射表中专钥（与 JSON 一致），后缀 …{tail}"
        return f"模型 {m!r} 与映射表专钥不一致，请检查；当前后缀 …{tail}"
    return f"模型 {m!r} 未在映射表中，使用全局/基线 Key，后缀 …{tail}"


def _sync_dify_key_into_loaded_modules() -> None:
    key = (os.environ.get("DIFY_API_KEY") or "").strip()
    if not key:
        return
    try:
        import config as _cfg

        _cfg.DIFY_API_KEY = key
    except Exception as exc:
        _logger.debug("同步 DIFY_API_KEY 到 config 失败: %s", exc)
    try:
        import speaker_eval.settings.dify as _dify

        _dify.DIFY_API_KEY = key
    except Exception as exc:
        _logger.debug("同步 DIFY_API_KEY 到 speaker_eval.settings.dify 失败: %s", exc)


def load_model_api_key_map() -> dict[str, str]:
    if not _KEYS_PATH.is_file():
        return {}
    try:
        raw = json.loads(_KEYS_PATH.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {}
        out: dict[str, str] = {}
        for k, v in raw.items():
            ks = str(k).strip()
            vs = str(v).strip()
            if ks and vs:
                out[ks] = vs
        return out
    except Exception:
        return {}


def set_dify_api_key_baseline(baseline: str) -> None:
    """
    记录「全局默认」密钥（一般为侧栏 DIFY_API_KEY 文本框内容）。
    在切换模型前调用；映射表未命中时使用该值写回 ``DIFY_API_KEY``。
    若传入空串，则回退为当前环境变量 ``DIFY_API_KEY``（便于子进程沿用父进程已注入的 Key）。
    """
    global _last_baseline
    b = (baseline or "").strip()
    if not b:
        b = (os.environ.get("DIFY_API_KEY") or "").strip()
    _last_baseline = b or None


def configure_api_key_for_model(model_name: str) -> None:
    """
    按 ``model_name`` 查映射表，命中则 ``os.environ['DIFY_API_KEY']`` 用专钥；
    否则用 ``set_dify_api_key_baseline`` 记录的默认值；若仍无则保持当前环境变量。
    """
    m = (model_name or "").strip()
    mp = load_model_api_key_map()
    custom = (mp.get(m) or "").strip()
    src = "mapped" if custom else ("baseline" if _last_baseline else "unchanged")
    if custom:
        os.environ["DIFY_API_KEY"] = custom
    elif _last_baseline:
        os.environ["DIFY_API_KEY"] = _last_baseline
    _sync_dify_key_into_loaded_modules()
    key = (os.environ.get("DIFY_API_KEY") or "").strip()
    tail = _key_tail(key)
    src_zh = {
        "mapped": "映射表专钥",
        "baseline": "全局侧栏基线",
        "unchanged": "保持当前环境变量",
    }.get(src, src)
    msg = (
        f"[DifyKey审计] model={m!r} source={src} ({src_zh}) mapped_table_entries={len(mp)} "
        f"app_key_len={len(key)} tail=…{tail}"
    )
    print(msg, flush=True)
    _logger.info("%s", msg)
    emit_dify_key_audit(m, note=f"configure_api_key_for_model·{src_zh}")


def restore_dify_api_key_baseline() -> None:
    """将 ``DIFY_API_KEY`` 恢复为最近一次 ``set_dify_api_key_baseline`` 的值。"""
    if _last_baseline:
        os.environ["DIFY_API_KEY"] = _last_baseline
        _sync_dify_key_into_loaded_modules()


def apply_dify_api_key_string(key: str, *, model_name: str | None = None) -> None:
    """
    将密钥写入 ``os.environ['DIFY_API_KEY']`` 并同步到已导入的 ``config`` / ``speaker_eval.settings.dify``
    （供子进程配置中的 ``dify_api_key_resolved`` 使用，避免仅改环境变量而模块内仍为旧值）。

    传入 ``model_name`` 时会额外打印与映射表是否一致的确认日志。
    """
    k = (key or "").strip()
    if not k:
        return
    os.environ["DIFY_API_KEY"] = k
    _sync_dify_key_into_loaded_modules()
    if (model_name or "").strip():
        emit_dify_key_audit(
            str(model_name).strip(),
            note="apply_dify_api_key_string·父进程解析的 dify_api_key_resolved",
        )
