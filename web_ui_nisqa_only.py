# -*- coding: utf-8 -*-
"""Web UI：独立 NISQA 客观音质批量评分（不经过 Dify）。"""
from __future__ import annotations

import html
import io
import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import streamlit as st

from config import RECORDED_DIR
from speaker_eval.settings.paths import AUDIO_EXTENSIONS, OUTPUT_DIR


@st.cache_data(show_spinner="正在生成 NISQA PDF…")
def _cached_nisqa_report_pdf_bytes(
    payload_json: str,
    eval_mode: str,
    pdf_ver: str,
) -> bytes:
    """参数名勿以下划线开头，否则 ``@st.cache_data`` 不会将其纳入缓存键。"""
    from web_ui_report_pdf import build_nisqa_only_report_pdf

    payload = json.loads(payload_json)
    pdf_b, pdf_msg = build_nisqa_only_report_pdf(payload, eval_mode=eval_mode)
    if pdf_b is None:
        raise RuntimeError(pdf_msg)
    return pdf_b


def collect_nisqa_input_paths(
    *,
    eval_mode: str,
    selected_audio_rel_paths: list[str] | None = None,
    dual_recorder: Any | None = None,
    recorded_dir: Path | None = None,
) -> tuple[list[Path], str]:
    """
    为「仅 NISQA」收集待评本地音频路径。

    双设备模式优先使用当前会话 ``local_wav``；否则在录音目录中按勾选音源名过滤或取全部 WAV。
    """
    rd = recorded_dir or RECORDED_DIR
    paths: list[Path] = []
    seen: set[str] = set()

    def _add(raw: str | Path) -> None:
        p = Path(raw)
        if not p.is_file():
            return
        try:
            key = str(p.resolve())
        except OSError:
            key = str(p)
        if key in seen:
            return
        seen.add(key)
        paths.append(p)

    if eval_mode == "dual_device" and dual_recorder is not None:
        for r in list(getattr(dual_recorder, "device_a_results", []) or []) + list(
            getattr(dual_recorder, "device_b_results", []) or []
        ):
            if not isinstance(r, dict):
                continue
            lw = str(r.get("local_wav") or "").strip()
            if lw:
                _add(lw)
        if paths:
            return paths, ""

    if not rd.is_dir():
        return [], f"录音目录不存在：{rd}"

    all_wavs = sorted(
        [
            p
            for p in rd.iterdir()
            if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS
        ],
        key=lambda x: x.name.lower(),
    )

    rels = [str(x).strip() for x in (selected_audio_rel_paths or []) if str(x).strip()]
    if rels:
        tokens: list[str] = []
        for rel in rels:
            stem = Path(rel).stem.lower()
            if len(stem) >= 4:
                tokens.append(stem)
            for part in Path(rel).parts:
                part_stem = Path(part).stem.lower()
                if len(part_stem) >= 4:
                    tokens.append(part_stem)
        tokens = list(dict.fromkeys(tokens))
        if tokens:
            for p in all_wavs:
                nm = p.name.lower()
                if any(tok in nm for tok in tokens):
                    _add(p)
            if paths:
                return paths, ""

    if all_wavs:
        return all_wavs, ""
    return [], (
        "未找到可评测的本地录音。请先完成录制，或确认 output/recorded 下存在 WAV/MP3 等文件。"
    )


def can_launch_nisqa_only(
    *,
    eval_mode: str,
    selected_audio_rel_paths: list[str] | None = None,
    dual_recorder: Any | None = None,
    recorded_dir: Path | None = None,
) -> bool:
    paths, _ = collect_nisqa_input_paths(
        eval_mode=eval_mode,
        selected_audio_rel_paths=selected_audio_rel_paths,
        dual_recorder=dual_recorder,
        recorded_dir=recorded_dir,
    )
    return bool(paths)


def execute_nisqa_only_run(
    *,
    eval_mode: str,
    selected_audio_rel_paths: list[str] | None = None,
    dual_recorder: Any | None = None,
    recorded_dir: Path | None = None,
    output_prefix: str = "",
    log: Callable[[str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict:
    """执行仅 NISQA 批量评分并写出 JSON/CSV。"""
    from nisqa_local import (
        build_nisqa_only_payload,
        ensure_weights,
        score_paths,
        write_nisqa_results_csv,
        write_nisqa_results_json,
    )

    import os

    os.environ["SPEAKER_NISQA_ENABLED"] = "1"
    paths, err = collect_nisqa_input_paths(
        eval_mode=eval_mode,
        selected_audio_rel_paths=selected_audio_rel_paths,
        dual_recorder=dual_recorder,
        recorded_dir=recorded_dir,
    )
    if not paths:
        raise FileNotFoundError(err or "未找到音频")

    ensure_weights(log=log)
    if log:
        log(f"[NISQA] 共 {len(paths)} 个文件，开始仅客观评分…")
    tracks = score_paths(paths, log=log, should_cancel=should_cancel)
    payload = build_nisqa_only_payload(
        tracks, source=f"web_ui_run:{eval_mode}"
    )
    if should_cancel and should_cancel() and len(tracks) < len(paths):
        summ = dict(payload.get("summary") or {})
        summ["cancelled"] = len(paths) - len(tracks)
        payload["summary"] = summ
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    prefix = (output_prefix.strip() or f"nisqa_only_{stamp}")
    out_dir = OUTPUT_DIR / "nisqa"
    jp = write_nisqa_results_json(out_dir / f"{prefix}.json", payload)
    cp = write_nisqa_results_csv(out_dir / f"{prefix}.csv", tracks)
    payload["output_json"] = str(jp)
    payload["output_csv"] = str(cp)
    return payload


def render_nisqa_only_report(
    pay: dict[str, Any],
    *,
    log_box: Any,
) -> None:
    """在「评测结果 / 测试报告」主区域展示仅 NISQA 批次结果。"""
    payload = pay.get("nisqa_payload")
    if not isinstance(payload, dict):
        st.warning("NISQA 报告数据无效。")
        return

    summ = payload.get("summary") or {}
    log_box.success(
        f"✅ NISQA 客观评分完成：{summ.get('ok', 0)}/{summ.get('total', 0)} 条"
        + (
            f"（失败 {summ.get('failed', 0)}，已停止 {summ.get('cancelled', 0)}）"
            if summ.get("failed") or summ.get("cancelled")
            else ""
        )
    )

    st.divider()
    st.subheader("📊 评测结果")
    st.markdown(
        '<p class="sub-muted" style="margin:0 0 0.65rem 0;">'
        "本地客观音质 (NISQA) · 非侵入式 MOS/维度预测 · "
        "与 Dify 主观五维分差独立，请勿混用标尺"
        "</p>",
        unsafe_allow_html=True,
    )
    _em = str(pay.get("eval_mode") or "")
    if _em == "dual_device":
        st.info(
            "本次为**仅 NISQA** 模式：已对双设备会话中的本地录音逐条客观评分，"
            "未调用 Dify 主观听感模型。"
        )
    else:
        st.info(
            "本次为**仅 NISQA** 模式：已对 `output/recorded`（或勾选音源对应文件）"
            "逐条客观评分，未调用 Dify。"
        )

    render_nisqa_only_results(
        payload,
        key_prefix="nisqa_report_main",
        show_banner=False,
        eval_mode=str(pay.get("eval_mode") or ""),
    )


def _nisqa_note_badge(note: str) -> str:
    palette = {
        "优秀": ("#ecfdf5", "#047857"),
        "良好": ("#eff6ff", "#1d4ed8"),
        "中等": ("#fffbeb", "#b45309"),
        "偏弱": ("#fff7ed", "#c2410c"),
        "较差": ("#fef2f2", "#b91c1c"),
    }
    bg, fg = palette.get(note, ("#f1f5f9", "#475569"))
    return (
        f'<span class="nisqa-badge" style="background:{bg};color:{fg};">'
        f"{html.escape(note)}</span>"
    )


def _nisqa_conclusion_badge(text: str) -> str:
    if "设备 A" in text:
        bg, fg = "#ecfdf5", "#047857"
    elif "设备 B" in text:
        bg, fg = "#eff6ff", "#1d4ed8"
    else:
        bg, fg = "#f1f5f9", "#475569"
    return (
        f'<span class="nisqa-badge" style="background:{bg};color:{fg};">'
        f"{html.escape(text)}</span>"
    )


def _parse_nisqa_avg(score: str) -> float | None:
    try:
        return float(str(score).strip())
    except (TypeError, ValueError):
        return None

def _render_nisqa_device_panel(device: str, avg_rows: list[dict[str, str]]) -> None:
    _tag = "div"
    cards: list[str] = []
    for row in avg_rows:
        dim = html.escape(str(row.get("维度", "")))
        score = html.escape(str(row.get("平均分", "—")))
        level = str(row.get("等级") or row.get("说明", ""))
        detail = html.escape(str(row.get("说明", "")))
        card = (
            f"<{_tag} class='nisqa-metric-card'>"
            f"<{_tag} class='nisqa-metric-label'>{dim}</{_tag}>"
            f"<{_tag} class='nisqa-metric-value'>{score}</{_tag}>"
            + _nisqa_note_badge(level)
            + f"<{_tag} class='nisqa-metric-hint'>{detail}</{_tag}>"
            + f"</{_tag}>"
        )
        cards.append(card)
    title = html.escape(device)
    grid = "".join(cards)
    st.markdown(
        f"<{_tag} class='nisqa-panel'><{_tag} class='nisqa-panel-title'>{title} · 维度平均</{_tag}>"
        f"<{_tag} class='nisqa-metric-grid'>{grid}</{_tag}></{_tag}>",
        unsafe_allow_html=True,
    )


def _render_nisqa_diff_table(diff_rows: list[dict[str, str]]) -> None:
    _tag = "div"
    body: list[str] = []
    for row in diff_rows:
        delta = str(row.get("A-B", ""))
        delta_style = "color:#64748b;font-weight:600;"
        if delta.startswith("+") and delta not in ("—", "+0.00"):
            delta_style = "color:#047857;font-weight:700;"
        elif delta.startswith("-") and delta not in ("—", "-0.00"):
            delta_style = "color:#1d4ed8;font-weight:700;"
        verdict_short = str(row.get("结论", ""))
        verdict_detail = html.escape(
            str(row.get("结论说明") or row.get("结论", ""))
        )
        body.append(
            f"<tr>"
            f"<td>{html.escape(str(row.get('维度', '')))}</td>"
            f"<td>{html.escape(str(row.get('设备A', '')))}</td>"
            f"<td>{html.escape(str(row.get('设备B', '')))}</td>"
            f"<td style='{delta_style}'>{html.escape(delta)}</td>"
            f"<td style='text-align:left;max-width:420px;'>"
            f"{_nisqa_conclusion_badge(verdict_short)}"
            f"<{_tag} class='nisqa-verdict-detail'>{verdict_detail}</{_tag}>"
            f"</td>"
            f"</tr>"
        )
    st.markdown(
        f"<{_tag} class='nisqa-panel'><{_tag} class='nisqa-panel-title'>设备 A vs B · 核心差异</{_tag}>"
        f"<table class='nisqa-diff-table'><thead><tr>"
        f"<th>维度</th><th>设备 A 平均</th><th>设备 B 平均</th><th>A−B</th><th>结论</th>"
        f"</tr></thead><tbody>{''.join(body)}</tbody></table></{_tag}>",
        unsafe_allow_html=True,
    )


def _render_nisqa_radar(
    device_avg_rows: dict[str, list[dict[str, str]]],
    *,
    key: str,
    layout: str = "default",
) -> None:
    """layout: ``default`` 专页用大图；``report`` 嵌入 Dify 报告时紧凑居中。"""
    if "设备 A" not in device_avg_rows or "设备 B" not in device_avg_rows:
        return
    rows_a = device_avg_rows["设备 A"]
    labels = [str(r.get("维度", "")) for r in rows_a]
    vals_a = [_parse_nisqa_avg(str(r.get("平均分", ""))) for r in rows_a]
    vals_b = [
        _parse_nisqa_avg(str(r.get("平均分", "")))
        for r in device_avg_rows["设备 B"]
    ]
    if not labels or any(v is None for v in vals_a + vals_b):
        return
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    n = len(labels)
    angles = np.linspace(0, 2 * np.pi, n, endpoint=False)
    angles = np.concatenate([angles, [angles[0]]])
    va = np.array(vals_a, dtype=float)
    vb = np.array(vals_b, dtype=float)

    report_mode = layout == "report"
    if report_mode:
        fig_w, fig_h = 4.6, 3.2
        title_fs, tick_fs, legend_fs = 9, 8, 7
        line_w, marker_sz = 1.5, 4
    else:
        fig_w, fig_h = 5.2, 5.2
        title_fs, tick_fs, legend_fs = 11, 9, 9
        line_w, marker_sz = 2.0, 6

    fig, ax = plt.subplots(figsize=(fig_w, fig_h), subplot_kw=dict(polar=True))
    ax.plot(
        angles,
        np.concatenate([va, [va[0]]]),
        "o-",
        color="#047857",
        label="设备 A",
        linewidth=line_w,
        markersize=marker_sz,
    )
    ax.fill(angles, np.concatenate([va, [va[0]]]), alpha=0.12, color="#047857")
    ax.plot(
        angles,
        np.concatenate([vb, [vb[0]]]),
        "o-",
        color="#1d4ed8",
        label="设备 B",
        linewidth=line_w,
        markersize=marker_sz,
    )
    ax.fill(angles, np.concatenate([vb, [vb[0]]]), alpha=0.10, color="#1d4ed8")
    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(labels, fontsize=tick_fs)
    ax.set_ylim(1, 5)
    ax.set_yticks([1, 2, 3, 4, 5])
    ax.set_yticklabels(["1", "2", "3", "4", "5"], fontsize=7 if report_mode else 8)
    ax.set_title(
        "五维雷达对比（NISQA 约 1–5 分）",
        fontsize=title_fs,
        pad=6 if report_mode else 16,
    )
    ax.grid(True, alpha=0.35)
    if report_mode:
        ax.legend(
            loc="upper center",
            bbox_to_anchor=(0.5, -0.14),
            ncol=2,
            fontsize=legend_fs,
            frameon=False,
        )
        fig.subplots_adjust(left=0.08, right=0.92, top=0.86, bottom=0.24)
    else:
        ax.legend(loc="upper right", bbox_to_anchor=(1.22, 1.05), fontsize=legend_fs)
        fig.tight_layout()

    if report_mode:
        _pad_l, _pad_mid, _pad_r = st.columns([0.85, 1.2, 0.85])
        with _pad_mid:
            st.pyplot(fig, clear_figure=True, use_container_width=True)
    else:
        st.pyplot(fig, clear_figure=True, use_container_width=True)


def _entries_to_detail_df(all_entries: list[dict[str, Any]]) -> pd.DataFrame:
    from nisqa_local import _fmt_nisqa_metric

    rows: list[dict[str, Any]] = []
    for entry in all_entries:
        m = entry.get("metrics") or {}
        rows.append(
            {
                "节目/刺激": entry.get("stimulus", ""),
                "设备": entry.get("device", ""),
                "录音文件": entry.get("file", ""),
                "MOS": _parse_nisqa_avg(_fmt_nisqa_metric(m.get("mos_pred", m.get("mos")))),
                "噪声": _parse_nisqa_avg(_fmt_nisqa_metric(m.get("noi_pred"))),
                "失真": _parse_nisqa_avg(_fmt_nisqa_metric(m.get("dis_pred"))),
                "音色": _parse_nisqa_avg(_fmt_nisqa_metric(m.get("col_pred"))),
                "响度": _parse_nisqa_avg(_fmt_nisqa_metric(m.get("loud_pred"))),
            }
        )
    return pd.DataFrame(rows)


def _render_nisqa_scale_disclaimer() -> None:
    from nisqa_local import nisqa_scale_disclaimer_text

    st.markdown(
        f'<p class="sub-muted" style="margin:0.35rem 0 0.85rem 0;">'
        f"{html.escape(nisqa_scale_disclaimer_text())}"
        f"</p>",
        unsafe_allow_html=True,
    )


def _render_nisqa_overview_body(
    report: dict[str, Any],
    *,
    key_prefix: str,
    report_layout: bool = False,
) -> None:
    _render_nisqa_scale_disclaimer()
    device_avg = report.get("device_avg_rows") or {}
    if report.get("has_ab_compare"):
        left, right = st.columns(2)
        with left:
            _render_nisqa_device_panel("设备 A", device_avg.get("设备 A", []))
        with right:
            _render_nisqa_device_panel("设备 B", device_avg.get("设备 B", []))
        _radar_layout = "report" if report_layout else "default"
        _render_nisqa_radar(
            device_avg,
            key=f"{key_prefix}_radar",
            layout=_radar_layout,
        )
        _render_nisqa_diff_table(list(report.get("diff_rows") or []))
    else:
        for device, avg_rows in device_avg.items():
            _render_nisqa_device_panel(device, avg_rows)


def _render_nisqa_detail_body(report: dict[str, Any], *, key_prefix: str) -> None:
    detail_df = _entries_to_detail_df(list(report.get("all_entries") or []))
    if detail_df.empty:
        st.info("暂无逐条录音明细。")
        return
    st.dataframe(
        detail_df,
        width="stretch",
        hide_index=True,
        column_config={
            "MOS": st.column_config.NumberColumn(format="%.2f"),
            "噪声": st.column_config.NumberColumn(format="%.2f"),
            "失真": st.column_config.NumberColumn(format="%.2f"),
            "音色": st.column_config.NumberColumn(format="%.2f"),
            "响度": st.column_config.NumberColumn(format="%.2f"),
        },
        key=f"{key_prefix}_detail_df",
    )


def render_nisqa_report_from_rows(
    rows: list[dict[str, Any]],
    *,
    key_prefix: str = "nisqa_embed",
    embedded_in_dify_report: bool = False,
) -> bool:
    """
    从含 ``objective_scores`` 的逐轨行渲染 NISQA 可视化（卡片 / 雷达 / 明细表）。

    用于 Dify 评测报告第六章；有数据返回 True。
    """
    from nisqa_local import collect_nisqa_report_data, has_nisqa_report_data

    if not rows or not has_nisqa_report_data(rows):
        return False

    report = collect_nisqa_report_data(rows)
    st.markdown(
        '<p class="nisqa-panel-title" style="margin:1rem 0 0.5rem 0;">'
        "📊 NISQA 客观音质（本地）</p>",
        unsafe_allow_html=True,
    )
    tab_overview, tab_detail = st.tabs(["总览", "录音明细"])
    with tab_overview:
        _render_nisqa_overview_body(
            report,
            key_prefix=key_prefix,
            report_layout=embedded_in_dify_report,
        )
    with tab_detail:
        _render_nisqa_detail_body(report, key_prefix=key_prefix)
    return True


def render_nisqa_only_results(
    payload: dict,
    *,
    key_prefix: str = "nisqa_page",
    show_banner: bool = True,
    eval_mode: str = "",
) -> None:
    """展示仅 NISQA 批次结果（专页与评测主页运行区共用）。"""
    from nisqa_local import render_nisqa_appendix_markdown, tracks_to_csv_rows

    if not isinstance(payload, dict) or not payload.get("tracks"):
        return

    tracks = list(payload.get("tracks") or [])
    summary = payload.get("summary") or {}
    flat = tracks_to_csv_rows(tracks)
    md = render_nisqa_appendix_markdown(tracks)
    stamp_dl = datetime.now().strftime("%Y%m%d_%H%M%S")

    if show_banner:
        st.success(
            f"NISQA 完成：成功 {summary.get('ok', 0)} / {summary.get('total', 0)} 条"
            + (
                f"（失败 {summary.get('failed', 0)}）"
                if summary.get("failed")
                else ""
            )
        )

    path_cols = st.columns(2)
    with path_cols[0]:
        if payload.get("output_json"):
            st.caption(f"JSON：`{payload['output_json']}`")
    with path_cols[1]:
        if payload.get("output_csv"):
            st.caption(f"CSV：`{payload['output_csv']}`")

    from nisqa_local import collect_nisqa_report_data

    report = collect_nisqa_report_data(tracks)
    tab_overview, tab_detail, tab_export = st.tabs(["总览", "录音明细", "导出"])

    with tab_overview:
        _render_nisqa_overview_body(report, key_prefix=key_prefix, report_layout=True)

    with tab_detail:
        _render_nisqa_detail_body(report, key_prefix=key_prefix)

    with tab_export:
        st.caption(
            "PDF 内容与「总览」「录音明细」标签页一致（含维度卡片、雷达、A/B 差异表与逐条明细）。"
        )
        try:
            from web_ui_report_pdf import (
                PDF_RENDERER_VERSION,
                build_nisqa_only_report_pdf,
                pdf_export_available,
                pdf_render_backend_label,
                suggested_nisqa_pdf_filename,
            )

            if not pdf_export_available():
                st.info("安装 PDF 依赖后可导出：`pip install -r requirements-pdf.txt`")
            else:
                st.caption(f"PDF 渲染：{pdf_render_backend_label()}")
                _payload_key = json.dumps(
                    payload, ensure_ascii=False, sort_keys=True, default=str
                )

                try:
                    _nisqa_pdf = _cached_nisqa_report_pdf_bytes(
                        _payload_key,
                        eval_mode or "",
                        PDF_RENDERER_VERSION,
                    )
                    st.download_button(
                        "下载 PDF（与 NISQA 专页一致）",
                        data=_nisqa_pdf,
                        file_name=suggested_nisqa_pdf_filename(),
                        mime="application/pdf",
                        type="primary",
                        key=f"{key_prefix}_dl_pdf",
                    )
                except Exception as _pdf_exc:
                    st.error(f"PDF 生成失败：{_pdf_exc}")
        except ImportError as _pdf_imp:
            st.caption(f"PDF 模块未加载：{_pdf_imp}")

        c1, c2, c3 = st.columns(3)
        with c1:
            st.download_button(
                "下载 JSON",
                data=json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
                file_name=f"nisqa_only_{stamp_dl}.json",
                mime="application/json",
                key=f"{key_prefix}_dl_json",
            )
        with c2:
            if flat:
                buf = io.StringIO()
                pd.DataFrame(flat).to_csv(buf, index=False, encoding="utf-8-sig")
                st.download_button(
                    "下载 CSV",
                    data=buf.getvalue().encode("utf-8-sig"),
                    file_name=f"nisqa_only_{stamp_dl}.csv",
                    mime="text/csv",
                    key=f"{key_prefix}_dl_csv",
                )
        with c3:
            if md.strip():
                st.download_button(
                    "下载 Markdown 表",
                    data=md.encode("utf-8"),
                    file_name=f"nisqa_only_{stamp_dl}.md",
                    mime="text/markdown",
                    key=f"{key_prefix}_dl_md",
                )
        if md.strip():
            with st.expander("Markdown 预览", expanded=False):
                st.markdown(md)


def render_nisqa_only_page() -> None:
    st.markdown(
        '<p class="main-title">📊 NISQA 客观音质 · 独立评分</p>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<p class="sub-muted">仅运行本地 NISQA 模型，<strong>不调用 Dify</strong>、不产生主观五维分。'
        "结果可导出 JSON / CSV / Markdown / PDF（与专页展示一致）。</p>",
        unsafe_allow_html=True,
    )

    try:
        from nisqa_local import (
            availability_message,
            ensure_weights,
            is_available,
            run_nisqa_batch,
            weights_path,
            weights_ready,
        )
    except ImportError:
        st.error("未找到 `nisqa_local` 模块。")
        return

    os.environ_nisqa = __import__("os")
    os.environ_nisqa.environ["SPEAKER_NISQA_ENABLED"] = "1"

    col_a, col_b = st.columns(2)
    with col_a:
        if is_available():
            st.success(availability_message())
        else:
            st.warning(availability_message())
    with col_b:
        st.caption(f"权重：`{weights_path()}`")

    source_mode = st.radio(
        "音频来源",
        ["项目录音目录 (output/recorded)", "自定义目录路径", "上传音频文件"],
        horizontal=False,
    )

    input_path: Path | None = None
    upload_paths: list[Path] = []

    if source_mode.startswith("项目录音"):
        input_path = RECORDED_DIR
        st.caption(f"目录：`{RECORDED_DIR.resolve()}`")
        if not RECORDED_DIR.is_dir():
            st.warning("录音目录不存在，请先完成一次采集或指定自定义路径。")
    elif source_mode.startswith("自定义"):
        custom = st.text_input(
            "目录绝对路径",
            value=str(RECORDED_DIR.resolve()),
            help="将递归扫描 wav / mp3 / flac / m4a / ogg / aac",
        )
        if custom.strip():
            input_path = Path(custom.strip())
    else:
        uploaded = st.file_uploader(
            "选择音频（可多选）",
            type=["wav", "mp3", "flac", "m4a", "ogg", "aac"],
            accept_multiple_files=True,
        )
        if uploaded:
            tmp_root = OUTPUT_DIR / "nisqa" / "_uploads"
            tmp_root.mkdir(parents=True, exist_ok=True)
            for uf in uploaded:
                dest = tmp_root / uf.name
                dest.write_bytes(uf.getvalue())
                upload_paths.append(dest)
            st.caption(f"已暂存 {len(upload_paths)} 个文件到 `{tmp_root}`")

    recursive = st.checkbox("递归扫描子目录", value=True)
    out_name = st.text_input(
        "输出文件名前缀（可选）",
        value="",
        placeholder="留空则使用 nisqa_only_时间戳",
    )

    if st.button("▶ 开始 NISQA 评分", type="primary", width="stretch"):
        if source_mode.startswith("上传"):
            if not upload_paths:
                st.error("请先上传至少一个音频文件。")
                st.stop()
            try:
                if not weights_ready():
                    with st.spinner("正在准备 NISQA 权重…"):
                        ensure_weights()
                from nisqa_local import (
                    build_nisqa_only_payload,
                    score_paths,
                    write_nisqa_results_csv,
                    write_nisqa_results_json,
                )

                with st.spinner(f"正在评测 {len(upload_paths)} 个文件…"):
                    tracks = score_paths(upload_paths)
                payload = build_nisqa_only_payload(
                    tracks, source="web_ui_upload"
                )
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                prefix = (out_name.strip() or f"nisqa_only_{stamp}")
                out_dir = OUTPUT_DIR / "nisqa"
                jp = write_nisqa_results_json(out_dir / f"{prefix}.json", payload)
                cp = write_nisqa_results_csv(out_dir / f"{prefix}.csv", tracks)
                payload["output_json"] = str(jp)
                payload["output_csv"] = str(cp)
                st.session_state["nisqa_only_payload"] = payload
            except Exception as exc:
                st.error(f"评测失败：{exc}")
                st.stop()
        else:
            if input_path is None or not input_path.exists():
                st.error("请输入有效目录路径。")
                st.stop()
            try:
                if not weights_ready():
                    with st.spinner("正在准备 NISQA 权重…"):
                        ensure_weights()
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                prefix = (out_name.strip() or f"nisqa_only_{stamp}")
                out_dir = OUTPUT_DIR / "nisqa"
                with st.spinner("正在批量评测…"):
                    payload = run_nisqa_batch(
                        input_path,
                        recursive=recursive,
                        output_json=out_dir / f"{prefix}.json",
                        output_csv=out_dir / f"{prefix}.csv",
                    )
                st.session_state["nisqa_only_payload"] = payload
            except Exception as exc:
                st.error(f"评测失败：{exc}")
                st.stop()

    payload = st.session_state.get("nisqa_only_payload")
    if not isinstance(payload, dict) or not payload.get("tracks"):
        st.info("选择来源并点击「开始 NISQA 评分」后，结果将显示于此。")
        with st.expander("命令行用法", expanded=False):
            st.code(
                "python -m nisqa_local --dir output/recorded\n"
                "python scripts/run_nisqa_only.py -d output/recorded -o my.json --csv my.csv",
                language="bash",
            )
        return

    render_nisqa_only_results(payload, key_prefix="nisqa_page")