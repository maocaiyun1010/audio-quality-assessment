# -*- coding: utf-8 -*-
"""
一键离线评测 — 根入口（转发至 ``speaker_eval.cli.main``）。

详细用法见 ``speaker_eval.cli.main`` 模块文档字符串。

``main_run_eval`` 供 Streamlit ``web_ui.py`` 以编程方式调用双机全流程。
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from live_eval_log import append_live_step
from speaker_eval.cli.main import main


def _session_safe_tag(session_tag: str) -> str:
    return "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in session_tag)[:64]


class WebUiEvalPipelineError(RuntimeError):
    """Web UI 子进程流水线失败时携带会话 safe_tag，便于父进程在录音已落盘时做多模型补评。"""

    def __init__(
        self,
        message: str,
        *,
        session_safe: str,
        pipeline_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.session_safe = (session_safe or "").strip()
        self.pipeline_code = pipeline_code


def _patch_recording_prefs(duration_sec: int, gain_db: float) -> None:
    """在已导入模块上同步时长/增益（Web UI 单进程内调用，无需重启）。"""
    import speaker_eval.adapters.audio.omnimic_portaudio as omp
    import speaker_eval.settings.recording as rec
    import sync_capture as sc

    v_dur = float(duration_sec)
    v_gain = float(gain_db)
    rec.PER_TRACK_PLAY_SECONDS = v_dur
    rec.OMNIMIC_GAIN_DB = v_gain
    sc.PER_TRACK_PLAY_SECONDS = v_dur
    omp.OMNIMIC_GAIN_DB = v_gain
    os.environ["SPEAKER_PER_TRACK_SEC"] = str(duration_sec)
    os.environ["SPEAKER_OMNIMIC_GAIN_DB"] = str(gain_db)


def _write_web_ui_score_json(analysis_json: Path, dest: Path) -> None:
    from markdown_report import DIMENSION_KEYS
    from scoring import eval_model_tags_for_track_row

    raw = json.loads(analysis_json.read_text(encoding="utf-8"))
    tracks = raw.get("tracks") or []
    comparison = bool(raw.get("comparison_mode"))
    rows = [t for t in tracks if t.get("ok") and t.get("parsed")]

    ref_base = 7.0
    dut_scores: dict[str, float] = {}
    ref_scores: dict[str, float] = {}

    if comparison:
        sums: dict[str, list[float]] = {k: [] for k in DIMENSION_KEYS}
        for t in rows:
            p = t.get("parsed") or {}
            for k in DIMENSION_KEYS:
                if k in p:
                    try:
                        sums[k].append(float(p[k]))
                    except (TypeError, ValueError):
                        pass
        for k in DIMENSION_KEYS:
            d = sum(sums[k]) / len(sums[k]) if sums[k] else 0.0
            dut_scores[k] = max(1.0, min(10.0, ref_base + d))
            ref_scores[k] = ref_base
    else:
        vals: dict[str, list[float]] = {k: [] for k in DIMENSION_KEYS}
        for t in rows:
            p = t.get("parsed") or {}
            for k in DIMENSION_KEYS:
                if k in p:
                    try:
                        vals[k].append(float(p[k]))
                    except (TypeError, ValueError):
                        pass
        for k in DIMENSION_KEYS:
            if vals[k]:
                dut_scores[k] = max(1.0, min(10.0, sum(vals[k]) / len(vals[k])))
            else:
                dut_scores[k] = ref_base
            ref_scores[k] = ref_base

    out_payload: dict = {
        "dut_scores": dut_scores,
        "ref_scores": ref_scores,
        "analysis_json": str(analysis_json.resolve()),
    }
    quality = raw.get("scoring_quality")
    if isinstance(quality, dict):
        out_payload["scoring_quality"] = quality
    else:
        total_tracks = len([t for t in tracks if isinstance(t, dict)])
        ok_tracks = len(rows)
        out_payload["scoring_quality"] = {
            "total_tracks": total_tracks,
            "ok_tracks": ok_tracks,
            "failed_tracks": max(0, total_tracks - ok_tracks),
            "all_scoring_failed": total_tracks > 0 and ok_tracks == 0,
            "partial_scoring_failed": total_tracks > 0 and 0 < ok_tracks < total_tracks,
        }
    tags = eval_model_tags_for_track_row()
    if tags.get("eval_model"):
        out_payload["web_ui_eval_model"] = tags["eval_model"]
        out_payload["eval_model"] = tags["eval_model"]
    if tags.get("dify_selected_model"):
        out_payload["dify_selected_model"] = tags["dify_selected_model"]

    per_track: list[dict] = []
    for t in tracks:
        if not isinstance(t, dict):
            continue
        per_track.append(
            {
                "track_index": t.get("track_index"),
                "stimulus": str(t.get("stimulus") or t.get("file") or "")[:260],
                "scoring_mode": t.get("scoring_mode"),
                "ok": bool(t.get("ok")),
                "eval_model": str(t.get("eval_model") or tags.get("eval_model") or "").strip() or None,
                "dify_selected_model": (
                    str(t.get("dify_selected_model") or tags.get("dify_selected_model") or "").strip()
                    or None
                ),
            }
        )
    # 去掉全 None 的冗余键，保留列表结构便于对账
    per_track = [
        {k: v for k, v in pt.items() if v is not None and v != ""}
        for pt in per_track
    ]
    if per_track:
        out_payload["per_track_eval_models"] = per_track

    if comparison:
        out_payload["comparison_mode"] = True
        out_payload["stimulus_pairwise"] = True
        out_payload["avg_delta_per_dim"] = {
            k: round(float(dut_scores[k]) - ref_base, 4) for k in DIMENSION_KEYS
        }

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out_payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main_run_eval(
    *,
    dut_serial: str,
    ref_serial: str,
    gain_db: float,
    duration: int,
) -> tuple[str, str]:
    """
    双机全流程：采集 → Dify → 报告。

    Returns:
        (Markdown 报告路径, Web 图表用分数字典 JSON 路径)
    """
    dut = (dut_serial or "").strip()
    ref = (ref_serial or "").strip()
    if not dut or not ref:
        raise ValueError("请填写被测设备与对比设备的序列号（ADB serial）")

    _patch_recording_prefs(int(duration), float(gain_db))

    append_live_step(
        "invoke",
        "开始调用",
        "初始化采集与 Dify 评测（界面所选 Gemini 等仅为展示名，实际以 Dify 应用配置为准）。",
    )

    from device_roles import labels_for_slot_count
    from speaker_eval.logging_config import setup_app_logging
    from speaker_eval.pipelines.evaluation import run_evaluation_pipeline
    from speaker_eval.settings import LOG_DIR

    from config import ANALYSIS_DIR, REPORT_DIR

    serials = [dut, ref]
    role_labels = labels_for_slot_count(len(serials))
    session_tag = datetime.now().strftime("manual_%Y%m%d_%H%M%S")
    safe = _session_safe_tag(session_tag)
    test_device_name = "多设备对比"
    dev_summary = f"{dut} / {ref}"

    logger = setup_app_logging(LOG_DIR, name="speaker_eval", file_prefix="web_ui")

    def user_print(m: str) -> None:
        print(m, flush=True)

    os.environ.setdefault("SPEAKER_RECORD_TOOL", "sounddevice")

    code, err_detail = run_evaluation_pipeline(
        serials,
        session_tag,
        test_device_name=test_device_name,
        dev_summary=dev_summary,
        record_tool="sounddevice",
        role_labels=role_labels,
        user_print=user_print,
        logger=logger,
    )

    if code != 0:
        title = {
            1: "评测流水线异常",
            2: "采集阶段无有效录音（退出码 2）",
            3: "评分阶段失败（退出码 3）",
        }.get(code, f"评测流水线失败（退出码 {code}）")
        hint = (
            "\n常见排查：① adb devices 中两台均为 device 且与界面序列号一致；② USB/无线调试稳定并已授权；"
            "③ assets/test_audio 下存在音源；④ 本机麦克风可录音且所选设备索引正确；⑤ 手机端外放音量足够。"
        )
        if code == 2:
            msg = f"{title}\n{err_detail}{hint}"
        else:
            msg = f"{title}\n{err_detail}"
        raise WebUiEvalPipelineError(msg, session_safe=safe, pipeline_code=code)

    candidates = sorted(
        ANALYSIS_DIR.glob(f"analysis_{safe}_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise WebUiEvalPipelineError(
            f"未在 {ANALYSIS_DIR} 找到本次会话 analysis 文件（前缀 analysis_{safe}_）",
            session_safe=safe,
            pipeline_code=None,
        )

    analysis_path = candidates[0]
    stem = analysis_path.stem
    md_path = REPORT_DIR / f"声学评测报告_{stem}.md"
    web_json = ANALYSIS_DIR / f"web_ui_scores_{safe}.json"
    _write_web_ui_score_json(analysis_path, web_json)

    if not md_path.is_file():
        md_path = analysis_path

    return (str(md_path), str(web_json))


if __name__ == "__main__":
    # 作为脚本直接运行时进入 CLI（解析参数、采集、评分、出报告）
    raise SystemExit(main())
