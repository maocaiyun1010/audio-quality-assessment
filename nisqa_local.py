# -*- coding: utf-8 -*-
"""
本地 NISQA 客观音质评测（可选旁路，不替代 Dify 主观五维）。

启用：``SPEAKER_NISQA_ENABLED=1``，安装 ``pip install -r requirements-nisqa.txt``，
权重：``python scripts/setup_nisqa_weights.py``。

结果写入每条 ``tracks[]`` 的 ``objective_scores`` 字段，不改变 ``parsed`` 五维 JSON。
"""
from __future__ import annotations

import csv
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

_SCORE_CACHE: dict[str, dict[str, Any]] = {}
_LOG_FN: Optional[Callable[[str], None]] = None
MIN_WEIGHT_BYTES = 100_000


def _log(msg: str) -> None:
    if _LOG_FN:
        _LOG_FN(msg)
    else:
        print(msg, flush=True)


def is_enabled() -> bool:
    return os.environ.get("SPEAKER_NISQA_ENABLED", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def on_failure_mode() -> str:
    from speaker_eval.settings.nisqa import NISQA_ON_FAILURE

    return NISQA_ON_FAILURE if NISQA_ON_FAILURE in ("skip", "fail") else "skip"


def weights_path() -> Path:
    from speaker_eval.settings.nisqa import (
        NISQA_MODEL_DIR,
        NISQA_MODEL_KIND,
        NISQA_WEIGHTS_FILE,
    )

    if NISQA_MODEL_KIND == "tts":
        name = "nisqa_tts.tar"
    elif NISQA_MODEL_KIND in ("mos", "mos_only", "transmitted_mos"):
        name = "nisqa_mos_only.tar"
    else:
        name = NISQA_WEIGHTS_FILE
    return NISQA_MODEL_DIR / name


def weights_ready() -> bool:
    """权重文件存在且大小符合 NISQA 预训练包的基本完整性要求。"""
    wp = weights_path()
    return wp.is_file() and wp.stat().st_size > MIN_WEIGHT_BYTES


def ensure_weights(*, log: Optional[Callable[[str], None]] = None) -> Path:
    """下载缺失或不完整的 NISQA 权重，返回本地权重路径。"""
    wp = weights_path()
    if weights_ready():
        return wp
    if log:
        log(f"[NISQA] 权重缺失或不完整，自动下载到 {wp}")
    from scripts.setup_nisqa_weights import ensure_nisqa_weights

    return ensure_nisqa_weights()


def is_available() -> bool:
    """依赖与权重是否就绪（不加载模型）。"""
    if not is_enabled():
        return False
    if not weights_ready():
        return False
    try:
        import nisqa.NISQA_model  # noqa: F401

        return True
    except ImportError:
        return False


def availability_message() -> str:
    if not is_enabled():
        return "NISQA 未启用（设置 SPEAKER_NISQA_ENABLED=1）"
    if not weights_path().is_file():
        return f"缺少权重文件：{weights_path()}（运行 python scripts/setup_nisqa_weights.py）"
    if not weights_ready():
        return f"权重文件不完整：{weights_path()}（重新运行 python scripts/setup_nisqa_weights.py）"
    try:
        import nisqa.NISQA_model  # noqa: F401
    except ImportError as exc:
        return f"未安装 nisqa 包：{exc}（pip install -r requirements-nisqa.txt）"
    return "就绪"


def _build_predict_args(wav_path: Path, output_dir: Path) -> dict[str, Any]:
    from speaker_eval.settings.nisqa import NISQA_PREDICT_BATCH_SIZE

    wp = weights_path()
    # 预训练 tar 的 args 可能不含 ms_channel；predict_file 仍用 args['ms_channel'] 取值。
    return {
        "mode": "predict_file",
        "pretrained_model": wp.name,
        "deg": str(wav_path.resolve()),
        "output_dir": str(output_dir),
        "num_workers": 0,
        "bs": NISQA_PREDICT_BATCH_SIZE,
        "tr_bs_val": NISQA_PREDICT_BATCH_SIZE,
        "tr_num_workers": 0,
        "tr_parallel": False,
        "ms_channel": None,
    }


def _row_from_results_csv(csv_path: Path, wav_path: Path) -> dict[str, Any]:
    if not csv_path.is_file():
        raise RuntimeError(f"NISQA 未生成结果文件: {csv_path}")
    with csv_path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise RuntimeError("NISQA 结果 CSV 为空")
    row = rows[-1]
    out: dict[str, Any] = {
        "file": wav_path.name,
        "path": str(wav_path.resolve()),
    }
    for key, val in row.items():
        if val is None or val == "":
            continue
        lk = str(key).strip().lower()
        if lk in ("deg", "filepath_deg", "file"):
            continue
        try:
            out[lk] = float(val)
        except (TypeError, ValueError):
            out[lk] = val
    mos = out.get("mos_pred", out.get("mos", out.get("overall")))
    if mos is not None:
        try:
            out["mos_pred"] = round(float(mos), 4)
        except (TypeError, ValueError):
            pass
    return out


def score_wav(wav_path: str | Path, *, log: Optional[Callable[[str], None]] = None) -> dict[str, Any]:
    """
  对单个 WAV 运行 NISQA，返回含 ``mos_pred`` 及多维指标的字典。

  失败时抛出异常（由调用方按 on_failure_mode 处理）。
    """
    global _LOG_FN
    prev_log = _LOG_FN
    if log is not None:
        _LOG_FN = log
    try:
        p = Path(wav_path).resolve()
        if not p.is_file():
            raise FileNotFoundError(f"音频不存在: {p}")
        key = str(p)
        if key in _SCORE_CACHE:
            return dict(_SCORE_CACHE[key])

        wp = ensure_weights(log=_log)

        from nisqa.NISQA_model import nisqaModel

        with tempfile.TemporaryDirectory(prefix="nisqa_out_") as td:
            out_dir = Path(td)
            args = _build_predict_args(p, out_dir)
            old = os.getcwd()
            try:
                os.chdir(str(wp.parent))
                model = nisqaModel(args)
                model.predict()
            finally:
                os.chdir(old)
            csv_path = out_dir / "NISQA_results.csv"
            metrics = _row_from_results_csv(csv_path, p)

        result = {
            "engine": "nisqa",
            "version": "2.0",
            "model_weights": wp.name,
            "metrics": metrics,
        }
        _SCORE_CACHE[key] = result
        return dict(result)
    finally:
        _LOG_FN = prev_log


def resolve_track_wav_paths(
    row: Mapping[str, Any],
    recorded_dir: Path,
) -> list[Path]:
    """从 scoring 行解析本地 WAV 路径（支持 ``wav_paths`` / ``file``）。"""
    found: list[Path] = []
    seen: set[str] = set()

    def _add(p: Path) -> None:
        try:
            rp = str(p.resolve())
        except OSError:
            rp = str(p)
        if rp in seen:
            return
        if p.is_file():
            seen.add(rp)
            found.append(p)

    raw_list = row.get("wav_paths")
    if isinstance(raw_list, (list, tuple)):
        for item in raw_list:
            pp = Path(str(item))
            if pp.is_file():
                _add(pp)
            else:
                _add(recorded_dir / pp.name)

    fn = str(row.get("file") or "").strip()
    if fn:
        pp = Path(fn)
        if pp.is_file():
            _add(pp)
        else:
            _add(recorded_dir / Path(fn).name)

    return found


def enrich_track_row_with_nisqa(
    row: dict[str, Any],
    *,
    recorded_dir: Path,
    log: Optional[Callable[[str], None]] = None,
) -> None:
    """
    若已启用 NISQA，为 ``row`` 附加 ``objective_scores``（就地修改）。

  不改变 ``parsed`` / ``ok`` / Dify 相关字段。
    """
    if not is_enabled():
        return

    paths = resolve_track_wav_paths(row, recorded_dir)
    if not paths:
        row["objective_scores"] = {
            "engine": "nisqa",
            "ok": False,
            "error": "未找到本地 WAV 路径",
        }
        return

    per_file: list[dict[str, Any]] = []
    errors: list[str] = []
    for wav in paths:
        try:
            res = score_wav(wav, log=log)
            per_file.append(res)
        except Exception as exc:
            errors.append(f"{wav.name}: {exc}")
            per_file.append(
                {
                    "engine": "nisqa",
                    "ok": False,
                    "file": wav.name,
                    "path": str(wav),
                    "error": str(exc),
                }
            )

    row["objective_scores"] = {
        "engine": "nisqa",
        "ok": not errors or len(errors) < len(paths),
        "per_file": per_file,
        "errors": errors,
    }
    if on_failure_mode() == "fail" and errors and len(errors) == len(paths):
        raise RuntimeError("; ".join(errors))


def clear_score_cache() -> None:
    _SCORE_CACHE.clear()


def analysis_objective_meta() -> dict[str, Any]:
    """写入 analysis JSON 顶层的客观评测元信息。"""
    return {
        "enabled": True,
        "engine": "nisqa",
        "weights": weights_path().name,
        "available": is_available(),
        "note": "客观 MOS/维度分与 Dify 主观五维分差独立，请勿混用标尺。",
    }


def _nisqa_display_label(row: Mapping[str, Any]) -> str:
    for key in ("stimulus", "音源名称", "节目", "file"):
        val = row.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    return "—"


def _fmt_nisqa_metric(val: Any) -> str:
    if val is None or val == "":
        return "—"
    try:
        return f"{float(val):.2f}"
    except (TypeError, ValueError):
        return str(val)


_NISQA_METRIC_LABELS: tuple[tuple[str, str], ...] = (
    ("mos_pred", "MOS"),
    ("noi_pred", "噪声"),
    ("dis_pred", "失真"),
    ("col_pred", "音色"),
    ("loud_pred", "响度"),
)


def _md_cell(val: Any) -> str:
    return str(val).replace("|", "\\|").replace("\n", " ")


def _metric_float(metrics: Mapping[str, Any], key: str) -> float | None:
    raw = metrics.get(key)
    if raw is None and key == "mos_pred":
        raw = metrics.get("mos")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


_NISQA_DIM_MEANINGS: dict[str, str] = {
    "MOS": "综合平均意见分（MOS），概括整体可听感与可懂度",
    "噪声": "背景噪声、嘶声与干扰的可察觉程度，越高通常越干净",
    "失真": "破音、削波与失真感，越高表示失真控制越好",
    "音色": "音色自然度与色彩/饱满感（coloration），越高越饱满自然",
    "响度": "响度适中与主观响亮感，过高或过低均可能拉低体验",
}

_NISQA_SCORE_THRESHOLDS = (
    (4.0, "优秀"),
    (3.5, "良好"),
    (3.0, "中等"),
    (2.0, "偏弱"),
    (0.0, "较差"),
)

NISQA_SCALE_DISCLAIMER = (
    "NISQA 约 1–5 分，为本地客观估计；与 Dify 主观五维分差 −3～+3 "
    "或报告中的 1–10 映射分不可直接换算。"
)


def nisqa_scale_disclaimer_text() -> str:
    """NISQA 标尺说明（Web / Markdown 公共展示，勿写入每条维度说明）。"""
    return NISQA_SCALE_DISCLAIMER


def _nisqa_score_level(score: float | None) -> str:
    """约 1–5 标尺上的简短等级（用于徽章/表格标签）。"""
    if score is None:
        return "无有效分"
    for threshold, level in _NISQA_SCORE_THRESHOLDS:
        if score >= threshold:
            return level
    return "较差"


def _nisqa_score_note(score: float | None) -> str:
    """兼容旧调用：返回等级简称。"""
    return _nisqa_score_level(score)


def _nisqa_score_explanation(
    score: float | None,
    *,
    metric_label: str,
) -> str:
    """按维度与分数档生成较详细的客观分说明（Web / Markdown 共用）。"""
    level = _nisqa_score_level(score)
    meaning = _NISQA_DIM_MEANINGS.get(metric_label, "该维度 NISQA 客观预测分")
    if score is None:
        return (
            f"【{level}】{meaning}：当前无有效预测值，可能未成功评分或 metrics 缺失。"
            "请确认已启用 NISQA 且录音路径正确。"
        )
    s = float(score)
    band_hint = {
        "优秀": (
            f"预测 {s:.2f} 分，处于优秀档（≥4.0）。{meaning}，"
            "模型判断该维度整体表现突出，可作为对比中的优势项。"
        ),
        "良好": (
            f"预测 {s:.2f} 分，处于良好档（3.5–4.0）。{meaning}，"
            "听感整体可接受，仍有小幅优化空间。"
        ),
        "中等": (
            f"预测 {s:.2f} 分，处于中等档（3.0–3.5）。{meaning}，"
            "表现中规中矩，建议结合逐条录音明细排查波动较大的曲目。"
        ),
        "偏弱": (
            f"预测 {s:.2f} 分，处于偏弱档（2.0–3.0）。{meaning}，"
            "模型认为该维度存在明显短板，宜在调音或采集链路中重点排查。"
        ),
        "较差": (
            f"预测 {s:.2f} 分，处于较差档（<2.0）。{meaning}，"
            "客观指标偏低，建议优先检查录音电平、噪声与失真问题。"
        ),
    }
    return f"【{level}】{band_hint.get(level, meaning)}"


def _infer_nisqa_device_label(filename: str, fallback: str = "全部录音") -> str:
    name = f"_{filename}_".lower()
    markers = (
        ("_a_", "设备 A"),
        ("_b_", "设备 B"),
        ("_d01_", "设备 A"),
        ("_d02_", "设备 B"),
        ("device_a", "设备 A"),
        ("device_b", "设备 B"),
    )
    for marker, label in markers:
        if marker in name:
            return label
    return fallback


def _metric_average(entries: list[dict[str, Any]], key: str) -> float | None:
    vals: list[float] = []
    for entry in entries:
        metrics = entry.get("metrics")
        if not isinstance(metrics, dict):
            continue
        val = _metric_float(metrics, key)
        if val is not None:
            vals.append(val)
    if not vals:
        return None
    return sum(vals) / len(vals)


def _device_diff_note(delta: float | None) -> str:
    """A−B 差值简短结论（用于徽章颜色匹配）。"""
    if delta is None:
        return "无有效对比"
    if delta >= 0.15:
        return "设备 A 更好"
    if delta <= -0.15:
        return "设备 B 更好"
    return "差异很小"


def _device_diff_explanation(
    delta: float | None,
    *,
    metric_label: str,
    avg_a: float | None,
    avg_b: float | None,
) -> str:
    """双设备同维度 A−B 的详细对比说明。"""
    meaning = _NISQA_DIM_MEANINGS.get(metric_label, "该维度")
    if delta is None or avg_a is None or avg_b is None:
        return (
            f"【无有效对比】{meaning}：设备 A/B 至少一侧缺少有效平均分，"
            "无法计算可靠差值。"
        )
    a_s, b_s = float(avg_a), float(avg_b)
    d = float(delta)
    abs_d = abs(d)
    short = _device_diff_note(d)
    if abs_d < 0.15:
        detail = (
            f"A 平均 {a_s:.2f}、B 平均 {b_s:.2f}，差值 {d:+.2f}（|Δ|<0.15）。"
            f"{meaning} 上两机表现接近，客观模型未给出明确优劣，"
            "建议结合主观 Dify 分差与逐曲明细综合判断。"
        )
    elif d >= 0.15:
        detail = (
            f"A 平均 {a_s:.2f}、B 平均 {b_s:.2f}，差值 {d:+.2f}（A 更高且 ≥0.15）。"
            f"在{meaning}维度上设备 A 客观预测更好，差值达到项目内「有义差异」阈值，"
            "可在报告结论中作为 A 的优势参考。"
        )
    else:
        detail = (
            f"A 平均 {a_s:.2f}、B 平均 {b_s:.2f}，差值 {d:+.2f}（B 更高且 |Δ|≥0.15）。"
            f"在{meaning}维度上设备 B 客观预测更好，建议在对比总结中体现 B 的该项优势。"
        )
    return f"【{short}】{detail}"


def collect_nisqa_report_data(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """从 tracks 行解析 NISQA 报告结构化数据（Web UI / Markdown 共用）。"""
    device_entries: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        obj = r.get("objective_scores")
        if not isinstance(obj, dict):
            continue
        stim = _nisqa_display_label(r)
        for item in obj.get("per_file") or []:
            if not isinstance(item, dict):
                continue
            m = item.get("metrics") if isinstance(item.get("metrics"), dict) else item
            if not isinstance(m, dict):
                continue
            fn = str(m.get("file") or item.get("file") or "—")
            label = _infer_nisqa_device_label(fn)
            device_entries.setdefault(label, []).append(
                {
                    "stimulus": stim,
                    "file": fn,
                    "device": label,
                    "metrics": m,
                }
            )
    device_order = sorted(
        device_entries,
        key=lambda x: {"设备 A": 0, "设备 B": 1, "全部录音": 9}.get(x, 5),
    )
    all_entries: list[dict[str, Any]] = []
    for device in device_order:
        all_entries.extend(device_entries[device])
    all_entries.sort(key=lambda e: (str(e.get("stimulus") or ""), str(e.get("file") or "")))

    device_avg_rows: dict[str, list[dict[str, str]]] = {}
    for device in device_order:
        if device == "全部录音" and len(device_order) > 1:
            continue
        entries = device_entries[device]
        device_avg_rows[device] = []
        for key, label in _NISQA_METRIC_LABELS:
            avg = _metric_average(entries, key)
            device_avg_rows[device].append(
                {
                    "维度": label,
                    "平均分": _fmt_nisqa_metric(avg),
                    "等级": _nisqa_score_level(avg),
                    "说明": _nisqa_score_explanation(avg, metric_label=label),
                }
            )

    diff_rows: list[dict[str, str]] = []
    if "设备 A" in device_entries and "设备 B" in device_entries:
        for key, label in _NISQA_METRIC_LABELS:
            avg_a = _metric_average(device_entries["设备 A"], key)
            avg_b = _metric_average(device_entries["设备 B"], key)
            delta = None if avg_a is None or avg_b is None else avg_a - avg_b
            short_verdict = _device_diff_note(delta)
            diff_rows.append(
                {
                    "维度": label,
                    "设备A": _fmt_nisqa_metric(avg_a),
                    "设备B": _fmt_nisqa_metric(avg_b),
                    "A-B": "—" if delta is None else f"{delta:+.2f}",
                    "结论": short_verdict,
                    "结论说明": _device_diff_explanation(
                        delta,
                        metric_label=label,
                        avg_a=avg_a,
                        avg_b=avg_b,
                    ),
                }
            )

    return {
        "device_entries": device_entries,
        "device_order": device_order,
        "all_entries": all_entries,
        "device_avg_rows": device_avg_rows,
        "diff_rows": diff_rows,
        "has_ab_compare": bool(diff_rows),
    }


def render_nisqa_appendix_markdown(rows: list[dict[str, Any]]) -> str:
    """可选 Markdown 附录：逐文件 NISQA 分数表。"""
    data = collect_nisqa_report_data(rows)
    device_entries = data["device_entries"]
    device_order = data["device_order"]
    all_entries = data["all_entries"]
    if not device_entries:
        return ""

    lines = ["### NISQA 客观音质（本地）", ""]

    lines.append("#### 录音明细")
    lines.append("")
    lines.append("| 节目/刺激 | 录音文件 | MOS | 噪声 | 失真 | 音色 | 响度 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for entry in all_entries:
        m = entry["metrics"]
        cells = [
            entry["stimulus"],
            entry["file"],
            _fmt_nisqa_metric(m.get("mos_pred", m.get("mos"))),
            _fmt_nisqa_metric(m.get("noi_pred")),
            _fmt_nisqa_metric(m.get("dis_pred")),
            _fmt_nisqa_metric(m.get("col_pred")),
            _fmt_nisqa_metric(m.get("loud_pred")),
        ]
        lines.append("| " + " | ".join(_md_cell(c) for c in cells) + " |")
    lines.append("")
    lines.append(f"> {NISQA_SCALE_DISCLAIMER}")
    lines.append("")

    for device, avg_rows in data["device_avg_rows"].items():
        lines.append(f"#### {device} 维度平均与分数说明")
        lines.append("")
        lines.append("| 维度 | 全歌曲平均分 | 分数说明 |")
        lines.append("| --- | --- | --- |")
        for row in avg_rows:
            lines.append(
                f"| {_md_cell(row['维度'])} | {_md_cell(row['平均分'])} | {_md_cell(row['说明'])} |"
            )
        lines.append("")

    if data["diff_rows"]:
        lines.append("#### 设备 A vs B 核心差异汇总")
        lines.append("")
        lines.append("| 维度 | 设备 A 平均 | 设备 B 平均 | A-B 差值 | 结论 |")
        lines.append("| --- | --- | --- | --- | --- |")
        for row in data["diff_rows"]:
            verdict_cell = _md_cell(row.get("结论说明") or row.get("结论", ""))
            lines.append(
                "| "
                + " | ".join(
                    _md_cell(row[k])
                    for k in ("维度", "设备A", "设备B", "A-B")
                )
                + f" | {verdict_cell} |"
            )
        lines.append("")

    return "\n".join(lines)


def has_nisqa_report_data(rows: list[dict[str, Any]]) -> bool:
    """是否存在可展示的 NISQA 客观分（供 Web 嵌入报告判断）。"""
    return bool(collect_nisqa_report_data(rows).get("device_entries"))


def strip_nisqa_appendix_from_section_six(section_six_md: str) -> str:
    """
    从第六章 Markdown 中移除 NISQA 附录块。

    Web 端改用卡片/雷达等组件展示时调用，避免与 ``render_nisqa_report_from_rows`` 重复渲染裸表格。
    下载用完整 Markdown 请勿调用本函数。
    """
    marker = "### NISQA 客观音质（本地）"
    if marker not in section_six_md:
        return section_six_md
    return section_six_md.split(marker, 1)[0].rstrip() + "\n"


def discover_audio_files(
    path: str | Path,
    *,
    recursive: bool = True,
) -> list[Path]:
    """扫描目录或单文件，返回支持的音频路径列表（已排序）。"""
    from speaker_eval.settings.paths import AUDIO_EXTENSIONS

    root = Path(path).resolve()
    if root.is_file():
        return [root] if root.suffix.lower() in AUDIO_EXTENSIONS else []
    if not root.is_dir():
        return []
    pattern = "**/*" if recursive else "*"
    found: list[Path] = []
    for p in root.glob(pattern):
        if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS:
            found.append(p)
    return sorted(found, key=lambda x: x.name.lower())


def score_paths(
    paths: Sequence[str | Path],
    *,
    log: Optional[Callable[[str], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> list[dict[str, Any]]:
    """对多条本地音频执行 NISQA，返回与 analysis ``tracks[]`` 兼容的行列表。"""
    ensure_weights(log=log)
    tracks: list[dict[str, Any]] = []
    total = len(paths)
    for i, raw in enumerate(paths, start=1):
        if should_cancel and should_cancel():
            if log:
                log("[NISQA] 用户已请求停止，未评文件将跳过")
            break
        p = Path(raw).resolve()
        if log:
            log(f"[NISQA] ({i}/{total}) {p.name}")
        row: dict[str, Any] = {
            "file": p.name,
            "stimulus": p.name,
            "path": str(p),
        }
        if not p.is_file():
            row["ok"] = False
            row["error"] = f"文件不存在: {p}"
            row["objective_scores"] = {
                "engine": "nisqa",
                "ok": False,
                "per_file": [],
                "errors": [row["error"]],
            }
            tracks.append(row)
            continue
        try:
            res = score_wav(p, log=log)
            row["ok"] = True
            row["objective_scores"] = {
                "engine": "nisqa",
                "ok": True,
                "per_file": [res],
                "errors": [],
            }
        except Exception as exc:
            row["ok"] = False
            row["error"] = str(exc)
            row["objective_scores"] = {
                "engine": "nisqa",
                "ok": False,
                "per_file": [
                    {
                        "engine": "nisqa",
                        "ok": False,
                        "file": p.name,
                        "path": str(p),
                        "error": str(exc),
                    }
                ],
                "errors": [str(exc)],
            }
        tracks.append(row)
    return tracks


def build_nisqa_only_payload(
    tracks: list[dict[str, Any]],
    *,
    source: str = "",
) -> dict[str, Any]:
    """独立 NISQA 批次结果（不写 Dify 主观分）。"""
    os.environ.setdefault("SPEAKER_NISQA_ENABLED", "1")
    try:
        meta = analysis_objective_meta()
    except Exception:
        meta = {"enabled": True, "engine": "nisqa"}
    ok_n = sum(1 for t in tracks if t.get("ok"))
    return {
        "mode": "nisqa_only",
        "scoring_mode": "nisqa_only",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": source,
        "engine": "nisqa",
        "tracks": tracks,
        "objective_scoring": meta,
        "summary": {
            "total": len(tracks),
            "ok": ok_n,
            "failed": len(tracks) - ok_n,
        },
    }


def tracks_to_csv_rows(tracks: list[dict[str, Any]]) -> list[dict[str, str]]:
    """将 tracks 展平为 CSV 行。"""
    rows: list[dict[str, str]] = []
    for t in tracks:
        obj = t.get("objective_scores")
        if not isinstance(obj, dict):
            continue
        stim = _nisqa_display_label(t)
        for item in obj.get("per_file") or []:
            if not isinstance(item, dict):
                continue
            m = item.get("metrics") if isinstance(item.get("metrics"), dict) else item
            if not isinstance(m, dict):
                m = {}
            rows.append(
                {
                    "stimulus": stim,
                    "file": str(m.get("file") or item.get("file") or t.get("file") or ""),
                    "path": str(m.get("path") or item.get("path") or t.get("path") or ""),
                    "ok": "1" if item.get("ok", t.get("ok")) else "0",
                    "mos_pred": _fmt_nisqa_metric(m.get("mos_pred", m.get("mos"))),
                    "noi_pred": _fmt_nisqa_metric(m.get("noi_pred")),
                    "dis_pred": _fmt_nisqa_metric(m.get("dis_pred")),
                    "col_pred": _fmt_nisqa_metric(m.get("col_pred")),
                    "loud_pred": _fmt_nisqa_metric(m.get("loud_pred")),
                    "error": str(item.get("error") or ""),
                }
            )
    return rows


def write_nisqa_results_json(path: str | Path, payload: Mapping[str, Any]) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def write_nisqa_results_csv(path: str | Path, tracks: list[dict[str, Any]]) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "stimulus",
        "file",
        "path",
        "ok",
        "mos_pred",
        "noi_pred",
        "dis_pred",
        "col_pred",
        "loud_pred",
        "error",
    ]
    flat = tracks_to_csv_rows(tracks)
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(flat)
    return out


def run_nisqa_batch(
    input_path: str | Path,
    *,
    recursive: bool = True,
    output_json: str | Path | None = None,
    output_csv: str | Path | None = None,
    log: Optional[Callable[[str], None]] = None,
) -> dict[str, Any]:
    """
    独立批量 NISQA：扫描目录或单文件，可选写出 JSON / CSV。

    默认输出目录：``output/nisqa/nisqa_only_<时间戳>.json``。
    """
    from speaker_eval.settings.paths import OUTPUT_DIR

    root = Path(input_path).resolve()
    paths = discover_audio_files(root, recursive=recursive)
    if not paths:
        raise FileNotFoundError(f"未找到可评测音频: {root}")

    if log:
        log(f"[NISQA] 共 {len(paths)} 个文件，开始评测…")
    tracks = score_paths(paths, log=log, should_cancel=should_cancel)
    payload = build_nisqa_only_payload(tracks, source=str(root))

    if output_json is None and output_csv is None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = OUTPUT_DIR / "nisqa"
        output_json = out_dir / f"nisqa_only_{stamp}.json"
        output_csv = out_dir / f"nisqa_only_{stamp}.csv"

    if output_json is not None:
        jp = write_nisqa_results_json(output_json, payload)
        payload["output_json"] = str(jp)
        if log:
            log(f"[NISQA] JSON: {jp}")
    if output_csv is not None:
        cp = write_nisqa_results_csv(output_csv, tracks)
        payload["output_csv"] = str(cp)
        if log:
            log(f"[NISQA] CSV: {cp}")

    return payload


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="本地 NISQA 客观音质评测（单文件或批量，无需 Dify）"
    )
    parser.add_argument("wav", nargs="?", help="单个音频文件路径")
    parser.add_argument(
        "--dir",
        "-d",
        dest="audio_dir",
        help="批量评测：目录路径（扫描 wav/mp3/flac 等）",
    )
    parser.add_argument(
        "--no-recursive",
        action="store_true",
        help="批量时仅扫描目录顶层，不递归子目录",
    )
    parser.add_argument(
        "-o",
        "--output-json",
        dest="output_json",
        help="写出 JSON 结果路径（批量默认 output/nisqa/nisqa_only_<时间>.json）",
    )
    parser.add_argument(
        "--csv",
        dest="output_csv",
        help="写出 CSV 结果路径（批量默认 output/nisqa/nisqa_only_<时间>.csv）",
    )
    parser.add_argument("--status", action="store_true", help="仅检查安装与权重")
    args = parser.parse_args(argv)

    if args.status:
        os.environ.setdefault("SPEAKER_NISQA_ENABLED", "1")
        print(availability_message())
        print(f"权重路径: {weights_path()}")
        return 0 if is_available() else 1

    os.environ.setdefault("SPEAKER_NISQA_ENABLED", "1")

    if args.audio_dir:
        try:
            payload = run_nisqa_batch(
                args.audio_dir,
                recursive=not args.no_recursive,
                output_json=args.output_json,
                output_csv=args.output_csv,
            )
        except Exception as exc:
            print(f"错误: {exc}", file=sys.stderr)
            return 1
        print(
            json.dumps(
                {
                    "summary": payload.get("summary"),
                    "output_json": payload.get("output_json"),
                    "output_csv": payload.get("output_csv"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if payload.get("summary", {}).get("failed", 0) == 0 else 2

    if not args.wav:
        parser.error("请提供音频文件路径、--dir 目录，或使用 --status")
    res = score_wav(args.wav)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
