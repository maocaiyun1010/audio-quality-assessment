# -*- coding: utf-8 -*-
"""
将 scoring 输出的 analysis JSON 转为 gen_report 所需结构，并生成 Word + Markdown 评测表。
支持单设备逐条与多设备「刺激比较」两种 analysis 结构。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from config import REPORT_DIR, discover_standard_tracks, ensure_output_dirs
from eval_source_summary import copy_nisqa_meta_from_track
from gen_report import generate_report
from markdown_report import (
    DIMENSION_KEYS,
    compute_dimension_statistics,
    one_line_summary_comparison,
    one_line_summary_single,
    render_evaluation_markdown,
    write_markdown,
)
from excel_summary import write_evaluation_xlsx
from tsv_report import render_evaluation_tsv, write_tsv


def _parse_track_index_from_filename(name: str) -> Optional[int]:
    """与录制命名 ``{session}_{idx:02d}_{slot}_...`` 对齐，避免误匹配会话时间戳。"""
    m = re.search(r"_(\d{2})_d\d{2}_", name)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    m2 = re.search(r"_(\d{2})_", name)
    if not m2:
        return None
    try:
        return int(m2.group(1))
    except ValueError:
        return None


def _idx_to_meta_from_playlist(playlist: list[dict[str, Any]]) -> dict[int, tuple[str, str]]:
    out: dict[int, tuple[str, str]] = {}
    for item in playlist:
        try:
            idx = int(item.get("index", 0))
        except (TypeError, ValueError):
            continue
        if idx <= 0:
            continue
        g = str(item.get("group") or "未知")
        src = str(item.get("source") or "")
        out[idx] = (g, src)
    return out


def _program_name(
    idx: Optional[int],
    idx_to_meta: dict[int, tuple[str, str]],
    row: dict[str, Any],
) -> str:
    """节目列：与 playlist 中音源路径（实际素材名）一致，禁止占位符。"""
    st = str(row.get("stimulus") or "").strip()
    if st:
        return st
    if idx is not None and idx in idx_to_meta:
        return str(idx_to_meta[idx][1] or "").strip()
    fn = str(row.get("file") or "").strip()
    return fn


def _parse_dim_value(raw: Any) -> float | None:
    if raw is None or raw == "" or raw == "—":
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    try:
        return float(str(raw).strip())
    except ValueError:
        return None


def _device_identity_for_report(d: dict[str, Any]) -> str:
    """优先 device_id / serial 等可作为「设备ID」展示的字段。"""
    for k in ("device_id", "deviceId", "adb_serial", "serial"):
        v = d.get(k)
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s
    return ""


def eval_model_label_from_analysis(data: dict[str, Any]) -> str:
    """从 analysis JSON 解析侧栏展示用的大模型名（单模型与多模型均适用）。"""
    for key in ("eval_model", "web_ui_eval_model"):
        v = str(data.get(key) or "").strip()
        if v:
            return v
    for row in data.get("tracks") or []:
        if not isinstance(row, dict):
            continue
        v = str(row.get("eval_model") or "").strip()
        if v:
            return v
    dl = str(data.get("device_label") or "")
    if "__" in dl:
        tail = dl.rsplit("__", 1)[-1].strip()
        if tail:
            return tail
    return ""


def _format_device_for_report(d: dict[str, Any]) -> str:
    """报告抬头用：名称/槽位 + 设备ID（序列号）。"""
    lab = str(d.get("label") or "").strip()
    slot = str(d.get("slot") or "").strip()
    ident = _device_identity_for_report(d)
    segs: list[str] = []
    if lab:
        segs.append(lab)
    if slot:
        segs.append(f"槽位 {slot}")
    head = " · ".join(segs) if segs else (slot or "—")
    if ident:
        return f"{head}（设备ID/序列号：{ident}）"
    return head


def build_word_from_analysis(
    analysis_json: Path,
    test_name: str = "喇叭音效 AI 辅助评测",
    test_device: str = "被测设备",
    ref_device: str = "参考机（可选）",
) -> tuple[Optional[Path], Optional[Path], Optional[Path], Optional[Path], str]:
    """
    生成 Word、Markdown、TSV 与 Excel 汇总表（.xlsx，表格模式打开）。
    返回 (docx_path, md_path, tsv_path, xlsx_path, message)。
    """
    ensure_output_dirs()
    try:
        data = json.loads(analysis_json.read_text(encoding="utf-8"))
    except Exception as exc:
        return None, None, None, None, f"读取分析文件失败: {exc}"

    tracks_rows: list[dict[str, Any]] = data.get("tracks") or []
    playlist: list[dict[str, Any]] = list(data.get("playlist") or [])
    devices = list(data.get("devices") or [])
    comparison_mode = bool(data.get("comparison_mode"))
    cross_session = bool(data.get("cross_session"))
    eval_model_name = eval_model_label_from_analysis(data)

    display_test = test_device
    display_ref = ref_device
    dut_label = ""
    ref_label = ""
    if len(devices) >= 2:
        display_test = _format_device_for_report(devices[0])
        display_ref = _format_device_for_report(devices[1])
        dut_label = display_test
        ref_label = display_ref
    elif len(devices) == 1:
        display_test = _format_device_for_report(devices[0])
        dut_label = display_test
    elif len(devices) > 2:
        display_test = _format_device_for_report(devices[0])
        display_ref = "；".join(_format_device_for_report(x) for x in devices[1:])
        dut_label = display_test
        ref_label = display_ref

    idx_to_meta = _idx_to_meta_from_playlist(playlist)
    if not idx_to_meta:
        for i, (g, fn) in enumerate(discover_standard_tracks(), start=1):
            idx_to_meta[i] = (g, fn)

    table_rows: list[dict[str, Any]] = []

    for row in tracks_rows:
        if row.get("ok") is False:
            continue
        parsed_chk = row.get("parsed")
        if not isinstance(parsed_chk, dict) or not any(
            k in parsed_chk for k in DIMENSION_KEYS
        ):
            continue

        idx = row.get("track_index")
        if idx is None:
            idx = _parse_track_index_from_filename(str(row.get("file") or ""))

        grp_row = str(row.get("group") or "").strip()
        if idx is not None and idx in idx_to_meta:
            group, _ = idx_to_meta[idx]
        elif grp_row:
            group = grp_row
        elif idx:
            group, _ = idx_to_meta.get(idx, ("未知", str(row.get("stimulus") or row.get("file") or "")))
        else:
            group = "未知"
        program = _program_name(idx, idx_to_meta, row)
        parsed = parsed_chk

        if comparison_mode or row.get("scoring_mode") in (
            "stimulus_compare",
            "stimulus_compare_cross_session",
        ):
            row_out: dict[str, Any] = {
                "节目": program,
                "分组": group,
                "对比总结": str(
                    parsed.get("对比总结")
                    or parsed.get("综合评价")
                    or ""
                ),
                "专业点评": str(parsed.get("专业点评") or row.get("raw") or "")[:800],
                "综合结论": str(parsed.get("综合结论") or "").strip(),
            }
            for dk in DIMENSION_KEYS:
                v = _parse_dim_value(parsed.get(dk))
                row_out[dk] = v

            copy_nisqa_meta_from_track(row_out, row)
            table_rows.append(row_out)
            continue

        row_out = {
            "节目": program,
            "分组": group,
            "专业点评": str(parsed.get("专业点评") or row.get("raw") or "")[:500],
            "对比总结": "",
        }
        zong = _parse_dim_value(parsed.get("综合分"))
        row_out["综合分"] = zong
        for dk in DIMENSION_KEYS:
            row_out[dk] = _parse_dim_value(parsed.get(dk))

        copy_nisqa_meta_from_track(row_out, row)
        table_rows.append(row_out)

    dim_avgs, grand = compute_dimension_statistics(table_rows)

    if comparison_mode:
        one_line = one_line_summary_comparison(dim_avgs)
        if cross_session:
            conclusion = (
                f"共 {len(table_rows)} 条音源完成**跨会话**被测相对对比（五维整数 -3～+3，正为被测优）。"
            )
        else:
            conclusion = (
                f"共 {len(table_rows)} 条音源完成**被测相对对比设备**主观听感比较（五维整数 -3～+3，正为被测优）。"
            )
        avg_label = "五维分差总平均（先按维度全节目平均，再对五维取平均）"
        overall_display = f"{grand:.2f}" if grand is not None else "N/A"
    else:
        one_line = one_line_summary_single(grand, len(table_rows))
        conclusion = (
            f"共 {len(table_rows)} 条音源完成模型评测（节目名与 assets 扫描路径一致）。"
        )
        avg_label = "五维总平均（先按维度全节目平均，再对五维取平均；单条另含综合分列）"
        overall_display = f"{grand:.2f}" if grand is not None else "N/A"

    result_data: dict[str, Any] = {
        "test_name": test_name,
        "test_device": display_test,
        "ref_device": display_ref,
        "eval_model": eval_model_name,
        "总体平均分": str(overall_display),
        "平均分说明": avg_label,
        "综合结论": conclusion,
        "单行评述": one_line,
        "音源条数": str(len(table_rows)),
        "comparison_mode": comparison_mode,
        "cross_session": cross_session,
        "明细": table_rows,
        "维度平均分": dim_avgs,
        "总平均分": grand,
    }

    stem = analysis_json.stem
    out_doc = REPORT_DIR / f"声学评测报告_{stem}.docx"
    out_md = REPORT_DIR / f"声学评测报告_{stem}.md"
    out_tsv = REPORT_DIR / f"声学评测报告_{stem}.tsv"
    out_xlsx = REPORT_DIR / f"声学评测报告_{stem}.xlsx"

    try:
        tsv_body = render_evaluation_tsv(
            test_name=test_name,
            test_device=display_test,
            ref_device=display_ref,
            eval_model_name=eval_model_name,
            comparison_mode=comparison_mode,
            cross_session=cross_session,
            rows=table_rows,
            dim_avgs=dim_avgs,
            grand=grand,
            dut_label=dut_label,
            ref_label=ref_label,
        )
        write_tsv(str(out_tsv), tsv_body)
        write_evaluation_xlsx(
            out_xlsx,
            test_name=test_name,
            test_device=display_test,
            ref_device=display_ref,
            eval_model_name=eval_model_name,
            comparison_mode=comparison_mode,
            cross_session=cross_session,
            rows=table_rows,
            dim_avgs=dim_avgs,
            grand=grand,
            dut_label=dut_label,
            ref_label=ref_label,
        )
    except Exception as exc:
        return None, None, None, None, f"生成 TSV/Excel 汇总失败: {exc}"

    try:
        md_body = render_evaluation_markdown(
            test_name=test_name,
            test_device=display_test,
            ref_device=display_ref,
            eval_model_name=eval_model_name,
            comparison_mode=comparison_mode,
            cross_session=cross_session,
            rows=table_rows,
            dim_avgs=dim_avgs,
            grand=grand,
            one_line=one_line,
        )
        write_markdown(str(out_md), md_body)
    except Exception as exc:
        return None, None, out_tsv, out_xlsx, f"生成 Markdown 失败: {exc}"

    try:
        generate_report(result_data, save_path=str(out_doc))
    except Exception as exc:
        return None, out_md, out_tsv, out_xlsx, f"生成 Word 失败: {exc}"

    speaker_doc: Path | None = None
    if comparison_mode and table_rows:
        try:
            from audio_test_report import (
                build_report_payload_from_analysis,
                default_speaker_report_path,
                generate_audio_test_report,
            )

            payload = build_report_payload_from_analysis(
                data,
                test_device=display_test,
                ref_device=display_ref,
                test_name=test_name,
            )
            payload["total_score_avg"] = grand
            payload["dimension_scores"] = {
                k: round(float(v), 2)
                for k, v in (dim_avgs or {}).items()
                if v is not None
            }
            speaker_doc = REPORT_DIR / f"音效对比报告_{analysis_json.stem}.docx"
            generate_audio_test_report(payload, str(speaker_doc))
            dated = default_speaker_report_path()
            if dated != speaker_doc:
                generate_audio_test_report(payload, str(dated))
        except Exception:
            speaker_doc = None

    extra = f" speaker={speaker_doc.name}" if speaker_doc else ""
    return (
        out_doc,
        out_md,
        out_tsv,
        out_xlsx,
        f"ok docx={out_doc.name} markdown={out_md.name} tsv={out_tsv.name} xlsx={out_xlsx.name}{extra}",
    )


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("用法: python report_builder.py <analysis.json>")
        raise SystemExit(1)
    p, md, tsv, xlsx, msg = build_word_from_analysis(Path(sys.argv[1]))
    print(msg, p, md, tsv, xlsx)
