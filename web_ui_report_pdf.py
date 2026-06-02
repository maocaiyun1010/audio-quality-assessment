# -*- coding: utf-8 -*-
"""
将 Web UI「评测结果」页内容导出为 PDF（布局与文案与页面一致）。

Windows 优先使用本机 **Edge 无头打印**（中文与页面一致）；否则回退 xhtml2pdf。
依赖：``pip install -r requirements-pdf.txt``（至少 markdown）。
"""
from __future__ import annotations

import base64
import html
import io
import json
import re
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from config import EVAL_METRICS
from eval_source_summary import (
    build_per_track_rows,
    load_analysis_from_score_json_path,
    rows_to_dataframe,
)
from markdown_report import (
    DIMENSION_KEYS,
    _core_conclusion_highlight_html,
    build_section_six_markdown,
    compute_dimension_statistics,
    one_line_summary_comparison,
    one_line_summary_single,
)
from web_ui_report_html import _DIM_SCORE_SHORT, diff_accent_color

_PDF_DEPS_HINT = "请执行：pip install -r requirements-pdf.txt"
# 用于 streamlit 缓存失效；字体/渲染策略变更时递增
PDF_RENDERER_VERSION = "20260602_hist_json_pdf_v3"

_HEADLESS_BROWSER_CANDIDATES: tuple[tuple[str, str], ...] = (
    ("edge", r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    ("edge", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    ("chrome", r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    ("chrome", r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    ("chromium", "/usr/bin/chromium"),
    ("chromium", "/usr/bin/chromium-browser"),
    ("chrome", "/usr/bin/google-chrome"),
)


def _markdown_available() -> bool:
    try:
        import markdown  # noqa: F401

        return True
    except ImportError:
        return False


def _xhtml2pdf_available() -> bool:
    try:
        import xhtml2pdf  # noqa: F401

        return True
    except ImportError:
        return False


def find_headless_browser() -> tuple[str, Path] | None:
    """返回 (名称, 可执行路径)；优先 Edge/Chrome 无头打印。"""
    for name, raw in _HEADLESS_BROWSER_CANDIDATES:
        p = Path(raw)
        if p.is_file():
            return name, p
    return None


def pdf_render_backend_label() -> str:
    hit = find_headless_browser()
    if hit:
        return f"{hit[0]} 无头打印（推荐，中文正常）"
    if _xhtml2pdf_available():
        return "xhtml2pdf（备用，中文可能异常）"
    return "不可用"


def pdf_export_available() -> bool:
    return _markdown_available() and (
        find_headless_browser() is not None or _xhtml2pdf_available()
    )


def _register_cjk_font() -> str:
    """注册 reportlab 字体并返回 CSS font-family 名。"""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfbase.ttfonts import TTFont

    candidates: list[tuple[str, Path, dict[str, Any]]] = [
        ("ttf", Path(r"C:\Windows\Fonts\simhei.ttf"), {}),
        ("ttf", Path(r"C:\Windows\Fonts\simsun.ttf"), {}),
        ("ttc", Path(r"C:\Windows\Fonts\msyh.ttc"), {"subfontIndex": 0}),
        ("ttc", Path(r"C:\Windows\Fonts\simsun.ttc"), {"subfontIndex": 0}),
        ("ttf", Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttf"), {}),
        ("ttc", Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"), {"subfontIndex": 0}),
    ]
    for _kind, p, kw in candidates:
        if not p.is_file():
            continue
        name = "ReportCJK"
        try:
            pdfmetrics.registerFont(TTFont(name, str(p), **kw))
            return name
        except Exception:
            continue
    # 兜底：使用 reportlab 内置 CJK CID 字体，避免回退 Helvetica 出现中文方框
    for cid_name in ("STSong-Light", "HeiseiMin-W3"):
        try:
            pdfmetrics.registerFont(UnicodeCIDFont(cid_name))
            return cid_name
        except Exception:
            continue
    return "Helvetica"


def _cjk_font_css() -> str:
    fam = _register_cjk_font()
    return (
        f"body, table, th, td, h1, h2, h3, p, li "
        f"{{ font-family: {fam}; font-size: 11px; }}"
    )


def _setup_matplotlib_cjk() -> None:
    from matplotlib import font_manager

    for p in (
        Path(r"C:\Windows\Fonts\simhei.ttf"),
        Path(r"C:\Windows\Fonts\msyh.ttc"),
        Path(r"C:\Windows\Fonts\simsun.ttf"),
        Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttf"),
    ):
        if not p.is_file():
            continue
        try:
            font_manager.fontManager.addfont(str(p))
            name = font_manager.FontProperties(fname=str(p)).get_name()
            plt.rcParams["font.sans-serif"] = [
                name,
                "Microsoft YaHei",
                "SimHei",
                "DejaVu Sans",
            ]
            plt.rcParams["axes.unicode_minus"] = False
            return
        except Exception:
            pass
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False


def _pdf_safe_text(text: str) -> str:
    """PDF 文案规范化（保留中文；仅替换易出方框的符号）。"""
    if not text:
        return ""
    t = str(text)
    t = t.replace("\u2212", "-").replace("\u2013", "-").replace("\u2014", "-")
    t = t.replace("\ufffd", "")
    return t


def _eval_theme_css() -> str:
    return """
body { font-size: 11px; color: #0f172a; line-height: 1.45; margin: 12px; }
h1 { font-size: 20px; color: #2563eb; border-bottom: 2px solid #2563eb; padding-bottom: 6px; }
h2 { font-size: 15px; color: #1e40af; margin-top: 18px; }
h3 { font-size: 13px; color: #334155; }
.section { margin-bottom: 14px; page-break-inside: avoid; }
.metric-box { border: 1px solid #e2e8f0; border-radius: 8px; padding: 8px; text-align: center; }
.metric-label { font-size: 10px; color: #64748b; }
.metric-value { font-size: 16px; font-weight: bold; color: #1d4ed8; }
.chart-row img { max-width: 100%; height: auto; }
.table-wrap table { border-collapse: collapse; width: 100%; }
.table-wrap th { background: #f1f5f9; }
.note { color: #64748b; font-size: 10px; }
"""


def _nisqa_theme_css() -> str:
    return """
body { color: #0f172a; line-height: 1.45; margin: 12px; }
h1 { font-size: 20px; color: #047857; border-bottom: 2px solid #047857; padding-bottom: 6px; }
h2 { font-size: 15px; color: #065f46; margin-top: 16px; }
h3 { font-size: 13px; color: #334155; }
.note { color: #64748b; font-size: 10px; }
.summary-box {
  background: #ecfdf5; border: 1px solid #a7f3d0; border-radius: 8px;
  padding: 10px 14px; margin: 10px 0;
}
"""


def _wrap_html_document(body_html: str, *, theme: str) -> str:
    theme_css = _nisqa_theme_css() if theme == "nisqa" else _eval_theme_css()
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"/>
<style>
html, body, table, th, td, div, p, h1, h2, h3, span, li, b, strong {{
  font-family: "Microsoft YaHei", "SimHei", "PingFang SC", "Noto Sans CJK SC", sans-serif !important;
}}
{theme_css}
</style></head><body>{body_html}</body></html>"""


def _html_to_pdf_edge(document_html: str, *, theme: str) -> bytes:
    browser = find_headless_browser()
    if browser is None:
        raise RuntimeError(
            "未找到 Edge/Chrome。请安装 Microsoft Edge 或 Google Chrome 后再导出 PDF。"
        )
    _name, exe = browser
    html_doc = _wrap_html_document(document_html, theme=theme)
    tmp_dir = Path(tempfile.mkdtemp(prefix="speaker_pdf_"))
    html_path = tmp_dir / "report.html"
    pdf_path = tmp_dir / "report.pdf"
    try:
        html_path.write_text(html_doc, encoding="utf-8")
        uri = html_path.resolve().as_uri()
        last_err: str | None = None
        for headless_flag in ("--headless=new", "--headless"):
            try:
                proc = subprocess.run(
                    [
                        str(exe),
                        headless_flag,
                        "--disable-gpu",
                        "--no-pdf-header-footer",
                        "--disable-extensions",
                        f"--print-to-pdf={pdf_path}",
                        uri,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=180,
                    check=False,
                )
                if pdf_path.is_file() and pdf_path.stat().st_size > 500:
                    return pdf_path.read_bytes()
                last_err = (proc.stderr or proc.stdout or "").strip()[:500]
            except subprocess.TimeoutExpired:
                last_err = "浏览器打印 PDF 超时（>180s）"
            except Exception as exc:
                last_err = str(exc)
        raise RuntimeError(last_err or "浏览器未生成 PDF 文件")
    finally:
        for p in (html_path, pdf_path):
            try:
                if p.is_file():
                    p.unlink()
            except Exception:
                pass
        try:
            tmp_dir.rmdir()
        except Exception:
            pass


def _html_to_pdf_xhtml2pdf(document_html: str, *, theme: str) -> bytes:
    from xhtml2pdf import pisa

    theme_css = _nisqa_theme_css() if theme == "nisqa" else _eval_theme_css()
    out = io.BytesIO()
    src = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"/>
<style>
{_cjk_font_css()}
{theme_css}
</style></head><body>{document_html}</body></html>"""
    status = pisa.CreatePDF(src, dest=out, encoding="utf-8")
    if status.err:
        raise RuntimeError("PDF 渲染失败（xhtml2pdf，中文可能显示为方框）")
    return out.getvalue()


def _html_to_pdf_bytes(document_html: str, *, theme: str = "eval") -> bytes:
    """HTML → PDF；优先 Edge/Chrome 无头打印。"""
    if find_headless_browser() is not None:
        return _html_to_pdf_edge(document_html, theme=theme)
    if _xhtml2pdf_available():
        return _html_to_pdf_xhtml2pdf(document_html, theme=theme)
    raise RuntimeError(
        "未找到 Edge/Chrome，且未安装 xhtml2pdf。请安装 Edge 或执行 pip install -r requirements-pdf.txt"
    )


def _strip_ch6_core_block(md: str) -> str:
    if "### 核心结论" not in md or "### 综合评价" not in md:
        return md
    lead, rest = md.split("### 核心结论", 1)
    if "### 综合评价" not in rest:
        return md
    _, tail = rest.split("### 综合评价", 1)
    return lead.rstrip() + "\n\n### 综合评价" + tail


def _md_to_html(md: str) -> str:
    import markdown

    body = (md or "").strip()
    if not body:
        return ""
    return markdown.markdown(
        body,
        extensions=["tables", "nl2br", "sane_lists"],
    )


def _html_dim_scores_highlight_pdf(
    *,
    eval_metrics: Sequence[str],
    dut_avg: np.ndarray,
    score_dut: float,
    pairwise: bool,
    diff_avg: np.ndarray,
    score_ref: float,
    diff: float,
) -> str:
    """PDF 用 table 布局的五维大卡（xhtml2pdf 对 CSS Grid 支持弱）。"""
    cells: list[str] = []
    for i, m in enumerate(eval_metrics):
        lab = html.escape(_DIM_SCORE_SHORT.get(m, m))
        val = float(dut_avg[i])
        delta = float(diff_avg[i])
        if pairwise:
            dc = diff_accent_color(delta)
            cells.append(
                f'<td width="16%" valign="top" style="padding:4px;">'
                f'<div style="background:#f8fafc;border:2px solid #cbd5e1;border-radius:10px;'
                f'padding:10px;text-align:center;">'
                f'<div style="font-size:10px;font-weight:bold;">{lab}</div>'
                f'<div style="font-size:9px;color:#64748b;">平均分差</div>'
                f'<div style="font-size:18px;font-weight:bold;color:{dc};">{delta:+.2f}</div>'
                f'<div style="font-size:9px;">映射 {val:.2f}</div></div></td>'
            )
        else:
            cells.append(
                f'<td width="16%" valign="top" style="padding:4px;">'
                f'<div style="background:#eff6ff;border:2px solid #2563eb;border-radius:10px;'
                f'padding:10px;text-align:center;">'
                f'<div style="font-size:10px;font-weight:bold;">{lab}</div>'
                f'<div style="font-size:18px;font-weight:bold;color:#1d4ed8;">{val:.2f}</div>'
                f"</div></td>"
            )
    if pairwise:
        dc_t = diff_accent_color(diff)
        cells.append(
            f'<td width="16%" valign="top" style="padding:4px;">'
            f'<div style="background:#1e3a8a;border-radius:10px;padding:10px;text-align:center;color:#fff;">'
            f'<div style="font-size:10px;font-weight:bold;">总平均分差</div>'
            f'<div style="font-size:20px;font-weight:bold;">{diff:+.2f}</div>'
            f'<div style="font-size:9px;">映射 {score_dut:.2f} · 基准 {score_ref:.2f}</div>'
            f"</div></td>"
        )
    else:
        cells.append(
            f'<td width="16%" valign="top" style="padding:4px;">'
            f'<div style="background:#2563eb;border-radius:10px;padding:10px;text-align:center;color:#fff;">'
            f'<div style="font-size:10px;font-weight:bold;">五维总平均</div>'
            f'<div style="font-size:20px;font-weight:bold;">{score_dut:.2f}</div>'
            f"</div></td>"
        )
    return '<table width="100%" cellspacing="4"><tr>' + "".join(cells) + "</tr></table>"


def _chart_data_uri(
    *,
    dut_avg: np.ndarray,
    ref_avg: np.ndarray,
    eval_metrics: Sequence[str],
    pairwise: bool,
    kind: str,
) -> str:
    _setup_matplotlib_cjk()
    if kind == "bar":
        fig, ax = plt.subplots(figsize=(9, 3.6))
        x = np.arange(len(eval_metrics))
        ld = "被测映射分" if pairwise else "测试机"
        lr = "对比基准(7)" if pairwise else "对比机"
        ax.bar(x - 0.2, dut_avg, 0.4, label=ld)
        ax.bar(x + 0.2, ref_avg, 0.4, label=lr)
        ax.set_xticks(x)
        ax.set_xticklabels(list(eval_metrics), rotation=25, fontsize=8)
        ax.legend(fontsize=8)
        ax.set_title("五维平均分 · 柱状对照", fontsize=10)
    else:
        fig, ax = plt.subplots(figsize=(5.5, 5.5), subplot_kw=dict(polar=True))
        angles = np.linspace(0, 2 * np.pi, len(eval_metrics), endpoint=False)
        angles = np.concatenate([angles, [angles[0]]])
        ld = "被测映射分" if pairwise else "测试机"
        lr = "对比基准(7)" if pairwise else "对比机"
        ax.plot(angles, np.concatenate([dut_avg, [dut_avg[0]]]), "o-", label=ld)
        ax.plot(angles, np.concatenate([ref_avg, [ref_avg[0]]]), "o-", label=lr)
        ax.legend(fontsize=8, loc="upper right", bbox_to_anchor=(1.25, 1.05))
        ax.set_title("五维平均分 · 雷达对照", fontsize=10)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120, bbox_inches="tight")
    plt.close(fig)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"


def _dataframe_to_html_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "<p>（无逐音源明细）</p>"
    disp = df.copy()
    for k in DIMENSION_KEYS:
        if k in disp.columns:
            disp[k] = disp[k].apply(
                lambda x: int(x)
                if pd.notna(x) and abs(float(x) - round(float(x))) < 1e-9
                else round(float(x), 1)
            )
    tbl = disp.to_html(index=False, border=1, escape=True)
    return (
        '<div class="table-wrap">'
        + tbl.replace(
            "<table",
            '<table cellspacing="0" cellpadding="4" style="width:100%;font-size:9px;"',
        )
        + "</div>"
    )


def build_eval_report_pdf(
    *,
    score_json_path: str | Path,
    dut_s: str,
    ref_s: str,
    mic_pick: str,
    model_line: str,
    analysis_json_path: str | Path | None = None,
) -> tuple[bytes | None, str]:
    """
    生成与 Web UI 评测结果页一致的 PDF。

    返回 ``(pdf_bytes, message)``；失败时 bytes 为 None。
    """
    if not pdf_export_available():
        return None, f"未安装 PDF 依赖。{_PDF_DEPS_HINT}"

    path = Path(score_json_path)
    if not path.is_file():
        return None, f"未找到评分 JSON：{path}"

    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        return None, f"读取评分 JSON 失败：{exc}"

    json_model = str(data.get("web_ui_eval_model") or data.get("eval_model") or "").strip()
    display_model = json_model or (model_line or "").strip() or "评测模型"

    pairwise = bool(data.get("comparison_mode") or data.get("stimulus_pairwise"))
    dut_avg = np.array([float(data["dut_scores"][m]) for m in EVAL_METRICS])
    ref_avg = np.array([float(data["ref_scores"][m]) for m in EVAL_METRICS])
    diff_avg = dut_avg - ref_avg
    score_dut = float(np.mean(dut_avg))
    score_ref = float(np.mean(ref_avg))
    diff = score_dut - score_ref

    if diff > 1.0:
        conclusion = f"[优] {dut_s} 综合音质显著优于 {ref_s}，领先 {diff:.1f} 分"
    elif diff > 0.3:
        conclusion = f"[优] {dut_s} 音质优于 {ref_s}"
    elif abs(diff) <= 0.3:
        conclusion = f"[相当] {dut_s} 与 {ref_s} 音质相当"
    else:
        conclusion = f"[注意] {dut_s} 略逊于 {ref_s}"

    _cd = "被测映射分（7+Δ）" if pairwise else "测试机"
    _cr = "对比基准（固定7）" if pairwise else "对比机"
    _cdf = "五维平均分差（-3~+3）" if pairwise else "分差"

    sections: list[str] = []
    sections.append(
        f"<h1>喇叭测试报告 · AI学习机智能音效评测</h1>"
        f'<p class="note">生成时间：{html.escape(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))}</p>'
        f"<p><b>评测模型</b>：{html.escape(display_model)}</p>"
        f"<p><b>被测</b>：{html.escape(dut_s)}<br/>"
        f"<b>对比</b>：{html.escape(ref_s)}<br/>"
        f"<b>麦克风</b>：{html.escape(mic_pick)}</p>"
    )
    if pairwise:
        sections.append(
            '<p class="note">同刺激双机对比：五维为整数分差 -3~+3；'
            "对比基准为固定 7 分展示，非对比机独立绝对分。</p>"
        )

    sections.append('<h2>一体化概览</h2><div class="section">')
    sections.append(
        _html_dim_scores_highlight_pdf(
            eval_metrics=list(EVAL_METRICS),
            dut_avg=dut_avg,
            score_dut=score_dut,
            pairwise=pairwise,
            diff_avg=diff_avg,
            score_ref=score_ref,
            diff=diff,
        )
    )

    analysis: dict[str, Any] | None = None
    if analysis_json_path:
        ap = Path(analysis_json_path)
        if ap.is_file():
            try:
                analysis = json.loads(ap.read_text(encoding="utf-8"))
            except Exception:
                analysis = None
    if analysis is None:
        analysis = load_analysis_from_score_json_path(path)
    rows: list[dict[str, Any]] = []
    section_six_md = ""
    six_ctx: dict[str, Any] | None = None
    if analysis:
        rows = build_per_track_rows(analysis)
        if rows:
            dim_s6, grand_s6 = compute_dimension_statistics(rows)
            section_six_md = build_section_six_markdown(
                comparison_mode=pairwise,
                dim_avgs=dim_s6,
                grand=grand_s6,
                rows=rows,
            )
            six_ctx = {"dim_avgs": dim_s6, "grand": grand_s6, "rows": rows}

    if six_ctx is not None:
        sections.append(
            _core_conclusion_highlight_html(
                comparison_mode=pairwise,
                grand=six_ctx["grand"],
                rows=six_ctx["rows"],
            )
        )
    else:
        sections.append(
            f'<div style="background:#f1f5f9;border:2px solid #94a3b8;border-radius:12px;'
            f'padding:16px;text-align:center;margin:12px 0;">'
            f'<p style="font-size:14px;font-weight:700;">{html.escape(conclusion)}</p></div>'
        )

    bar_uri = _chart_data_uri(
        dut_avg=dut_avg,
        ref_avg=ref_avg,
        eval_metrics=EVAL_METRICS,
        pairwise=pairwise,
        kind="bar",
    )
    radar_uri = _chart_data_uri(
        dut_avg=dut_avg,
        ref_avg=ref_avg,
        eval_metrics=EVAL_METRICS,
        pairwise=pairwise,
        kind="radar",
    )
    sections.append(
        f'<table width="100%" class="chart-row"><tr>'
        f'<td width="55%"><img src="{bar_uri}" alt="柱状图"/></td>'
        f'<td width="45%"><img src="{radar_uri}" alt="雷达图"/></td>'
        f"</tr></table>"
    )

    sum_rows = "".join(
        f"<tr><td>{html.escape(m)}</td>"
        f"<td>{dut_avg[i]:.2f}</td><td>{ref_avg[i]:.2f}</td><td>{diff_avg[i]:+.2f}</td></tr>"
        for i, m in enumerate(EVAL_METRICS)
    )
    sections.append(
        f"<h3>评分汇总（全表）</h3>"
        f'<table border="1" cellpadding="4" cellspacing="0" style="width:100%;">'
        f"<tr><th>指标</th><th>{html.escape(_cd)}</th>"
        f"<th>{html.escape(_cr)}</th><th>{html.escape(_cdf)}</th></tr>"
        f"{sum_rows}</table>"
    )
    m1 = "被测五维平均（映射分）" if pairwise else "测试机五维平均"
    m2 = "对比基准（固定）" if pairwise else "对比机五维平均"
    m3 = "平均分差（-3~+3）" if pairwise else "平均分差"
    sections.append(
        f'<table width="100%" cellspacing="8"><tr>'
        f'<td width="33%" class="metric-box"><div class="metric-label">{html.escape(m1)}</div>'
        f'<div class="metric-value">{score_dut:.2f}</div></td>'
        f'<td width="33%" class="metric-box"><div class="metric-label">{html.escape(m2)}</div>'
        f'<div class="metric-value">{score_ref:.2f}</div></td>'
        f'<td width="33%" class="metric-box"><div class="metric-label">{html.escape(m3)}</div>'
        f'<div class="metric-value">{diff:+.2f}</div></td>'
        f"</tr></table></div>"
    )

    sections.append("<h2>测试报告详情</h2>")
    if rows:
        df = rows_to_dataframe(rows)
        sections.append("<h3>逐音源评测明细</h3>")
        sections.append(_dataframe_to_html_table(df))
    else:
        sections.append("<p class='note'>未定位到 analysis JSON，逐音源表明细不可用。</p>")

    scale_line = ""
    if pairwise:
        scale_line = (
            "<ul><li><b>评分标尺</b>：同刺激双机对比，五维为整数分差 -3~+3；"
            "对比基准为固定 7 分展示。</li></ul>"
        )
    report_intro = (
        f"<h3>报告摘要</h3><p><b>结论摘要</b>：{html.escape(conclusion)}</p>{scale_line}"
        f"<p><b>设备</b>：被测 {html.escape(dut_s)}；对比 {html.escape(ref_s)}；"
        f"麦克风 {html.escape(mic_pick)}</p>"
    )
    sections.append(report_intro)

    if section_six_md:
        sections.append("<h2>本次评测最终结论与结果汇总</h2>")
        s6_web = _strip_ch6_core_block(section_six_md)
        try:
            from nisqa_local import strip_nisqa_appendix_from_section_six

            s6_web = strip_nisqa_appendix_from_section_six(s6_web)
        except Exception:
            pass
        html_s6 = _md_to_html(s6_web)
        html_s6 = re.sub(
            r"<div style=\"background:linear-gradient[^\"]*\"[^>]*>.*?</div>",
            "",
            html_s6,
            count=1,
            flags=re.DOTALL,
        )
        sections.append(f'<div class="section">{html_s6}</div>')
        nisqa_html = _build_nisqa_visual_section_html(rows)
        if nisqa_html:
            sections.append(f'<div class="section">{nisqa_html}</div>')

    try:
        pdf_bytes = _html_to_pdf_bytes("".join(sections), theme="eval")
    except Exception as exc:
        return None, f"生成 PDF 失败：{exc}"

    return pdf_bytes, "ok"


def suggested_pdf_filename(model_line: str) -> str:
    safe = re.sub(r'[<>:"/\\|?*]', "_", (model_line or "report").strip())[:40]
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"喇叭评测报告_{safe}_{ts}.pdf"


# ----- 仅 NISQA 专页 PDF（与 web_ui_nisqa_only 展示一致） -----


def _nisqa_badge_html(note: str) -> str:
    palette = {
        "优秀": ("#ecfdf5", "#047857"),
        "良好": ("#eff6ff", "#1d4ed8"),
        "中等": ("#fffbeb", "#b45309"),
        "偏弱": ("#fff7ed", "#c2410c"),
        "较差": ("#fef2f2", "#b91c1c"),
    }
    bg, fg = palette.get(note, ("#f1f5f9", "#475569"))
    return (
        f'<span style="background:{bg};color:{fg};padding:2px 8px;'
        f'border-radius:6px;font-size:9px;">{html.escape(note)}</span>'
    )


def _nisqa_device_panel_html(device: str, avg_rows: list[dict[str, str]]) -> str:
    cards: list[str] = []
    for row in avg_rows:
        dim = html.escape(str(row.get("维度", "")))
        score = html.escape(str(row.get("平均分", "—")))
        level = str(row.get("等级") or row.get("说明", ""))
        detail = html.escape(str(row.get("说明", "")))
        cards.append(
            f'<td width="20%" valign="top" style="padding:4px;">'
            f'<div style="border:1px solid #e2e8f0;border-radius:8px;padding:8px;text-align:center;">'
            f'<div style="font-size:10px;font-weight:bold;">{dim}</div>'
            f'<div style="font-size:16px;font-weight:bold;color:#1d4ed8;">{score}</div>'
            f'<div style="margin:4px 0;">{_nisqa_badge_html(level)}</div>'
            f'<div style="font-size:8px;color:#64748b;">{detail}</div>'
            f"</div></td>"
        )
    return (
        f'<h3>{html.escape(device)} · 维度平均</h3>'
        f'<table width="100%" cellspacing="4"><tr>{"".join(cards)}</tr></table>'
    )


def _nisqa_conclusion_badge_html(text: str) -> str:
    if "设备 A" in text:
        bg, fg = "#ecfdf5", "#047857"
    elif "设备 B" in text:
        bg, fg = "#eff6ff", "#1d4ed8"
    else:
        bg, fg = "#f1f5f9", "#475569"
    return (
        f'<span style="background:{bg};color:{fg};padding:2px 6px;'
        f'border-radius:6px;font-size:9px;">{html.escape(text)}</span>'
    )


def _nisqa_diff_table_html(diff_rows: list[dict[str, str]]) -> str:
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
            f"<td style='font-size:9px;'>"
            f"{_nisqa_conclusion_badge_html(verdict_short)}<br/>{verdict_detail}"
            f"</td>"
            f"</tr>"
        )
    return (
        "<h3>设备 A vs B · 核心差异</h3>"
        '<table border="1" cellpadding="4" cellspacing="0" width="100%" style="font-size:9px;">'
        "<tr><th>维度</th><th>设备 A 平均</th><th>设备 B 平均</th><th>A-B</th><th>结论</th></tr>"
        + "".join(body)
        + "</table>"
    )


def _nisqa_radar_chart_uri(device_avg_rows: dict[str, list[dict[str, str]]]) -> str:
    if "设备 A" not in device_avg_rows or "设备 B" not in device_avg_rows:
        return ""
    rows_a = device_avg_rows["设备 A"]
    labels = [str(r.get("维度", "")) for r in rows_a]

    def _avg(s: str) -> float | None:
        try:
            return float(str(s).strip())
        except (TypeError, ValueError):
            return None

    vals_a = [_avg(str(r.get("平均分", ""))) for r in rows_a]
    vals_b = [_avg(str(r.get("平均分", ""))) for r in device_avg_rows["设备 B"]]
    if not labels or any(v is None for v in vals_a + vals_b):
        return ""
    _setup_matplotlib_cjk()
    n = len(labels)
    angles = np.linspace(0, 2 * np.pi, n, endpoint=False)
    angles = np.concatenate([angles, [angles[0]]])
    va = np.array(vals_a, dtype=float)
    vb = np.array(vals_b, dtype=float)
    fig, ax = plt.subplots(figsize=(4.6, 3.2), subplot_kw=dict(polar=True))
    ax.plot(angles, np.concatenate([va, [va[0]]]), "o-", color="#047857", label="设备 A")
    ax.plot(angles, np.concatenate([vb, [vb[0]]]), "o-", color="#1d4ed8", label="设备 B")
    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(labels, fontsize=8)
    ax.set_ylim(1, 5)
    ax.set_title("五维雷达对比（NISQA 约 1–5 分）", fontsize=9)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.12), ncol=2, fontsize=7)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120, bbox_inches="tight")
    plt.close(fig)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _nisqa_detail_table_html(report: dict[str, Any], *, heading: str = "") -> str:
    from web_ui_nisqa_only import _entries_to_detail_df

    df = _entries_to_detail_df(list(report.get("all_entries") or []))
    if df.empty:
        return "<p>（暂无逐条录音明细）</p>"
    head = f"<h3>{html.escape(heading)}</h3>" if heading else ""
    tbl = df.to_html(index=False, border=1, escape=True).replace(
        "<table",
        '<table cellpadding="4" cellspacing="0" style="width:100%;font-size:9px;"',
    )
    return head + tbl


def render_nisqa_visual_html(
    report: dict[str, Any],
    *,
    show_heading: bool = True,
    show_disclaimer: bool = True,
) -> str:
    """
    与 Web ``render_nisqa_report_from_rows`` 一致：总览（卡片/雷达/差异）+ 录音明细。
    勿使用 ``render_nisqa_appendix_markdown`` 裸表（顺序与版式不同）。
    """
    from nisqa_local import nisqa_scale_disclaimer_text

    parts: list[str] = []
    if show_heading:
        parts.append("<h2>📊 NISQA 客观音质（本地）</h2>")
    if show_disclaimer:
        parts.append(
            f'<p class="note">{html.escape(nisqa_scale_disclaimer_text())}</p>'
        )
    parts.append("<h3>总览</h3>")
    device_avg = report.get("device_avg_rows") or {}
    if report.get("has_ab_compare"):
        parts.append(
            '<table width="100%" cellspacing="8"><tr>'
            f'<td width="50%" valign="top">{_nisqa_device_panel_html("设备 A", device_avg.get("设备 A", []))}</td>'
            f'<td width="50%" valign="top">{_nisqa_device_panel_html("设备 B", device_avg.get("设备 B", []))}</td>'
            "</tr></table>"
        )
        radar_uri = _nisqa_radar_chart_uri(device_avg)
        if radar_uri:
            parts.append(
                f'<p style="text-align:center;margin:12px 0;">'
                f'<img src="{radar_uri}" alt="NISQA雷达图" width="420"/></p>'
            )
        parts.append(_nisqa_diff_table_html(list(report.get("diff_rows") or [])))
    else:
        for device, avg_rows in device_avg.items():
            parts.append(_nisqa_device_panel_html(device, avg_rows))

    parts.append(_nisqa_detail_table_html(report, heading="录音明细"))
    return "".join(parts)


def _build_nisqa_visual_section_html(rows: list[dict[str, Any]]) -> str:
    """从 analysis 逐轨行生成 NISQA 可视化 HTML（无数据则返回空串）。"""
    try:
        from nisqa_local import collect_nisqa_report_data, has_nisqa_report_data

        if not has_nisqa_report_data(rows):
            return ""
        report = collect_nisqa_report_data(rows)
        return render_nisqa_visual_html(report)
    except Exception:
        return ""


def build_nisqa_only_report_pdf(
    payload: Mapping[str, Any],
    *,
    eval_mode: str = "",
) -> tuple[bytes | None, str]:
    """
    生成与 Web UI「仅 NISQA」专页一致的 PDF（总览 + 录音明细）。

    ``payload`` 为 ``execute_nisqa_only_run`` 返回的结构（含 ``tracks``、``summary``）。
    """
    if not pdf_export_available():
        return None, f"未安装 PDF 依赖。{_PDF_DEPS_HINT}"

    tracks = list(payload.get("tracks") or [])
    if not tracks:
        return None, "无 NISQA 评分数据（tracks 为空）"

    from nisqa_local import collect_nisqa_report_data

    report = collect_nisqa_report_data(tracks)
    summary = payload.get("summary") or {}
    summ_line = (
        f"成功 {summary.get('ok', 0)} / {summary.get('total', 0)} 条"
        + (
            f"（失败 {summary.get('failed', 0)}，已停止 {summary.get('cancelled', 0)}）"
            if summary.get("failed") or summary.get("cancelled")
            else ""
        )
    )

    sections: list[str] = []
    sections.append(
        "<h1>NISQA 客观音质评测报告</h1>"
        f'<p class="note">生成时间：{html.escape(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))}</p>'
        f'<div class="summary-box"><b>批次结果</b>：{html.escape(summ_line)}</div>'
    )
    if eval_mode == "dual_device":
        sections.append(
            '<p class="note">本次为<strong>仅 NISQA</strong>模式：已对双设备会话中的本地录音逐条客观评分，'
            "未调用 Dify 主观听感模型。</p>"
        )
    else:
        sections.append(
            '<p class="note">本次为<strong>仅 NISQA</strong>模式：已对本地录音逐条客观评分，未调用 Dify。</p>'
        )

    sections.append(
        render_nisqa_visual_html(report, show_heading=True, show_disclaimer=True)
    )

    out_paths = []
    if payload.get("output_json"):
        out_paths.append(f"JSON：{payload['output_json']}")
    if payload.get("output_csv"):
        out_paths.append(f"CSV：{payload['output_csv']}")
    if out_paths:
        sections.append(
            f'<p class="note">输出文件：{html.escape(" ｜ ".join(out_paths))}</p>'
        )

    try:
        return _html_to_pdf_bytes("".join(sections), theme="nisqa"), "ok"
    except Exception as exc:
        return None, f"生成 PDF 失败：{exc}"


def suggested_nisqa_pdf_filename() -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"NISQA客观音质报告_{ts}.pdf"
