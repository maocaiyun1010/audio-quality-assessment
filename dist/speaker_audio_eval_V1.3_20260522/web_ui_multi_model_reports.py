# -*- coding: utf-8 -*-
"""
多模型报告扩展：在**同一批录音**基础上，为额外模型重新走 Dify 评分并各写一套 analysis + Word/Markdown。

- 常规模式（``main_run_eval`` / ``web_ui_eval_worker``）：复用 ``score_recorded_session``。
- 双设备单麦模式：对配对 WAV 列表重新逐轨调用 ``DualDeviceScorer``。

``inputs.audio_eval_prompt`` / ``selected_model`` 仍由 ``difyclient`` / ``scoring`` 既有逻辑从环境变量与
``web_ui_prompt_overrides.json`` 读取；此处仅切换 ``SPEAKER_EVAL_MODEL_NAME``。
"""
from __future__ import annotations

import json
import math
import os
import statistics
import traceback
from pathlib import Path
from typing import Any, Callable, Optional

LogFn = Optional[Callable[[str, str, str], None]]


def sanitize_model_tag(name: str) -> str:
    s = "".join(
        c if c.isalnum() or c in ("-", "_", ".") else "_"
        for c in (name or "").strip()
    )[:48]
    return s or "model"


def session_safe_from_web_ui_score_path(score_json: str) -> str:
    stem = Path(score_json).stem
    pfx = "web_ui_scores_"
    if not stem.startswith(pfx):
        raise ValueError(f"非常规 web_ui 分数字段路径，无法解析会话标签: {score_json}")
    return stem[len(pfx) :]


def _log(log: LogFn, level: str, title: str, detail: str = "") -> None:
    if log:
        log(str(level or "info"), str(title or ""), str(detail or ""))


def append_main_mode_models_for_session_safe(
    *,
    session_safe: str,
    models: list[str],
    log: LogFn = None,
    dify_api_key_baseline: str | None = None,
    phase_label: str = "顺序评分",
) -> list[dict[str, str]]:
    """
    在已知 ``session_safe``（与 ``{safe}_playlist.json`` / WAV 前缀一致）下，按 ``models`` 顺序
    依次为每个模型调用 ``score_recorded_session`` 并生成报告与 ``web_ui_scores_*``。

    用于：① 首轮子进程成功后的追加模型；② 子进程失败但录音已落盘时的**全模型补评**。
    """
    from config import ANALYSIS_DIR
    from report_builder import build_word_from_analysis
    from run_all import _write_web_ui_score_json
    from scoring import score_recorded_session

    from web_ui_dify_model_keys import (
        configure_api_key_for_model,
        restore_dify_api_key_baseline,
        set_dify_api_key_baseline,
    )

    safe = (session_safe or "").strip()
    if not safe:
        return []

    if dify_api_key_baseline is not None:
        set_dify_api_key_baseline(dify_api_key_baseline)
    else:
        set_dify_api_key_baseline((os.environ.get("DIFY_API_KEY") or "").strip())

    os.environ.setdefault("SPEAKER_WEB_UI_REGULAR_USE_SINGLE_PROMPTS", "1")

    _n = len([x for x in models if str(x).strip()])
    _log(
        log,
        "info",
        f"多模型报告（常规·{phase_label}）",
        f"会话 safe={safe!r}；待评模型数={_n}。"
        "将**按列表顺序依次**调用 score_recorded_session 与报告生成；"
        "任一模型抛错或评分失败仅跳过该模型，不影响后续模型。",
    )
    out: list[dict[str, str]] = []
    try:
        for model in models:
            m = (model or "").strip()
            if not m:
                continue
            try:
                _log(log, "info", "多模型·顺序", f"开始模型 {m!r}（{phase_label}）…")
                configure_api_key_for_model(m)
                os.environ["SPEAKER_EVAL_MODEL_NAME"] = m
                tag = sanitize_model_tag(m)

                def _line(msg: str) -> None:
                    _log(log, "info", f"模型「{m}」", msg)

                apath, smsg = score_recorded_session(
                    safe,
                    device_label=f"多设备对比__{tag}",
                    log=_line,
                )
                if not apath:
                    _log(log, "error", f"模型「{m}」评分失败", str(smsg or ""))
                    _log(log, "warning", "多模型·继续", f"已跳过 {m!r}，继续下一模型。")
                    continue
                try:
                    _doc, md, _tsv, _xlsx, rmsg = build_word_from_analysis(
                        apath,
                        test_name="喇叭音效 AI 辅助评测",
                        test_device="多设备对比",
                        ref_device="",
                    )
                except Exception as exc:
                    _log(
                        log,
                        "error",
                        f"模型「{m}」报告生成异常",
                        f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}",
                    )
                    _log(log, "warning", "多模型·继续", f"已跳过 {m!r}，继续下一模型。")
                    continue
                score_side = ANALYSIS_DIR / f"web_ui_scores_{safe}__{tag}.json"
                try:
                    _write_web_ui_score_json(Path(apath), score_side)
                except Exception as exc:
                    _log(
                        log,
                        "error",
                        f"模型「{m}」写入 web_ui_scores 失败",
                        f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}",
                    )
                    _log(log, "warning", "多模型·继续", f"已跳过 {m!r}，继续下一模型。")
                    continue
                _log(
                    log,
                    "info",
                    f"模型「{m}」·数据绑定",
                    f"SPEAKER_EVAL_MODEL_NAME={m!r}；web_ui_scores={score_side.name}；analysis={apath.name}",
                )
                _log(log, "success", f"模型「{m}」报告已生成", str(rmsg or ""))
                out.append(
                    {
                        "model": m,
                        "markdown": str(md) if md else "",
                        "analysis": str(apath),
                        "score_json": str(score_side),
                    }
                )
            except Exception as exc:
                _log(
                    log,
                    "error",
                    f"模型「{m}」流程未捕获异常",
                    f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}",
                )
                _log(log, "warning", "多模型·继续", f"已跳过 {m!r}，继续下一模型。")
    finally:
        restore_dify_api_key_baseline()
    return out


def append_main_mode_model_reports(
    *,
    score_json: str,
    extra_models: list[str],
    log: LogFn = None,
    dify_api_key_baseline: str | None = None,
) -> list[dict[str, str]]:
    """
    为 ``extra_models`` 中每个模型追加一套 analysis + 报告（常规多机采集会话）。

    要求：与首轮评测相同的 ``session`` 下 ``output/recorded`` 中 playlist/WAV 仍完整。

    ``dify_api_key_baseline``：侧栏全局 Dify Key；与 ``web_ui_dify_api_keys_by_model.json`` 联用切换专钥。
    """
    safe = session_safe_from_web_ui_score_path(score_json)
    return append_main_mode_models_for_session_safe(
        session_safe=safe,
        models=list(extra_models or []),
        log=log,
        dify_api_key_baseline=dify_api_key_baseline,
        phase_label="追加模型",
    )


def _extract_effective_parsed(d: dict[str, Any]) -> dict[str, Any]:
    p = d if isinstance(d, dict) else {}
    ans = p.get("answer")
    if isinstance(ans, dict):
        return ans
    return p


def _consistency_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        if isinstance(v, float) and math.isnan(v):
            return None
        return float(v)
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


def _consistency_safe_stem(score_json: str | Path) -> str:
    stem = Path(score_json).stem
    prefix = "web_ui_scores_"
    if stem.startswith(prefix):
        stem = stem[len(prefix) :]
    return stem.split("__", 1)[0] or "session"


def _consistency_metric_values(payload: dict[str, Any]) -> tuple[dict[str, float], bool]:
    """多模型一致性比较用分值：刺激比较取 avg_delta_per_dim，常规取 dut_scores。"""
    from markdown_report import DIMENSION_KEYS

    pairwise = bool(payload.get("comparison_mode") or payload.get("stimulus_pairwise"))
    src = payload.get("avg_delta_per_dim") if pairwise else payload.get("dut_scores")
    if not isinstance(src, dict):
        src = {}
    out: dict[str, float] = {}
    for k in DIMENSION_KEYS:
        fv = _consistency_float(src.get(k))
        if fv is not None:
            out[k] = fv
    return out, pairwise


def _consistency_level(stddev: float | None, pairwise: bool) -> str:
    if stddev is None:
        return "数据不足"
    tight = 0.25 if pairwise else 0.45
    medium = 0.60 if pairwise else 0.90
    if stddev <= tight:
        return "高一致"
    if stddev <= medium:
        return "中等一致"
    return "分歧较大"


def write_multi_model_consistency_report(
    *,
    primary_score_json: str,
    extra_reports: list[dict[str, str]],
    primary_model: str = "",
    log: LogFn = None,
) -> dict[str, str]:
    """
    基于多模型的 ``web_ui_scores_*.json`` 生成一致性统计报告。

    该函数只读已生成结果，不触发 Dify、不改变任何单模型报告；输出 Markdown/TSV/JSON 三个旁路工件。
    """
    from config import REPORT_DIR
    from markdown_report import DIMENSION_KEYS

    bundles: list[dict[str, str]] = []
    p = str(primary_score_json or "").strip()
    if p:
        bundles.append({"model": (primary_model or "主模型").strip() or "主模型", "score_json": p})
    for ex in extra_reports or []:
        sx = str(ex.get("score_json") or "").strip()
        if not sx:
            continue
        bundles.append({"model": str(ex.get("model") or "模型").strip() or "模型", "score_json": sx})

    rows: list[dict[str, Any]] = []
    for b in bundles:
        path = Path(b["score_json"])
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            _log(log, "warning", "多模型一致性·读取失败", f"{path.name}: {exc}")
            continue
        dims, pairwise = _consistency_metric_values(payload)
        q = payload.get("scoring_quality") if isinstance(payload.get("scoring_quality"), dict) else {}
        overall_vals = [dims[k] for k in DIMENSION_KEYS if k in dims]
        overall = sum(overall_vals) / len(overall_vals) if overall_vals else None
        rows.append(
            {
                "model": str(b.get("model") or payload.get("web_ui_eval_model") or "模型"),
                "score_json": str(path),
                "pairwise": pairwise,
                "dims": dims,
                "overall": overall,
                "total_tracks": q.get("total_tracks"),
                "ok_tracks": q.get("ok_tracks"),
                "failed_tracks": q.get("failed_tracks"),
            }
        )

    if len(rows) < 2:
        return {}

    any_pairwise = any(bool(r.get("pairwise")) for r in rows)
    dim_stats: list[dict[str, Any]] = []
    for k in DIMENSION_KEYS:
        vals = [float(r["dims"][k]) for r in rows if k in r.get("dims", {})]
        if not vals:
            dim_stats.append({"dimension": k, "n": 0})
            continue
        mean_v = sum(vals) / len(vals)
        std_v = statistics.pstdev(vals) if len(vals) >= 2 else 0.0
        dim_stats.append(
            {
                "dimension": k,
                "n": len(vals),
                "mean": mean_v,
                "stddev": std_v,
                "min": min(vals),
                "max": max(vals),
                "range": max(vals) - min(vals),
                "level": _consistency_level(std_v, any_pairwise),
            }
        )

    overall_vals = [float(r["overall"]) for r in rows if r.get("overall") is not None]
    overall_std = statistics.pstdev(overall_vals) if len(overall_vals) >= 2 else None
    overall_level = _consistency_level(overall_std, any_pairwise)
    scale_desc = "刺激比较分差（-3～+3）" if any_pairwise else "常规绝对分（1～10）"

    safe = _consistency_safe_stem(primary_score_json)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    md_path = REPORT_DIR / f"多模型一致性统计_{safe}.md"
    tsv_path = REPORT_DIR / f"多模型一致性统计_{safe}.tsv"
    json_path = REPORT_DIR / f"多模型一致性统计_{safe}.json"

    model_headers = [str(r["model"]) for r in rows]
    md: list[str] = [
        "# 多模型一致性统计",
        "",
        f"- **统计口径**：{scale_desc}",
        f"- **参与模型数**：{len(rows)}",
        f"- **总分一致性**：{overall_level}"
        + (f"（总体标准差 {overall_std:.3f}）" if overall_std is not None else "（数据不足）"),
        "",
        "## 模型覆盖率",
        "",
        "| 模型 | 有效轨 | 总轨 | 失败轨 | 总体均值 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        ov = r.get("overall")
        md.append(
            "| "
            + " | ".join(
                [
                    str(r["model"]),
                    str(r.get("ok_tracks") if r.get("ok_tracks") is not None else "—"),
                    str(r.get("total_tracks") if r.get("total_tracks") is not None else "—"),
                    str(r.get("failed_tracks") if r.get("failed_tracks") is not None else "—"),
                    f"{float(ov):+.3f}" if any_pairwise and ov is not None else (f"{float(ov):.3f}" if ov is not None else "—"),
                ]
            )
            + " |"
        )
    md.extend(
        [
            "",
            "## 五维一致性统计",
            "",
            "| 维度 | "
            + " | ".join(model_headers)
            + " | 均值 | 标准差 | 极差 | 判定 |",
            "| --- | "
            + " | ".join(["---"] * len(model_headers))
            + " | --- | --- | --- | --- |",
        ]
    )
    for st in dim_stats:
        k = str(st.get("dimension") or "")
        cells = []
        for r in rows:
            val = (r.get("dims") or {}).get(k)
            if val is None:
                cells.append("—")
            else:
                cells.append(f"{float(val):+.3f}" if any_pairwise else f"{float(val):.3f}")
        mean_s = f"{float(st['mean']):+.3f}" if any_pairwise and "mean" in st else (f"{float(st['mean']):.3f}" if "mean" in st else "—")
        std_s = f"{float(st['stddev']):.3f}" if "stddev" in st else "—"
        range_s = f"{float(st['range']):.3f}" if "range" in st else "—"
        md.append(
            "| "
            + " | ".join([k, *cells, mean_s, std_s, range_s, str(st.get("level") or "数据不足")])
            + " |"
        )
    md.extend(
        [
            "",
            "> 说明：该表用于观察不同模型对同一批录音的评分一致性；标准差/极差越小，跨模型结论越稳定。",
            "",
        ]
    )

    tsv_lines = ["维度\t" + "\t".join(model_headers) + "\t均值\t标准差\t极差\t判定"]
    for st in dim_stats:
        k = str(st.get("dimension") or "")
        vals = []
        for r in rows:
            val = (r.get("dims") or {}).get(k)
            vals.append("" if val is None else f"{float(val):.6g}")
        tsv_lines.append(
            "\t".join(
                [
                    k,
                    *vals,
                    "" if "mean" not in st else f"{float(st['mean']):.6g}",
                    "" if "stddev" not in st else f"{float(st['stddev']):.6g}",
                    "" if "range" not in st else f"{float(st['range']):.6g}",
                    str(st.get("level") or "数据不足"),
                ]
            )
        )

    payload = {
        "scale": scale_desc,
        "model_count": len(rows),
        "overall_stddev": overall_std,
        "overall_consistency": overall_level,
        "models": rows,
        "dimension_stats": dim_stats,
    }
    md_path.write_text("\n".join(md), encoding="utf-8")
    tsv_path.write_text("\n".join(tsv_lines), encoding="utf-8")
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _log(log, "success", "多模型一致性统计已生成", str(md_path))
    return {"markdown": str(md_path), "tsv": str(tsv_path), "json": str(json_path)}


def append_dual_device_model_reports(
    *,
    paired_audios: list[tuple[str, str, str]],
    extra_models: list[str],
    dut_label: str,
    ref_label: str,
    analysis_base_stem: str,
    log: LogFn = None,
    dify_api_key_baseline: str | None = None,
) -> list[dict[str, str]]:
    """
    双设备模式：在已有配对 WAV 上，为额外模型重新跑逐轨 Dify 并合并写 analysis + 报告。
    ``analysis_base_stem`` 与首轮 ``analysis_{stem}_main.json`` 中的 ``stem`` 一致。
    """
    from config import ANALYSIS_DIR

    from dual_device_scoring import DualDeviceScorer
    from report_builder import build_word_from_analysis
    from run_all import _write_web_ui_score_json

    if not paired_audios or not extra_models:
        return []

    _log(
        log,
        "info",
        "多模型报告（双设备）",
        f"配对轨数={len(paired_audios)}；追加模型数={len([x for x in extra_models if str(x).strip()])}。"
        "将**按列表顺序**逐模型、逐轨调用 Dify；单轨失败记入日志；"
        "整模型未捕获异常时跳过该模型并继续后续模型。",
    )

    from web_ui_dify_model_keys import (
        configure_api_key_for_model,
        restore_dify_api_key_baseline,
        set_dify_api_key_baseline,
    )

    if dify_api_key_baseline is not None:
        set_dify_api_key_baseline(dify_api_key_baseline)
    else:
        set_dify_api_key_baseline((os.environ.get("DIFY_API_KEY") or "").strip())

    out: list[dict[str, str]] = []
    try:
        for model in extra_models:
            m = (model or "").strip()
            if not m:
                continue
            try:
                _log(
                    log,
                    "info",
                    "双设备多模型·顺序追加",
                    f"开始模型 {m!r}（共 {len(paired_audios)} 条配对轨）…",
                )
                configure_api_key_for_model(m)
                os.environ["SPEAKER_EVAL_MODEL_NAME"] = m
                tag = sanitize_model_tag(m)

                def _scorer_log(msg: str) -> None:
                    _log(log, "info", f"双设备·追加模型「{m}」", str(msg).strip())

                scorer = DualDeviceScorer(log=_scorer_log)
                merged_tracks: list[dict[str, Any]] = []

                for cursor, row in enumerate(paired_audios):
                    track_name, audio_a_path, audio_b_path = row
                    _log(
                        log,
                        "info",
                        f"双设备·追加「{m}」",
                        f"评分轨 [{cursor + 1}/{len(paired_audios)}]: {track_name}",
                    )
                    try:
                        _, result = scorer.score_dual_device_comparison(
                            audio_a_path=audio_a_path,
                            audio_b_path=audio_b_path,
                            device_a_label="被测设备A",
                            device_b_label="对比设备B",
                            stimulus_label=track_name,
                            persist_analysis=False,
                        )
                    except Exception as exc:
                        _log(
                            log,
                            "error",
                            f"模型「{m}」轨异常",
                            f"{track_name}: {type(exc).__name__}: {exc}\n{traceback.format_exc()}",
                        )
                        continue

                    track_row: dict[str, Any] = {}
                    if isinstance(result, dict):
                        tracks = result.get("tracks") or []
                        if tracks and isinstance(tracks[0], dict):
                            track_row = dict(tracks[0])

                    track_ok = bool(track_row.get("ok"))
                    parsed_eff = _extract_effective_parsed(track_row.get("parsed") or {})
                    if (not track_ok) and parsed_eff:
                        track_ok = any(
                            k in parsed_eff
                            for k in ("声音响度", "人声清晰度", "听感舒适度", "失真与噪声", "频响平衡")
                        )
                        if track_ok:
                            track_row["ok"] = True
                            track_row["error"] = None
                            track_row["parsed"] = parsed_eff

                    if track_ok:
                        track_row["track_index"] = cursor + 1
                        track_row["stimulus"] = track_row.get("stimulus") or track_name
                        if "group" not in track_row and "_" in track_name:
                            track_row["group"] = track_name.split("_", 1)[0]
                        merged_tracks.append(track_row)
                    else:
                        err_msg = None
                        if isinstance(result, dict):
                            err_msg = result.get("error")
                        _log(
                            log,
                            "error",
                            f"模型「{m}」轨失败",
                            f"{track_name}: {err_msg or '未返回可解析评分结果'}",
                        )

                if not merged_tracks:
                    _log(log, "error", f"模型「{m}」双设备追加失败", "所有音源评分均失败")
                    _log(log, "warning", "多模型·继续", f"已跳过 {m!r}，继续下一模型。")
                    continue

                merged_analysis_path = ANALYSIS_DIR / f"analysis_{analysis_base_stem}_main__{tag}.json"
                score_json_path = ANALYSIS_DIR / f"web_ui_scores_{analysis_base_stem}__{tag}.json"

                _ser_a = (dut_label or "").strip()
                _ser_b = (ref_label or "").strip()
                merged_payload = {
                    "session_tag": f"{analysis_base_stem}__{tag}",
                    "comparison_mode": True,
                    "scoring_rule_set": "pairwise_minus3_to_plus3_dual_device_stepwise",
                    "devices": [
                        {"slot": "d01", "label": "被测设备A", "serial": _ser_a},
                        {"slot": "d02", "label": "对比设备B", "serial": _ser_b},
                    ],
                    "tracks": merged_tracks,
                }
                if m:
                    merged_payload["eval_model"] = m
                try:
                    merged_analysis_path.write_text(
                        json.dumps(merged_payload, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    _write_web_ui_score_json(merged_analysis_path, score_json_path)
                except Exception as exc:
                    _log(
                        log,
                        "error",
                        f"模型「{m}」写入 analysis/web_ui_scores 失败",
                        f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}",
                    )
                    _log(log, "warning", "多模型·继续", f"已跳过 {m!r}，继续下一模型。")
                    continue
                _log(
                    log,
                    "info",
                    f"模型「{m}」·数据绑定（双设备）",
                    f"SPEAKER_EVAL_MODEL_NAME={m!r}；web_ui_scores={score_json_path.name}；analysis={merged_analysis_path.name}",
                )

                try:
                    _doc, md, _tsv, _xlsx, rmsg = build_word_from_analysis(
                        merged_analysis_path,
                        test_name="双设备单麦对比测评",
                        test_device=_ser_a or "被测设备A",
                        ref_device=_ser_b or "对比设备B",
                    )
                except Exception as exc:
                    _log(
                        log,
                        "error",
                        f"模型「{m}」双设备报告生成异常",
                        f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}",
                    )
                    _log(log, "warning", "多模型·继续", f"已跳过 {m!r}，继续下一模型。")
                    continue
                _log(log, "success", f"模型「{m}」双设备报告已生成", str(rmsg or ""))
                out.append(
                    {
                        "model": m,
                        "markdown": str(md) if md else "",
                        "analysis": str(merged_analysis_path),
                        "score_json": str(score_json_path),
                    }
                )
            except Exception as exc:
                _log(
                    log,
                    "error",
                    f"模型「{m}」双设备追加流程未捕获异常",
                    f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}",
                )
                _log(log, "warning", "多模型·继续", f"已跳过 {m!r}，继续下一模型。")
    finally:
        restore_dify_api_key_baseline()
    return out
