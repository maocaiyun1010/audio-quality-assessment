# -*- coding: utf-8 -*-
"""
一键离线评测流水线：采集 → Dify 评分 → Word/Markdown/TSV/Excel。

与 CLI 解耦，便于测试与 HTTP 服务复用同一编排。
"""
from __future__ import annotations

import logging
import os
import traceback
from typing import Callable, Optional, Sequence

from speaker_eval.adapters.audio import get_record_tool
from speaker_eval.settings import ensure_output_dirs

from live_eval_log import append_live_step
from report_builder import build_word_from_analysis
from scoring import score_recorded_session
from sync_capture import run_multi_device_capture


def run_evaluation_pipeline(
    serials: list[str],
    session_tag: str,
    *,
    test_device_name: str,
    dev_summary: str,
    record_tool: str | None,
    role_labels: Optional[Sequence[str]],
    user_print: Callable[[str], None],
    logger: logging.Logger,
) -> tuple[int, str]:
    """
    返回 (进程退出码, 说明文本)。

    退出码：0 成功；1 未捕获异常；2 无有效录音；3 评分失败。
    非 0 时第二项为可展示给用户的简要原因（含采集摘要或各轨 message）。
    """
    ensure_output_dirs()
    logger.info(
        "流水线开始 session=%s devices=%s record_tool=%s",
        session_tag,
        len(serials),
        get_record_tool(record_tool),
    )

    try:

        def cap_log(m: str) -> None:
            user_print(m)

        items, cap_msg = run_multi_device_capture(
            serials,
            session_tag,
            log=cap_log,
            device_role_labels=role_labels,
            record_tool=record_tool,
        )
        user_print("\n采集结束: " + cap_msg)
        for it in items:
            tag = f"[{it.device_slot}]" if it.device_slot else ""
            user_print(
                f"  {'OK ' if it.ok else 'FAIL'} {tag} {it.group} {it.filename} -> {it.local_wav}"
            )

        if not any(it.ok for it in items):
            logger.error("采集阶段无有效录音，终止评分")
            append_live_step(
                "error",
                "采集失败",
                (cap_msg or "无有效录音")[:800],
            )
            user_print("无有效录音，终止后续评分。")
            detail_lines: list[str] = [f"采集阶段摘要: {cap_msg}"]
            if not items:
                detail_lines.append(
                    "未产生任何采集条目。常见原因：① ADB 序列号错误或设备未在线；② 向手机推送音源失败（USB/授权）；"
                    "③ assets/test_audio 下无可用音源文件。"
                )
            else:
                detail_lines.append("失败条目（message）:")
                for it in items:
                    if not it.ok:
                        detail_lines.append(
                            f"  - [{it.device_slot or '?'}] {it.group} / {it.filename}: "
                            f"{(it.message or '').strip() or '无详情'}"
                        )
            return 2, "\n".join(detail_lines)

        append_live_step(
            "audio",
            "音频接收",
            f"已生成有效录音 {sum(1 for x in items if x.ok)} 条，准备提交 Dify。",
        )
        append_live_step(
            "scoring",
            "评分计算",
            "上传附件并等待 Dify/模型返回（进度见下方运行日志）…",
        )
        apath, smsg = score_recorded_session(session_tag, device_label=test_device_name, log=cap_log)
        user_print("\n评分结束: " + smsg)
        if not apath:
            logger.error("评分未产出 analysis: %s", smsg)
            append_live_step("error", "评分失败", (smsg or "未产出 analysis JSON")[:800])
            return 3, (smsg or "评分未产出 analysis JSON")

        append_live_step(
            "report",
            "结论生成",
            "汇总逐条结果并生成 Word / Markdown / 表格…",
        )
        _regular_single = (os.environ.get("SPEAKER_WEB_UI_REGULAR_USE_SINGLE_PROMPTS") or "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        multi = len(serials) > 1 and not _regular_single
        doc, md, tsv, xlsx, rmsg = build_word_from_analysis(
            apath,
            test_device=test_device_name,
            ref_device=dev_summary,
            test_name="喇叭音效 AI 辅助评测（多设备刺激比较）" if multi else "喇叭音效 AI 辅助评测",
        )
        user_print("\n报告: " + rmsg)
        user_print("  Word: " + str(doc))
        user_print("  Markdown: " + str(md))
        user_print("  TSV(粘贴): " + str(tsv))
        user_print("  Excel表格: " + str(xlsx))
        logger.info("流水线正常结束 session=%s", session_tag)
        append_live_step("complete", "完成状态", "流水线已成功结束。")
        return 0, ""
    except Exception as exc:  # noqa: BLE001
        logger.exception("评测流水线异常终止: %s", exc)
        append_live_step("error", "流水线异常", f"{type(exc).__name__}: {exc}"[:800])
        user_print("未捕获异常已兜底打印：", flush=True)
        traceback.print_exc()
        return 1, f"{type(exc).__name__}: {exc}"


def resolve_role_labels_for_serials(
    serials: list[str],
    *,
    skip_confirm: bool,
    clog: Callable[[str], None],
) -> tuple[list[str] | None, list[str] | None]:
    """
    多机时交互确认 DUT/REF；返回 (新 serials 列表, role_labels) 或取消时 (None, None)。
    """
    from device_roles import confirm_dut_ref_interactive, labels_for_slot_count

    if len(serials) < 2:
        return serials, None
    ordered = confirm_dut_ref_interactive(list(serials), skip=skip_confirm, log=clog)
    if ordered is None:
        return None, None
    return ordered, labels_for_slot_count(len(ordered))
