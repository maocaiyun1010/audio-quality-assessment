# -*- coding: utf-8 -*-
"""Streamlit Web UI：双机全流程评测、五维汇总、报告导出、提示词查阅、设备与麦克风选择。"""
from __future__ import annotations

import html
import inspect
import json
import os
import platform
import subprocess
import sys
import tempfile
from collections import Counter
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import streamlit as st

import config
from config import EVAL_METRICS
from eval_source_summary import (
    build_per_track_rows,
    load_analysis_from_score_json_path,
    rows_to_dataframe,
)
from web_ui_report_html import html_dim_scores_highlight
from gen_report import generate_report
from markdown_report import (
    _core_conclusion_highlight_html,
    build_section_six_markdown,
    compute_dimension_statistics,
    one_line_summary_comparison,
    one_line_summary_single,
)
from speaker_eval.settings import SAMPLE_RATE
from difyclient import DifyClient, _build_stimulus_compare_query
from scoring import (
    DEFAULT_SCORING_QUERY,
    get_audio_eval_prompt_override,
    get_effective_scoring_query,
    get_stimulus_compare_extras,
    stimulus_compare_prompt_mode,
    save_prompt_overrides,
)
from speaker_eval.adapters.adb import list_connected_adb_devices
from web_ui_model_list_config import effective_model_choices


def _all_eval_models_for_payload() -> list[str]:
    """以 ``selected_llm_models`` 为准；为空时退回 ``selected_llm_model``（与多选首项保持同步）。"""
    ml = [str(x).strip() for x in (st.session_state.get("selected_llm_models") or []) if str(x).strip()]
    if ml:
        return ml
    sm = str(st.session_state.get("selected_llm_model", "")).strip()
    return [sm] if sm else []


def _primary_eval_model_name() -> str:
    xs = _all_eval_models_for_payload()
    return xs[0] if xs else ""


def _dedupe_models_preserve_order(names: list[str]) -> list[str]:
    """侧栏多选可能误选重复项；评测顺序保留首次出现。"""
    seen: set[str] = set()
    out: list[str] = []
    for x in names:
        t = str(x).strip()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _recorded_session_ready(session_safe: str) -> bool:
    """常规模式：子进程已写入 playlist 且至少一条 WAV 时，可在父进程对多模型补评。"""
    from config import RECORDED_DIR

    s = (session_safe or "").strip()
    if not s:
        return False
    if not (RECORDED_DIR / f"{s}_playlist.json").is_file():
        return False
    return any(RECORDED_DIR.glob(f"{s}_*.wav"))


def _resolve_pipeline_report_paths(report_path: str) -> tuple[Path, Path]:
    """
    返回 (流水线 Markdown 路径, Word .docx 路径)。

    报告文件名为 ``声学评测报告_{analysis_stem}.md`` / ``.docx``。
    当 ``main_run_eval`` 因缺省退回 ``analysis_*.json`` 时，不能直接对路径 ``.with_suffix('.docx')``，
    否则会错指向 ``analysis_*.docx`` 而非真正的 Word 产物。
    """
    from config import REPORT_DIR

    p = Path(report_path)
    if p.name.startswith("声学评测报告_") and p.suffix.lower() == ".md":
        return p, p.with_suffix(".docx")
    if p.suffix.lower() == ".json" and p.name.startswith("analysis_"):
        base = REPORT_DIR / f"声学评测报告_{p.stem}"
        return base.with_suffix(".md"), base.with_suffix(".docx")
    if p.suffix.lower() == ".md":
        return p, p.with_suffix(".docx")
    base = REPORT_DIR / f"声学评测报告_{p.stem}"
    return base.with_suffix(".md"), base.with_suffix(".docx")


def _build_demo_eval_payload(score_json_path: Path, export_format: str) -> dict | None:
    """
    由历史 ``web_ui_scores_*.json`` 构造与真实评测成功时等价的 session payload，
    用于在不跑采集的情况下复现「评测结果」一体化区域的图表与第六章等展示。
    """
    try:
        score_json_path = score_json_path.resolve()
    except Exception:
        pass
    if not score_json_path.is_file():
        return None
    from config import REPORT_DIR
    from eval_source_summary import analysis_json_path_for_web_scores

    analysis_p = analysis_json_path_for_web_scores(score_json_path)
    if analysis_p is not None and analysis_p.is_file():
        md_candidate = REPORT_DIR / f"声学评测报告_{analysis_p.stem}.md"
        report_path = str(md_candidate if md_candidate.is_file() else analysis_p)
    else:
        report_path = str(score_json_path)

    dut_s = "被测机（历史预览）"
    ref_s = "对比机（历史预览）"
    if analysis_p is not None and analysis_p.is_file():
        try:
            data = json.loads(analysis_p.read_text(encoding="utf-8"))
            devs = data.get("devices") or []
            if len(devs) >= 1:
                d0 = devs[0]
                lab0 = str(d0.get("label") or "").strip() or "被测"
                ser0 = str(d0.get("serial") or "").strip()
                dut_s = f"{lab0}（{ser0}）" if ser0 else lab0
            if len(devs) >= 2:
                d1 = devs[1]
                lab1 = str(d1.get("label") or "").strip() or "对比"
                ser1 = str(d1.get("serial") or "").strip()
                ref_s = f"{lab1}（{ser1}）" if ser1 else lab1
        except Exception:
            pass

    payload: dict = {
        "dut_s": dut_s,
        "ref_s": ref_s,
        "mic_pick": "（历史数据预览·未重新采集）",
        "export_format": export_format,
        "report_path": report_path,
        "score_json": str(score_json_path),
    }
    try:
        score_blob = json.loads(score_json_path.read_text(encoding="utf-8"))
        if isinstance(score_blob, dict):
            wm = str(score_blob.get("web_ui_eval_model") or "").strip()
            if wm:
                payload["eval_models"] = [wm]
    except Exception:
        pass
    return payload


def _hist_web_scores_bucket(path: Path) -> str:
    """
    历史 ``web_ui_scores_*.json`` 文件名分桶，供侧栏筛选。

    - ``dual_webui``：Web 双设备单麦模式写入，stem 形如 ``web_ui_scores_dual_webui_%Y%m%d_%H%M%S``
    - ``dual_device_legacy``：旧版 ``web_ui_scores_dual_device_*``（见 ``analysis_json_path_for_web_scores`` 兼容分支）
    - ``multi_model_extra``：多模型追加，stem 中在会话段后出现 ``__``（如 ``...__Gemini``）
    - ``regular``：其余主会话（常见 ``manual_*`` 等）
    """
    stem = path.stem
    if not stem.startswith("web_ui_scores_"):
        return "other"
    rest = stem[len("web_ui_scores_") :]
    if rest.startswith("dual_webui_"):
        return "dual_webui"
    if rest.startswith("dual_device_"):
        return "dual_device_legacy"
    if "__" in rest:
        return "multi_model_extra"
    return "regular"


def _list_hist_web_scores_for_ui(analysis_dir: Path, *, bucket: str, cap: int) -> list[Path]:
    raw = sorted(
        analysis_dir.glob("web_ui_scores_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[: max(cap * 5, 80)]
    if bucket == "all":
        out = raw
    else:
        out = [p for p in raw if _hist_web_scores_bucket(p) == bucket]
    return out[:cap]


def _model_label_from_web_scores_path(path: Path) -> str:
    """从 web_ui_scores JSON 或文件名 ``__模型标签`` 后缀解析评测模型展示名。"""
    cache: dict = st.session_state.setdefault("_hist_scores_model_cache", {})
    key = str(path.resolve())
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    cached = cache.get(key)
    if cached and cached[0] == mtime:
        return str(cached[1] or "")

    label = ""
    stem = path.stem
    if stem.startswith("web_ui_scores_"):
        rest = stem[len("web_ui_scores_") :]
        if "__" in rest:
            _tag = rest.rsplit("__", 1)[-1].strip()
            if _tag:
                label = _tag.replace("_", " ")
    if not label and path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for _k in ("web_ui_eval_model", "eval_model", "dify_selected_model"):
                    _m = str(data.get(_k) or "").strip()
                    if _m:
                        label = _m
                        break
        except Exception:
            pass
    cache[key] = (mtime, label)
    return label


def _hist_web_scores_select_label(path: Path) -> str:
    """历史分数下拉项：文件名 + 评测模型（若有）。"""
    model = _model_label_from_web_scores_path(path)
    if model:
        return f"{path.name}  ·  模型：{model}"
    return f"{path.name}  ·  （未记录模型名）"


def _discover_all_tracks_for_ui() -> list[tuple[str, str]]:
    """用于 UI 展示候选音源，不受环境变量筛选影响。"""
    try:
        return config.discover_standard_tracks(apply_env_filter=False)
    except TypeError:
        return config.discover_standard_tracks()


def _apply_selected_tracks_env(selected_rel_paths: list[str], play_full_track: bool) -> None:
    """将 Web UI 勾选结果与播放模式写入环境变量，供采集/评分链路统一读取。"""
    os.environ["SPEAKER_SELECTED_TRACKS_JSON"] = json.dumps(
        list(selected_rel_paths or []), ensure_ascii=False
    )
    os.environ["SPEAKER_PLAY_FULL_TRACK"] = "1" if play_full_track else "0"


def _nisqa_enabled_from_env() -> bool:
    return os.environ.get("SPEAKER_NISQA_ENABLED", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _sync_nisqa_env(enabled: bool) -> None:
    """侧栏开关 → 当前进程与子进程 ``env`` 使用的环境变量。"""
    if enabled:
        os.environ["SPEAKER_NISQA_ENABLED"] = "1"
    else:
        os.environ.pop("SPEAKER_NISQA_ENABLED", None)


def _apply_nisqa_to_track_row(row: dict) -> None:
    """双设备等未走 ``scoring._append_scoring_row`` 时，为单条 track 附加 NISQA。"""
    try:
        from nisqa_local import enrich_track_row_with_nisqa, is_enabled

        if not is_enabled():
            return
        from config import RECORDED_DIR

        enrich_track_row_with_nisqa(row, recorded_dir=RECORDED_DIR)
    except Exception:
        pass


# 图表中文标签（Windows 常见黑体/雅黑）
if platform.system() == "Windows":
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

# ====================== 页面设置 ======================
st.set_page_config(
    page_title="AI学习机智能音效评测",
    page_icon="🎧",
    layout="wide",
    initial_sidebar_state="expanded",
)
# ====================== 样式 ======================
st.markdown(
    """
<style>
.main-title { font-size: 32px; color: #2563eb; font-weight: 700; margin-bottom: 12px; }
.sub-muted { color: #64748b; font-size: 14px; }
div[data-testid="stSidebarNav"] { font-weight: 600; }
.eval-overview { margin-bottom: 1.25rem; }
.eval-overview h4 { margin-bottom: 0.35rem; }
.dim-score-hero-grid {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 12px;
  margin: 6px 0 20px 0;
}
@media (max-width: 1200px) {
  .dim-score-hero-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
}
@media (max-width: 700px) {
  .dim-score-hero-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
.live-eval-wrap {
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  padding: 10px 12px;
  margin-bottom: 10px;
  background: #f8fafc;
  font-size: 0.92rem;
}
.live-eval-row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 6px 0;
  border-bottom: 1px solid #e2e8f0;
}
.live-eval-row:last-child { border-bottom: none; }
.live-eval-ico { flex: 0 0 1.5rem; font-size: 1rem; }
.live-eval-title { flex: 0 0 7.5rem; color: #0f172a; }
.live-eval-title small.live-eval-ts {
  display: block;
  color: #94a3b8;
  font-weight: 400;
  font-size: 0.72rem;
}
.live-eval-done .live-eval-title { color: #0f766e; }
.live-eval-active .live-eval-title { color: #1d4ed8; font-weight: 600; }
.live-eval-detail { color: #475569; flex: 1; min-width: 0; word-break: break-word; }
.live-eval-hint { color: #94a3b8; font-size: 0.82rem; flex: 1; }
.nisqa-panel {
  border: 1px solid #e2e8f0;
  border-radius: 14px;
  padding: 16px 18px;
  margin-bottom: 14px;
  background: linear-gradient(165deg, #f8fafc 0%, #ffffff 88%);
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.06);
}
.nisqa-panel-title {
  font-size: 1.02rem;
  font-weight: 700;
  color: #1e3a8a;
  margin: 0 0 12px 0;
}
.nisqa-metric-grid {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 10px;
}
@media (max-width: 1100px) {
  .nisqa-metric-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
}
.nisqa-metric-card {
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  padding: 10px 12px;
  background: #fff;
  text-align: center;
  vertical-align: top;
}
.nisqa-metric-label { font-size: 0.78rem; color: #64748b; font-weight: 600; }
.nisqa-metric-value { font-size: 1.45rem; font-weight: 800; color: #0f172a; line-height: 1.2; margin: 4px 0; }
.nisqa-metric-hint {
  font-size: 0.72rem;
  color: #64748b;
  line-height: 1.45;
  margin-top: 8px;
  text-align: left;
  font-weight: 400;
}
.nisqa-verdict-detail {
  display: block;
  font-size: 0.82rem;
  color: #475569;
  line-height: 1.5;
  margin-top: 6px;
  text-align: left;
  font-weight: 400;
}
.nisqa-badge {
  display: inline-block;
  padding: 2px 10px;
  border-radius: 999px;
  font-size: 0.75rem;
  font-weight: 700;
}
.nisqa-diff-table { width: 100%; border-collapse: collapse; font-size: 0.92rem; }
.nisqa-diff-table th {
  background: #eff6ff;
  color: #1e3a8a;
  font-weight: 700;
  padding: 10px 12px;
  border-bottom: 2px solid #bfdbfe;
  text-align: center;
}
.nisqa-diff-table td {
  padding: 9px 12px;
  border-bottom: 1px solid #e2e8f0;
  text-align: center;
  color: #334155;
}
.nisqa-diff-table tr:last-child td { border-bottom: none; }
.nisqa-radar-report-wrap {
  max-width: 520px;
  margin: 0.25rem auto 0.5rem auto;
}
</style>
""",
    unsafe_allow_html=True,
)


def _patch_input_device(spec: str) -> None:
    """同步 SPEAKER_INPUT_DEVICE 到已加载的 recording / device_query 模块（与 CLI 环境变量行为一致）。"""
    spec = (spec or "").strip()
    if spec:
        os.environ["SPEAKER_INPUT_DEVICE"] = spec
    else:
        os.environ.pop("SPEAKER_INPUT_DEVICE", None)

    import speaker_eval.adapters.audio.device_query as dq
    import speaker_eval.settings.recording as rec

    s = os.environ.get("SPEAKER_INPUT_DEVICE", "").strip()
    if s.isdigit():
        rec.INPUT_DEVICE_ID = int(s)
        rec.INPUT_DEVICE_NAME_SUBSTR = ""
    else:
        rec.INPUT_DEVICE_ID = None
        rec.INPUT_DEVICE_NAME_SUBSTR = s
    dq.INPUT_DEVICE_ID = rec.INPUT_DEVICE_ID
    dq.INPUT_DEVICE_NAME_SUBSTR = rec.INPUT_DEVICE_NAME_SUBSTR


def _patch_omnimic_gain(gain_db: float) -> None:
    """同步 OmniMic 增益到环境变量与已加载模块，避免沿用旧默认值。"""
    g = float(gain_db)
    os.environ["SPEAKER_OMNIMIC_GAIN_DB"] = str(g)
    try:
        import speaker_eval.settings.recording as rec

        rec.OMNIMIC_GAIN_DB = g
    except Exception:
        pass
    try:
        import speaker_eval.adapters.audio.omnimic_portaudio as omp

        omp.OMNIMIC_GAIN_DB = g
    except Exception:
        pass


def _apply_dify_env(api_key: str, user: str) -> None:
    """同步 Dify 密钥与用户到环境变量与 config（须在页面任意「开始评测」逻辑之前定义，供同一次 run 内调用）。"""
    key = (api_key or "").strip()
    usr = (user or "").strip()
    if key:
        os.environ["DIFY_API_KEY"] = key
    if usr:
        os.environ["DIFY_USER"] = usr
    config.DIFY_API_KEY = key or config.DIFY_API_KEY
    config.DIFY_USER = usr or config.DIFY_USER
    try:
        import speaker_eval.settings.dify as _dify

        if key:
            _dify.DIFY_API_KEY = key
        if usr:
            _dify.DIFY_USER = usr
    except Exception as exc:
        print(f"[web_ui] 同步 Dify 配置到 settings 模块失败：{exc}", file=sys.stderr)


def _try_prepare_dual_device_eval_session(
    *,
    mic_spec: str,
    dify_api: str,
    dify_user: str,
    provider_id: str = "dify",
    seedpace_api: str = "",
    seedpace_api_url: str = "",
) -> tuple[bool, str]:
    """
    点击「手动开始测评」后立即准备会话状态（不依赖脚本执行到页面底部）。
    返回 (True, "") 或 (False, 错误说明)。

    若用户在界面中已通过「从已有清单导入」写入 ``_dual_eval_import_paired``，
    则直接使用磁盘上的配对 WAV，不要求本会话内已完成两段录制。
    """
    recorder = st.session_state.get("_dual_device_full_recorder") or st.session_state.get("_dual_device_recorder")
    if recorder is None:
        return False, "未找到双设备录制器，请刷新页面后重试。"
    imported = st.session_state.get("_dual_eval_import_paired")
    if imported:
        paired_audios = list(imported)
    else:
        if not getattr(recorder, "is_complete", False):
            return False, "两段设备未全部录制完成，无法开始测评（或请先导入 *_dual_playlist.json）"
        try:
            paired_audios = recorder.get_paired_audio_paths()
        except Exception as exc:
            return False, str(exc)
    if not paired_audios:
        return False, "未找到配对的音频文件（请确认 A/B 每轨均录制成功）"
    if provider_id == "dify" and not (dify_user or "").strip():
        return (
            False,
            "请在侧栏填写「DIFY_USER」：须为贵司 Dify 控制台认可的终端用户标识（常见为**企业邮箱**或 SSO 账号）。"
            "留空或随意字符串会导致上传/对话返回 **User Not Exists**（HTTP 400），多模型评测也无法继续。",
        )
    if provider_id == "seedpace" and not (seedpace_api or "").strip():
        return False, "请选择 Seedpace Gateway 时填写 SEEDPACE_API_KEY。"
    try:
        _patch_input_device(mic_spec)
        _apply_dify_env(dify_api, dify_user)
        from web_ui_dify_model_keys import (
            configure_api_key_for_model,
            describe_current_key_for_model,
            set_dify_api_key_baseline,
        )

        set_dify_api_key_baseline((dify_api or "").strip())
        _sel = _primary_eval_model_name()
        os.environ["SPEAKER_LLM_PROVIDER"] = provider_id
        if provider_id == "dify":
            configure_api_key_for_model(_sel)
        else:
            from seedpace_audio_client import (
                normalize_seedpace_api_key,
                normalize_seedpace_api_url,
                seedpace_model_name,
            )

            os.environ["SEEDPACE_MODEL"] = seedpace_model_name(_sel)
            os.environ["SEEDPACE_API_KEY"] = normalize_seedpace_api_key(seedpace_api or "")
            os.environ["SEEDPACE_API_URL"] = normalize_seedpace_api_url(seedpace_api_url or "")
        os.environ["SPEAKER_EVAL_MODEL_NAME"] = _sel
        if provider_id == "dify":
            _append_run_log("info", "Dify Key 与模型对齐", describe_current_key_for_model(_sel))
        else:
            _append_run_log(
                "info",
                "Seedpace 模型与 Key",
                f"model={os.environ.get('SEEDPACE_MODEL', '')!r}；key 长度={len(os.environ.get('SEEDPACE_API_KEY', ''))}",
            )
        st.session_state["_dify_api_baseline_for_eval"] = (dify_api or "").strip()
        st.session_state["_dual_eval_models"] = list(_all_eval_models_for_payload())
        st.session_state["_dual_eval_state"] = {
            "paired_audios": paired_audios,
            "cursor": 0,
            "merged_tracks": [],
            "parsed_list": [],
        }
        # 与嵌套 dict 解耦，避免部分环境下嵌套 cursor 未持久化导致反复评同一轨
        st.session_state["_dual_eval_cursor"] = 0
        st.session_state["_dual_eval_scorer"] = None
        st.session_state["_dual_eval_running"] = True
        st.session_state["_dual_eval_stop_requested"] = False
        st.session_state.pop("_eval_success_payload", None)
        st.session_state.pop("_eval_error_msg", None)
        st.session_state.pop("_dual_start_eval_clicked", None)
    except Exception as exc:
        return False, str(exc)
    return True, ""


def _list_input_devices(*, show_all_hostapis: bool = False) -> list[tuple[int, str]]:
    """返回 (device_index, label) 列表，仅含输入通道>0 的设备。

    Windows 下 PortAudio 会为 MME / DirectSound / WASAPI 各列一套索引，同一支麦克风常出现
    多条。默认（show_all_hostapis=False）**仅保留 WASAPI**；若过滤后为空则回退为全量。
    勾选「全部驱动」时行为与旧版一致，列出所有 Host API（项会很多）。
    """
    try:
        import sounddevice as sd

        raw: list[tuple[int, dict]] = []
        for i, d in enumerate(sd.query_devices()):
            if int(d.get("max_input_channels", 0) or 0) > 0:
                raw.append((i, d))

        use_rows = raw
        only_one_host = False
        if platform.system() == "Windows" and not show_all_hostapis:
            wasapi_hi: int | None = None
            for hi, api in enumerate(sd.query_hostapis()):
                if "WASAPI" in str(api.get("name", "")).upper():
                    wasapi_hi = hi
                    break
            if wasapi_hi is not None:
                filtered = [(i, d) for i, d in raw if int(d.get("hostapi", -1)) == wasapi_hi]
                if filtered:
                    use_rows = filtered
                    only_one_host = True

        out: list[tuple[int, str]] = []
        for i, d in use_rows:
            name = str(d.get("name", "?"))
            hi = d.get("hostapi")
            host = ""
            if hi is not None and not only_one_host:
                try:
                    host = str(sd.query_hostapis(int(hi)).get("name", ""))
                except Exception:
                    host = ""
            label = f"[{i}] {name}"
            if host:
                label += f" — {host}"
            out.append((i, label))
        return out
    except Exception:
        return []


def _mic_spec_from_ui(mic_choice: str) -> str:
    """选项形如 ``[12] 设备名 — host``，提取索引供 SPEAKER_INPUT_DEVICE。"""
    if mic_choice == "（系统默认）":
        return ""
    try:
        bracket = mic_choice.split("]")[0]
        return bracket.replace("[", "").strip()
    except Exception:
        return ""


def _first_omnimic_row_index(mic_list: list[tuple[int, str]]) -> int | None:
    """返回列表中首条设备名含 OmniMic 的下标（相对 mic_list）；无则 None。"""
    for i, (_, label) in enumerate(mic_list):
        if "omnimic" in label.lower():
            return i
    return None


# 固定大模型选项（展示名；实际推理以 Dify 云端编排为准）
DEFAULT_LLM_MODEL = "Gemini 3.1 Pro Preview"
FIXED_LLM_OPTIONS: tuple[str, ...] = (
    DEFAULT_LLM_MODEL,
    "Gemini 2.5 Pro",
    "Doubao-Seed-2.0-pro",
    "Doubao-Seed-2.0-Lite",
)

_CUSTOM_MODELS_FILE = Path(__file__).resolve().parent / "web_ui_custom_models.json"


def _load_custom_models() -> list[str]:
    if not _CUSTOM_MODELS_FILE.is_file():
        return []
    try:
        raw = json.loads(_CUSTOM_MODELS_FILE.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            return [str(x).strip() for x in raw if str(x).strip()]
    except Exception:
        pass
    return []


def _save_custom_models(items: list[str]) -> None:
    seen: set[str] = set()
    out: list[str] = []
    fixed_set = set(FIXED_LLM_OPTIONS)
    for x in items:
        t = str(x).strip()
        if not t or t in seen:
            continue
        seen.add(t)
        if t not in fixed_set:
            out.append(t)
    _CUSTOM_MODELS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _CUSTOM_MODELS_FILE.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")


def _all_llm_options() -> list[str]:
    """固定项 + 已保存的自定义项（去重）。"""
    custom = _load_custom_models()
    fixed_set = set(FIXED_LLM_OPTIONS)
    merged = list(FIXED_LLM_OPTIONS)
    for c in sorted(set(custom)):
        if c not in fixed_set:
            merged.append(c)
    return merged


_ROOT = Path(__file__).resolve().parent
_WORKER = _ROOT / "web_ui_eval_worker.py"
_TRACK_SELECTION_FILE = _ROOT / "web_ui_selected_tracks.json"
_MODEL_SELECTION_FILE = _ROOT / "web_ui_selected_models.json"


def _load_saved_model_selection() -> list[str] | None:
    """读取上次保存的大模型多选列表；None 表示无可用存档。"""
    if not _MODEL_SELECTION_FILE.is_file():
        return None
    try:
        data = json.loads(_MODEL_SELECTION_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [str(x).strip() for x in data if str(x).strip()]
    except Exception:
        pass
    return None


def _save_model_selection(items: list[str]) -> None:
    try:
        _MODEL_SELECTION_FILE.write_text(
            json.dumps(_dedupe_models_preserve_order(list(items or [])), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def _initial_llm_models_for_sidebar(sidebar_opts: list[str]) -> list[str]:
    """
    侧栏多选初始值：上次保存 > 默认 ``Gemini 3.1 Pro Preview`` > 环境变量 > 列表首项。
    仅保留当前候选项中存在的名称。
    """
    opts = [str(x).strip() for x in (sidebar_opts or []) if str(x).strip()]
    if not opts:
        return []

    def _pick_one(name: str) -> str | None:
        n = (name or "").strip()
        return n if n and n in opts else None

    saved = _load_saved_model_selection()
    if saved:
        norm = _dedupe_models_preserve_order([x for x in saved if x in opts])
        if norm:
            return norm

    default_one = _pick_one(DEFAULT_LLM_MODEL)
    if default_one:
        return [default_one]

    env_one = _pick_one(os.environ.get("SPEAKER_EVAL_MODEL_NAME", ""))
    if env_one:
        return [env_one]

    legacy = _pick_one(str(st.session_state.get("selected_llm_model", "") or ""))
    if legacy:
        return [legacy]

    return [opts[0]]


def _load_saved_track_selection() -> list[str] | None:
    """读取上次保存的音源选择；None 表示无可用存档。"""
    if not _TRACK_SELECTION_FILE.is_file():
        return None
    try:
        data = json.loads(_TRACK_SELECTION_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [str(x).strip() for x in data if str(x).strip()]
    except Exception:
        pass
    return None


def _save_track_selection(items: list[str]) -> None:
    try:
        _TRACK_SELECTION_FILE.write_text(
            json.dumps(list(items or []), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass

# 实时步骤条（与 live_eval_log.append_live_step 的 step 键一致）
_LIVE_STEP_DEFS: tuple[tuple[str, str, str], ...] = (
    ("invoke", "开始调用", "子进程与流水线启动"),
    ("audio", "音频接收", "本机 WAV 已就绪"),
    ("scoring", "评分计算", "Dify 上传附件 → 等待模型回复"),
    ("report", "结论生成", "汇总并写 Word / Markdown"),
    ("complete", "完成状态", "结果文件已写入"),
)


def _read_live_eval_jsonl(path: str) -> tuple[dict[str, dict], str | None]:
    """读取 JSONL，返回 (每 step 最新一条, 若有 error 则附错误摘要)。"""
    latest: dict[str, dict] = {}
    err_detail: str | None = None
    try:
        p = Path(path)
        if not p.is_file():
            return latest, None
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            sk = str(o.get("step") or "")
            if sk:
                latest[sk] = o
                if sk == "error":
                    err_detail = str(o.get("detail") or o.get("title") or "出错")
    except Exception:
        pass
    return latest, err_detail


def _render_live_eval_timeline(live_path: str | None) -> None:
    """渲染 Dify/Gemini 评测链路步骤（读临时 JSONL）。"""
    if not live_path:
        return
    latest, err = _read_live_eval_jsonl(live_path)
    if err:
        st.error(f"评测链路报告异常：{err[:600]}")
    order = [x[0] for x in _LIVE_STEP_DEFS]
    parts: list[str] = []
    for i, (key, label_cn, hint) in enumerate(_LIVE_STEP_DEFS):
        rec = latest.get(key)
        prev_done = i == 0 or all(order[j] in latest for j in range(i))
        if rec:
            ts = html.escape(str(rec.get("ts") or ""))
            det = (rec.get("detail") or "").strip()
            det_h = (
                f'<span class="live-eval-detail">{html.escape(det)}</span>'
                if det
                else f'<span class="live-eval-hint">{html.escape(hint)}</span>'
            )
            parts.append(
                f'<div class="live-eval-row live-eval-done">'
                f'<span class="live-eval-ico">✅</span>'
                f'<span class="live-eval-title"><b>{html.escape(label_cn)}</b>'
                f'<small class="live-eval-ts">{ts}</small></span>{det_h}</div>'
            )
        elif prev_done:
            parts.append(
                f'<div class="live-eval-row live-eval-active">'
                f'<span class="live-eval-ico">⏳</span>'
                f'<span class="live-eval-title"><b>{html.escape(label_cn)}</b></span>'
                f'<span class="live-eval-hint">进行中… {html.escape(hint)}</span></div>'
            )
        else:
            parts.append(
                f'<div class="live-eval-row">'
                f'<span class="live-eval-ico">○</span>'
                f'<span class="live-eval-title"><b>{html.escape(label_cn)}</b></span>'
                f'<span class="live-eval-hint">{html.escape(hint)}</span></div>'
            )
    st.markdown(
        '<div class="live-eval-wrap">' + "".join(parts) + "</div>",
        unsafe_allow_html=True,
    )


def _append_run_log(level: str, title: str, detail: str = "") -> None:
    t = str(title or "")
    d = str(detail or "").strip()
    logs = st.session_state.setdefault("_run_logs", [])
    logs.append(
        {
            "ts": datetime.now().strftime("%H:%M:%S"),
            "level": str(level or "info"),
            "title": t,
            # 展开详情默认至少展示标题，避免“无额外详情”的空白感
            "detail": d if d else t,
        }
    )
    if len(logs) > 500:
        st.session_state["_run_logs"] = logs[-500:]


def _dual_track_live_log(status) -> Callable[[str], None]:
    """单轨 Dify 评分：同时写运行日志并在 ``st.status`` 内实时刷新（避免 spinner 期间日志假死）。"""

    def _log(msg: str) -> None:
        ms = str(msg or "").strip()
        if not ms:
            return
        if ms.startswith("[Dify]"):
            _append_run_log("info", ms, "")
        else:
            _append_run_log("info", ms, "")
        try:
            ts = datetime.now().strftime("%H:%M:%S")
            status.write(f"`{ts}` {ms}")
        except Exception:
            pass

    return _log


def _dual_multimodel_st_log(status) -> Callable[[str, str, str], None]:
    """
    双设备「追加模型」阶段：同时写 ``_run_logs`` 与当前页的 ``st.status``。
    运行日志框在本轮脚本末尾才会随 rerun 刷新；status 可在同一次 run 内逐条显示。
    """

    def _log(level: str, title: str, detail: str = "") -> None:
        _append_run_log(level, title, detail)
        try:
            ts = datetime.now().strftime("%H:%M:%S")
            lv = str(level or "info").lower()
            icon = {"error": "❌", "warning": "⚠️", "success": "✅"}.get(lv, "ℹ️")
            t = str(title or "")
            d = str(detail or "").strip()
            if len(d) > 900:
                d = d[:900] + "…"
            body = f"{icon} `[{ts}]` **{t}**"
            if d:
                body += f"\n{d}"
            status.write(body)
        except Exception:
            pass

    return _log


def _render_run_logs_box() -> None:
    logs: list[dict] = list(st.session_state.get("_run_logs") or [])
    expand = bool(st.session_state.get("run_log_expand_details", False))
    try:
        panel = st.container(border=True, height=280)
    except TypeError:
        panel = st.container(border=True)
    with panel:
        if not logs:
            st.caption("暂无运行事件。")
            return
        if not expand:
            lines: list[str] = []
            for rec in logs[-180:]:
                ts = rec.get("ts", "")
                lv = str(rec.get("level", "info")).lower()
                icon = {"error": "❌", "warning": "⚠️", "success": "✅"}.get(lv, "ℹ️")
                lines.append(f"[{ts}] {icon} {rec.get('title', '')}")
            st.text_area(
                "运行事件（紧凑列表）",
                value="\n".join(lines),
                height=250,
                disabled=True,
                label_visibility="collapsed",
            )
            return
        for rec in logs[-80:]:
            ts = rec.get("ts", "")
            lv = str(rec.get("level", "info")).lower()
            icon = {"error": "❌", "warning": "⚠️", "success": "✅"}.get(lv, "ℹ️")
            title = str(rec.get("title", ""))
            detail = str(rec.get("detail", ""))
            header = f"[{ts}] {icon} {title}"
            with st.expander(header, expanded=False):
                if detail:
                    st.code(detail, language="text")
                else:
                    st.caption("无额外详情")


def _is_nisqa_only_running() -> bool:
    return bool(st.session_state.get("_nisqa_only_running"))


def _is_eval_running() -> bool:
    p = st.session_state.get("_eval_popen")
    return p is not None and p.poll() is None


def _finalize_eval_subprocess() -> None:
    """子进程已退出时读取结果并写入 session_state，然后 rerun。"""
    p = st.session_state.get("_eval_popen")
    if not p or p.poll() is None:
        return
    out_path = Path(st.session_state.get("_eval_out_path") or "")
    cfg_path = Path(st.session_state.get("_eval_cfg_path") or "")
    try:
        if out_path.is_file():
            res = json.loads(out_path.read_text(encoding="utf-8"))
        else:
            res = {"ok": False, "error": "未生成结果文件（进程可能被中断）"}
    except Exception as exc:
        res = {"ok": False, "error": str(exc)}
    try:
        if cfg_path.is_file():
            cfg_path.unlink(missing_ok=True)
        if out_path.is_file():
            out_path.unlink(missing_ok=True)
    except Exception:
        pass
    st.session_state.pop("_eval_popen", None)
    st.session_state.pop("_eval_cfg_path", None)
    st.session_state.pop("_eval_out_path", None)
    ctx = st.session_state.pop("_eval_ctx", {})
    if res.get("ok") and res.get("report_path") and res.get("score_json"):
        emods = _dedupe_models_preserve_order(list(ctx.get("eval_models") or []))
        extra_reports: list[dict[str, str]] = []
        if len(emods) > 1:
            _append_run_log(
                "info",
                "多模型追加评分",
                f"主评测已结束；将按侧栏所选顺序，为另外 {len(emods) - 1} 个模型**依次**复用本次录音请求 Dify 并生成独立报告。"
                "单模型失败会跳过并继续下一模型。本阶段可能持续数分钟且日志更新较少，请勿关闭页面。",
            )
            try:
                from web_ui_multi_model_reports import append_main_mode_model_reports

                extra_reports = append_main_mode_model_reports(
                    score_json=str(res["score_json"]),
                    extra_models=emods[1:],
                    log=_append_run_log,
                    dify_api_key_baseline=str(ctx.get("dify_api_baseline") or ""),
                )
                _extras_expected = len(
                    [x for x in (emods[1:] or []) if str(x).strip()]
                )
                if _extras_expected and len(extra_reports) < _extras_expected:
                    _miss = _extras_expected - len(extra_reports)
                    _w = (
                        f"多模型追加：预期成功 {_extras_expected} 个，实际完成 {len(extra_reports)} 个，"
                        f"有 {_miss} 个未生成报告（详见运行事件日志）。"
                    )
                    st.session_state["_eval_multi_model_warn"] = _w
                    _append_run_log("warning", "多模型追加·部分失败", _w)
            except Exception as exc:
                import traceback

                st.session_state["_eval_multi_model_warn"] = f"主评测已完成，但追加模型阶段发生全局异常：{exc}"
                _append_run_log("error", "多模型追加报告异常", traceback.format_exc())
        pl: dict = {**ctx, **res}
        if extra_reports:
            pl["extra_model_reports"] = extra_reports
        if len(emods) > 1:
            try:
                from web_ui_multi_model_reports import write_multi_model_consistency_report

                cons = write_multi_model_consistency_report(
                    primary_score_json=str(res["score_json"]),
                    extra_reports=extra_reports,
                    primary_model=str(emods[0] or "").strip() or "主模型",
                    log=_append_run_log,
                )
                if cons:
                    pl["multi_model_consistency_report"] = cons
            except Exception as exc:
                _append_run_log("warning", "多模型一致性统计生成失败", str(exc))
        st.session_state["_eval_success_payload"] = pl
    else:
        base_err = str(res.get("error", "评测失败") or "评测失败")
        st.session_state["_eval_error_msg"] = base_err
        emods = _dedupe_models_preserve_order(list(ctx.get("eval_models") or []))
        safe = (res.get("session_safe") or "").strip()
        baseline = str(ctx.get("dify_api_baseline") or "")
        if emods and safe and _recorded_session_ready(safe):
            _append_run_log(
                "info",
                "多模型·录音补评",
                "子进程未返回成功，但检测到本次会话的 playlist 与 WAV；"
                f"将在页面进程内按顺序对 {len(emods)} 个所选模型依次调用 Dify（与首轮子进程失败解耦）。",
            )
            recovery: list[dict[str, str]] = []
            try:
                from web_ui_multi_model_reports import append_main_mode_models_for_session_safe

                recovery = append_main_mode_models_for_session_safe(
                    session_safe=safe,
                    models=emods,
                    log=_append_run_log,
                    dify_api_key_baseline=baseline or None,
                    phase_label="录音补评（子进程失败后）",
                )
            except Exception as exc:
                import traceback

                _append_run_log(
                    "error",
                    "多模型·录音补评阶段异常",
                    f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}",
                )
            if recovery:
                st.session_state.pop("_eval_error_msg", None)
                first = recovery[0]
                pl_rec: dict = {
                    **ctx,
                    "ok": True,
                    "report_path": str(first.get("markdown") or ""),
                    "score_json": str(first.get("score_json") or ""),
                }
                if len(recovery) > 1:
                    pl_rec["extra_model_reports"] = recovery[1:]
                if len(recovery) > 1:
                    try:
                        from web_ui_multi_model_reports import write_multi_model_consistency_report

                        cons = write_multi_model_consistency_report(
                            primary_score_json=str(first.get("score_json") or ""),
                            extra_reports=recovery[1:],
                            primary_model=str(first.get("model") or emods[0] or "").strip() or "主模型",
                            log=_append_run_log,
                        )
                        if cons:
                            pl_rec["multi_model_consistency_report"] = cons
                    except Exception as exc:
                        _append_run_log("warning", "多模型一致性统计生成失败", str(exc))
                st.session_state["_eval_success_payload"] = pl_rec
                _snippet = base_err.replace("\n", " ")[:400]
                st.session_state["_eval_multi_model_warn"] = (
                    "首轮子进程未完全成功，但本次录音已落盘，已按所选模型顺序在页面进程内完成补评。"
                    f" 子进程原错误摘要：{_snippet}"
                )
                _append_run_log("warning", "多模型·录音补评完成", st.session_state["_eval_multi_model_warn"])
            elif len(emods) > 1:
                st.session_state["_eval_multi_model_warn"] = (
                    f"检测到录音文件（会话 {safe!r}），但所选 {len(emods)} 个模型补评均未成功；"
                    "请查看运行事件日志中的 Dify 与评分错误。"
                )
                _append_run_log("warning", "多模型·录音补评无产出", st.session_state["_eval_multi_model_warn"])
    st.rerun()


def _strip_ch6_core_block_for_web(section_six_md: str) -> str:
    """第六章 Markdown 中「核心结论」与页顶大卡片重复，Web 展示时去掉该段，下载仍用完整版。"""
    if "### 核心结论" not in section_six_md or "### 综合评价" not in section_six_md:
        return section_six_md
    lead, rest = section_six_md.split("### 核心结论", 1)
    if "### 综合评价" not in rest:
        return section_six_md
    _, tail = rest.split("### 综合评价", 1)
    return lead.rstrip() + "\n\n### 综合评价" + tail


def _render_eval_results(
    *,
    dut_s: str,
    ref_s: str,
    mic_pick: str,
    export_format: str,
    report_path: str,
    score_json: str,
    log_box,
    extra_model_reports: list[dict[str, str]] | None = None,
    eval_models: list[str] | None = None,
    multi_model_consistency_report: dict[str, str] | None = None,
) -> None:
    """评测成功后渲染图表与报告（原 ``if start`` 内逻辑）。"""
    extras = list(extra_model_reports or [])
    if eval_models:
        em_list = list(eval_models)
    else:
        try:
            with open(score_json, encoding="utf-8") as f0:
                _meta_em = json.load(f0)
        except Exception:
            _meta_em = {}
        _stored_m = (
            str(_meta_em.get("web_ui_eval_model") or "").strip()
            if isinstance(_meta_em, dict)
            else ""
        )
        em_list = [_stored_m] if _stored_m else list(_all_eval_models_for_payload())
    bundles: list[dict[str, str]] = []
    try:
        with open(score_json, encoding="utf-8") as f0:
            meta0 = json.load(f0)
    except Exception:
        meta0 = {}
    prim_lbl = str(meta0.get("web_ui_eval_model") or "").strip()
    if em_list:
        prim_lbl = str(em_list[0] or "").strip() or prim_lbl
    if not prim_lbl:
        prim_lbl = "评测模型"
    bundles.append({"label": prim_lbl, "score_json": score_json, "report_path": report_path})
    for ex in extras:
        sx = str(ex.get("score_json") or "").strip()
        if sx and Path(sx).is_file():
            bundles.append(
                {
                    "label": (str(ex.get("model") or "").strip() or "模型"),
                    "score_json": sx,
                    "report_path": str(ex.get("markdown") or ex.get("report_path") or ""),
                }
            )

    log_box.success("✅ 评测完成")
    _append_run_log("success", "评测完成")
    _append_run_log(
        "info",
        "报告视图·模型列表",
        f"共 {len(bundles)} 套：{[b['label'] for b in bundles]!r}",
    )

    st.divider()
    st.subheader("📊 评测结果")
    st.markdown(
        '<p class="sub-muted" style="margin:0 0 0.65rem 0;">'
        "AI学习机智能音效评测 · "
        "音效评估 · AI评测 · 评分汇总 · 报告展示 · 一体化显示"
        "</p>",
        unsafe_allow_html=True,
    )
    cons_report = dict(multi_model_consistency_report or {})
    cons_md = Path(str(cons_report.get("markdown") or ""))
    if len(bundles) > 1 and cons_md.is_file():
        with st.expander("多模型一致性统计表（跨模型稳定性）", expanded=False):
            try:
                st.markdown(cons_md.read_text(encoding="utf-8"))
            except Exception as exc:
                st.caption(f"读取一致性统计失败：{exc}")
            c1, c2, c3 = st.columns(3)
            with c1:
                st.download_button(
                    "下载一致性 Markdown",
                    data=cons_md.read_bytes(),
                    file_name=cons_md.name,
                    mime="text/markdown",
                    key="dl_multi_model_consistency_md",
                )
            cons_tsv = Path(str(cons_report.get("tsv") or ""))
            if cons_tsv.is_file():
                with c2:
                    st.download_button(
                        "下载一致性 TSV",
                        data=cons_tsv.read_bytes(),
                        file_name=cons_tsv.name,
                        mime="text/tab-separated-values",
                        key="dl_multi_model_consistency_tsv",
                    )
            cons_json = Path(str(cons_report.get("json") or ""))
            if cons_json.is_file():
                with c3:
                    st.download_button(
                        "下载一致性 JSON",
                        data=cons_json.read_bytes(),
                        file_name=cons_json.name,
                        mime="application/json",
                        key="dl_multi_model_consistency_json",
                    )
    if len(bundles) > 1:
        _lab_opts = [b["label"] for b in bundles]
        _sel_lab = st.selectbox(
            "切换查看各模型的完整报告与图表（与对应模型评测数据一致）",
            options=_lab_opts,
            key="web_ui_eval_report_pick_lab",
        )
        _idx = _lab_opts.index(_sel_lab)
    else:
        _idx = 0
    b = bundles[_idx]
    score_json = b["score_json"]
    report_path = b["report_path"]
    model_line = b["label"]

    with open(score_json, encoding="utf-8") as f:
        data = json.load(f)

    pairwise = bool(data.get("comparison_mode") or data.get("stimulus_pairwise"))

    dut_avg = np.array([data["dut_scores"][m] for m in EVAL_METRICS])
    ref_avg = np.array([data["ref_scores"][m] for m in EVAL_METRICS])
    diff_avg = dut_avg - ref_avg

    score_dut = float(np.mean(dut_avg))
    score_ref = float(np.mean(ref_avg))
    diff = score_dut - score_ref

    if diff > 1.0:
        conclusion = f"✅ {dut_s} 综合音质显著优于 {ref_s}，领先 {diff:.1f} 分"
    elif diff > 0.3:
        conclusion = f"✅ {dut_s} 音质优于 {ref_s}"
    elif abs(diff) <= 0.3:
        conclusion = f"⚖️ {dut_s} 与 {ref_s} 音质相当"
    else:
        conclusion = f"⚠️ {dut_s} 略逊于 {ref_s}"

    _ev_json = str(data.get("web_ui_eval_model") or "").strip()
    _append_run_log(
        "info",
        "报告视图·模型一致性",
        f"当前视图模型={model_line!r}；web_ui_scores 内 web_ui_eval_model={_ev_json!r}；文件={Path(score_json).name}",
    )
    if _ev_json and model_line != _ev_json:
        _append_run_log(
            "warning",
            "报告模型字段与标签名不一致",
            f"标签={model_line!r} 但 JSON 记录={_ev_json!r}（图表与表以本 JSON 为准）。",
        )

    st.markdown(f"**当前报告对应评测模型**： `{html.escape(model_line)}`")
    if pairwise:
        st.info(
            "**同刺激双机对比模式**：模型对两路录音输出的是五维 **整数分差 −3～+3**（被测相对对比）。"
            "下表「对比」列为 **固定基准 7 分**，仅用于与「被测映射分（7+平均分差）」同尺度对照，"
            "**并非**对对比机单独打的 1～10 绝对听感分。"
        )

    rp = Path(report_path)
    md_report_path, docx_report_path = _resolve_pipeline_report_paths(report_path)

    _section_six_md = ""
    _six_word_ctx: dict | None = None
    _analysis_payload = load_analysis_from_score_json_path(score_json)
    _rows: list = []
    _df_detail = pd.DataFrame()
    if _analysis_payload:
        _rows = build_per_track_rows(_analysis_payload)
        _df_detail = rows_to_dataframe(_rows)
        if not _df_detail.empty and _rows:
            _dim_s6, _grand_s6 = compute_dimension_statistics(_rows)
            _section_six_md = build_section_six_markdown(
                comparison_mode=pairwise,
                dim_avgs=_dim_s6,
                grand=_grand_s6,
                rows=_rows,
            )
            _one_line_s6 = (
                one_line_summary_comparison(_dim_s6)
                if pairwise
                else one_line_summary_single(_grand_s6, len(_rows))
            )
            _six_word_ctx = {
                "dim_avgs": _dim_s6,
                "grand": _grand_s6,
                "rows": _rows,
                "one_line": _one_line_s6,
            }

    _scale_line = (
        "\n- **评分标尺**：同刺激双机对比，五维为整数分差 −3～+3（被测相对对比）；"
        "下表「对比基准」为固定 7 分展示，非对比机独立绝对分。\n"
        if pairwise
        else "\n"
    )
    if _section_six_md:
        report_md = f"""# 喇叭测试报告
生成时间：{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

## 评估模型 / 大模型（展示）
- **{model_line}**
- （说明：实际推理模型以 Dify 应用配置为准；上列为界面所选展示名，可与 Dify 侧模型对齐填写。）
{_scale_line}
## 设备
- 被测：{dut_s}
- 对比：{ref_s}
- 麦克风：{mic_pick}

## 说明
- **「本次评测最终结论与结果汇总」**（下文）含：全节目五维平均分差表、综合评价与优化建议（与流水线报告终章一致）；**核心结论主卡片**已展示于上方「一体化概览」。
- **映射分**（7+Δ）、柱状图、雷达图与 JSON 五维汇总表已在本页 **上方** 展示，此处不再重复 Markdown 表格。

**结论摘要**：{conclusion}
"""
        report_md_download = (
            report_md + "\n\n## 本次评测最终结论与结果汇总\n\n" + _section_six_md
        )
    else:
        if pairwise:
            _summary_block = f"""## 五维平均分
- 被测（映射分）：{score_dut:.2f}
- 对比基准（固定）：{score_ref:.2f}
- 平均分差（被测相对对比）：{diff:+.2f}
"""
            _table_head = """## 各维分数
| 指标 | 被测映射分 | 对比基准 | 五维分差 |
|------|------------|----------|----------|
"""
        else:
            _summary_block = f"""## 五维平均分
- 测试机：{score_dut:.2f}
- 对比机：{score_ref:.2f}
- 分差：{diff:+.2f}
"""
            _table_head = """## 各维分数
| 指标 | 测试机 | 对比机 | 分差 |
|------|--------|--------|------|
"""

        report_md = f"""# 喇叭测试报告
生成时间：{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

## 评估模型 / 大模型（展示）
- **{model_line}**
- （说明：实际推理模型以 Dify 应用配置为准；上列为界面所选展示名，可与 Dify 侧模型对齐填写。）
{_scale_line}
## 设备
- 被测：{dut_s}
- 对比：{ref_s}
- 麦克风：{mic_pick}

{_summary_block}
## 结论
{conclusion}

{_table_head}"""
        for m, a, b, d in zip(EVAL_METRICS, dut_avg, ref_avg, diff_avg):
            report_md += f"| {m} | {a:.2f} | {b:.2f} | {d:+.2f} |\n"
        report_md_download = report_md

    _cd, _cr, _cdf = (
        ("被测映射分（7+Δ）", "对比基准（固定7）", "五维平均分差（−3～+3）")
        if pairwise
        else ("测试机", "对比机", "分差")
    )
    _score_df = pd.DataFrame(
        {
            "指标": EVAL_METRICS,
            _cd: dut_avg.round(2),
            _cr: ref_avg.round(2),
            _cdf: diff_avg.round(2),
        }
    )

    with st.container(border=True):
        if pairwise:
            st.markdown("##### 刺激比较 · 评分汇总（上半屏）")
            st.caption(
                "**各维与总体的平均分差**（JSON 聚合，标尺 −3～+3）见下方大卡；映射分（7+Δ）为小字对照。"
                "随后为核心结论、对照图与全表；**下半屏**为测试报告正文，不重复渲染图表。"
            )
        else:
            st.markdown("##### 一体化概览")
            st.caption(
                "五维各分与总平均、核心结论、柱状/雷达图与汇总表均在本区；"
                "下方「测试报告详情」为逐音源与正文，不重复渲染图表。"
            )
        st.markdown(
            html_dim_scores_highlight(
                eval_metrics=list(EVAL_METRICS),
                dut_avg=dut_avg,
                score_dut=score_dut,
                pairwise=pairwise,
                diff_avg=diff_avg,
                score_ref=score_ref,
                diff=diff,
            ),
            unsafe_allow_html=True,
        )
        if _six_word_ctx is not None:
            st.markdown(
                _core_conclusion_highlight_html(
                    comparison_mode=pairwise,
                    grand=_six_word_ctx["grand"],
                    rows=_six_word_ctx["rows"],
                ),
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                '<div style="background:linear-gradient(165deg,#f8fafc 0%,#e2e8f0 100%);'
                "border:2px solid #94a3b8;border-radius:12px;padding:18px 22px;"
                'margin:10px 0 18px;text-align:center;">'
                '<p style="margin:0;font-size:1.15em;font-weight:700;color:#334155;">'
                f"{html.escape(conclusion)}"
                "</p></div>",
                unsafe_allow_html=True,
            )

        _fig_col1, _fig_col2 = st.columns(2)
        _ld_f = "被测映射分" if pairwise else "测试机"
        _lr_f = "对比基准(7)" if pairwise else "对比机"
        with _fig_col1:
            st.caption("五维平均分 · 柱状对照（映射分）")
            fig_bar, ax_b = plt.subplots(figsize=(9, 3.8))
            x = np.arange(len(EVAL_METRICS))
            ax_b.bar(x - 0.2, dut_avg, 0.4, label=_ld_f)
            ax_b.bar(x + 0.2, ref_avg, 0.4, label=_lr_f)
            ax_b.set_xticks(x)
            ax_b.set_xticklabels(EVAL_METRICS, rotation=25)
            ax_b.legend()
            if pairwise:
                ax_b.set_title("同刺激对比：右侧柱为固定展示基准，非对比机独立测分")
            st.pyplot(fig_bar)
        with _fig_col2:
            st.caption("五维平均分 · 雷达对照")
            fig_r, ax_r = plt.subplots(figsize=(6, 6), subplot_kw=dict(polar=True))
            angles = np.linspace(0, 2 * np.pi, len(EVAL_METRICS), endpoint=False)
            angles = np.concatenate([angles, [angles[0]]])
            ax_r.plot(angles, np.concatenate([dut_avg, [dut_avg[0]]]), "o-", label=_ld_f)
            ax_r.plot(angles, np.concatenate([ref_avg, [ref_avg[0]]]), "o-", label=_lr_f)
            if pairwise:
                ax_r.set_title("雷达：外圈为被测；内圈为固定基准 7（非独立评分）", fontsize=10)
            ax_r.legend()
            st.pyplot(fig_r)

        if pairwise:
            st.markdown("##### 评分汇总（全表）· 刺激比较")
            st.caption("下表与上方大卡同源（JSON）；**分差列**为被测相对对比机，与模型 −3～+3 标尺一致。")
        else:
            st.markdown("##### 评分汇总（全表）")
        _sum_cfg: dict = {"指标": st.column_config.TextColumn("指标", width="medium")}
        _sum_cfg[_cd] = st.column_config.NumberColumn(_cd, format="%.2f", width="small")
        _sum_cfg[_cr] = st.column_config.NumberColumn(_cr, format="%.2f", width="small")
        _sum_cfg[_cdf] = st.column_config.NumberColumn(_cdf, format="%.2f", width="small")
        st.dataframe(_score_df, use_container_width=True, hide_index=True, column_config=_sum_cfg)
        _mx1, _mx2, _mx3 = st.columns(3)
        with _mx1:
            st.metric("被测五维平均（映射分）" if pairwise else "测试机五维平均", f"{score_dut:.2f}")
        with _mx2:
            st.metric(
                "对比基准（固定，非独立打分）" if pairwise else "对比机五维平均",
                f"{score_ref:.2f}",
            )
        with _mx3:
            st.metric(
                "平均分差（被测相对对比，−3～+3 标尺）" if pairwise else "平均分差（测试−对比）",
                f"{diff:+.2f}",
            )

    st.divider()
    st.subheader("📄 测试报告详情")

    if _analysis_payload:
        if not _df_detail.empty:
            st.subheader("逐音源评测明细")
            st.caption(
                "数据来自本次会话 **output/analysis** 下与流水线一致的 analysis JSON；"
                "五维为 Dify 返回之整数分差（刺激比较 −3～+3）；**缺失维度按 0**；"
                "「综合结论」缺省时展示「对比总结」全文（若有）；"
                "「对比总结」缺省时展示「综合评价」（Web 自定义 prompt 常用此键）；"
                "「专业点评」「综合评价」取自解析字段，无则显示「—」。"
            )
            _disp = _df_detail.copy()
            for _k in EVAL_METRICS:
                if _k in _disp.columns:
                    _disp[_k] = _disp[_k].apply(
                        lambda x: int(x)
                        if pd.notna(x) and abs(float(x) - round(float(x))) < 1e-9
                        else round(float(x), 1)
                    )
            st.dataframe(
                _disp,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "音源名称": st.column_config.TextColumn("音源名称", width="large"),
                    "分组": st.column_config.TextColumn("分组", width="small"),
                    "综合结论": st.column_config.TextColumn("综合结论", width="small"),
                    "对比总结": st.column_config.TextColumn("对比总结", width="medium"),
                    "专业点评": st.column_config.TextColumn("专业点评", width="large"),
                    "综合评价": st.column_config.TextColumn("综合评价", width="large"),
                },
            )
        else:
            st.info("本次 analysis 中暂无成功解析的逐条音源记录。")
    else:
        st.caption(
            "未定位到与本次 **web_ui_scores** 同会话的 analysis JSON，逐音源表明细不可用。"
            "（期望路径：`output/analysis/analysis_<会话>_*.json`）"
        )

    st.markdown(report_md)

    if _section_six_md:
        st.divider()
        st.subheader("本次评测最终结论与结果汇总")
        _s6_web = _strip_ch6_core_block_for_web(_section_six_md)
        try:
            from nisqa_local import strip_nisqa_appendix_from_section_six

            _s6_web = strip_nisqa_appendix_from_section_six(_s6_web)
        except Exception:
            pass
        st.markdown(_s6_web, unsafe_allow_html=True)
        if _rows:
            try:
                from web_ui_nisqa_only import render_nisqa_report_from_rows

                render_nisqa_report_from_rows(
                    _rows,
                    key_prefix=f"eval_nisqa_{_idx}",
                    embedded_in_dify_report=True,
                )
            except Exception as _nisqa_ui_exc:
                st.caption(f"NISQA 可视化加载失败：{_nisqa_ui_exc}")

    st.subheader("导出报告")
    st.caption(
        "「PDF（与评测结果页一致）」含一体化概览、柱状/雷达图、评分汇总表、"
        "逐音源明细、第六章终稿及 NISQA 可视化（卡片/雷达/差异表，与评测页一致）。"
    )
    try:
        from web_ui_report_pdf import (
            PDF_RENDERER_VERSION,
            build_eval_report_pdf,
            pdf_export_available,
            pdf_render_backend_label,
            suggested_pdf_filename,
        )

        if not pdf_export_available():
            st.info("安装 PDF 依赖后可导出：`pip install -r requirements-pdf.txt`")
        else:
            st.caption(f"PDF 渲染：{pdf_render_backend_label()}")

            @st.cache_data(show_spinner="正在生成 PDF（与评测结果页一致）…")
            def _cached_eval_pdf(
                _score_json: str,
                _dut: str,
                _ref: str,
                _mic: str,
                _model: str,
                _pdf_ver: str,
            ) -> bytes:
                pdf_b, pdf_msg = build_eval_report_pdf(
                    score_json_path=_score_json,
                    dut_s=_dut,
                    ref_s=_ref,
                    mic_pick=_mic,
                    model_line=_model,
                )
                if pdf_b is None:
                    raise RuntimeError(pdf_msg)
                return pdf_b

            try:
                _pdf_bytes = _cached_eval_pdf(
                    score_json,
                    dut_s,
                    ref_s,
                    mic_pick,
                    model_line,
                    PDF_RENDERER_VERSION,
                )
                st.download_button(
                    "下载 PDF（与评测结果页一致）",
                    data=_pdf_bytes,
                    file_name=suggested_pdf_filename(model_line),
                    mime="application/pdf",
                    type="primary",
                    key=f"dl_pdf_eval_{_idx}",
                )
            except Exception as _pdf_exc:
                st.error(f"PDF 生成失败：{_pdf_exc}")
    except ImportError as _pdf_imp:
        st.caption(f"PDF 模块未加载：{_pdf_imp}")

    if export_format == "Word":
        st.caption(
            f"流水线 Word：`{docx_report_path}` ｜ Markdown：`{md_report_path}`"
        )
        if docx_report_path.is_file():
            st.download_button(
                "下载 Word 报告（.docx，流水线生成）",
                data=docx_report_path.read_bytes(),
                file_name=docx_report_path.name,
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                type="primary",
                key=f"dl_word_pipeline_{_idx}",
            )
        else:
            st.warning(
                "未找到本次会话的 Word 文件（请确认流水线已成功执行「生成 Word」步骤，"
                f"且路径为：`{docx_report_path.name}`）。"
            )
        if _six_word_ctx is not None:
            try:
                _ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                _wtmp = Path(tempfile.gettempdir()) / f"喇叭评测_WebUI_{_ts}.docx"
                _result_for_word = {
                    "test_name": "喇叭音效 AI 辅助评测（Web UI）",
                    "test_device": dut_s,
                    "ref_device": ref_s,
                    "总体平均分": (
                        f"{_six_word_ctx['grand']:.2f}"
                        if _six_word_ctx["grand"] is not None
                        else "N/A"
                    ),
                    "平均分说明": (
                        "五维分差总平均（先按维度全节目平均，再对五维取平均）"
                        if pairwise
                        else "五维总平均（先按维度全节目平均，再对五维取平均）"
                    ),
                    "综合结论": f"共 {len(_six_word_ctx['rows'])} 条音源完成模型评测。",
                    "单行评述": _six_word_ctx["one_line"],
                    "音源条数": str(len(_six_word_ctx["rows"])),
                    "comparison_mode": pairwise,
                    "cross_session": False,
                    "明细": [
                        {**dict(r), "节目": str(r.get("音源名称") or "")}
                        for r in _six_word_ctx["rows"]
                    ],
                    "维度平均分": _six_word_ctx["dim_avgs"],
                    "总平均分": _six_word_ctx["grand"],
                }
                generate_report(_result_for_word, save_path=str(_wtmp))
                st.download_button(
                    "下载 Word（本页生成，含逐条表与第六章终稿）",
                    data=_wtmp.read_bytes(),
                    file_name=f"speaker_eval_webui_{_ts}.docx",
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    key=f"dl_word_webui_ch6_{_idx}",
                )
            except Exception as _w_exc:
                st.caption(f"本页 Word 生成失败：{_w_exc}")
        with st.expander("备用：下载本页 Markdown 摘要（非流水线完整版）", expanded=False):
            st.download_button(
                "下载 Markdown 摘要",
                data=report_md_download.encode("utf-8"),
                file_name=f"喇叭评测摘要_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md",
                mime="text/markdown",
                key=f"dl_md_summary_word_mode_{_idx}",
            )
    else:
        st.caption(f"流水线 Markdown：`{md_report_path}` ｜ Word：`{docx_report_path}`")
        st.download_button(
            "下载 Markdown 摘要（本页生成）",
            data=report_md_download.encode("utf-8"),
            file_name=f"喇叭评测摘要_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md",
            mime="text/markdown",
            key=f"dl_md_summary_md_mode_{_idx}",
        )
        if md_report_path.is_file():
            st.download_button(
                "下载完整 Markdown 报告（流水线生成）",
                data=md_report_path.read_bytes(),
                file_name=md_report_path.name,
                mime="text/markdown",
                key=f"dl_md_pipeline_{_idx}",
            )
        else:
            st.info(f"未找到流水线 Markdown 文件：`{md_report_path.name}`（可能本次仅产出了 JSON）。")
        if docx_report_path.is_file():
            st.download_button(
                "下载 Word 报告（.docx，流水线生成）",
                data=docx_report_path.read_bytes(),
                file_name=docx_report_path.name,
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                key=f"dl_word_secondary_{_idx}",
            )

def _extract_compare_query_snippet() -> str:
    """从 ``_build_stimulus_compare_query`` 源码中提取 ``return f\"\"\" ... \"\"\"`` 片段（仅展示）。"""
    try:
        src_lines = inspect.getsource(_build_stimulus_compare_query).splitlines()
        start = None
        for i, line in enumerate(src_lines):
            if "return f" in line and '"""' in line:
                start = i
                break
        if start is None:
            return "（未能自动截取 query 字符串，请在仓库中打开 difyclient.py 查看 _build_stimulus_compare_query。）"
        out: list[str] = [src_lines[start]]
        for j in range(start + 1, len(src_lines)):
            out.append(src_lines[j])
            if src_lines[j].strip() == '"""':
                break
        return "\n".join(out)
    except Exception as exc:
        return f"（读取失败：{exc}）"


# ----- 侧栏：导航 -----
with st.sidebar:
    st.image("https://cdn-icons-png.flaticon.com/512/3059/3059976.png", width=70)
    st.markdown("### 导航")
    nav = st.radio(
        "页面",
        ["评测主页", "提示词与模板"],
        horizontal=False,
        label_visibility="collapsed",
    )

    st.divider()
    st.subheader("🤖 评估模型")
    _opts = _all_llm_options()
    _sidebar_opts = effective_model_choices(_opts)
    if not _sidebar_opts:
        _sidebar_opts = list(_opts or [])
    if "selected_llm_models" not in st.session_state:
        st.session_state["selected_llm_models"] = _initial_llm_models_for_sidebar(_sidebar_opts)

    _raw_ml = [str(x).strip() for x in (st.session_state.get("selected_llm_models") or []) if str(x).strip()]
    _norm_ml = _dedupe_models_preserve_order(_raw_ml)
    if _sidebar_opts:
        _norm_ml = [x for x in _norm_ml if x in _sidebar_opts]
    if _sidebar_opts and not _norm_ml:
        _norm_ml = _initial_llm_models_for_sidebar(_sidebar_opts)
    if _norm_ml != list(st.session_state.get("selected_llm_models") or []):
        st.session_state["selected_llm_models"] = _norm_ml
    st.session_state["selected_llm_model"] = (_norm_ml[0] if _norm_ml else "")

    def _on_multi_llm_change() -> None:
        ml = [str(x).strip() for x in (st.session_state.get("selected_llm_models") or []) if str(x).strip()]
        if ml:
            st.session_state["selected_llm_model"] = ml[0]
            _save_model_selection(ml)

    _mm_kw: dict = {
        "label": "大模型 / 评测模型（可多选）",
        "options": _sidebar_opts,
        "key": "selected_llm_models",
        "help": "合并原「展示与报告」与「评测模型」：**列表首项**写入 ``SPEAKER_EVAL_MODEL_NAME``，并作为 Dify ``selected_model``（若应用要求）；"
        "须与工作流枚举一致。若存在 web_ui_dify_api_keys_by_model.json，选项以其键名（模型名）为主；否则可用 web_ui_model_list.json；再否则为内置 + web_ui_custom_models.json。"
        "**顺序即评测顺序**——子进程先用首项完成采集与首轮评分，再依次为第 2、3… 项复用录音追加评分与报告；某一模型异常时跳过该项，其余继续。",
        "on_change": _on_multi_llm_change,
    }
    try:
        st.multiselect(**_mm_kw, min_selections=1)
    except TypeError:
        st.multiselect(**_mm_kw)

    st.caption(
        "若存在 `web_ui_dify_api_keys_by_model.json`，侧栏优先列出其中配置的模型名（可多选排在前的专钥模型）；"
        "否则可用 `web_ui_model_list.json` 覆盖候选项；再否则为内置 + `web_ui_custom_models.json`。"
    )

    st.caption("自定义模型（保存到 `web_ui_custom_models.json`，下次启动仍保留）")
    st.text_input(
        "自定义模型名称",
        key="custom_llm_draft",
        placeholder="输入后点击下方按钮加入列表",
        help="与固定列表合并显示；若与固定项重名将提示已存在。",
    )
    if st.button("➕ 添加到下拉列表并保存", width="stretch"):
        _name = (st.session_state.get("custom_llm_draft") or "").strip()
        if not _name:
            st.session_state["_llm_add_err"] = "请输入模型名称"
        elif _name in _all_llm_options():
            st.session_state["_llm_add_err"] = "该名称已在列表中"
        else:
            st.session_state.pop("_llm_add_err", None)
            _cur = _load_custom_models()
            _cur.append(_name)
            _save_custom_models(_cur)
            st.session_state["selected_llm_model"] = _name
            st.session_state["selected_llm_models"] = [_name]
            _save_model_selection([_name])
            st.session_state["custom_llm_draft"] = ""
            st.rerun()
    _err = st.session_state.pop("_llm_add_err", None)
    if _err:
        st.warning(_err)

# 子进程评测结束后写回结果（须在任何 st.stop() 分支之前执行，否则停留在提示词页时无法结算）
_finalize_eval_subprocess()

# ----- 分支：提示词页 -----
if nav == "提示词与模板":
    st.markdown(
        '<p class="main-title">📝 Dify 音效评估提示词 · 查阅与优化草稿</p>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<p class="sub-muted">单机提示词与双机「补充说明」可<strong>保存到项目目录</strong> '
        "<code>web_ui_prompt_overrides.json</code>，下次评测（含子进程）会自动生效；"
        "完整内置模板仍以源码为准。</p>"
        "<p class=\"sub-muted\">若 Dify 要求 ``audio_eval_prompt``：该字段通常<strong>限 255 字以内</strong>；"
        "程序默认填入内置短文，<strong>完整评测说明仍在对话 query</strong>。"
        "可在此页或环境变量 <code>DIFY_AUDIO_EVAL_PROMPT</code> 自定义短文（超长会自动截断）。</p>",
        unsafe_allow_html=True,
    )

    t1, t2, t3 = st.tabs(
        [
            "单机 · 绝对分（SCORING_QUERY）",
            "双机 · 刺激比较（补充说明）",
            "Dify 表单 · audio_eval_prompt",
        ]
    )
    if "draft_audio_eval_prompt" not in st.session_state:
        st.session_state["draft_audio_eval_prompt"] = get_audio_eval_prompt_override() or ""

    with t1:
        st.markdown("##### 单机录音 / 绝对听感 1–10 分")
        if "draft_scoring_query" not in st.session_state:
            st.session_state["draft_scoring_query"] = get_effective_scoring_query()
        st.text_area(
            "提示词正文（保存后对单机评分生效）",
            height=420,
            key="draft_scoring_query",
        )
        c1, c2 = st.columns(2)
        with c1:
            st.download_button(
                "导出为 .txt",
                data=str(st.session_state.get("draft_scoring_query", "")).encode("utf-8"),
                file_name="scoring_query_draft.txt",
                mime="text/plain",
            )
        with c2:
            if st.button("恢复为内置默认（需再点页面底部保存生效）", key="reset_single"):
                st.session_state["draft_scoring_query"] = DEFAULT_SCORING_QUERY
                st.rerun()

    with t2:
        _pm = stimulus_compare_prompt_mode()
        st.markdown("##### 双机同刺激 · 完整提示词（-3～+3 分差）")
        st.caption(
            f"当前发送模式：**{'仅最终提示词 + 本轮上下文' if _pm == 'final' else '内置长模板 + 下方补充说明'}**。"
            "保存非空正文后默认 **final**（不再重复拼接内置模板）。"
            "若需旧行为，在 JSON 中设 ``\"stimulus_compare_prompt_mode\": \"append\"``。"
        )
        with st.expander("参考：内置模板节选（append 模式才会拼接，只读）", expanded=False):
            st.code(_extract_compare_query_snippet(), language="text")
        if "draft_compare_query" not in st.session_state:
            st.session_state["draft_compare_query"] = get_stimulus_compare_extras()
        st.text_area(
            "双机评分提示词正文（保存后作为 Dify query 主体）",
            height=420,
            key="draft_compare_query",
            help="填写完整评测说明与 JSON Schema；程序仅再附音源名、附件序号等上下文。留空则使用内置模板。",
        )
        st.download_button(
            "导出补充说明为 .txt",
            data=str(st.session_state.get("draft_compare_query", "")).encode("utf-8"),
            file_name="stimulus_compare_extras_draft.txt",
            mime="text/plain",
        )

    with t3:
        st.markdown("##### Dify ``inputs.audio_eval_prompt``（开始表单）")
        st.caption(
            "留空并保存：使用客户端内置短文（&lt;256 字）填入表单字段；完整模板仍在每条请求的 query。"
            "若要在工作流里区分「表单槽」与「对话正文」，可在此写自定义短提示（勿超过约 250 字）。"
        )
        st.text_area(
            "audio_eval_prompt（可选；保存后写入 JSON）",
            height=200,
            key="draft_audio_eval_prompt",
            help="对应 inputs.audio_eval_prompt，多数租户限制 <256 字符，超长将截断。关闭传入：DIFY_OMIT_AUDIO_EVAL_PROMPT_INPUT=1。",
        )
        st.download_button(
            "导出为 .txt",
            data=str(st.session_state.get("draft_audio_eval_prompt", "")).encode("utf-8"),
            file_name="audio_eval_prompt_draft.txt",
            mime="text/plain",
        )

    st.divider()
    _s1, _s2 = st.columns([1, 2])
    with _s1:
        if st.button("保存提示词并生效", type="primary", key="save_prompt_overrides_btn"):
            try:
                save_prompt_overrides(
                    str(st.session_state.get("draft_scoring_query", "")),
                    str(st.session_state.get("draft_compare_query", "")),
                    str(st.session_state.get("draft_audio_eval_prompt", "")),
                )
                st.success(
                    "已写入 web_ui_prompt_overrides.json；后续评测与 CLI 评分将使用该配置。"
                )
            except OSError as exc:
                st.error(f"保存失败：{exc}")
    with _s2:
        st.caption(
            f"当前文件路径：`{Path(__file__).resolve().parent / 'web_ui_prompt_overrides.json'}`"
        )

    st.stop()

# ====================== 以下：评测主页 ======================
with st.sidebar:
    st.subheader("📱 Android 设备")
    if st.button("🔄 刷新已连接设备", width="stretch"):
        st.session_state["adb_devices"] = list_connected_adb_devices()
        st.rerun()

    if "adb_devices" not in st.session_state:
        st.session_state["adb_devices"] = list_connected_adb_devices()

    devices: list[str] = list(st.session_state["adb_devices"])

    if not devices:
        st.warning("未检测到状态为 device 的 ADB 设备，请连接 USB/无线调试并授权。")
        dut = st.text_input("被测机 serial（手动）", value=config.DUT_SERIAL, key="dut_man0")
        ref = st.text_input("对比机 serial（手动）", value=config.REF_SERIAL, key="ref_man0")
    elif len(devices) == 1:
        st.info("当前仅 1 台：**双机对比**需两台不同设备；第二台可手动填写序列号。")
        dut = st.selectbox("被测机", devices, key="dut_one")
        ref = st.text_input("对比机（手动输入第二台 serial）", value=config.REF_SERIAL, key="ref_man1")
    else:
        dut = st.selectbox("被测机（测试机）", devices, index=0, key="dut_sel")
        others = [x for x in devices if x != dut]
        ref_idx = 1 if len(others) > 1 else 0
        ref = st.selectbox("对比机（参考机）", others, index=min(ref_idx, len(others) - 1), key="ref_sel")

    st.subheader("🎙️ 麦克风")
    if platform.system() == "Windows":
        _show_all_mic = st.checkbox(
            "显示全部音频驱动（MME / DirectSound / WASAPI 等，同一麦克风会重复多条）",
            value=False,
            key="mic_show_all_hostapis",
            help="默认只列出 WASAPI，与常见本机录音一致并减少重复项；仅在需要指定非 WASAPI 索引时勾选。",
        )
    else:
        _show_all_mic = False
    mic_list = _list_input_devices(show_all_hostapis=_show_all_mic)
    mic_options = ["（系统默认）"] + [f"{label}" for _, label in mic_list]
    _om_row = _first_omnimic_row_index(mic_list)
    if "recording_mic_pick" not in st.session_state and _om_row is not None:
        st.session_state["recording_mic_pick"] = mic_options[_om_row + 1]
    if "recording_gain_db_slider" not in st.session_state:
        st.session_state["recording_gain_db_slider"] = (
            0 if _om_row is not None else int(config.GAIN_DB)
        )

    mic_pick = st.selectbox(
        "录音输入设备",
        options=mic_options,
        key="recording_mic_pick",
        help="写入环境变量 SPEAKER_INPUT_DEVICE（设备索引）。检测到 OmniMic 时默认选中；Windows 默认仅 WASAPI。",
    )
    _mic_spec = _mic_spec_from_ui(mic_pick)

    st.subheader("🎚️ 录音参数")
    st.caption(f"采样率：**{SAMPLE_RATE} Hz**（PortAudio / WAV 原生，不重采样）")
    gain = st.slider("录音增益 (dB)", -18, 6, key="recording_gain_db_slider")
    play_full_track = st.checkbox(
        "播放完整音频（不受时长设置影响）",
        value=bool(st.session_state.get("play_full_track_enabled", False)),
        key="play_full_track_enabled",
        help=(
            "开启后每条按 soundfile 探测的文件时长播放与录制（MP3/WAV 均可），忽略“时长(秒)”滑块；"
            "末尾默认追加 0.35s 缓冲以防尾音被截断（环境变量 SPEAKER_FULL_TRACK_END_PAD_SEC）。"
        ),
    )
    duration = st.slider("时长(秒)", 10, 60, int(config.DURATION), disabled=play_full_track)

    st.subheader("🎵 音源选择")
    _all_tracks_for_ui = _discover_all_tracks_for_ui()
    _track_options = [rel for _, rel in _all_tracks_for_ui]
    _name_counts = Counter(Path(rel).name for rel in _track_options)
    _track_label_map: dict[str, str] = {}
    for grp, rel in _all_tracks_for_ui:
        base = Path(rel).name
        _track_label_map[rel] = f"{base}（{grp}）" if _name_counts[base] > 1 else base
    if "selected_audio_rel_paths" not in st.session_state:
        _saved = _load_saved_track_selection()
        if _saved is None:
            st.session_state["selected_audio_rel_paths"] = list(_track_options)
        else:
            st.session_state["selected_audio_rel_paths"] = [x for x in _saved if x in _track_options]
    else:
        st.session_state["selected_audio_rel_paths"] = [
            x for x in st.session_state.get("selected_audio_rel_paths", []) if x in _track_options
        ]

    if _track_options:
        _sel_col1, _sel_col2 = st.columns(2)
        with _sel_col1:
            if st.button("全选", key="select_all_tracks_btn", width="stretch"):
                st.session_state["selected_audio_rel_paths"] = list(_track_options)
        with _sel_col2:
            if st.button("全部不选", key="clear_all_tracks_btn", width="stretch"):
                st.session_state["selected_audio_rel_paths"] = []
        st.multiselect(
            "仅评测勾选音频（默认全选）",
            options=_track_options,
            key="selected_audio_rel_paths",
            format_func=lambda rel: _track_label_map.get(rel, rel),
            help="支持单独勾选/取消勾选；录制与评测仅处理勾选项。",
        )
        st.caption(
            f"已选 {len(st.session_state.get('selected_audio_rel_paths', []))} / {len(_track_options)} 个音源"
        )
        _save_track_selection(list(st.session_state.get("selected_audio_rel_paths", [])))
    else:
        st.warning("未发现可用音源，请检查 assets/test_audio 目录。")
        st.session_state["selected_audio_rel_paths"] = []

    st.subheader("🧠 AI 评分接口")
    _provider_default = (
        "Seedpace Gateway"
        if (os.environ.get("SPEAKER_LLM_PROVIDER") or "").strip().lower() == "seedpace"
        else "Dify"
    )
    llm_provider = st.radio(
        "评分 API",
        ["Dify", "Seedpace Gateway"],
        index=0 if _provider_default == "Dify" else 1,
        horizontal=True,
        key="llm_provider_choice",
        help="默认 Dify 保持原调用链；选择 Seedpace 时仅评分请求切换到新 chat/completions 接口。",
    )
    _provider_id = "seedpace" if llm_provider == "Seedpace Gateway" else "dify"
    os.environ["SPEAKER_LLM_PROVIDER"] = _provider_id
    if "dify_upload_max_audio_sec_slider" not in st.session_state:
        _raw_cap = (os.environ.get("DIFY_UPLOAD_MAX_AUDIO_SECONDS") or "60").strip()
        try:
            _init_cap = int(float(_raw_cap))
        except ValueError:
            _init_cap = 60
        st.session_state["dify_upload_max_audio_sec_slider"] = max(0, min(300, _init_cap))
    dify_upload_max_sec = st.slider(
        "上传 Dify 最长音频 (秒)",
        min_value=0,
        max_value=300,
        step=5,
        key="dify_upload_max_audio_sec_slider",
        help=(
            "仅影响上传至 Dify 的音频长度，本地 output/recorded 录音文件保持完整。"
            "0 = 不截断、整文件上传；>0 时若录音超过该秒数，只上传开头 N 秒（默认 60）。"
            "与「播放完整音频」无关，整曲录制后仍可在此限制上传时长以省 token。"
        ),
    )
    os.environ["DIFY_UPLOAD_MAX_AUDIO_SECONDS"] = str(int(dify_upload_max_sec))
    dify_api = st.text_input("DIFY_API_KEY", value=config.DIFY_API_KEY, type="password")
    dify_user = st.text_input("DIFY_USER", value=config.DIFY_USER)
    _seedpace_env_key = os.environ.get("SEEDPACE_API_KEY", "")
    seedpace_api = st.text_input(
        "SEEDPACE_API_KEY",
        value=_seedpace_env_key,
        type="password",
        disabled=_provider_id != "seedpace",
        help="仅选择 Seedpace Gateway 时使用；不会写入代码文件。",
    )
    seedpace_api_url = st.text_input(
        "SEEDPACE_API_URL",
        value=os.environ.get(
            "SEEDPACE_API_URL",
            "https://study-ai-gateway.seedpace.com/pre-gen-text/v1/chat/completions",
        ),
        disabled=_provider_id != "seedpace",
    )
    if _provider_id == "seedpace":
        from seedpace_audio_client import (
            normalize_seedpace_api_key,
            normalize_seedpace_api_url,
        )

        _sk = normalize_seedpace_api_key(seedpace_api)
        _su = normalize_seedpace_api_url(seedpace_api_url)
        if _sk:
            os.environ["SEEDPACE_API_KEY"] = _sk
        if _su:
            os.environ["SEEDPACE_API_URL"] = _su
    st.caption(
        "Dify 为原有默认链路；Seedpace Gateway 使用 OpenAI 风格 chat/completions，"
        "音频按现有 Dify 风格 files 字段随请求发送。"
    )
    if _provider_id == "seedpace":
        st.caption(
            "**Seedpace 注意**：URL 必须含 ``/pre-gen-text/v1/chat/completions``；"
            "评测模型建议只选 **Gemini 3.1 Pro Preview** 或 **gemini-2.5-pro**（Doubao 等会 HTTP 404）。"
            "Key 只填 token，勿填 ``Authorization: Bearer``。"
        )
    st.caption(
        "「DIFY_USER」会随每次上传/对话传给 Dify；须与贵司实例要求一致（常见为**企业邮箱**）。"
        "若日志出现 **User Not Exists / invalid_param**，请改为控制台认可的账号后再测。"
    )
    st.caption(
        "若各模型对应**不同 Dify 应用**：在项目根目录放置 ``web_ui_dify_api_keys_by_model.json``，"
        "键为侧栏须选的**模型名**，值为该应用的 ``app-...`` Key；"
        "配置非空时侧栏「评估模型」列表会**优先展示这些键名**。"
        "未出现在映射表中的名称仍可用上方全局 DIFY_API_KEY。示例见 ``web_ui_dify_api_keys_by_model.json.example``。"
    )

    st.subheader("📊 本地客观音质 (NISQA)")
    if "nisqa_enabled" not in st.session_state:
        st.session_state["nisqa_enabled"] = _nisqa_enabled_from_env()
    nisqa_enabled = st.checkbox(
        "启用 NISQA 本地客观评分",
        key="nisqa_enabled",
        help=(
            "在 Dify 主观五维评分之外，对每条录音附加 NISQA MOS 等客观指标（写入 analysis 的 objective_scores）。"
            "默认关闭，不影响现有流程；需先安装依赖并下载权重。"
        ),
    )
    _sync_nisqa_env(bool(nisqa_enabled))
    try:
        from nisqa_local import (
            availability_message,
            ensure_weights,
            is_available,
            weights_path,
            weights_ready,
        )

        if nisqa_enabled and not weights_ready():
            with st.spinner("正在准备 NISQA 本地权重..."):
                try:
                    ensure_weights()
                except Exception as exc:
                    st.warning(f"NISQA 权重自动下载失败：{exc}")

        _nisqa_st = availability_message()
        if nisqa_enabled and is_available():
            st.success(_nisqa_st)
        elif nisqa_enabled:
            st.warning(_nisqa_st)
        else:
            st.caption(_nisqa_st)
        if nisqa_enabled:
            st.caption(f"权重：`{weights_path()}`")
    except ImportError:
        if nisqa_enabled:
            st.warning("未安装 nisqa 模块：`pip install -r requirements-nisqa.txt`")
    with st.expander("NISQA 安装说明", expanded=False):
        st.markdown(
            "1. `pip install -r requirements-nisqa.txt`\n"
            "2. `python scripts/setup_nisqa_weights.py`\n"
            "3. 勾选上方开关后重新评测\n\n"
            "环境变量：`SPEAKER_NISQA_ENABLED=1`（本开关会自动写入）。"
            "失败策略默认 `SPEAKER_NISQA_ON_FAILURE=skip`，不中断 Dify 评分。"
        )

    st.subheader("📂 报告导出")
    export_format = st.selectbox("导出格式", ["Markdown", "Word"])

    st.divider()
    st.subheader("📊 历史数据预览")
    st.caption(
        "不跑采集：用已有 **web_ui_scores** + 同会话 **analysis** 复现下方报告、逐音源表与第六章。"
        "文件名约定见下方说明；可按来源筛选。"
    )
    from config import ANALYSIS_DIR as _AN_DIR

    _hist_bucket_labels: dict[str, str] = {
        "all": "全部（按修改时间）",
        "dual_webui": "Web 双设备单麦（dual_webui_*）",
        "regular": "常规 / 子进程主会话",
        "multi_model_extra": "多模型追加（文件名含 __）",
        "dual_device_legacy": "旧版 dual_device_*",
    }
    _hist_filter = st.radio(
        "历史分数 JSON 筛选",
        options=list(_hist_bucket_labels.keys()),
        format_func=lambda k: _hist_bucket_labels[str(k)],
        horizontal=True,
        key="demo_hist_web_scores_bucket",
    )
    st.caption(
        "命名规则（均在 ``output/analysis/``）：主文件为 ``web_ui_scores_{会话safe}.json``。"
        "其中 **Web 双设备单麦** 的 ``{safe}`` 形如 ``dual_webui_YYYYMMDD_HHMMSS``，"
        "对应 ``analysis_{safe}_*.json``；**常规 Web/子进程**常见 ``manual_*``；"
        "多模型追加常为 ``__模型标签`` 双下划线后缀；旧版双设备见 ``web_ui_scores_dual_device_*``。"
    )

    _hist_cap = 40
    _hist_files = _list_hist_web_scores_for_ui(_AN_DIR, bucket=str(_hist_filter), cap=_hist_cap)
    if _hist_files:
        _h_idx = st.selectbox(
            "选择历史分数 JSON",
            options=list(range(len(_hist_files))),
            format_func=lambda i: _hist_web_scores_select_label(_hist_files[int(i)]),
            key="demo_hist_web_scores_idx",
        )
        _sel_hist = _hist_files[int(_h_idx)]
        _sel_model = _model_label_from_web_scores_path(_sel_hist)
        if _sel_model:
            st.caption(f"当前选中评测模型：**{_sel_model}**")
        if st.button("加载到报告界面", width="stretch", key="demo_load_hist_btn"):
            _demo = _build_demo_eval_payload(_sel_hist, export_format)
            if _demo:
                st.session_state["_eval_success_payload"] = _demo
                st.session_state.pop("_eval_error_msg", None)
                st.rerun()
            else:
                st.warning("所选文件无效或已删除。")
    else:
        if str(_hist_filter) == "all":
            st.caption(f"目录 `{_AN_DIR}` 下暂无 web_ui_scores_*.json，请先完整跑过一次评测。")
        else:
            st.caption(
                f"当前筛选「{_hist_bucket_labels.get(str(_hist_filter), _hist_filter)}」下暂无文件。"
                f"可改选「全部」或确认 `output/analysis` 下是否已有对应会话的 `web_ui_scores_*.json`。"
            )

    if st.session_state.get("_eval_success_payload") and not _is_eval_running() and not _is_nisqa_only_running():
        if st.button("🗑 清除报告预览", width="stretch", key="clear_demo_preview"):
            st.session_state.pop("_eval_success_payload", None)
            st.session_state.pop("nisqa_only_payload", None)
            st.rerun()

selected_audio_rel_paths: list[str] = list(st.session_state.get("selected_audio_rel_paths", []))
play_full_track = bool(st.session_state.get("play_full_track_enabled", False))
_apply_selected_tracks_env(selected_audio_rel_paths, play_full_track)
_has_selected_tracks = bool(selected_audio_rel_paths)

# 首页标题区：模型信息（优化醒目版）
st.markdown(
    '<h1 style="font-size:42px; font-weight:900; color:#165DFF; text-align:center;">'
    "🔊 AI学习机智能音效评测</h1>",
    unsafe_allow_html=True,
)
mcol1, mcol2 = st.columns([3, 1])
with mcol1:
    _ml_show = _all_eval_models_for_payload()
    _show_model = (
        "、".join(_ml_show)
        if len(_ml_show) > 1
        else (_ml_show[0] if _ml_show else (str(st.session_state.get("selected_llm_model", "") or "").strip() or "（未选择）"))
    )
    st.markdown(
        f'<p class="sub-muted">当前选用模型（展示；多选时首项为主展示名）：<strong style="color:#1e40af">{html.escape(_show_model)}</strong></p>',
        unsafe_allow_html=True,
    )
with mcol2:
    st.caption("ADB 设备数")
    st.markdown(f"**{len(devices)}** 台" if devices else "**0** 台")

if devices:
    with st.expander("📋 已连接设备序列号列表", expanded=False):
        st.code("\n".join(devices), language="text")

st.divider()

# ====================== 测评模式选择 ======================
st.markdown(
    '<div style="background:#f0f9ff;border-left:4px solid #2563eb;padding:12px 16px;margin-bottom:16px;border-radius:6px;">'
    '<p style="margin:0;font-size:1rem;font-weight:600;color:#1e40af;">📋 选择测评模式</p>'
    '</div>',
    unsafe_allow_html=True,
)

if "eval_mode" not in st.session_state:
    st.session_state["eval_mode"] = "dual_device"

eval_mode = st.radio(
    "测评模式",
    options=["dual_device", "normal"],
    format_func=lambda x: "常规模式（原方案）" if x == "normal" else "双设备单麦对比模式（新增）",
    key="eval_mode_radio",
    horizontal=True,
    label_visibility="collapsed",
)
st.session_state["eval_mode"] = eval_mode

# 双设备录制器须在「手动开始测评」等逻辑之前就绪，避免仅点击按钮时尚未执行下方专用 UI 区块导致无法进入评测。
if eval_mode == "dual_device" and "_dual_device_full_recorder" not in st.session_state:
    from dual_device_full_recorder import DualDeviceFullRecorder

    st.session_state["_dual_device_full_recorder"] = DualDeviceFullRecorder(
        log=print,
        mic_spec=_mic_spec_from_ui(mic_pick),
    )

# 根据模式显示不同的提示文案
if eval_mode == "dual_device":
    st.markdown(
        '<div style="background:#fef3c7;border:2px solid #f59e0b;border-radius:10px;padding:16px;margin-bottom:16px;">'
        '<p style="margin:0 0 8px 0;font-size:1.05rem;font-weight:700;color:#92400e;">'
        '⚠️ 双设备单麦对比模式 · 固定摆放规范'
        '</p>'
        '<ul style="margin:0;padding-left:20px;color:#78350f;font-size:0.95rem;">'
        '<li><strong>麦克风要求</strong>：仅使用单个 OmniMic 麦克风，全程固定位置不动</li>'
        '<li><strong>录制流程</strong>：第一步录制【被测设备A】→ 第二步录制【对比设备B】</li>'
        '<li><strong>强制统一标准</strong>：喇叭正对麦克风、固定15cm距离、同高度同角度同音量同环境</li>'
        '<li><strong>独立录制</strong>：两次独立录制，不能同时播放、不能混音</li>'
        '<li><strong>评分规则</strong>：固定使用刺激比较 -3+3 分差评分规则</li>'
        '</ul>'
        '</div>',
        unsafe_allow_html=True,
    )
else:
    st.caption(
        "评测流程仍由既有 `main_run_eval` / 采集与 Dify 流水线执行，本页仅增加展示与设备选择；"
        "多音源顺序与 CLI 一致。"
    )

if not _has_selected_tracks:
    st.warning("当前未勾选任何音源，请在侧边栏“音源选择”中至少勾选 1 个后再开始。")

col1, col2 = st.columns([2, 1])
with col1:
    with st.container(border=True):
        st.subheader("📌 当前配置")
        c1, c2 = st.columns(2)
        _cfg_ml = _all_eval_models_for_payload()
        _cfg_m = "、".join(_cfg_ml) if _cfg_ml else "（未选）"
        with c1:
            st.info(f"被测：{dut or '（未填）'}")
            st.info(f"增益：{gain} dB")
            st.info(f"麦克风：{mic_pick}")
            st.info(f"采样率：{SAMPLE_RATE} Hz")
        with c2:
            st.info(f"对比：{ref or '（未填）'}")
            st.info(f"大模型：{_cfg_m}")
            st.info(f"AI用户：{dify_user or '（未填）'}")

with col2:
    with st.container(border=True):
        st.subheader("🚀 运行")
        start = False
        stop_eval = False
        stop_dual_eval = False
        start_nisqa_only = False
        stop_nisqa_only = False
        _run = _is_eval_running()
        _dual_run = bool(st.session_state.get("_dual_eval_running", False))
        _nisqa_run = _is_nisqa_only_running()
        _dual_rec_for_nisqa = (
            st.session_state.get("_dual_device_full_recorder")
            or st.session_state.get("_dual_device_recorder")
            if eval_mode == "dual_device"
            else None
        )
        if _run:
            stop_eval = st.button("⏹ 停止测试", type="secondary", width="stretch", help="终止子进程，可能导致本次评测无结果或文件不完整")
            start = False
            stop_dual_eval = False
            start_nisqa_only = False
        elif _nisqa_run:
            stop_nisqa_only = st.button(
                "⏹ 停止 NISQA 评分",
                type="secondary",
                width="stretch",
                help="在当前文件评完后停止，不会中断正在推理的单条",
            )
            start = False
            stop_dual_eval = False
            start_nisqa_only = False
        else:
            # 常规模式：直接显示开始按钮
            if eval_mode == "normal":
                start = st.button(
                    "✅ 开始全自动评测",
                    type="primary",
                    width="stretch",
                    disabled=not _has_selected_tracks,
                    help="请至少勾选 1 个音源后再开始" if not _has_selected_tracks else None,
                )
            # 双设备模式：显示手动测评按钮（需先完成两段录制）
            else:
                _dual_recorder = (
                    st.session_state.get("_dual_device_full_recorder")
                    or st.session_state.get("_dual_device_recorder")
                )
                _dual_import_ok = bool(st.session_state.get("_dual_eval_import_paired"))
                _dual_ready = bool(
                    _dual_recorder is not None
                    and (_dual_recorder.is_complete or _dual_import_ok)
                )
                # 录制完成后配对评测用的是会话内已录 WAV；若用户事后「全部不选」音源，仍应能点击测评。
                _can_evaluate = bool(_has_selected_tracks or _dual_ready)
                if _dual_run:
                    start_dual_eval = False
                    stop_dual_eval = st.button(
                        "⏹ 停止手动测评",
                        type="secondary",
                        width="stretch",
                        help="在当前音源处理完成后停止后续测评。",
                    )
                else:
                    start_dual_eval = st.button(
                        "✅ 手动开始测评",
                        key="dual_manual_start_eval_btn",
                        type="primary" if (_can_evaluate and _dual_ready) else "secondary",
                        width="stretch",
                        disabled=not _can_evaluate,
                        help=(
                            "请至少勾选 1 个音源（或已完成两段录制 / 已导入清单）后再开始"
                            if not _can_evaluate
                            else (
                                "已就绪：点击开始 Dify 双路评测"
                                if _dual_ready
                                else "已点击后将校验双设备录制是否完整"
                            )
                        ),
                    )
                    stop_dual_eval = False
                # 同一次 run 内立即完成 Dify 会话初始化（_apply_dify_env 已提前定义），避免依赖脚本执行到页尾。
                if start_dual_eval:
                    if not st.session_state.get("_dual_eval_running", False):
                        _append_run_log("info", "已点击【手动开始测评】，正在准备 Dify 双路评测…")
                        _ok_dual, _err_dual = _try_prepare_dual_device_eval_session(
                            mic_spec=_mic_spec,
                            dify_api=dify_api,
                            dify_user=dify_user,
                            provider_id=_provider_id,
                            seedpace_api=seedpace_api,
                            seedpace_api_url=seedpace_api_url,
                        )
                        if _ok_dual:
                            _n = len((st.session_state.get("_dual_eval_state") or {}).get("paired_audios") or [])
                            _append_run_log("info", f"双设备评测已就绪（{_n} 条音源），即将上传 Dify…")
                            st.rerun()
                        else:
                            st.error(f"❌ 无法开始双设备测评：{_err_dual}")
                            _append_run_log("error", "双设备测评启动失败", str(_err_dual))
                start = False  # 常规模式的start保持False
            stop_eval = False

            from web_ui_nisqa_only import can_launch_nisqa_only

            _can_nisqa_only = can_launch_nisqa_only(
                eval_mode=eval_mode,
                selected_audio_rel_paths=selected_audio_rel_paths,
                dual_recorder=_dual_rec_for_nisqa,
            )
            st.divider()
            start_nisqa_only = st.button(
                "📊 仅 NISQA 本地客观评分",
                type="secondary",
                width="stretch",
                disabled=not _can_nisqa_only or _dual_run,
                help=(
                    "不调用 Dify，仅对当前会话本地录音运行 NISQA（双设备用已录 WAV，常规模式用 output/recorded）"
                    if _can_nisqa_only
                    else "请先完成录制或确认 output/recorded 下存在可评音频"
                ),
            )
            stop_nisqa_only = False

# ====================== 双设备模式专用UI ======================
if eval_mode == "dual_device":
    st.divider()
    st.subheader("🎙️ 双设备单麦对比 - 完整录制流程")
    
    st.markdown(
        '<div style="background:#e0f2fe;border-left:4px solid #0284c7;padding:12px 16px;margin-bottom:16px;border-radius:6px;">'
        '<p style="margin:0;font-size:0.95rem;color:#0369a1;">'
        '📋 <strong>录制流程说明：</strong><br>'
        '1️⃣ 扫描音源 → 推送至被测设备A → 按节目循环（播放+录音）<br>'
        '2️⃣ 保持麦克风不动 → 推送至对比设备B → 按节目循环（播放+录音）<br>'
        '3️⃣ 两段都完成后 → 按节目配对送Dify评分 → 生成报告<br>'
        '💡 若中途某条失败，再次点击同一步按钮可<strong>续录</strong>（跳过已成功音源）；'
        '需全部重来请点「清除重录」。'
        '</p>'
        '</div>',
        unsafe_allow_html=True,
    )
    
    recorder = st.session_state.get("_dual_device_full_recorder") or st.session_state.get("_dual_device_recorder")
    if recorder is None:
        st.error("未找到双设备录制器，请切换到双设备模式后刷新页面重试。")
        st.stop()
    
    # 显示当前状态
    status_col1, status_col2, status_col3 = st.columns(3)
    with status_col1:
        if recorder.is_device_a_complete:
            st.success(f"✅ 【被测设备A】已完成 ({len(recorder.device_a_results)} 个音源)")
        elif recorder.device_a_results:
            st.warning(f"⚠️ 【被测设备A】部分完成 ({len([r for r in recorder.device_a_results if r.get('ok')])}/{len(recorder.device_a_results)})")
        else:
            st.info("⭕ 【被测设备A】未开始")
    
    with status_col2:
        if recorder.is_device_b_complete:
            st.success(f"✅ 【对比设备B】已完成 ({len(recorder.device_b_results)} 个音源)")
        elif recorder.device_b_results:
            st.warning(f"⚠️ 【对比设备B】部分完成 ({len([r for r in recorder.device_b_results if r.get('ok')])}/{len(recorder.device_b_results)})")
        else:
            st.info("⭕ 【对比设备B】未开始")
    
    with status_col3:
        _imp_n = len(st.session_state.get("_dual_eval_import_paired") or [])
        if st.session_state.get("_dual_eval_running"):
            st.warning("🧠 Dify 评分进行中…")
            st.caption(
                "单轨上传+推理可能需数分钟；请展开页内「评分 [n/N]」状态条查看实时进度，"
                "下方「运行事件日志」在每轨结束后刷新。"
            )
        elif _imp_n:
            st.success(f"✅ 已导入 {_imp_n} 对录音（清单）")
            st.caption("可点击右侧【手动开始测评】，无需重新采集")
        elif recorder.is_complete:
            st.success("✅ 两段设备就绪")
            st.caption("可点击右侧【手动开始测评】")
        else:
            st.info("⏳ 等待录制完成或导入清单")
            st.caption("需集齐双设备所有音源才能测评，或从下方导入已有 dual_playlist.json")

    with st.expander("📂 使用已有录音直接评测（导入 *_dual_playlist.json）", expanded=False):
        st.caption(
            "使用此前会话写入 ``output/recorded/`` 的清单文件；其中每条 ``local_wav`` 须仍指向本机存在的 WAV。"
            "载入后会**自动清空本页内存里的旧录制列表（不删磁盘文件）**，避免您之后若点「重新录制」时"
            "误把清单里的同一批 WAV 当作待清理会话而删除。"
        )
        _paths_full: list[str] = []
        _recorded_dir_display = "output/recorded"
        try:
            from config import RECORDED_DIR

            _recorded_dir_display = str(RECORDED_DIR.resolve())
            if RECORDED_DIR.is_dir():
                _paths_full = [
                    str(p.resolve())
                    for p in sorted(
                        RECORDED_DIR.glob("*_dual_playlist.json"),
                        key=lambda x: x.stat().st_mtime,
                        reverse=True,
                    )
                ]
        except Exception:
            _paths_full = []

        def _playlist_label(full: str) -> str:
            fp = Path(full)
            return f"{fp.name}  ·  {fp.parent}"

        if _paths_full:
            st.selectbox(
                "从录音目录选择清单（output/recorded）",
                options=_paths_full,
                format_func=_playlist_label,
                key="dual_playlist_select",
                help="按修改时间倒序，最新在上",
            )
        else:
            st.info(
                f"目录 `{_recorded_dir_display}` 下暂无 ``*_dual_playlist.json``，请用手动路径。"
            )

        _manual_pl = st.text_input(
            "或手动输入清单完整路径（填写后优先于上方下拉）",
            key="dual_playlist_path_input",
            placeholder=r"D:\...\output\recorded\xxxx_dual_playlist.json",
        )
        ic1, ic2 = st.columns(2)
        with ic1:
            if st.button("载入清单", key="dual_import_playlist_btn", width="stretch"):
                try:
                    from dual_device_full_recorder import load_paired_audios_from_dual_playlist_path

                    raw = (_manual_pl or "").strip()
                    if not raw and _paths_full:
                        raw = str(st.session_state.get("dual_playlist_select") or "").strip()
                    if not raw:
                        raise ValueError("请在下拉中选择清单，或填写手动路径")
                    rows = load_paired_audios_from_dual_playlist_path(raw)
                    st.session_state["_dual_eval_import_paired"] = rows
                    _drec_imp = st.session_state.get("_dual_device_full_recorder") or st.session_state.get(
                        "_dual_device_recorder"
                    )
                    if _drec_imp is not None and hasattr(
                        _drec_imp, "detach_in_memory_results_keep_wav_files"
                    ):
                        _drec_imp.detach_in_memory_results_keep_wav_files()
                        _append_run_log(
                            "info",
                            "已脱本会话双设备录制内存列表（磁盘 WAV 未删除），与导入清单解耦",
                            "",
                        )
                    _append_run_log("info", "已从清单载入配对录音", raw)
                    st.success(f"已载入 {len(rows)} 对音源。")
                    st.rerun()
                except Exception as e:
                    st.error(str(e))
        with ic2:
            if st.button("清除导入", key="dual_clear_import_btn", width="stretch"):
                st.session_state.pop("_dual_eval_import_paired", None)
                _append_run_log("warning", "已清除导入的配对清单")
                st.rerun()
    
    # 播放预览：默认折叠，展开后再渲染各轨 st.audio，减轻首屏与 rerun 负担
    if recorder.device_a_results or recorder.device_b_results:
        if st.session_state.get("_dual_eval_running"):
            st.info("🔇 评分进行中已暂时隐藏音源预览，完成后将恢复显示。")
        with st.expander("🔊 音频预览", expanded=False):
            if st.session_state.get("_dual_eval_running"):
                st.caption("评分结束后展开即可试听各轨 WAV。")
            else:
                if recorder.device_a_results:
                    st.markdown("**【被测设备A】录制的音源：**")
                    for result in recorder.device_a_results:
                        if result.get("ok") and result.get("local_wav"):
                            wav_path = result["local_wav"]
                            track_name = f"{result['group']}_{result['filename']}"
                            st.audio(wav_path, format="audio/wav", sample_rate=SAMPLE_RATE)
                            st.caption(f"↑ {track_name}")

                if recorder.device_b_results:
                    st.markdown("**【对比设备B】录制的音源：**")
                    for result in recorder.device_b_results:
                        if result.get("ok") and result.get("local_wav"):
                            wav_path = result["local_wav"]
                            track_name = f"{result['group']}_{result['filename']}"
                            st.audio(wav_path, format="audio/wav", sample_rate=SAMPLE_RATE)
                            st.caption(f"↑ {track_name}")

st.subheader("📝 评测进度与运行记录")
log_box = st.container(border=True)
with log_box:
    st.markdown("##### 📊 评测进度（子进程 · 五步）")
    st.caption(
        "仅 **常规模式子进程评测** 时更新：采集 → Dify 评分 → 报告等阶段；"
        "「评分计算」一行会显示上传/等模型等细粒度说明（约 **1s** 刷新）。"
        "界面所选 Gemini 等为展示名，实际以 Dify 编排为准。"
    )
    _lp_now = st.session_state.get("_eval_live_log_path")
    if _lp_now and Path(_lp_now).is_file():
        if _is_eval_running() and hasattr(st, "fragment"):
            try:

                @st.fragment(run_every=1.0)
                def _frag_live_timeline() -> None:
                    _p = st.session_state.get("_eval_live_log_path")
                    if _p:
                        _render_live_eval_timeline(_p)

                _frag_live_timeline()
            except Exception:
                _render_live_eval_timeline(_lp_now)
        else:
            _render_live_eval_timeline(_lp_now)
    elif _is_eval_running():
        st.info("▶ 正在启动子进程，评测进度条将随后显示…")
    elif _is_nisqa_only_running():
        st.info(
            "▶ **NISQA 本地客观评分进行中**… 请稍候；可点击运行区「⏹ 停止 NISQA 评分」"
            "在**下一条**文件开始前中断（当前条推理无法强行打断）。"
        )
    else:
        st.caption("当前无子进程评测进度（双设备手动评分请看页内「评分 [n/N]」状态条 + 下方运行事件日志）。")

    st.divider()
    st.markdown("##### 📋 运行事件日志")
    st.caption(
        "记录本页操作与评分结果（启动、每轨成败、报告路径、Dify Key 对齐等）。"
        "双设备手动评分时 **[Dify] 实时输出** 在页内「评分 [n/N]」状态条；"
        "本区在每轨或每轮脚本结束后刷新。"
    )
    st.checkbox(
        "展开运行事件全文",
        key="run_log_expand_details",
        help="仅影响下方「运行事件日志」：关闭为紧凑标题列表；开启可逐条展开查看完整 detail。",
    )
    _render_run_logs_box()

# ====================== 双设备模式录制按钮（在log_box定义后） ======================
if eval_mode == "dual_device":
    recorder = st.session_state.get("_dual_device_full_recorder")
    if recorder:
        # 默认同步全局设备；允许在双设备区域临时修改，避免某一步设备未在全局列表中时无法继续。
        _dut_global = (dut or "").strip()
        _ref_global = (ref or "").strip()
        if "_dual_follow_global_devices" not in st.session_state:
            st.session_state["_dual_follow_global_devices"] = True
        if "_dual_device_a_serial" not in st.session_state:
            st.session_state["_dual_device_a_serial"] = _dut_global
        if "_dual_device_b_serial" not in st.session_state:
            st.session_state["_dual_device_b_serial"] = _ref_global
        if st.session_state.get("_dual_follow_global_devices", True):
            st.session_state["_dual_device_a_serial"] = _dut_global
            st.session_state["_dual_device_b_serial"] = _ref_global

        st.markdown("##### 📱 设备配置")
        _sync_col, _follow_col, _hint_col = st.columns([1, 1, 2])
        with _sync_col:
            if st.button("同步全局设备", key="sync_dual_serials_btn", width="stretch"):
                st.session_state["_dual_device_a_serial"] = _dut_global
                st.session_state["_dual_device_b_serial"] = _ref_global
                st.rerun()
        with _follow_col:
            st.checkbox(
                "持续跟随全局",
                key="_dual_follow_global_devices",
                help="开启后双设备配置始终与侧边栏被测机/对比机保持同步。",
            )
        with _hint_col:
            st.caption("默认持续同步侧边栏全局设备；关闭“持续跟随全局”后可手动编辑。")

        device_a_serial = st.text_input(
            "被测设备A的ADB序列号",
            key="_dual_device_a_serial",
            disabled=bool(st.session_state.get("_dual_follow_global_devices", True)),
            help="默认同步全局“被测机”；可按需修改。",
        )
        device_b_serial = st.text_input(
            "对比设备B的ADB序列号",
            key="_dual_device_b_serial",
            disabled=bool(st.session_state.get("_dual_follow_global_devices", True)),
            help="默认同步全局“对比机”；可按需修改。",
        )
        
        step1_col, step2_col, clear_col = st.columns([1, 1, 1])
        
        with step1_col:
            can_record_a = bool(device_a_serial.strip()) and _has_selected_tracks
            _partial_a = getattr(recorder, "device_a_has_partial", False) or (
                bool(recorder.device_a_results) and not recorder.is_device_a_complete
            )
            _complete_a = recorder.is_device_a_complete
            if _partial_a:
                _btn_a_label = "▶ 续录【被测设备A】（从失败处继续）"
                _btn_a_help = (
                    "从上次失败的音源继续，已成功条目不会重录。"
                    "若需全部重录请使用右侧「清除重录」。"
                )
            elif _complete_a:
                _btn_a_label = "🔁 重新录制【被测设备A】"
                _btn_a_help = "将清除设备 A 的录音；若已有设备 B 录音也会一并清除后重录。"
            else:
                _btn_a_label = "📹 第一步：录制【被测设备A】"
                _btn_a_help = "需要先填写被测设备A序列号并至少勾选 1 个音源"
            if st.button(
                _btn_a_label,
                type="primary" if not _complete_a else "secondary",
                width="stretch",
                disabled=not can_record_a,
                help=_btn_a_help,
            ):
                try:
                    st.info(f"▶ 开始录制【被测设备A】 (设备: {device_a_serial})...")
                    _append_run_log("info", f"开始录制【被测设备A】({device_a_serial.strip()})")
                    if _complete_a:
                        recorder.clear_device_a_recordings()
                        if recorder.device_b_results:
                            recorder.clear_device_b_recordings()
                        st.info("🧹 已清除 A/B 历史录音，将从第一步重新录制。")
                        _append_run_log("warning", "用户选择重新录制设备 A，已清除 A/B 历史录音")
                    elif _partial_a:
                        _append_run_log(
                            "info",
                            "续录【被测设备A】",
                            "跳过已成功音源，从失败处继续",
                        )
                    # 应用麦克风配置
                    _patch_input_device(_mic_spec)
                    _patch_omnimic_gain(float(gain))
                    ok, msg = recorder.record_device_a(
                        device_serial=device_a_serial.strip(),
                        duration=float(duration),
                    )
                    if ok:
                        st.success(f"✅ 【被测设备A】录制完成！共 {len(recorder.device_a_results)} 个音源")
                        _append_run_log(
                            "success",
                            f"【被测设备A】录制完成（{len(recorder.device_a_results)} 条）",
                        )
                    else:
                        st.error(f"❌ 录制失败：{msg}")
                        _append_run_log("error", "【被测设备A】录制失败", str(msg))
                    st.rerun()
                except Exception as e:
                    st.error(f"❌ 录制异常：{e}")
                    _append_run_log("error", "【被测设备A】录制异常", str(e))
        
        with step2_col:
            can_record_b = recorder.is_device_a_complete and bool(device_b_serial.strip()) and _has_selected_tracks
            _partial_b = getattr(recorder, "device_b_has_partial", False) or (
                bool(recorder.device_b_results) and not recorder.is_device_b_complete
            )
            _complete_b = recorder.is_device_b_complete
            if _partial_b:
                _btn_b_label = "▶ 续录【对比设备B】（从失败处继续）"
                _btn_b_help = "从上次失败的音源继续；已成功条目不会重录。"
            elif _complete_b:
                _btn_b_label = "🔁 重新录制【对比设备B】"
                _btn_b_help = "仅清除设备 B 的录音后重录；设备 A 录音保留。"
            else:
                _btn_b_label = "📹 第二步：录制【对比设备B】"
                _btn_b_help = "必须先完成A录制、填写对比设备B序列号，并至少勾选 1 个音源"
            if st.button(
                _btn_b_label,
                type="primary" if (recorder.is_device_a_complete and not _complete_b) else "secondary",
                width="stretch",
                disabled=not can_record_b,
                help=_btn_b_help,
            ):
                try:
                    st.info(f"▶ 开始录制【对比设备B】 (设备: {device_b_serial})...")
                    _append_run_log("info", f"开始录制【对比设备B】({device_b_serial.strip()})")
                    if _complete_b and hasattr(recorder, "clear_device_b_recordings"):
                        recorder.clear_device_b_recordings()
                        _append_run_log("warning", "用户选择重新录制设备 B，已清除 B 历史录音")
                    elif _partial_b:
                        _append_run_log(
                            "info",
                            "续录【对比设备B】",
                            "跳过已成功音源，从失败处继续",
                        )
                    # 应用麦克风配置
                    _patch_input_device(_mic_spec)
                    _patch_omnimic_gain(float(gain))
                    ok, msg = recorder.record_device_b(
                        device_serial=device_b_serial.strip(),
                        duration=float(duration),
                    )
                    if ok:
                        st.success(f"✅ 【对比设备B】录制完成！共 {len(recorder.device_b_results)} 个音源")
                        _append_run_log(
                            "success",
                            f"【对比设备B】录制完成（{len(recorder.device_b_results)} 条）",
                        )
                    else:
                        st.error(f"❌ 录制失败：{msg}")
                        _append_run_log("error", "【对比设备B】录制失败", str(msg))
                    st.rerun()
                except Exception as e:
                    st.error(f"❌ 录制异常：{e}")
                    _append_run_log("error", "【对比设备B】录制异常", str(e))
        
        with clear_col:
            if st.button(
                "🗑️ 清除重录",
                type="secondary",
                width="stretch",
                help="删除已录制的所有音频文件，重新开始",
            ):
                recorder.clear_recordings()
                st.info("🗑️ 已清除所有录制文件")
                _append_run_log("warning", "已清除双设备录制结果")
                st.rerun()

if stop_dual_eval:
    st.session_state["_dual_eval_stop_requested"] = True
    _append_run_log("warning", "已请求停止双设备手动测评")
    st.rerun()

# 不在双设备 Dify 评分期间每秒全页 st.rerun()：会与长时间同步 HTTP 冲突，且阻塞时界面无法刷新。

if _is_eval_running():
    log_box.info("▶ 评测子进程运行中… 可点击「停止测试」中断。")
    if not st.session_state.get("_run_proc_logged", False):
        _append_run_log("info", "评测子进程运行中")
        st.session_state["_run_proc_logged"] = True
    if hasattr(st, "fragment"):
        try:

            @st.fragment(run_every=2)
            def _poll_eval_done() -> None:
                p = st.session_state.get("_eval_popen")
                if p is not None and p.poll() is not None:
                    st.rerun()

            _poll_eval_done()
        except Exception:
            st.caption("若评测结束后界面未刷新，请点击页面空白处或按 F5。")
    else:
        st.caption("评测进行中… 结束后若界面未更新，请点击页面或刷新浏览器。")
else:
    st.session_state.pop("_run_proc_logged", None)

_err_msg = st.session_state.pop("_eval_error_msg", None)
if _err_msg:
    log_box.error(_err_msg)
    _append_run_log("error", "评测失败", str(_err_msg))
_mm_warn = st.session_state.pop("_eval_multi_model_warn", None)
if _mm_warn:
    log_box.warning(_mm_warn)
    _append_run_log("warning", "多模型追加报告", str(_mm_warn))

_pay = st.session_state.get("_eval_success_payload")
if _pay and not _is_eval_running() and not _is_nisqa_only_running():
    if _pay.get("mode") == "nisqa_only":
        from web_ui_nisqa_only import render_nisqa_only_report

        render_nisqa_only_report(_pay, log_box=log_box)
    else:
        _render_eval_results(
            dut_s=_pay["dut_s"],
            ref_s=_pay["ref_s"],
            mic_pick=_pay["mic_pick"],
            export_format=_pay["export_format"],
            report_path=_pay["report_path"],
            score_json=_pay["score_json"],
            log_box=log_box,
            extra_model_reports=_pay.get("extra_model_reports"),
            eval_models=_pay.get("eval_models"),
            multi_model_consistency_report=_pay.get("multi_model_consistency_report"),
        )


if stop_eval:
    try:
        _p = st.session_state.get("_eval_popen")
        if _p is not None and _p.poll() is None:
            _p.terminate()
            try:
                _p.wait(timeout=15)
            except subprocess.TimeoutExpired:
                _p.kill()
    finally:
        for _k in ("_eval_cfg_path", "_eval_out_path"):
            _tp = st.session_state.pop(_k, None)
            try:
                if _tp and Path(_tp).is_file():
                    Path(_tp).unlink()
            except Exception:
                pass
        _ll = st.session_state.pop("_eval_live_log_path", None)
        try:
            if _ll and Path(_ll).is_file():
                Path(_ll).unlink(missing_ok=True)
        except Exception:
            pass
        st.session_state.pop("_eval_popen", None)
        log_box.warning("已停止评测（子进程已结束；未完成时通常无有效报告）。")
        _append_run_log("warning", "已停止评测（子进程结束）")
    st.rerun()

if start_nisqa_only:
    st.session_state["_nisqa_only_running"] = True
    st.session_state["_nisqa_only_pending"] = True
    st.session_state.pop("_nisqa_only_cancel", None)
    st.session_state.pop("_eval_success_payload", None)
    _append_run_log("info", "已启动仅 NISQA 本地客观评分")
    st.rerun()

if stop_nisqa_only:
    st.session_state["_nisqa_only_cancel"] = True
    st.session_state["_nisqa_only_running"] = False
    st.session_state.pop("_nisqa_only_pending", None)
    log_box.warning("⏹ 已请求停止 NISQA 评分（下一条开始前生效）。")
    _append_run_log("warning", "用户停止 NISQA 仅客观评分")
    st.rerun()

if (
    st.session_state.get("_nisqa_only_running")
    and st.session_state.get("_nisqa_only_pending")
    and not st.session_state.get("_nisqa_only_cancel")
):
    from web_ui_nisqa_only import execute_nisqa_only_run

    _dual_rec_nisqa = (
        st.session_state.get("_dual_device_full_recorder")
        or st.session_state.get("_dual_device_recorder")
        if eval_mode == "dual_device"
        else None
    )
    st.session_state.pop("_nisqa_only_pending", None)
    try:
        if hasattr(st, "status"):
            with st.status("NISQA 本地客观评分进行中…", expanded=True) as _nisqa_status:
                _nisqa_payload = execute_nisqa_only_run(
                    eval_mode=eval_mode,
                    selected_audio_rel_paths=selected_audio_rel_paths,
                    dual_recorder=_dual_rec_nisqa,
                    log=lambda msg: _append_run_log("info", msg),
                    should_cancel=lambda: bool(
                        st.session_state.get("_nisqa_only_cancel")
                    ),
                )
                _nisqa_status.update(label="NISQA 评分完成", state="complete")
        else:
            with st.spinner("NISQA 本地客观评分进行中…"):
                _nisqa_payload = execute_nisqa_only_run(
                    eval_mode=eval_mode,
                    selected_audio_rel_paths=selected_audio_rel_paths,
                    dual_recorder=_dual_rec_nisqa,
                    log=lambda msg: _append_run_log("info", msg),
                    should_cancel=lambda: bool(
                        st.session_state.get("_nisqa_only_cancel")
                    ),
                )
        st.session_state["_eval_success_payload"] = {
            "mode": "nisqa_only",
            "nisqa_payload": _nisqa_payload,
            "eval_mode": eval_mode,
            "dut_s": (dut or "").strip() or "—",
            "ref_s": (ref or "").strip() or "—",
            "mic_pick": mic_pick,
            "export_format": export_format,
        }
        st.session_state["nisqa_only_payload"] = _nisqa_payload
        st.session_state["_nisqa_only_running"] = False
        st.session_state.pop("_nisqa_only_cancel", None)
        summ = _nisqa_payload.get("summary") or {}
        log_box.success(
            f"✅ NISQA 仅客观评分完成：{summ.get('ok', 0)}/{summ.get('total', 0)} 条"
        )
        _append_run_log(
            "success",
            "NISQA 仅客观评分完成",
            f"json={_nisqa_payload.get('output_json')}",
        )
        st.rerun()
    except Exception as _nisqa_exc:
        st.session_state["_nisqa_only_running"] = False
        st.session_state.pop("_nisqa_only_cancel", None)
        log_box.error(f"NISQA 评分失败：{_nisqa_exc}")
        _append_run_log("error", "NISQA 仅客观评分失败", str(_nisqa_exc))

if start:
    try:
        if not _has_selected_tracks:
            raise ValueError("请先在侧边栏勾选至少 1 个音源。")
        dut_s = (dut or "").strip()
        ref_s = (ref or "").strip()
        if not dut_s or not ref_s:
            raise ValueError("请填写被测机与对比机序列号（ADB serial）。")
        if dut_s == ref_s:
            raise ValueError("被测机与对比机不能为同一序列号。")
        if _provider_id == "dify" and not (dify_user or "").strip():
            raise ValueError(
                "请填写侧栏「DIFY_USER」：须为贵司 Dify 认可的终端用户标识（常见为**企业邮箱**）；"
                "留空易导致上传/评分返回 **User Not Exists**（HTTP 400）。"
            )
        if _provider_id == "seedpace" and not (seedpace_api or "").strip():
            raise ValueError("请选择 Seedpace Gateway 时填写 SEEDPACE_API_KEY。")

        log_box.info("▶ 麦克风与 AI 评分接口配置…")
        _append_run_log("info", f"开始常规模式评测：评分接口={_provider_id}")
        _patch_input_device(_mic_spec)
        _apply_dify_env(dify_api, dify_user)
        from web_ui_dify_model_keys import (
            configure_api_key_for_model,
            describe_current_key_for_model,
            set_dify_api_key_baseline,
        )

        set_dify_api_key_baseline((dify_api or "").strip())
        _sel = _primary_eval_model_name()
        if _provider_id == "dify":
            configure_api_key_for_model(_sel)
        os.environ["SPEAKER_EVAL_MODEL_NAME"] = _sel
        if _provider_id == "dify":
            _append_run_log("info", "Dify Key 与模型对齐", describe_current_key_for_model(_sel))
        else:
            from seedpace_audio_client import (
                normalize_seedpace_api_key,
                normalize_seedpace_api_url,
                seedpace_model_name,
            )

            os.environ["SEEDPACE_MODEL"] = seedpace_model_name(_sel)
            os.environ["SEEDPACE_API_KEY"] = normalize_seedpace_api_key(seedpace_api or "")
            os.environ["SEEDPACE_API_URL"] = normalize_seedpace_api_url(seedpace_api_url or "")
            _append_run_log("info", "Seedpace 模型与 Key", f"model={os.environ['SEEDPACE_MODEL']!r}；key 长度={len(os.environ['SEEDPACE_API_KEY'])}")

        if not _WORKER.is_file():
            raise FileNotFoundError(f"缺少子进程脚本: {_WORKER}")

        _cfg_f = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".json", delete=False)
        _old_lp = st.session_state.pop("_eval_live_log_path", None)
        if _old_lp:
            try:
                Path(_old_lp).unlink(missing_ok=True)
            except Exception:
                pass
        _live_f = tempfile.NamedTemporaryFile(mode="wb", suffix=".jsonl", delete=False)
        _live_f.close()
        _live_path = _live_f.name
        st.session_state["_eval_live_log_path"] = _live_path

        _eval_env: dict[str, str] = {
            "SPEAKER_SELECTED_TRACKS_JSON": json.dumps(
                selected_audio_rel_paths, ensure_ascii=False
            ),
            "SPEAKER_PLAY_FULL_TRACK": "1" if play_full_track else "0",
            "DIFY_UPLOAD_MAX_AUDIO_SECONDS": str(int(dify_upload_max_sec)),
            "SPEAKER_LLM_PROVIDER": _provider_id,
            # 与侧边栏「单机 · 绝对分」及 web_ui_prompt_overrides.json 中 scoring_query 一致，逐条 WAV 打 1～10，不做双路刺激比较差分。
            "SPEAKER_WEB_UI_REGULAR_USE_SINGLE_PROMPTS": "1",
        }
        if bool(st.session_state.get("nisqa_enabled")):
            _eval_env["SPEAKER_NISQA_ENABLED"] = "1"
        if _sel:
            _eval_env["SPEAKER_EVAL_MODEL_NAME"] = _sel
        if _provider_id == "seedpace":
            _eval_env["SEEDPACE_MODEL"] = os.environ.get("SEEDPACE_MODEL", "")
            _eval_env["SEEDPACE_API_KEY"] = (seedpace_api or "").strip()
            _eval_env["SEEDPACE_API_URL"] = (seedpace_api_url or "").strip()

        json.dump(
            {
                "dut_serial": dut_s,
                "ref_serial": ref_s,
                "gain_db": float(gain),
                "duration": int(duration),
                "live_log_path": _live_path,
                "selected_tracks": selected_audio_rel_paths,
                "env": _eval_env,
                "dify_api_key_baseline": (dify_api or "").strip(),
                # 与侧栏「模型→专钥」映射一致，子进程不依赖继承父进程环境时的偶然顺序
                "dify_api_key_resolved": (os.environ.get("DIFY_API_KEY") or "").strip(),
            },
            _cfg_f,
            ensure_ascii=False,
        )
        _cfg_f.close()
        _out_f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        _out_f.close()

        _popen_kw: dict = {"cwd": str(_ROOT), "env": os.environ.copy()}
        if sys.platform == "win32":
            _popen_kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

        _proc = subprocess.Popen(
            [sys.executable, str(_WORKER), _cfg_f.name, _out_f.name],
            **_popen_kw,
        )
        st.session_state["_eval_popen"] = _proc
        st.session_state["_eval_cfg_path"] = _cfg_f.name
        st.session_state["_eval_out_path"] = _out_f.name
        st.session_state["_eval_ctx"] = {
            "mic_pick": mic_pick,
            "export_format": export_format,
            "dut_s": dut_s,
            "ref_s": ref_s,
            "eval_models": list(_all_eval_models_for_payload()),
            "dify_api_baseline": (dify_api or "").strip(),
        }
        st.session_state.pop("_eval_success_payload", None)
        st.session_state.pop("_eval_error_msg", None)
        st.session_state.pop("_nisqa_only_running", None)
        st.session_state.pop("_nisqa_only_pending", None)
        st.session_state.pop("_nisqa_only_cancel", None)
        log_box.info("▶ 已启动子进程评测（可点击「停止测试」中断）…")
        _append_run_log("info", "已启动评测子进程")
        st.rerun()
    except Exception as e:
        log_box.error(f"错误：{str(e)}")
        _append_run_log("error", "常规模式启动失败", str(e))

# ====================== 双设备模式测评逻辑（可中断） ======================
# 会话由「手动开始测评」按钮处 _try_prepare_dual_device_eval_session 启动，不再依赖页尾延迟执行。

if eval_mode == "dual_device" and st.session_state.get("_dual_eval_running", False):
    try:
        if st.session_state.get("_dual_eval_stop_requested", False):
            st.session_state["_dual_eval_running"] = False
            st.session_state.pop("_dual_eval_state", None)
            st.session_state.pop("_dual_eval_cursor", None)
            st.session_state.pop("_dual_eval_scorer", None)
            st.session_state.pop("_dual_eval_stop_requested", None)
            log_box.warning("⏹ 已停止双设备手动测评。")
            _append_run_log("warning", "双设备手动测评已停止")
            st.rerun()

        state = dict(st.session_state.get("_dual_eval_state") or {})
        paired_audios = list(state.get("paired_audios") or [])
        cursor = int(st.session_state.get("_dual_eval_cursor", state.get("cursor") or 0))
        merged_tracks = list(state.get("merged_tracks") or [])
        parsed_list = list(state.get("parsed_list") or [])

        if cursor < len(paired_audios):
            from dual_device_scoring import DualDeviceScorer
            from web_ui_dify_model_keys import (
                configure_api_key_for_model,
                describe_current_key_for_model,
                set_dify_api_key_baseline,
            )

            set_dify_api_key_baseline(str(st.session_state.get("_dify_api_baseline_for_eval") or "").strip())
            _sync_sel = _primary_eval_model_name()
            os.environ["SPEAKER_EVAL_MODEL_NAME"] = _sync_sel
            os.environ["SPEAKER_LLM_PROVIDER"] = _provider_id
            if _provider_id == "dify":
                configure_api_key_for_model(_sync_sel)
            else:
                from seedpace_audio_client import (
                    normalize_seedpace_api_key,
                    normalize_seedpace_api_url,
                    seedpace_model_name,
                )

                os.environ["SEEDPACE_MODEL"] = seedpace_model_name(_sync_sel)
                os.environ["SEEDPACE_API_KEY"] = normalize_seedpace_api_key(seedpace_api or "")
                os.environ["SEEDPACE_API_URL"] = normalize_seedpace_api_url(seedpace_api_url or "")
            if cursor == 0:
                if _provider_id == "dify":
                    _append_run_log(
                        "info",
                        "Dify Key 与模型对齐",
                        describe_current_key_for_model(_sync_sel),
                    )
                else:
                    _append_run_log(
                        "info",
                        "Seedpace 模型与 Key",
                        f"model={os.environ.get('SEEDPACE_MODEL', '')!r}；key 长度={len(os.environ.get('SEEDPACE_API_KEY', ''))}",
                    )

            def _extract_effective_parsed(d: dict) -> dict:
                p = d if isinstance(d, dict) else {}
                ans = p.get("answer")
                if isinstance(ans, dict):
                    return ans
                return p

            # 每轨单独 rerun；用 st.status 实时刷日志（spinner 会拖到 Dify 结束才刷新，易误判「卡在评分日志」）。
            chunk_size = 1
            processed = 0
            while cursor < len(paired_audios) and processed < chunk_size:
                if st.session_state.get("_dual_eval_stop_requested", False):
                    break
                track_name, audio_a_path, audio_b_path = paired_audios[cursor]
                _append_run_log("info", f"开始评分 [{cursor + 1}/{len(paired_audios)}]: {track_name}")
                _status_label = (
                    f"评分 [{cursor + 1}/{len(paired_audios)}] {track_name}（上传 → Dify → 解析）"
                )
                with st.status(_status_label, expanded=True) as _track_st:
                    scorer = st.session_state.get("_dual_eval_scorer")
                    if scorer is None:
                        scorer = DualDeviceScorer(log=_dual_track_live_log(_track_st))
                        st.session_state["_dual_eval_scorer"] = scorer
                    else:
                        scorer.log = _dual_track_live_log(_track_st)
                    try:
                        _, result = scorer.score_dual_device_comparison(
                            audio_a_path=audio_a_path,
                            audio_b_path=audio_b_path,
                            device_a_label="被测设备A",
                            device_b_label="对比设备B",
                            stimulus_label=track_name,
                            persist_analysis=False,
                        )
                    except Exception as _track_exc:
                        result = {"ok": False, "error": str(_track_exc)}
                        _dual_track_live_log(_track_st)(
                            f"❌ 本轨异常：{_track_exc}"
                        )
                    try:
                        _track_st.update(
                            label=f"已完成 [{cursor + 1}/{len(paired_audios)}] {track_name}",
                            state="complete",
                            expanded=False,
                        )
                    except Exception:
                        pass

                track_row = {}
                if isinstance(result, dict):
                    tracks = result.get("tracks") or []
                    if tracks and isinstance(tracks[0], dict):
                        track_row = dict(tracks[0])
                track_ok = bool(track_row.get("ok"))
                parsed_eff = _extract_effective_parsed(track_row.get("parsed") or {})
                if (not track_ok) and parsed_eff:
                    track_ok = any(
                        k in parsed_eff for k in ("声音响度", "人声清晰度", "听感舒适度", "失真与噪声", "频响平衡")
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
                    _apply_nisqa_to_track_row(track_row)
                    merged_tracks.append(track_row)
                    if parsed_eff:
                        parsed_list.append(parsed_eff)
                    _append_run_log("success", f"{track_name} 评分完成")
                else:
                    err_msg = None
                    if isinstance(result, dict):
                        err_msg = result.get("error")
                    _append_run_log("error", f"{track_name} 评分失败", str(err_msg or "未返回可解析评分结果"))
                cursor += 1
                processed += 1

            state["cursor"] = cursor
            state["merged_tracks"] = merged_tracks
            state["parsed_list"] = parsed_list
            st.session_state["_dual_eval_cursor"] = cursor
            st.session_state["_dual_eval_state"] = state
            # 主动续跑下一批，避免依赖 fragment 定时刷新导致“点击后不推进”。
            # 最后一轨完成后 cursor==len，本轮仍在本分支内，无法进入下方的收尾 else；必须 rerun 后下一帧才会合并/生成报告。
            if st.session_state.get("_dual_eval_running", False):
                st.rerun()
        else:
            from config import ANALYSIS_DIR
            from run_all import _write_web_ui_score_json
            from web_ui_multi_model_reports import (
                append_dual_device_model_reports,
                sanitize_model_tag,
                write_multi_model_consistency_report,
            )

            _append_run_log(
                "info",
                f"全部 {len(paired_audios)} 轨 Dify 评分已结束，正在合并 analysis 并生成报告…",
            )
            log_box.info(
                f"📄 正在生成总报告（{len(merged_tracks)} 轨有效结果），Word 生成可能需数十秒…"
            )

            _paired_snapshot = list(state.get("paired_audios") or [])
            emods_dd = _dedupe_models_preserve_order(
                list(st.session_state.pop("_dual_eval_models", None) or _all_eval_models_for_payload())
            )
            _ser_a = (st.session_state.get("_dual_device_a_serial") or dut or "").strip()
            _ser_b = (st.session_state.get("_dual_device_b_serial") or ref or "").strip()
            _dut_label = (dut or "").strip() or "被测设备A"
            _ref_label = (ref or "").strip() or "对比设备B"

            _safe_tag = datetime.now().strftime("dual_webui_%Y%m%d_%H%M%S")
            extra_dd: list[dict[str, str]] = []
            multi_warn: str | None = None
            md_path: str = ""
            score_json_path: str = ""

            if not merged_tracks:
                if len(emods_dd) > 1 and _paired_snapshot:
                    _failed_first = emods_dd[0] or "（首个模型）"
                    _append_run_log(
                        "warning",
                        "双设备·首模型全失败",
                        f"列表首项「{_failed_first}」在所有音轨上均未成功；"
                        f"将按顺序对其余 {len(emods_dd) - 1} 个模型分别请求 Dify（每模型独立密钥）。"
                        "若日志为 User Not Exists，请把侧栏「DIFY_USER」改为贵司 Dify 认可的账号（常见为企业邮箱）。",
                    )
                    recov: list[dict[str, str]] = []
                    with st.status(
                        f"首模型全部失败：正在为其余 {len(emods_dd) - 1} 个模型请求 Dify（多轨；"
                        "请展开本状态查看实时进度；下方「运行事件日志」在本阶段结束后才会刷新）",
                        expanded=True,
                    ) as _mm_st:
                        try:
                            recov = append_dual_device_model_reports(
                                paired_audios=_paired_snapshot,
                                extra_models=emods_dd[1:],
                                dut_label=_ser_a,
                                ref_label=_ser_b,
                                analysis_base_stem=_safe_tag,
                                log=_dual_multimodel_st_log(_mm_st),
                                dify_api_key_baseline=str(
                                    st.session_state.get("_dify_api_baseline_for_eval") or ""
                                ),
                            )
                        except Exception as ex:
                            import traceback

                            _append_run_log("error", "双设备·首失败后追加模型异常", traceback.format_exc())
                            _append_run_log("warning", "双设备·追加模型", str(ex))
                            try:
                                _mm_st.update(
                                    label="追加模型阶段出错（已记入运行事件日志）",
                                    state="error",
                                    expanded=True,
                                )
                            except Exception:
                                pass
                            recov = []
                    if not recov:
                        raise RuntimeError("所有音源评分均失败")
                    primary = recov[0]
                    md_path = str(primary.get("markdown") or "")
                    score_json_path = str(primary.get("score_json") or "")
                    extra_dd = recov[1:]
                    multi_warn = (
                        f"首个模型「{_failed_first}」全部音轨失败，已用后续模型中**第一个成功**的结果作为主报告；"
                        "其余成功模型见「追加报告」。请确认各模型 Key 与「DIFY_USER」有效。"
                    )
                else:
                    raise RuntimeError("所有音源评分均失败")
            else:
                _pmain = (_primary_eval_model_name() or "").strip()
                _ptag = sanitize_model_tag(_pmain) if _pmain else ""
                _main_mid = f"_main__{_ptag}" if _ptag else "_main"
                merged_analysis_path = ANALYSIS_DIR / f"analysis_{_safe_tag}{_main_mid}.json"
                score_json_p = ANALYSIS_DIR / f"web_ui_scores_{_safe_tag}.json"

                merged_payload = {
                    "session_tag": _safe_tag,
                    "comparison_mode": True,
                    "scoring_rule_set": "pairwise_minus3_to_plus3_dual_device_stepwise",
                    "devices": [
                        {"slot": "d01", "label": "被测设备A", "serial": _ser_a},
                        {"slot": "d02", "label": "对比设备B", "serial": _ser_b},
                    ],
                    "tracks": merged_tracks,
                }
                if _pmain:
                    merged_payload["eval_model"] = _pmain
                    os.environ["SPEAKER_EVAL_MODEL_NAME"] = _pmain
                try:
                    from nisqa_local import analysis_objective_meta, is_available, is_enabled

                    if is_enabled() and is_available():
                        merged_payload["objective_scoring"] = analysis_objective_meta()
                except Exception:
                    pass
                merged_analysis_path.write_text(
                    json.dumps(merged_payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                _append_run_log("info", f"已写入 {merged_analysis_path.name}")

                _write_web_ui_score_json(merged_analysis_path, score_json_p)

                from report_builder import build_word_from_analysis

                _append_run_log("info", "正在生成 Word / Markdown 报告（可能需数十秒）…")
                _, md_path_obj, _, _, msg = build_word_from_analysis(
                    merged_analysis_path,
                    test_name="双设备单麦对比测评",
                    test_device=_dut_label,
                    ref_device=_ref_label,
                )
                md_path = str(md_path_obj) if md_path_obj else ""
                score_json_path = str(score_json_p)
                _append_run_log("info", "报告生成结果", str(msg))

                if len(emods_dd) > 1 and _paired_snapshot:
                    _append_run_log(
                        "info",
                        "多模型追加评分（双设备）",
                        f"主报告已生成；将按所选顺序，为另外 {len(emods_dd) - 1} 个模型**依次**对 {len(_paired_snapshot)} 条配对音轨逐轨请求 Dify。"
                        "单模型异常会跳过并继续下一模型。请展开下方状态查看实时进度；运行事件日志在本阶段结束后刷新。",
                    )
                    try:
                        with st.status(
                            f"正在为其余 {len(emods_dd) - 1} 个评测模型生成双设备报告（多轨 Dify；"
                            "请展开查看每条轨/每个模型的进度）",
                            expanded=True,
                        ) as _mm_st2:
                            extra_dd = append_dual_device_model_reports(
                                paired_audios=_paired_snapshot,
                                extra_models=emods_dd[1:],
                                dut_label=_ser_a,
                                ref_label=_ser_b,
                                analysis_base_stem=_safe_tag,
                                log=_dual_multimodel_st_log(_mm_st2),
                                dify_api_key_baseline=str(
                                    st.session_state.get("_dify_api_baseline_for_eval") or ""
                                ),
                            )
                        _dd_exp = len([x for x in (emods_dd[1:] or []) if str(x).strip()])
                        if _dd_exp and len(extra_dd) < _dd_exp:
                            _dmiss = _dd_exp - len(extra_dd)
                            _dw = (
                                f"双设备多模型追加：预期 {_dd_exp} 个，实际完成 {len(extra_dd)} 个，"
                                f"有 {_dmiss} 个未生成报告（详见运行事件日志）。"
                            )
                            _append_run_log("warning", "多模型追加（双设备）·部分失败", _dw)
                    except Exception as ex:
                        import traceback

                        _append_run_log("error", "额外模型报告（双设备）异常", traceback.format_exc())
                        _append_run_log("warning", "额外模型报告（双设备）", str(ex))

            _dual_pay: dict = {
                "dut_s": _dut_label,
                "ref_s": _ref_label,
                "mic_pick": mic_pick,
                "export_format": export_format,
                "report_path": md_path,
                "score_json": score_json_path,
                "eval_models": list(emods_dd),
            }
            if extra_dd:
                _dual_pay["extra_model_reports"] = extra_dd
            if extra_dd:
                try:
                    cons_dd = write_multi_model_consistency_report(
                        primary_score_json=score_json_path,
                        extra_reports=extra_dd,
                        primary_model=str((emods_dd[0] if emods_dd else "") or "").strip() or "主模型",
                        log=_append_run_log,
                    )
                    if cons_dd:
                        _dual_pay["multi_model_consistency_report"] = cons_dd
                except Exception as ex:
                    _append_run_log("warning", "多模型一致性统计生成失败（双设备）", str(ex))
            if multi_warn:
                st.session_state["_eval_multi_model_warn"] = multi_warn
            st.session_state["_eval_success_payload"] = _dual_pay
            st.session_state["_dual_eval_running"] = False
            st.session_state.pop("_dual_eval_state", None)
            st.session_state.pop("_dual_eval_cursor", None)
            st.session_state.pop("_dual_eval_scorer", None)
            st.session_state.pop("_dual_eval_stop_requested", None)
            _append_run_log("success", "双设备对比测评完成")
            st.rerun()
    except Exception as e:
        st.session_state["_dual_eval_running"] = False
        st.session_state.pop("_dual_eval_state", None)
        st.session_state.pop("_dual_eval_cursor", None)
        st.session_state.pop("_dual_eval_scorer", None)
        st.session_state.pop("_dual_eval_stop_requested", None)
        log_box.error(f"❌ 双设备测评失败：{str(e)}")
        _append_run_log("error", "双设备测评失败", str(e))
