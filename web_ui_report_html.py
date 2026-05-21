# -*- coding: utf-8 -*-
"""Web 评测结果页 HTML 片段（五维大卡等）。"""
from __future__ import annotations

import html
from typing import Sequence

import numpy as np

_DIM_SCORE_SHORT: dict[str, str] = {
    "声音响度": "响度",
    "人声清晰度": "人声",
    "听感舒适度": "舒适度",
    "失真与噪声": "失真/噪声",
    "频响平衡": "平衡",
}


def diff_accent_color(delta: float) -> str:
    if delta > 0.05:
        return "#059669"
    if delta < -0.05:
        return "#dc2626"
    return "#1d4ed8"


def html_dim_scores_highlight(
    *,
    eval_metrics: Sequence[str],
    dut_avg: np.ndarray,
    score_dut: float,
    pairwise: bool,
    diff_avg: np.ndarray,
    score_ref: float,
    diff: float,
) -> str:
    """一体化概览顶部五维大卡（Streamlit 页 CSS Grid）。"""
    parts: list[str] = []
    for i, m in enumerate(eval_metrics):
        lab = html.escape(_DIM_SCORE_SHORT.get(m, m))
        val = float(dut_avg[i])
        delta = float(diff_avg[i])
        if pairwise:
            dc = diff_accent_color(delta)
            parts.append(
                f'<div style="background:#f8fafc;border:2px solid #cbd5e1;border-radius:12px;'
                f'padding:14px 10px;text-align:center;min-height:118px;display:flex;flex-direction:column;'
                f'justify-content:center;align-items:center;">'
                f'<div style="font-size:0.88rem;color:#475569;font-weight:700;margin-bottom:4px;">{lab}</div>'
                f'<div style="font-size:0.72rem;color:#64748b;font-weight:600;margin-bottom:2px;">'
                f"平均分差（−3～+3）</div>"
                f'<div style="font-size:2.1rem;font-weight:900;color:{dc};line-height:1.1;">{delta:+.2f}</div>'
                f'<div style="margin-top:6px;font-size:0.84rem;font-weight:600;color:#64748b;">'
                f"映射分 {val:.2f}</div>"
                f"</div>"
            )
        else:
            parts.append(
                f'<div style="background:#eff6ff;border:2px solid #2563eb;border-radius:12px;'
                f'padding:14px 10px;text-align:center;min-height:108px;display:flex;flex-direction:column;'
                f'justify-content:center;align-items:center;">'
                f'<div style="font-size:0.92rem;color:#334155;font-weight:700;margin-bottom:6px;">{lab}</div>'
                f'<div style="font-size:2.05rem;font-weight:900;color:#1d4ed8;line-height:1.1;">{val:.2f}</div>'
                f"</div>"
            )
    if pairwise:
        dc_t = diff_accent_color(diff)
        total = (
            f'<div style="background:linear-gradient(160deg,#0f172a 0%,#1e3a8a 52%,#1d4ed8 100%);'
            f'border-radius:12px;padding:14px 10px;text-align:center;min-height:118px;'
            f'display:flex;flex-direction:column;justify-content:center;align-items:center;'
            f'box-shadow:0 4px 18px rgba(15,23,42,0.45);">'
            f'<div style="font-size:0.88rem;color:rgba(255,255,255,0.9);font-weight:700;margin-bottom:4px;">'
            f"五维总平均分差</div>"
            f'<div style="font-size:0.72rem;color:rgba(255,255,255,0.75);font-weight:600;margin-bottom:2px;">'
            f"标尺 −3～+3（JSON 聚合）</div>"
            f'<div style="font-size:2.2rem;font-weight:900;color:{dc_t};line-height:1.1;">{diff:+.2f}</div>'
            f'<div style="margin-top:8px;font-size:0.82rem;font-weight:600;color:rgba(255,255,255,0.88);">'
            f"映射均分 {score_dut:.2f} · 基准 {score_ref:.2f}</div>"
            f"</div>"
        )
    else:
        total = (
            f'<div style="background:linear-gradient(160deg,#1e3a8a 0%,#2563eb 48%,#3b82f6 100%);'
            f'border-radius:12px;padding:14px 10px;text-align:center;min-height:108px;'
            f'display:flex;flex-direction:column;justify-content:center;align-items:center;'
            f'box-shadow:0 4px 14px rgba(37,99,235,0.35);">'
            f'<div style="font-size:0.92rem;color:rgba(255,255,255,0.92);font-weight:700;margin-bottom:6px;">'
            f"五维总平均</div>"
            f'<div style="font-size:2.15rem;font-weight:900;color:#fff;line-height:1.1;">{score_dut:.2f}</div>'
            f'<div style="margin-top:6px;font-size:0.82rem;font-weight:600;color:rgba(255,255,255,0.88);">'
            f"（测试机五维算术平均）</div>"
            f"</div>"
        )
    parts.append(total)
    return '<div class="dim-score-hero-grid">' + "".join(parts) + "</div>"
