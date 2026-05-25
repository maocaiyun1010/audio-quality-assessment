# -*- coding: utf-8 -*-
"""
音效评测汇总表：制表符分隔（TSV），粘贴到 Excel 自动分列。
不使用 Markdown 表格；多机模式含 −3～+3 分差标尺说明与被测/对比分列评述。
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from markdown_report import DIMENSION_KEYS, _dim_numeric, compute_dimension_statistics

# Excel 友好：UTF-8 带 BOM，避免中文乱码
UTF8_BOM = "\ufeff"

SCALE_PAIRWISE = (
    "评分标准（刺激比较，被测相对对比）："
    "-3坏得很 -2坏 -1稍差 0相同 1稍好 2更好 3好得多；正分表示被测优于对比。"
)

SCALE_SINGLE = (
    "评分标准（单终端绝对分）：五维为 1～10 分整数，分值越高越好；综合分为五维算术平均（一位小数）。"
)


def _tsv_cell(s: Any) -> str:
    t = "" if s is None else str(s)
    t = t.replace("\t", " ").replace("\r", " ").replace("\n", " ")
    return t.strip()


def _row_mean_five(row: Mapping[str, Any]) -> float | None:
    vals = [_dim_numeric(row, k) for k in DIMENSION_KEYS]
    nums = [x for x in vals if x is not None]
    if not nums:
        return None
    return round(sum(nums) / len(nums), 2)


def _fmt_num(v: float | None, *, as_int_if_whole: bool) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    if as_int_if_whole and abs(v - round(v)) < 1e-9:
        return str(int(round(v)))
    return f"{v:.2f}"


def comparison_overall_narrative(
    dim_avgs: Mapping[str, float | None],
    *,
    dut_label: str,
    ref_label: str,
) -> str:
    """分别概括被测机与对比机（分差：正=被测优于对比）。"""
    cn = {
        "声音响度": "响度",
        "人声清晰度": "人声清晰度",
        "听感舒适度": "听感舒适度",
        "失真与噪声": "失真与噪声",
        "频响平衡": "频响平衡",
    }
    dut_strong: list[str] = []
    ref_strong: list[str] = []
    for k in DIMENSION_KEYS:
        a = dim_avgs.get(k)
        if a is None:
            continue
        if a >= 0.15:
            dut_strong.append(cn[k])
        elif a <= -0.15:
            ref_strong.append(cn[k])
    dtxt = "、".join(dut_strong) if dut_strong else "与对比未形成稳定优势项"
    rtxt = "、".join(ref_strong) if ref_strong else "未呈现稳定劣势项"
    return (
        f"被测机（{dut_label}）：列平均上在 {dtxt} 相对对比机更易取得正向分差；"
        f"其余维度与对比机差距较小或互有胜负，整体属细粒度听感差异。\n"
        f"对比机（{ref_label}）：在 {rtxt} 上相对被测更易占优（分差为负表示对比更优）；"
        f"建议结合更多语声/曲艺素材与现场复听复核。"
    )


def single_overall_narrative(grand: float | None, n: int) -> str:
    if grand is None:
        return f"单终端共 {n} 条节目，五维有效分不足，无法给出总评。"
    return (
        f"单终端共 {n} 条节目，五维跨节目总均分为 {grand:.2f}（1～10）；"
        "各节目本行均分见表末列，详细以各维度得分为准。"
    )


def render_evaluation_tsv(
    *,
    test_name: str,
    test_device: str,
    ref_device: str,
    eval_model_name: str = "",
    comparison_mode: bool,
    cross_session: bool,
    rows: list[dict[str, Any]],
    dim_avgs: dict[str, float | None],
    grand: float | None,
    dut_label: str = "",
    ref_label: str = "",
) -> str:
    lines: list[str] = []
    lines.append(f"报告标题\t{_tsv_cell(test_name)}")
    _em = (eval_model_name or "").strip()
    if _em:
        lines.append(f"评测大模型\t{_tsv_cell(_em)}")
    lines.append(f"被测/主测标识\t{_tsv_cell(test_device)}")
    lines.append(f"对比/参考信息\t{_tsv_cell(ref_device)}")
    mode = (
        "跨会话刺激比较（五维整数−3～+3，正为被测优）"
        if cross_session
        else (
            "同会话刺激比较（五维整数−3～+3，正为被测优）"
            if comparison_mode
            else "单终端绝对分（五维1～10）"
        )
    )
    lines.append(f"评测模式\t{mode}")
    lines.append("")

    hdr = ["测评音频"] + list(DIMENSION_KEYS) + ["本行五维均分"]
    if not comparison_mode:
        hdr.append("综合分")
    lines.append("\t".join(hdr))

    for r in rows:
        audio = _tsv_cell(r.get("节目"))
        cells = [audio]
        for k in DIMENSION_KEYS:
            v = _dim_numeric(r, k)
            if comparison_mode:
                cells.append(_fmt_num(v, as_int_if_whole=True) if v is not None else "")
            else:
                cells.append(_fmt_num(v, as_int_if_whole=False) if v is not None else "")
        rm = _row_mean_five(r)
        cells.append(f"{rm:.2f}" if rm is not None else "")
        if not comparison_mode:
            z = _dim_numeric(r, "综合分")
            cells.append(_fmt_num(z, as_int_if_whole=False) if z is not None else "")
        lines.append("\t".join(cells))

    # 一行汇总：前五列为各维度「全节目算术平均」，末列为「所有维度总平均分」（五维列均值再平均）
    foot = ["各维度列平均及总平均"]
    for k in DIMENSION_KEYS:
        a = dim_avgs.get(k)
        foot.append(f"{a:.2f}" if a is not None else "")
    foot.append(f"{grand:.2f}" if grand is not None else "")
    if not comparison_mode:
        znums = [_dim_numeric(r, "综合分") for r in rows]
        znums = [x for x in znums if x is not None]
        zavg = round(sum(znums) / len(znums), 2) if znums else None
        foot.append(f"{zavg:.2f}" if zavg is not None else "")
    lines.append("\t".join(foot))

    lines.append("")
    lines.append(SCALE_PAIRWISE if comparison_mode else SCALE_SINGLE)
    lines.append("")

    dl = dut_label.strip() or test_device.strip() or "被测"
    rl = ref_label.strip() or "对比样机"
    if comparison_mode:
        lines.append(comparison_overall_narrative(dim_avgs, dut_label=dl, ref_label=rl))
    else:
        lines.append(single_overall_narrative(grand, len(rows)))

    body = "\n".join(lines)
    return UTF8_BOM + body


def write_tsv(path: str, content: str) -> None:
    from pathlib import Path

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    # 用户给定示例：两条语声 + 分差，打印 TSV 便于核对
    from markdown_report import compute_dimension_statistics

    demo_rows: list[dict[str, Any]] = [
        {
            "节目": "语声/01-诵读-赤壁怀古-苏轼 1'49'' Nb.mp3",
            "分组": "语声",
            "声音响度": 1,
            "人声清晰度": 2,
            "听感舒适度": -1,
            "失真与噪声": 1,
            "频响平衡": 1,
        },
        {
            "节目": "语声/02-诵读-钗头凤-陆游 1'12'' Ng.mp3",
            "分组": "语声",
            "声音响度": 1,
            "人声清晰度": 1,
            "听感舒适度": 0,
            "失真与噪声": 0,
            "频响平衡": 1,
        },
    ]
    da, gr = compute_dimension_statistics(demo_rows)
    out = render_evaluation_tsv(
        test_name="音效评测汇总（示例数据）",
        test_device="测试机（被测）",
        ref_device="对比样机",
        comparison_mode=True,
        cross_session=False,
        rows=demo_rows,
        dim_avgs=da,
        grand=gr,
        dut_label="测试机",
        ref_label="对比样机",
    )
    from config import REPORT_DIR

    demo_path = REPORT_DIR / "示例_用户给定数据_音效评测.tsv"
    write_tsv(str(demo_path), out)
    from excel_summary import write_evaluation_xlsx

    xlsx_path = REPORT_DIR / "示例_用户给定数据_音效评测.xlsx"
    write_evaluation_xlsx(
        xlsx_path,
        test_name="音效评测汇总（示例数据）",
        test_device="测试机（被测）",
        ref_device="对比样机",
        comparison_mode=True,
        cross_session=False,
        rows=demo_rows,
        dim_avgs=da,
        grand=gr,
        dut_label="测试机",
        ref_label="对比样机",
    )
    print(out)
    print(f"\n已写入 TSV: {demo_path}", flush=True)
    print(f"已写入 Excel 表格: {xlsx_path}", flush=True)
