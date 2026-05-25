# -*- coding: utf-8 -*-
"""
调用 Dify 评分：

- **物理上始终是「单路麦克风」**：同一时间只录一台设备外放（分时顺序录），不会多机同时响、一路混录。
- **单机一次会话**：每台只接一台安卓时，`score_recorded_session` 对每条 WAV 单独打绝对分。
- **多机同一会话**：`run_all.py` 不接 `-d` 或接多台时，会先确认 **被测机（d01）/ 对比机（d02）** 再采集；同一音源下各槽位各录一条，评分阶段 **合并前两路 WAV** 做被测 vs 对比（`analyze_audios_stimulus_compare`）。
- **多机分两次会话（仍单路麦克）**：可先 `run_all.py -d 被测SN` 再 `run_all.py -d 对比SN`，
  然后用 `score_cross_session_pairwise(被测会话tag, 对比会话tag)` 按音源序号对齐后做与上相同的 **-3～+3 分差** 对比（见文件末尾 `__main__` 示例）。
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from config import ANALYSIS_DIR, DIFY_USER, ensure_output_dirs
from audio_model_client import create_audio_model_client, current_audio_model_provider
from eval_source_summary import stamp_parsed_with_stimulus
from difyclient import (
    _agent_dbg_upload,
    first_balanced_json_object_slice,
    iter_balanced_json_object_slices,
)

# Web UI「提示词与模板」保存路径（与 web_ui.py 同目录）
PROMPT_OVERRIDES_PATH = Path(__file__).resolve().parent / "web_ui_prompt_overrides.json"

# 五维键名（单机绝对分与刺激比较差分共用键名；刺激比较时值域须为整数 -3～+3）
SCORING_FIVE_DIM_KEYS: tuple[str, ...] = (
    "声音响度",
    "人声清晰度",
    "听感舒适度",
    "失真与噪声",
    "频响平衡",
)

_STIMULUS_SCORE_INT_RANGE = frozenset(range(-3, 4))

_MARKDOWN_FENCE_INNER_RE = re.compile(
    r"```(?:json|JSON)?\s*([\s\S]*?)\s*```",
    re.MULTILINE,
)
_THINK_WRAPPER_RE = re.compile(
    r"<(?:think|redacted_thinking|thinking)\b[^>]*>[\s\S]*?</(?:think|redacted_thinking|thinking)>",
    re.IGNORECASE,
)


def _env_truthy(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes", "on")


def _stimulus_compare_max_attempts() -> int:
    """
    刺激比较（双路同评）在「空正文 / 解析不出五维 / 疑似拒答」时的最大 **完整 Dify 对话** 次数。

    默认 **3**：首轮仍解析失败或误判拒答时，最多再试 2 次（共 3 次对话）；上传仅在首次进行，后续复用 ``upload_file_id``。
    若首轮已足够稳定，可设 ``DIFY_STIMULUS_COMPARE_MAX_ATTEMPTS=1`` 只评一次；需更多缓冲可调大（上限 15）。
    """
    raw = (os.environ.get("DIFY_STIMULUS_COMPARE_MAX_ATTEMPTS") or "").strip()
    if not raw:
        return 3
    try:
        n = int(raw)
    except ValueError:
        return 3
    return max(1, min(15, n))


def _sanitize_model_tag_for_filename(name: str) -> str:
    """与 web_ui_multi_model_reports.sanitize_model_tag 一致，避免循环 import 重复实现。"""
    s = "".join(
        c if c.isalnum() or c in ("-", "_", ".") else "_"
        for c in (name or "").strip()
    )[:48]
    return s or "model"


def _analysis_path_model_suffix(device_label: str) -> str:
    """
    在 analysis 文件名中附带当前 SPEAKER_EVAL_MODEL_NAME，使主模型与追加模型报告一一对应；
    若 device_label 已含 ``__tag``（多模型追加路径），则不再重复拼接。
    """
    m = (os.environ.get("SPEAKER_EVAL_MODEL_NAME") or "").strip()
    if not m:
        return ""
    st = _sanitize_model_tag_for_filename(m)
    if not st:
        return ""
    if f"__{st}" in device_label:
        return ""
    return f"__{st}"


# 单路录音（仅一台设备一条 WAV）：无双机同刺激对比，用绝对听感分。
DEFAULT_SCORING_QUERY = """你是资深电声与音频测试工程师。当前附件为**单路麦克风录音**（无外接对比机的同音源 A/B 素材），
请基于该录音判断终端**内置喇叭**外放经采集后的主观听感。

从以下五个维度**分别、独立**给出 **1–10 的整数分**（每维必须是整数，不得为小数或区间；1 极差，10 极好）：
「声音响度」「人声清晰度」「听感舒适度」「失真与噪声」「频响平衡」。

另给出「综合分」：取五维整数分的算术平均，**保留 1 位小数**（仅此字段允许小数）。
并给出不超过 120 字的中文专业点评。

**必须只输出一个 JSON 对象**，不要 Markdown 代码围栏，不要其它解释文字。Schema：
{
  "声音响度": <int 1-10>,
  "人声清晰度": <int 1-10>,
  "听感舒适度": <int 1-10>,
  "失真与噪声": <int 1-10>,
  "频响平衡": <int 1-10>,
  "综合分": <float 一位小数>,
  "专业点评": "<中文>"
}
"""


def _read_prompt_overrides_json() -> dict[str, Any]:
    if not PROMPT_OVERRIDES_PATH.is_file():
        return {}
    try:
        raw = json.loads(PROMPT_OVERRIDES_PATH.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def get_effective_scoring_query() -> str:
    """单机评分用提示词：优先 ``web_ui_prompt_overrides.json`` 中的 ``scoring_query``。"""
    data = _read_prompt_overrides_json()
    q = data.get("scoring_query")
    if isinstance(q, str) and q.strip():
        return q.strip()
    return DEFAULT_SCORING_QUERY


def get_stimulus_compare_extras() -> str:
    """双机刺激比较提示词：``web_ui_prompt_overrides.json`` 的 ``stimulus_compare_extras``。"""
    data = _read_prompt_overrides_json()
    q = data.get("stimulus_compare_extras")
    if isinstance(q, str) and q.strip():
        return q.strip()
    return ""


def stimulus_compare_prompt_mode() -> str:
    """
    双机刺激比较 query 组装方式。

    - ``final``：仅发送你在 JSON 中保存的完整提示词 + 程序附带的「本轮上下文」（音源名、附件序号），
      不再拼接 difyclient 内置长模板，避免与自定义提示词重复。
    - ``append``：在内置模板后追加「补充说明」（旧行为）。

    判定：JSON ``stimulus_compare_prompt_mode`` 为 ``final``/``append`` 时优先；
    环境变量 ``SPEAKER_DIFY_FINAL_PROMPT_ONLY=1`` → final；
    ``SPEAKER_DIFY_APPEND_BUILTIN_PROMPT=1`` → append；
    默认：若 ``stimulus_compare_extras`` 非空 → ``final``，否则 ``append``。
    """
    data = _read_prompt_overrides_json()
    explicit = str(data.get("stimulus_compare_prompt_mode") or "").strip().lower()
    if explicit in ("final", "append"):
        return explicit
    if _env_truthy("SPEAKER_DIFY_APPEND_BUILTIN_PROMPT"):
        return "append"
    if _env_truthy("SPEAKER_DIFY_FINAL_PROMPT_ONLY"):
        return "final"
    return "final" if get_stimulus_compare_extras() else "append"


def compose_stimulus_compare_extra_instruction(
    runtime_extra: str = "",
) -> tuple[str, str]:
    """
    组装传给 Dify 的 ``extra_instruction`` 与 ``prompt_mode``（``final`` | ``builtin``）。

    ``runtime_extra``：仅含本轮事实（角色/槽位/设备标签等），不含评分规则长文。
    """
    mode = stimulus_compare_prompt_mode()
    user = get_stimulus_compare_extras()
    runtime = (runtime_extra or "").strip()
    if mode == "final":
        parts = [p for p in (user, runtime) if p]
        return ("\n\n".join(parts), "final")
    parts: list[str] = []
    if runtime:
        parts.append(runtime)
    if user:
        parts.append("【自 Web UI 保存的补充说明】\n" + user)
    return ("\n\n".join(parts), "builtin")


def get_selected_model_override() -> Optional[str]:
    """Dify ``inputs.selected_model``；返回 ``None`` 则由 ``difyclient`` 读环境变量。"""
    data = _read_prompt_overrides_json()
    q = data.get("selected_model")
    if isinstance(q, str) and q.strip():
        return q.strip()
    return None


def get_audio_eval_prompt_override() -> Optional[str]:
    """
    Dify Chat ``inputs.audio_eval_prompt``（开始表单必填时常用；常见长度上限 ``<256`` 字符）。

    返回 ``None`` 表示未单独配置：``difyclient`` 使用内置短文；完整指令仍在请求的 ``query`` 正文。
    """
    data = _read_prompt_overrides_json()
    q = data.get("audio_eval_prompt")
    if isinstance(q, str) and q.strip():
        return q.strip()
    return None


def eval_model_tags_for_track_row() -> dict[str, str]:
    """
    侧栏/环境变量中的评测大模型名与 Dify ``selected_model``，写入每条 ``tracks[]`` 与 ``web_ui_scores`` 便于区分。
    """
    tags: dict[str, str] = {}
    m = (os.environ.get("SPEAKER_EVAL_MODEL_NAME") or "").strip()
    if m:
        tags["eval_model"] = m
    sel = get_selected_model_override()
    if isinstance(sel, str) and sel.strip():
        tags["dify_selected_model"] = sel.strip()
    else:
        env_sel = (os.environ.get("DIFY_SELECTED_MODEL") or "").strip()
        if env_sel:
            tags["dify_selected_model"] = env_sel
    return tags


def _append_scoring_row(rows: list[dict[str, Any]], row: dict[str, Any]) -> None:
    merged = {**eval_model_tags_for_track_row(), **row}
    try:
        from nisqa_local import enrich_track_row_with_nisqa, is_enabled, on_failure_mode

        if is_enabled():
            from config import RECORDED_DIR

            enrich_track_row_with_nisqa(merged, recorded_dir=RECORDED_DIR)
    except RuntimeError:
        if on_failure_mode() == "fail":
            raise
    except Exception:
        pass
    rows.append(merged)


def _build_scoring_quality_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """写入 analysis 的轻量可信度摘要；不改变既有成功/失败判定。"""
    total = len(rows)
    ok_n = sum(1 for r in rows if r.get("ok"))
    parsed_n = sum(1 for r in rows if r.get("parsed"))
    failed_n = total - ok_n
    modes: dict[str, int] = {}
    error_samples: list[str] = []
    for r in rows:
        mode = str(r.get("scoring_mode") or "unknown").strip() or "unknown"
        modes[mode] = modes.get(mode, 0) + 1
        if r.get("ok"):
            continue
        err = str(r.get("error") or "").strip()
        if err and len(error_samples) < 8:
            stim = str(r.get("stimulus") or r.get("file") or "").strip()
            error_samples.append(f"{stim}: {err}" if stim else err)
    return {
        "total_tracks": total,
        "ok_tracks": ok_n,
        "failed_tracks": failed_n,
        "parsed_tracks": parsed_n,
        "all_scoring_failed": total > 0 and ok_n == 0,
        "partial_scoring_failed": total > 0 and 0 < failed_n < total,
        "scoring_modes": modes,
        "error_samples": error_samples,
    }


def save_prompt_overrides(
    scoring_query: str,
    stimulus_compare_extras: str,
    audio_eval_prompt: str = "",
) -> None:
    """由 Web UI「保存」写入 JSON，并刷新本进程内 ``SCORING_QUERY`` 常量。"""
    global SCORING_QUERY
    payload = {
        "scoring_query": scoring_query,
        "stimulus_compare_extras": stimulus_compare_extras,
        "audio_eval_prompt": (audio_eval_prompt or "").strip(),
    }
    PROMPT_OVERRIDES_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    SCORING_QUERY = get_effective_scoring_query()


# 兼容旧代码 ``from scoring import SCORING_QUERY``；启动时合并文件覆盖。
SCORING_QUERY = get_effective_scoring_query()


def _session_safe_tag(session_tag: str) -> str:
    return "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in session_tag)[:64]


def _pick_track_wav(recorded_dir: Path, safe: str, track_index: int) -> Optional[Path]:
    """同一音源序号下取一条代表 WAV（多槽时取文件名排序第一条，一般为 d01）。"""
    paths = list(recorded_dir.glob(f"{safe}_{track_index:02d}_*.wav"))
    if not paths:
        return None
    paths.sort(key=lambda p: p.name)
    return paths[0]


def _dict_looks_like_scoring_payload(d: dict[str, Any]) -> bool:
    """识别单机绝对分、刺激比较差分等评分 JSON（含长曲名《…》作键值）。"""
    if any(k in d for k in SCORING_FIVE_DIM_KEYS):
        return True
    if "综合分" in d:
        return True
    if "音源" in d and ("对比总结" in d or "专业点评" in d):
        return True
    if "音源名称" in d or "分组" in d:
        return True
    return False


def _try_parse_json_dict_from_snippet(s: str) -> Optional[dict[str, Any]]:
    if not s or not isinstance(s, str):
        return None
    t = s.strip()
    try:
        o = json.loads(t)
        return o if isinstance(o, dict) else None
    except json.JSONDecodeError:
        pass
    chunk = first_balanced_json_object_slice(t)
    if chunk:
        try:
            o = json.loads(chunk)
            return o if isinstance(o, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def _find_scoring_subdict(node: Any, depth: int = 0) -> Optional[dict[str, Any]]:
    """
    在工作流返回的嵌套结构（如 ``outputs`` / ``choices`` / 字符串化 JSON）中查找含五维的 dict。
    """
    if depth > 16:
        return None
    if isinstance(node, dict):
        if _dict_looks_like_scoring_payload(node):
            return node
        for v in node.values():
            r = _find_scoring_subdict(v, depth + 1)
            if r is not None:
                return r
    elif isinstance(node, list):
        for it in node:
            r = _find_scoring_subdict(it, depth + 1)
            if r is not None:
                return r
    elif isinstance(node, str) and "{" in node and len(node) > 2:
        inner = _try_parse_json_dict_from_snippet(node)
        if inner is not None:
            return _find_scoring_subdict(inner, depth + 1)
    return None


def _prefer_answer_scoring_dict(root: dict[str, Any]) -> dict[str, Any]:
    """
    优先使用 Dify/API 常见的 ``response['answer']`` 内层 JSON（含字符串化 JSON、
    《月光小夜曲》《赤壁怀古》等长字符串值）。
    """
    if _dict_looks_like_scoring_payload(root):
        return root
    ans = root.get("answer")
    if isinstance(ans, dict) and _dict_looks_like_scoring_payload(ans):
        return ans
    if isinstance(ans, str):
        inner = _try_parse_json_dict_from_snippet(ans)
        if inner is not None and _dict_looks_like_scoring_payload(inner):
            return inner
    data = root.get("data")
    if isinstance(data, dict):
        sub = _prefer_answer_scoring_dict(data)
        if _dict_looks_like_scoring_payload(sub):
            return sub
    return root


def _normalize_jsonish_text(s: str) -> str:
    """去掉 BOM / 零宽字符；将常见弯引号换成 ASCII，避免模型输出「合法肉眼 JSON」但 json.loads 失败。"""
    if not s:
        return s
    t = s.replace("\ufeff", "").strip()
    for zw in ("\u200b", "\u200c", "\u200d", "\u2060"):
        t = t.replace(zw, "")
    trans = str.maketrans(
        {
            "\u201c": '"',
            "\u201d": '"',
            "\u201e": '"',
            "\u201f": '"',
            "\u2033": '"',
            "\u00b4": "'",
            "\u2018": "'",
            "\u2019": "'",
            "\u201a": "'",
        }
    )
    return t.translate(trans)


def _strip_llm_think_wrappers(s: str) -> str:
    """去掉常见「思考」包裹块，避免干扰 JSON 切片。"""
    if not s:
        return s
    return _THINK_WRAPPER_RE.sub("", s).strip()


def _collect_markdown_fence_payloads(s: str) -> list[str]:
    """提取正文中所有 Markdown 代码围栏内层（可有多段 ```json ... ```）。"""
    if not s:
        return []
    return [m.group(1).strip() for m in _MARKDOWN_FENCE_INNER_RE.finditer(s)]


def _try_parse_scoring_payload_dict(cand: str) -> Optional[dict[str, Any]]:
    """从一段文本中解析出含五维（或综合分/音源点评等）的评分 dict；失败返回 None。"""
    cand = (cand or "").strip()
    if not cand:
        return None
    obj: Optional[dict[str, Any]] = None
    try:
        o = json.loads(cand)
        obj = o if isinstance(o, dict) else None
    except json.JSONDecodeError:
        pass
    if obj is None:
        chunk = first_balanced_json_object_slice(cand)
        if not chunk:
            return None
        try:
            o = json.loads(chunk)
            obj = o if isinstance(o, dict) else None
        except json.JSONDecodeError:
            return None
    if obj is None:
        return None
    out = _prefer_answer_scoring_dict(obj)
    if not _dict_looks_like_scoring_payload(out):
        nested = _find_scoring_subdict(obj)
        if nested is not None:
            out = nested
    if not _dict_looks_like_scoring_payload(out):
        return None
    return out


def _extract_json_object(text: str) -> Optional[dict[str, Any]]:
    if not text or not isinstance(text, str):
        return None
    s0 = _normalize_jsonish_text(text)
    s0 = _strip_llm_think_wrappers(s0)

    fence_inners = [_normalize_jsonish_text(x) for x in _collect_markdown_fence_payloads(s0)]
    candidates: list[str] = []
    for c in (s0, *fence_inners):
        c = (c or "").strip()
        if c:
            candidates.append(c)

    seen: set[str] = set()
    uniq: list[str] = []
    for c in candidates:
        if not c or c in seen:
            continue
        seen.add(c)
        uniq.append(c)

    for blob in candidates:
        b = (blob or "").strip()
        if not b:
            continue
        for ch in iter_balanced_json_object_slices(b):
            if ch not in seen:
                seen.add(ch)
                uniq.append(ch)

    best: Optional[dict[str, Any]] = None
    best_hits = -1
    best_len = -1
    for cand in uniq:
        got = _try_parse_scoring_payload_dict(cand)
        if not got:
            continue
        hits = sum(1 for k in SCORING_FIVE_DIM_KEYS if k in got)
        blen = len(json.dumps(got, ensure_ascii=False))
        if best is None or hits > best_hits or (hits == best_hits and blen > best_len):
            best, best_hits, best_len = got, hits, blen

    if best is None:
        return None

    obj_for_log = best
    _ok_scores = _dict_looks_like_scoring_payload(best)
    # #region agent log
    _agent_dbg_upload(
        "H3",
        "scoring.py:_extract_json_object:after_unwrap",
        "answer_unwrap",
        {
            "had_top_level_answer": "answer" in obj_for_log,
            "out_has_scoring_keys": _ok_scores,
            "dim_hits": best_hits,
            "n_candidates": len(uniq),
        },
    )
    # #endregion
    return best


def validate_and_normalize_stimulus_compare_five_dims(
    parsed: dict[str, Any],
) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """
    刺激比较专用：五维分差必须为 **整数** 且 ∈ **[-3, +3]**。

    合法时返回 ``(规范化后的 dict, None)``（五维键写回 ``int``，其余字段原样保留）；
    不合法时返回 ``(None, "异常评分：…")``，供上层 **剔除该次解析并触发重试**。
    """
    out = dict(parsed)
    problems: list[str] = []
    for k in SCORING_FIVE_DIM_KEYS:
        v = out.get(k)
        if v is None:
            problems.append(f"「{k}」缺失")
            continue
        if isinstance(v, bool):
            problems.append(f"「{k}」={v!r}（布尔非法）")
            continue
        iv: int
        if isinstance(v, int):
            iv = v
        elif isinstance(v, float):
            if abs(v - round(v)) > 1e-6:
                problems.append(f"「{k}」={v}（须为整数）")
                continue
            iv = int(round(v))
        elif isinstance(v, str):
            sv = v.strip()
            try:
                fv = float(sv)
            except (TypeError, ValueError):
                problems.append(f"「{k}」={v!r}（非数字）")
                continue
            if abs(fv - round(fv)) > 1e-6:
                problems.append(f"「{k}」={v!r}（须为整数）")
                continue
            iv = int(round(fv))
        else:
            problems.append(f"「{k}」类型非法")
            continue
        if iv not in _STIMULUS_SCORE_INT_RANGE:
            problems.append(f"「{k}」={iv}（须在 -3～+3 的整数）")
            continue
        out[k] = iv
    if problems:
        return None, "异常评分（五维须为整数 -3～+3）：" + "；".join(problems)
    return out, None
_STIMULUS_AUDIO_REFUSAL_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"无法直接(?:播放|解析|分析)"),
    re.compile(r"无法实际播放"),
    re.compile(r"无法实际试听"),
    re.compile(r"无法直接试听"),
    re.compile(r"无法(?:获取|听取)?.*试听"),
    re.compile(r"试听音频附件"),
    re.compile(r"仅提供音频文件名"),
    re.compile(r"仅有文件名"),
    re.compile(r"无法直接获取.*音频流"),
    re.compile(r"系统无法"),
    re.compile(r"不能直接播放"),
    re.compile(r"信息不足"),
    re.compile(r"不足以进行主观"),
    re.compile(r"默认记为\s*0"),
    re.compile(r"各项评分默认"),
    re.compile(r"无法对比.*(?:设备|两台)"),
    re.compile(r"无法.*解析.*音频"),
    re.compile(r"附件.*无法"),
)


def _stimulus_five_dims_all_zero(parsed: dict[str, Any]) -> bool:
    got: list[float] = []
    for k in SCORING_FIVE_DIM_KEYS:
        v = parsed.get(k)
        if v is None:
            return False
        try:
            got.append(float(v))
        except (TypeError, ValueError):
            return False
    return all(abs(x) < 1e-9 for x in got)


def _stimulus_response_looks_like_audio_refusal(text: str, parsed: dict[str, Any]) -> bool:
    blob = f"{text}\n{parsed.get('专业点评', '')}\n{parsed.get('综合评价', '')}\n{parsed.get('对比总结', '')}"
    if any(rx.search(blob) for rx in _STIMULUS_AUDIO_REFUSAL_RES):
        return True
    if not _stimulus_five_dims_all_zero(parsed):
        return False
    rev = str(parsed.get("专业点评", "")) + str(parsed.get("综合评价", ""))
    if len(rev) < 12:
        return False
    if any(
        w in rev
        for w in ("系统", "无法", "不足", "解析", "播放", "附件", "音频", "直接", "模型")
    ):
        return True
    return False


def _dify_stimulus_compare_with_refusal_retries(
    client: Any,
    *,
    paths_ordered: list[str],
    slots_order: list[str],
    stimulus_label: str,
    extra_instruction: str,
    comparison_variant: str,
    log: Callable[..., None],
    log_prefix: str,
    prompt_mode: str = "builtin",
) -> tuple[Optional[str], Optional[dict[str, Any]], Optional[str]]:
    """
    返回 (raw_text, parsed, error)。
    成功时 error 为 None；失败时 parsed 为 None，raw_text 可能为最后一次模型原文。
    """
    last_text: Optional[str] = None
    last_err: Optional[str] = None
    _max_attempts = _stimulus_compare_max_attempts()
    cached_upload_entries: Optional[list[dict[str, Any]]] = None
    for attempt in range(_max_attempts):
        if attempt:
            _gap = 5.5 + float(attempt) * 2.5
            log(
                f"{log_prefix} 模型返回疑似「未读音频 / 拒答 / 五维全 0 占位」，"
                f"第 {attempt + 1}/{_max_attempts} 次重试（间隔约 {_gap:.1f}s）…"
            )
            time.sleep(_gap)
        try:
            _post_delay: Optional[float] = None
            if attempt > 0:
                # 重试时逐步加长上传后等待，降低「附件未就绪」类拒答（与历史 8/12/16/22s 对齐并可扩展）
                _post_delay = min(45.0, 8.0 + float(attempt - 1) * 4.0)
            if cached_upload_entries is None:
                cached_upload_entries = client.upload_audios_for_stimulus_compare(
                    paths_ordered,
                    slots_order,
                )
            text = client.chat_stimulus_compare_with_uploaded_files(
                cached_upload_entries,
                stimulus_label=stimulus_label,
                device_slot_labels=slots_order,
                extra_instruction=extra_instruction,
                dut_attachment_index=0,
                ref_attachment_index=1,
                comparison_variant=comparison_variant,
                audio_eval_prompt=get_audio_eval_prompt_override(),
                selected_model=get_selected_model_override(),
                post_upload_chat_delay_sec=_post_delay,
                log_reuse_hint=attempt > 0,
                prompt_mode=prompt_mode,
            )
        except Exception as exc:  # noqa: BLE001
            last_err = str(exc)
            last_text = None
            continue
        last_text = text
        raw_s = text if isinstance(text, str) else ("" if text is None else str(text))
        if not raw_s.strip():
            last_err = "Dify 返回空正文"
            continue
        log(f"{log_prefix} 模型已返回 {len(raw_s)} 字符，正在解析并校验 JSON…")
        parsed = _extract_json_object(raw_s)
        if not parsed:
            last_err = "未能解析出含五维的评分 JSON"
            continue
        if _stimulus_response_looks_like_audio_refusal(raw_s, parsed):
            last_err = "模型拒答或未消费音频附件（占位全 0）"
            continue
        parsed_ok, verr = validate_and_normalize_stimulus_compare_five_dims(parsed)
        if verr:
            last_err = verr
            log(f"{log_prefix} {verr}，将重试…")
            continue
        parsed_ok = stamp_parsed_with_stimulus(parsed_ok, stimulus_label)
        return text, parsed_ok, None

    if current_audio_model_provider() == "seedpace":
        refuse_hint = (
            "连续多次仍失败时（Seedpace）：① HTTP 404 多为 URL 或 model 名错误——"
            "URL 须为 …/pre-gen-text/v1/chat/completions；侧栏请只选 gemini-3.1-pro-preview / Gemini 3.1 Pro Preview"
            "（或 gemini-2.5-pro），勿选 Doubao 等 Dify 专用名；② SEEDPACE_API_KEY 只填 token，不要带 Bearer；"
            "③ 该网关可能不解析 files 音频附件，若返回 200 但无法听音评分，需向网关确认多模态格式。"
        )
    else:
        refuse_hint = (
            "连续多次仍失败时：① 检查 Dify 应用 LLM 节点是否接入附件/音频多模态；② 增大 DIFY_STIMULUS_FIRST_TRACK_GAP_SEC；"
            "③ 调大 DIFY_STIMULUS_POST_UPLOAD_CHAT_DELAY_SEC；④ 音源 1、2 整轨失败时各自动冷却再评一次（DIFY_STIMULUS_EARLY_TRACK_RETRY_COOLDOWN_SEC，默认 12；兼容旧名 TRACK1_RETRY）。"
            "⑤ 若后台出现多条同音源记录且分数漂移：在 Dify 侧调低 LLM 的 temperature/top_p 或启用 seed；"
            "客户端环境变量 DIFY_STIMULUS_COMPARE_MAX_ATTEMPTS：默认 3（有限重试）；=1 仅单次对话；可增至 ≤15。"
        )
    if last_err and not last_text:
        return None, None, f"{last_err}。{refuse_hint}"
    return last_text, None, f"{last_err or '模型未给出可用分差'}。{refuse_hint}"


def build_dual_stepwise_pairwise_extra_instruction(device_a_label: str, device_b_label: str) -> str:
    """Web 双设备分步模式：与流水线双机刺激比较共用标尺，仅角色文案区分。"""
    return (
        "【双设备分步对比模式 · 硬性规则】\n"
        "角色定义（用于分差含义，请务必遵守）：\n"
        f"- 【被测设备】= 第 1 个音频附件，{device_a_label}\n"
        f"- 【对比设备】= 第 2 个音频附件，{device_b_label}\n"
        "\n"
        "评分标尺：\n"
        "- 每维允许取值（仅此 7 个整数）：-3、-2、-1、0、1、2、3\n"
        "- 正分＝被测优于对比；负分＝被测劣于对比；0＝两者相当\n"
        "- 禁止小数、禁止区间描述、禁止域外整数\n"
    )


def run_pairwise_stimulus_dify_compare(
    client: Any,
    *,
    paths_ordered: list[str],
    slots_order: list[str],
    stimulus_label: str,
    extra_instruction: str,
    comparison_variant: str,
    log: Callable[..., None],
    log_prefix: str,
    prompt_mode: str = "builtin",
) -> tuple[Optional[str], Optional[dict[str, Any]], Optional[str]]:
    """双机刺激比较（单音源）：常规采集流水线与 Web 双设备分步共用同一 Dify 重试栈。"""
    return _dify_stimulus_compare_with_refusal_retries(
        client,
        paths_ordered=paths_ordered,
        slots_order=slots_order,
        stimulus_label=stimulus_label,
        extra_instruction=extra_instruction,
        comparison_variant=comparison_variant,
        log=log,
        log_prefix=log_prefix,
        prompt_mode=prompt_mode,
    )


def _load_playlist(safe_tag: str) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    from config import RECORDED_DIR, discover_standard_tracks

    pl_path = RECORDED_DIR / f"{safe_tag}_playlist.json"
    raw: dict[str, Any] = {}
    if pl_path.is_file():
        try:
            raw = json.loads(pl_path.read_text(encoding="utf-8"))
        except Exception:
            raw = {}
    devices = list(raw.get("devices") or [])
    items = list(raw.get("items") or [])
    if not items:
        items = [
            {"index": i, "group": g, "source": r, "device_remote": ""}
            for i, (g, r) in enumerate(discover_standard_tracks(), start=1)
        ]
    return raw, devices, items


def score_recorded_session(
    session_tag: str,
    device_label: str = "DUT",
    log: Optional[Callable[[str], None]] = None,
) -> tuple[Optional[Path], str]:
    """
    根据 playlist.json：多设备时对每个音源做一次刺激比较评分；单设备时逐条单文件评分。
    """
    log = log or (lambda _m: None)
    ensure_output_dirs()

    from config import RECORDED_DIR

    safe_tag = _session_safe_tag(session_tag)

    _, devices, items = _load_playlist(safe_tag)
    comparison_mode = len(devices) >= 2
    # Web UI 常规模式（仍采两台机）要求与「单机音效测评」一致：每条 WAV 用 scoring_query 打绝对分，而非双路刺激比较。
    if comparison_mode and _env_truthy("SPEAKER_WEB_UI_REGULAR_USE_SINGLE_PROMPTS"):
        comparison_mode = False
        log(
            "已启用常规模式单机提示词：各设备录音将分别使用 scoring_query（绝对分 1～10），"
            "不再合并双路做刺激比较差分。"
        )

    # #region agent log
    _agent_dbg_upload(
        "H1",
        "scoring.py:score_recorded_session:after_playlist_load",
        "devices_and_comparison_gate",
        {
            "safe_tag": safe_tag,
            "n_devices": len(devices),
            "device_slots": [str(d.get("slot", "")) for d in devices],
            "comparison_mode": comparison_mode,
            "playlist_json_exists": (RECORDED_DIR / f"{safe_tag}_playlist.json").is_file(),
        },
    )
    # #endregion

    client = create_audio_model_client(log=log)
    rows: list[dict[str, Any]] = []

    # #region agent log
    try:
        _agent_dbg_upload(
            "SESSION",
            "scoring.py:score_recorded_session:after_client",
            "scoring_start",
            {
                "safe_tag": safe_tag,
                "comparison_mode": comparison_mode,
                "cwd": str(Path.cwd()),
            },
        )
    except Exception:
        pass
    # #endregion

    if comparison_mode:
        slots_order = [str(d.get("slot") or f"d{i:02d}") for i, d in enumerate(devices, start=1)]
        n_items = len(items)
        _first_track_gap = 5.0
        _raw_gap = os.environ.get("DIFY_STIMULUS_FIRST_TRACK_GAP_SEC", "").strip()
        if _raw_gap:
            try:
                _first_track_gap = max(0.0, float(_raw_gap))
            except ValueError:
                pass
        _leader_stim_armed = True
        _early_retry_cool = 12.0
        _raw_er = (
            os.environ.get("DIFY_STIMULUS_EARLY_TRACK_RETRY_COOLDOWN_SEC", "").strip()
            or os.environ.get("DIFY_STIMULUS_TRACK1_RETRY_COOLDOWN_SEC", "").strip()
        )
        if _raw_er:
            try:
                _early_retry_cool = max(0.0, float(_raw_er))
            except ValueError:
                pass
        _early_retry_used: set[int] = set()
        for item in items:
            try:
                idx = int(item.get("index", 0))
            except (TypeError, ValueError):
                continue
            if idx <= 0:
                continue
            paths_ordered: list[str] = []
            for slot in slots_order:
                found = list(RECORDED_DIR.glob(f"{safe_tag}_{idx:02d}_{slot}_*.wav"))
                if not found:
                    paths_ordered.append("")
                    continue
                paths_ordered.append(str(sorted(found)[0]))

            if not any(paths_ordered):
                _append_scoring_row(rows,
                    {
                        "track_index": idx,
                        "stimulus": item.get("source", ""),
                        "scoring_mode": "stimulus_compare",
                        "file": f"track_{idx:02d}_missing_wavs",
                        "ok": False,
                        "error": "未找到该音源下任一台设备的录音文件",
                        "raw": None,
                        "parsed": None,
                    }
                )
                continue

            if any(not p for p in paths_ordered):
                _append_scoring_row(rows,
                    {
                        "track_index": idx,
                        "stimulus": item.get("source", ""),
                        "scoring_mode": "stimulus_compare",
                        "file": f"track_{idx:02d}_partial",
                        "ok": False,
                        "error": "部分设备录音缺失，无法做完整刺激比较",
                        "raw": None,
                        "parsed": None,
                    }
                )
                continue

            stim = str(item.get("source") or f"track_{idx}")
            log(f"[音源 {idx}/{n_items}] 刺激比较 Dify（{len(paths_ordered)} 路录音）: {stim}")
            extra = ""
            if len(devices) >= 2:
                d0, d1 = devices[0], devices[1]
                lab0 = str(d0.get("label") or "").strip() or f"槽位{d0.get('slot', 'd01')}"
                lab1 = str(d1.get("label") or "").strip() or f"槽位{d1.get('slot', 'd02')}"
                extra = (
                    "角色定义（用于分差含义，请务必遵守）：\n"
                    f"- 【被测设备】= 第 1 个音频附件，{lab0}，槽位 {d0.get('slot', 'd01')}，序列号 {d0.get('serial', '')}\n"
                    f"- 【对比设备】= 第 2 个音频附件，{lab1}，槽位 {d1.get('slot', 'd02')}，序列号 {d1.get('serial', '')}\n"
                )
                if len(devices) > 2:
                    extra += (
                        f"- 另有 {len(devices) - 2} 路附件为同音源下其它终端录音，仅供文字参考；"
                        "**五维分差 JSON 仅评价被测相对对比设备**，勿为第三台及以后单独输出分数字段。\n"
                    )
            extra, _pmode = compose_stimulus_compare_extra_instruction(extra)
            if _leader_stim_armed:
                if _first_track_gap > 0:
                    log(
                        f"[音源 {idx}/{n_items}] 刺激比较：首轨前附加等待 {_first_track_gap:.1f}s"
                        f"（缓解首包附件未就绪；0 关闭，环境变量 DIFY_STIMULUS_FIRST_TRACK_GAP_SEC）…"
                    )
                    time.sleep(_first_track_gap)
                _leader_stim_armed = False
            text, parsed, err = run_pairwise_stimulus_dify_compare(
                client,
                paths_ordered=paths_ordered,
                slots_order=slots_order,
                stimulus_label=stim,
                extra_instruction=extra,
                comparison_variant="same_session",
                log=log,
                log_prefix=f"[音源 {idx}/{n_items}]",
                prompt_mode=_pmode,
            )
            # 证据：同一会话常见「前几轨偶发拒答、后续轨正常」——对音源序号 1、2 各允许一次冷却整轨再评。
            if err and idx in (1, 2) and idx not in _early_retry_used and _early_retry_cool > 0:
                _early_retry_used.add(idx)
                # #region agent log
                _agent_dbg_upload(
                    "H9",
                    "scoring.py:score_recorded_session:early_track_second_pass",
                    "cooldown_before_re_score",
                    {"track_index": idx, "cool_sec": float(_early_retry_cool)},
                )
                # #endregion
                log(
                    f"[音源 {idx}/{n_items}] 刺激比较：整轨未通过，{_early_retry_cool:.1f}s 后自动再评一次"
                    f"（DIFY_STIMULUS_EARLY_TRACK_RETRY_COOLDOWN_SEC / 旧名 TRACK1_RETRY，0 关闭）…"
                )
                time.sleep(_early_retry_cool)
                text, parsed, err = run_pairwise_stimulus_dify_compare(
                    client,
                    paths_ordered=paths_ordered,
                    slots_order=slots_order,
                    stimulus_label=stim,
                    extra_instruction=extra,
                    comparison_variant="same_session",
                    log=log,
                    log_prefix=f"[音源 {idx}/{n_items}]·二次",
                    prompt_mode=_pmode,
                )
            if err:
                _append_scoring_row(rows,
                    {
                        "track_index": idx,
                        "stimulus": stim,
                        "scoring_mode": "stimulus_compare",
                        "file": Path(paths_ordered[0]).name,
                        "wav_paths": paths_ordered,
                        "ok": False,
                        "error": err,
                        "raw": text,
                        "parsed": None,
                    }
                )
            else:
                _append_scoring_row(rows,
                    {
                        "track_index": idx,
                        "stimulus": stim,
                        "scoring_mode": "stimulus_compare",
                        "file": Path(paths_ordered[0]).name,
                        "wav_paths": paths_ordered,
                        "ok": True,
                        "error": None,
                        "raw": text,
                        "parsed": parsed,
                    }
                )
            time.sleep(2.0)
    else:

        def _wav_index(p: Path) -> tuple[int, str]:
            m = re.match(rf"^{re.escape(safe_tag)}_(\d+)_", p.name)
            ti = int(m.group(1)) if m else 10**9
            ms = re.search(r"_d\d{2}_", p.name)
            slot = ms.group(0) if ms else ""
            return (ti, slot)

        wavs = sorted(RECORDED_DIR.glob(f"{safe_tag}_*.wav"), key=_wav_index)
        if not wavs:
            return None, f"未找到录制文件，前缀={safe_tag}"

        stim_by_idx: dict[int, str] = {}
        group_by_idx: dict[int, str] = {}
        for item in items:
            try:
                ix = int(item.get("index", 0))
            except (TypeError, ValueError):
                continue
            if ix <= 0:
                continue
            stim_by_idx[ix] = str(item.get("source") or "")
            group_by_idx[ix] = str(item.get("group") or "未知")

        for i, wav in enumerate(wavs, start=1):
            log(f"[{i}/{len(wavs)}] Dify 评分: {wav.name}")
            m_idx = re.match(rf"^{re.escape(safe_tag)}_(\d+)_", wav.name)
            tidx = int(m_idx.group(1)) if m_idx else i
            stim = stim_by_idx.get(tidx, "")
            grp = group_by_idx.get(tidx, "未知")
            try:
                text = client.analyze_audio(
                    str(wav),
                    get_effective_scoring_query(),
                    audio_eval_prompt=get_audio_eval_prompt_override(),
                    selected_model=get_selected_model_override(),
                )
            except Exception as exc:  # noqa: BLE001
                text = None
                log(f"调用失败: {exc}")
                _append_scoring_row(rows,
                    {
                        "track_index": tidx,
                        "stimulus": stim,
                        "group": grp,
                        "file": wav.name,
                        "scoring_mode": "single",
                        "ok": False,
                        "error": str(exc),
                        "raw": None,
                        "parsed": None,
                    }
                )
                continue

            parsed = _extract_json_object(text or "")
            raw_s = text if isinstance(text, str) else ("" if text is None else str(text))
            if not raw_s.strip():
                _append_scoring_row(rows,
                    {
                        "track_index": tidx,
                        "stimulus": stim,
                        "group": grp,
                        "file": wav.name,
                        "scoring_mode": "single",
                        "ok": False,
                        "error": "Dify 未返回可用正文（流式为空或无法解析），请检查工作流与模型流式输出。",
                        "raw": text,
                        "parsed": None,
                    }
                )
            elif not parsed:
                _append_scoring_row(rows,
                    {
                        "track_index": tidx,
                        "stimulus": stim,
                        "group": grp,
                        "file": wav.name,
                        "scoring_mode": "single",
                        "ok": False,
                        "error": "未能解析出含五维/综合分的评分 JSON（常见：流式末包仅有用量/计费元数据，或模型未按 Schema 输出）。",
                        "raw": text,
                        "parsed": None,
                    }
                )
            else:
                _append_scoring_row(rows,
                    {
                        "track_index": tidx,
                        "stimulus": stim,
                        "group": grp,
                        "file": wav.name,
                        "scoring_mode": "single",
                        "ok": True,
                        "error": None,
                        "raw": text,
                        "parsed": parsed,
                    }
                )
            time.sleep(1.2)

    playlist_items = items
    playlist_devices = devices
    if not playlist_devices and items:
        playlist_items = items
        playlist_devices = []

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    _msfx = _analysis_path_model_suffix(device_label)
    out_path = ANALYSIS_DIR / f"analysis_{safe_tag}_{device_label}{_msfx}_{ts}.json"
    _first_ok = next((r for r in rows if r.get("ok") and r.get("parsed")), None)
    _parsed0 = (_first_ok or {}).get("parsed") or {}
    _vals: list[float | None] = []
    for _k in SCORING_FIVE_DIM_KEYS:
        try:
            _vals.append(float(_parsed0.get(_k)))
        except (TypeError, ValueError):
            _vals.append(None)
    _finite = [v for v in _vals if v is not None]
    _vmin = min(_finite) if _finite else None
    _vmax = max(_finite) if _finite else None
    _range_hint = "ambiguous_or_partial"
    if _vmin is not None and _vmax is not None:
        if -3.01 <= _vmin and _vmax <= 3.01:
            _range_hint = "likely_pairwise_minus3_plus3"
        elif 0.5 <= _vmin and _vmax <= 10.5:
            _range_hint = "likely_absolute_1_to_10"

    _n_stim = sum(1 for r in rows if r.get("scoring_mode") == "stimulus_compare")
    _n_single = sum(1 for r in rows if r.get("scoring_mode") == "single")

    # #region agent log
    _agent_dbg_upload(
        "H4",
        "scoring.py:score_recorded_session:before_write_analysis_json",
        "first_ok_track_score_shape",
        {
            "comparison_mode": comparison_mode,
            "rows_stimulus_compare": _n_stim,
            "rows_single": _n_single,
            "first_scoring_mode": (_first_ok or {}).get("scoring_mode"),
            "dimension_vals_sample": _vals,
            "value_range_hint": _range_hint,
        },
    )
    # #endregion

    try:
        from nisqa_local import analysis_objective_meta, is_enabled, is_available, availability_message

        if is_enabled():
            log(f"[NISQA] {availability_message()}")
    except Exception:
        pass

    quality_summary = _build_scoring_quality_summary(rows)
    if quality_summary.get("all_scoring_failed"):
        log("警告：本次评分所有音源均失败，analysis 仍会落盘供排障使用。")
    elif quality_summary.get("partial_scoring_failed"):
        log(
            "提示：本次评分存在部分失败条目，"
            f"成功 {quality_summary['ok_tracks']}/{quality_summary['total_tracks']} 条。"
        )

    _model_meta = eval_model_tags_for_track_row()
    payload = {
        "session_tag": session_tag,
        "device_label": device_label,
        "dify_user": DIFY_USER,
        "created_at": ts,
        "comparison_mode": comparison_mode,
        "scoring_rule_set": (
            "pairwise_minus3_to_plus3_same_session"
            if comparison_mode
            else "single_absolute_1_to_10"
        ),
        "playlist": playlist_items,
        "devices": playlist_devices,
        "scoring_quality": quality_summary,
        "tracks": rows,
    }
    if _model_meta.get("eval_model"):
        payload["eval_model"] = _model_meta["eval_model"]
    if _model_meta.get("dify_selected_model"):
        payload["dify_selected_model"] = _model_meta["dify_selected_model"]
    try:
        from nisqa_local import analysis_objective_meta, is_enabled, is_available

        if is_enabled() and is_available():
            payload["objective_scoring"] = analysis_objective_meta()
    except Exception:
        pass
    try:
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        return None, f"写入分析 JSON 失败: {exc}"

    ok_n = sum(1 for r in rows if r.get("ok"))
    mode_s = "刺激比较（按音源）" if comparison_mode else "单设备逐条"
    return out_path, f"完成 {ok_n}/{len(rows)} 条（{mode_s}），输出: {out_path}"


def score_cross_session_pairwise(
    session_tag_dut: str,
    session_tag_ref: str,
    device_label: str = "跨会话_被测_vs_对比",
    log: Optional[Callable[[str], None]] = None,
) -> tuple[Optional[Path], str]:
    """
    两次单独采集（每次只连一台机、单路麦克），评分时按 **音源序号** 对齐，两路合并送 Dify 做与现场双机相同的刺激比较差分。

    前提：两次会话使用 **同一套 assets 音源与顺序**（playlist 中 index、source 一致），
    且每条录音文件名为 ``{safe_tag}_{idx:02d}_{slot}_....wav``（单机时 slot 一般为 d01）。
    """
    log = log or (lambda _m: None)
    ensure_output_dirs()
    from config import RECORDED_DIR

    sd = _session_safe_tag(session_tag_dut)
    sr = _session_safe_tag(session_tag_ref)
    _, _, items = _load_playlist(sd)
    if not items:
        _, _, items = _load_playlist(sr)
    if not items:
        return None, "未找到任一会话的 playlist/items，无法对齐音源"

    client = create_audio_model_client(log=log)
    rows: list[dict[str, Any]] = []
    n = len(items)

    for item in items:
        try:
            idx = int(item.get("index", 0))
        except (TypeError, ValueError):
            continue
        if idx <= 0:
            continue
        stim = str(item.get("source") or f"track_{idx}")
        p_dut = _pick_track_wav(RECORDED_DIR, sd, idx)
        p_ref = _pick_track_wav(RECORDED_DIR, sr, idx)
        log(f"[音源 {idx}/{n}] 跨会话对比: {stim}")

        if not p_dut or not p_ref:
            _append_scoring_row(rows,
                {
                    "track_index": idx,
                    "stimulus": stim,
                    "scoring_mode": "stimulus_compare_cross_session",
                    "ok": False,
                    "error": f"缺文件 dut={p_dut} ref={p_ref}",
                    "raw": None,
                    "parsed": None,
                }
            )
            continue

        if stimulus_compare_prompt_mode() == "final":
            runtime_cross = (
                f"【跨会话 · 本轮上下文】\n"
                f"- 被测：第 1 个音频附件（会话 {session_tag_dut}）\n"
                f"- 对比：第 2 个音频附件（会话 {session_tag_ref}）\n"
            )
        else:
            runtime_cross = f"""【跨会话 · 硬性规则】（与主提示「评分原则」「JSON Schema」完全对齐，不得放宽）

一、比较对象
  · 仅比较【被测设备】相对【对比设备】
  · 同一音源标识下，**内置喇叭**经麦克风采集后的主观听感（不看屏幕/UI）

二、五维与分值（每维必须单独输出 1 个整数）
  · 维度名称（须与 JSON 键一致）：声音响度｜人声清晰度｜听感舒适度｜失真与噪声｜频响平衡
  · 每维允许取值（仅此 7 个整数）：-3、-2、-1、0、1、2、3
  · 含义：正分＝被测优于对比；负分＝被测劣于对比；0＝两者相当

三、禁止项
  · 禁止小数、禁止区间描述（如「约 1」「1～2」）
  · 禁止域外整数（如 ±4、5、10 等）
  · 禁止多维度合并成一条分、禁止省略任一维度
  · 禁止因「跨会话 / 分时录制」改用 1～10 或其它量表

四、输出形式
  · 仅输出主提示规定的 JSON；键名、顺序与 Schema 一致

五、角色与附件序号
  · 【被测】＝第 1 个音频附件（会话「{session_tag_dut}」，单路麦克）
  · 【对比】＝第 2 个音频附件（会话「{session_tag_ref}」，单路麦克）
"""
        extra, _pmode = compose_stimulus_compare_extra_instruction(runtime_cross)
        text, parsed, err = run_pairwise_stimulus_dify_compare(
            client,
            paths_ordered=[str(p_dut), str(p_ref)],
            slots_order=["被测(会话A)", "对比(会话B)"],
            stimulus_label=stim,
            extra_instruction=extra,
            comparison_variant="cross_session",
            log=log,
            log_prefix=f"[音源 {idx}/{n}]",
            prompt_mode=_pmode,
        )
        if err:
            _append_scoring_row(rows,
                {
                    "track_index": idx,
                    "stimulus": stim,
                    "scoring_mode": "stimulus_compare_cross_session",
                    "file": p_dut.name,
                    "wav_paths": [str(p_dut), str(p_ref)],
                    "ok": False,
                    "error": err,
                    "raw": text,
                    "parsed": None,
                }
            )
        else:
            _append_scoring_row(rows,
                {
                    "track_index": idx,
                    "stimulus": stim,
                    "scoring_mode": "stimulus_compare_cross_session",
                    "file": p_dut.name,
                    "wav_paths": [str(p_dut), str(p_ref)],
                    "ok": True,
                    "error": None,
                    "raw": text,
                    "parsed": parsed,
                }
            )
        time.sleep(1.2)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    combo = _session_safe_tag(f"{session_tag_dut}__vs__{session_tag_ref}")[:60]
    _msfx_x = _analysis_path_model_suffix(device_label)
    out_path = ANALYSIS_DIR / f"analysis_cross_{combo}_{device_label}{_msfx_x}_{ts}.json"
    playlist_items = items
    playlist_devices = [
        {"slot": "d01", "serial": f"会话:{session_tag_dut}", "label": "被测(会话A)"},
        {"slot": "d02", "serial": f"会话:{session_tag_ref}", "label": "对比(会话B)"},
    ]
    _model_meta_x = eval_model_tags_for_track_row()
    payload = {
        "session_tag": f"{session_tag_dut}__vs__{session_tag_ref}",
        "device_label": device_label,
        "dify_user": DIFY_USER,
        "created_at": ts,
        "comparison_mode": True,
        "cross_session": True,
        "scoring_rule_set": "pairwise_minus3_to_plus3_cross_session",
        "session_dut": session_tag_dut,
        "session_ref": session_tag_ref,
        "playlist": playlist_items,
        "devices": playlist_devices,
        "tracks": rows,
    }
    if _model_meta_x.get("eval_model"):
        payload["eval_model"] = _model_meta_x["eval_model"]
    if _model_meta_x.get("dify_selected_model"):
        payload["dify_selected_model"] = _model_meta_x["dify_selected_model"]
    try:
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        return None, f"写入分析 JSON 失败: {exc}"

    ok_n = sum(1 for r in rows if r.get("ok"))
    return out_path, f"跨会话对比完成 {ok_n}/{len(rows)} 条，输出: {out_path}"


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="评分：默认单会话；可选跨会话双机对比")
    ap.add_argument(
        "--cross-dut",
        metavar="SESSION_TAG",
        help="跨会话模式：被测侧会话 tag（与 --cross-ref 同时使用）",
    )
    ap.add_argument(
        "--cross-ref",
        metavar="SESSION_TAG",
        help="跨会话模式：对比侧会话 tag",
    )
    ap.add_argument("--label", default="跨会话_被测_vs_对比", help="写入 JSON 的 device_label")
    args = ap.parse_args()
    if args.cross_dut and args.cross_ref:
        p, msg = score_cross_session_pairwise(args.cross_dut, args.cross_ref, device_label=args.label)
        print(msg)
        if p:
            print(p)
    else:
        ap.print_help()

