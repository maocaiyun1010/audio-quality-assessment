# -*- coding: utf-8 -*-
"""
从评测流水线产出的 analysis JSON 构建「逐音源」汇总表与五维平均分。

与 Dify 返回字段兼容：音源名称 / 音源、分组、五维整数、综合结论 / 对比总结、专业点评、综合评价。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from markdown_report import DIMENSION_KEYS

_DIMS: tuple[str, ...] = DIMENSION_KEYS


def _scalar_str_for_cell(v: Any) -> str:
    """
    表格展示用纯文本：忽略 Dify/误解析产生的 JSON Schema 碎片（如 ``{"type": "string"}``），
    避免 ``str(dict)`` 进入「音源名称」「分组」列。
    """
    if v is None:
        return ""
    if isinstance(v, (str, int, float, bool)):
        return str(v).strip()
    if isinstance(v, dict):
        keys = set(v.keys())
        if keys <= {"type", "description", "enum", "default", "title"}:
            return ""
        for sub in ("value", "text", "label", "name", "title", "content"):
            inner = v.get(sub)
            if isinstance(inner, (str, int, float)):
                s = str(inner).strip()
                if s:
                    return s
        return ""
    if isinstance(v, (list, tuple)):
        if len(v) == 1 and isinstance(v[0], (str, int, float)):
            return str(v[0]).strip()
        return ""
    return str(v).strip()


def analysis_json_path_for_web_scores(score_json_path: Path) -> Path | None:
    """
    由 ``output/analysis/web_ui_scores_{safe}.json`` 解析对应 ``analysis_{safe}_*.json``（取最新 mtime）。

    其中 ``{safe}`` 为 stem 去掉前缀 ``web_ui_scores_`` 后的整段，例如：

    - 常规子进程：**``manual_YYYYMMDD_HHMMSS``** 等；
    - **Web 双设备单麦**：**``dual_webui_YYYYMMDD_HHMMSS``**，对应 ``analysis_dual_webui_*_*.json``；
    - 多模型追加：``{safe}`` 中常含 ``__模型标签``，仍按 ``analysis_{safe}_*.json`` 配对。

    兼容旧版双设备命名 ``web_ui_scores_dual_device_*.json`` → ``analysis_dual_device_webui_*.json``。
    """
    stem = score_json_path.stem
    prefix = "web_ui_scores_"
    if not stem.startswith(prefix):
        return None
    from config import ANALYSIS_DIR

    if stem.startswith("web_ui_scores_dual_device_"):
        cands = sorted(
            ANALYSIS_DIR.glob("analysis_dual_device_webui_*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        return cands[0] if cands else None

    safe = stem[len(prefix) :]
    cands = sorted(
        ANALYSIS_DIR.glob(f"analysis_{safe}_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return cands[0] if cands else None


_BOOKTITLE_IN_NAME_RE = re.compile(r"《([^》]+)》")
_ANGLE_TITLE_RE = re.compile(r"[＜<]([^＞>]+)[＞>]")
_TRAILING_DURATION_NB_RE = re.compile(
    r"\s+(?:\d+['\"′″]{1,2}\s*)*(?:Nb|Ng)\s*$",
    re.IGNORECASE,
)
_NUMBERED_TRACK_RE = re.compile(r"^\d+-[\u4e00-\u9fffA-Za-z0-9]+-(.+?)-.+$")


def display_source_name_from_stimulus(stimulus: str) -> str:
    """
    从流水线 ``stimulus`` / 文件名得到「音源名称」列展示用短名（不含分组路径）。

    优先 ``《曲名》``，其次 ``＜标题＞``；语声类 ``01-诵读-赤壁怀古-苏轼`` 取中间段「赤壁怀古」。
    """
    raw = (stimulus or "").strip()
    if not raw:
        return ""
    name = raw.split("/", 1)[-1].strip() if "/" in raw else raw
    name = name.replace("''", "'").replace("‘", "'").replace("’", "'")
    m = _BOOKTITLE_IN_NAME_RE.search(name)
    if m:
        return m.group(1).strip()
    m = _ANGLE_TITLE_RE.search(name)
    if m:
        return m.group(1).strip()
    for ext in (".mp3", ".wav", ".m4a", ".flac", ".ogg"):
        if name.lower().endswith(ext):
            name = name[: -len(ext)]
            break
    name = _TRAILING_DURATION_NB_RE.sub("", name).strip()
    m = _NUMBERED_TRACK_RE.match(name)
    if m:
        return m.group(1).strip()
    if "-" in name:
        parts = [p.strip() for p in name.split("-") if p.strip()]
        if len(parts) >= 3 and parts[0].isdigit():
            return parts[2]
    return name.strip()


def stamp_parsed_with_stimulus(
    parsed: Mapping[str, Any], stimulus: str
) -> dict[str, Any]:
    """
    用流水线音源键覆盖模型自填的「音源名称」（模型常按听感写描述，如「古风女声朗读」，
    与录音文件名《船歌》等不一致）。
    """
    out = dict(parsed)
    stim = (stimulus or "").strip()
    if not stim:
        return out
    short = display_source_name_from_stimulus(stim) or stim
    out["音源名称"] = short
    if "音源" in out:
        out["音源"] = short
    if "/" in stim:
        g = stim.split("/", 1)[0].strip()
        if g:
            out["分组"] = g
    elif not _scalar_str_for_cell(out.get("分组")):
        if "_" in stim:
            out["分组"] = stim.split("_", 1)[0].strip()
    return out


def _pick_source_name(parsed: Mapping[str, Any], track: Mapping[str, Any]) -> str:
    stim = _scalar_str_for_cell(track.get("stimulus"))
    if stim:
        return display_source_name_from_stimulus(stim) or stim
    file_ = _scalar_str_for_cell(track.get("file"))
    if file_:
        return display_source_name_from_stimulus(file_) or file_
    for key in ("音源名称", "音源"):
        s = _scalar_str_for_cell(parsed.get(key))
        if not s:
            continue
        if "/" in s or s.lower().endswith((".mp3", ".wav", ".m4a")):
            return display_source_name_from_stimulus(s) or s
        return s
    return ""


def _pick_group(parsed: Mapping[str, Any], track: Mapping[str, Any]) -> str:
    g = _scalar_str_for_cell(parsed.get("分组"))
    if g:
        return g
    stim = _scalar_str_for_cell(track.get("stimulus"))
    if "/" in stim:
        return stim.split("/")[0].strip()
    return ""


def _pick_conclusion(parsed: Mapping[str, Any]) -> str:
    for key in ("综合结论", "对比总结"):
        s = _scalar_str_for_cell(parsed.get(key))
        if s:
            return s
    return "—"


def _pick_comparison_summary(parsed: Mapping[str, Any]) -> str:
    """
    对比总结列：优先 Dify 默认键「对比总结」；Web 自定义 prompt 常用「综合评价」承载同类短文。
    """
    for key in ("对比总结", "综合评价"):
        s = _scalar_str_for_cell(parsed.get(key))
        if s:
            return s
    return "—"


def _pick_text_field(parsed: Mapping[str, Any], key: str) -> str:
    s = _scalar_str_for_cell(parsed.get(key))
    return s if s else "—"


def _parsed_has_dimension_scores(p: Mapping[str, Any]) -> bool:
    """逐音源表仅展示含五维键的解析结果，避免旧 analysis 中误解析的计费 JSON 显示为全 0。"""
    return any(k in p for k in _DIMS)


def _dim_float(parsed: Mapping[str, Any], key: str) -> float:
    v = parsed.get(key)
    if v is None:
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def copy_nisqa_meta_from_track(
    row: dict[str, Any], track: Mapping[str, Any]
) -> None:
    """保留 NISQA 客观分与刺激键，供报告第六章附录渲染。"""
    obj = track.get("objective_scores")
    if isinstance(obj, dict):
        row["objective_scores"] = obj
    for key in ("stimulus", "file"):
        val = track.get(key)
        if val is not None and str(val).strip():
            row[key] = val


def build_per_track_rows(analysis_payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """从 analysis JSON 的 ``tracks`` 提取各行（仅 ``ok`` 且含 ``parsed``）。"""
    out: list[dict[str, Any]] = []
    for t in analysis_payload.get("tracks") or []:
        if not t.get("ok"):
            continue
        p = t.get("parsed")
        if not isinstance(p, dict):
            p = {}
        if not _parsed_has_dimension_scores(p):
            continue
        row: dict[str, Any] = {
            "音源名称": _pick_source_name(p, t),
            "分组": _pick_group(p, t),
            "综合结论": _pick_conclusion(p),
            "对比总结": _pick_comparison_summary(p),
            "专业点评": _pick_text_field(p, "专业点评"),
            "综合评价": _pick_text_field(p, "综合评价"),
        }
        for k in _DIMS:
            row[k] = _dim_float(p, k)
        copy_nisqa_meta_from_track(row, t)
        out.append(row)
    return out


def rows_to_dataframe(rows: list[dict[str, Any]]) -> pd.DataFrame:
    """列顺序固定，便于报告对齐。"""
    if not rows:
        return pd.DataFrame()
    cols = ["音源名称", "分组", *_DIMS, "综合结论", "对比总结", "专业点评", "综合评价"]
    df = pd.DataFrame(rows)
    for c in cols:
        if c not in df.columns:
            df[c] = 0.0 if c in _DIMS else "—"
    return df[cols]


def dimension_averages_1f(df: pd.DataFrame) -> dict[str, float]:
    """五维列算术平均，保留 1 位小数。"""
    if df.empty:
        return {}
    return {k: round(float(df[k].mean()), 1) for k in _DIMS if k in df.columns}


def load_analysis_from_score_json_path(score_json_path: str | Path) -> dict[str, Any] | None:
    """读取与本次 Web UI 分数 JSON 同会话的 analysis JSON；失败返回 None。"""
    p = Path(score_json_path)
    if p.is_file():
        try:
            meta = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(meta, dict):
                ap = str(meta.get("analysis_json") or meta.get("analysis_path") or "").strip()
                if ap:
                    apth = Path(ap)
                    if apth.is_file():
                        return json.loads(apth.read_text(encoding="utf-8"))
        except Exception:
            pass
    aj = analysis_json_path_for_web_scores(p)
    if aj is None or not aj.is_file():
        return None
    try:
        return json.loads(aj.read_text(encoding="utf-8"))
    except Exception:
        return None
