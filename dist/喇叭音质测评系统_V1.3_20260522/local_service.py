# -*- coding: utf-8 -*-
"""
本地 HTTP 服务：供 Dify 工作流「HTTP 请求」节点调用（本机直连，无 Agent / 无 Docker）。

启动：python local_service.py（或由根目录「启动程序.bat」菜单选择 HTTP 服务）。

POST /api/v1/run_eval  JSON 示例：
  {"session_tag": "EVT_001", "run_capture": true, "run_scoring": true, "run_report": true}
  不传 device_serial / device_serials 时，自动对 adb devices 中 **全部** device 评测。

指定多台：
  {"device_serials": ["SN1", "SN2"], "session_tag": "EVT_001"}
  报告除 Word 外会生成 Markdown、TSV 与 **Excel 汇总表（.xlsx，表格模式）**：
  响应字段 ``report_markdown``、``report_tsv``、``report_xlsx``。
多机时可选 ``dut_serial`` + ``ref_serial``（须同时出现在 device_serials 或枚举结果中），将二者重排到列表最前作为 d01/d02。
或单台（兼容）：
  {"device_serial": "SN1", "session_tag": "EVT_001"}
  可选 ``record_tool``: ``sounddevice``（本机）或 ``omnimic``（专业：默认同上；可选 ``SPEAKER_OMNIMIC_EXE`` 走外部程序）。
"""
from __future__ import annotations

import os
import traceback
from datetime import datetime
from typing import Any, Optional

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from config import SERVICE_HOST, SERVICE_PORT, ensure_output_dirs
from device_roles import labels_for_slot_count, reorder_serials_by_serial
from report_builder import build_word_from_analysis
from scoring import score_recorded_session
from sync_capture import list_connected_adb_devices, run_multi_device_capture

app = FastAPI(title="Speaker AI Eval Local API", version="1.0")


def _service_token() -> str:
    return (os.environ.get("SPEAKER_SERVICE_TOKEN") or "").strip()


def _debug_traceback_enabled() -> bool:
    return (os.environ.get("SPEAKER_SERVICE_DEBUG_TRACEBACK") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _assert_authorized(x_speaker_eval_token: Optional[str]) -> None:
    token = _service_token()
    if not token:
        return
    if (x_speaker_eval_token or "").strip() != token:
        raise HTTPException(status_code=401, detail="missing or invalid X-Speaker-Eval-Token")


class EvalRequest(BaseModel):
    device_serial: Optional[str] = Field(
        default=None,
        description="单台序列号；与 device_serials 二选一",
    )
    device_serials: Optional[list[str]] = Field(
        default=None,
        description="多台序列号；为空且未传 device_serial 则用 adb 全部 device",
    )
    session_tag: str = Field(
        default_factory=lambda: datetime.now().strftime("sess_%Y%m%d_%H%M%S"),
        description="会话标签，用于输出文件前缀",
    )
    run_capture: bool = True
    run_scoring: bool = True
    run_report: bool = True
    test_device_name: str = "被测设备"
    ref_device_name: str = "参考机（可选）"
    test_name: str = "喇叭音效 AI 辅助评测"
    dut_serial: Optional[str] = Field(
        default=None,
        description="多机时被测机序列号；与 ref_serial 同时传入则重排为 DUT→REF→其余",
    )
    ref_serial: Optional[str] = Field(
        default=None,
        description="多机时对比机序列号；与 dut_serial 配对使用",
    )
    record_tool: Optional[str] = Field(
        default=None,
        description="录音：sounddevice=本机；omnimic=OmniMic专业（默认同环境变量 SPEAKER_RECORD_TOOL）",
    )


def _resolve_serials(body: EvalRequest) -> list[str]:
    if body.device_serials:
        return [s.strip() for s in body.device_serials if s and str(s).strip()]
    if body.device_serial and str(body.device_serial).strip():
        return [str(body.device_serial).strip()]
    return list_connected_adb_devices()


@app.on_event("startup")
def _startup() -> None:
    ensure_output_dirs()
    if str(SERVICE_HOST).strip() not in ("127.0.0.1", "localhost", "::1") and not _service_token():
        print(
            "[local_service] 警告：HTTP 服务监听非 localhost 且未设置 SPEAKER_SERVICE_TOKEN，"
            "请勿暴露到公网或不可信网络。",
            flush=True,
        )


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "service": "speaker-ai-eval"}


@app.post("/api/v1/run_eval")
def run_eval(
    body: EvalRequest,
    x_speaker_eval_token: Optional[str] = Header(default=None, alias="X-Speaker-Eval-Token"),
) -> dict[str, Any]:
    _assert_authorized(x_speaker_eval_token)
    log_lines: list[str] = []

    def log(msg: str) -> None:
        log_lines.append(msg)

    result: dict[str, Any] = {
        "ok": True,
        "session_tag": body.session_tag,
        "device_serials": [],
        "recorded": [],
        "analysis_json": None,
        "report_docx": None,
        "messages": log_lines,
        "errors": [],
    }

    try:
        serials = _resolve_serials(body)
        if (
            len(serials) >= 2
            and body.dut_serial
            and body.ref_serial
            and str(body.dut_serial).strip()
            and str(body.ref_serial).strip()
        ):
            serials = reorder_serials_by_serial(
                serials,
                str(body.dut_serial).strip(),
                str(body.ref_serial).strip(),
            )
        result["device_serials"] = serials
        if not serials:
            result["errors"].append("未找到可用 ADB 设备，请连接或传入 device_serial(s)。")
            result["ok"] = False
            return result

        role_labels = labels_for_slot_count(len(serials)) if len(serials) >= 2 else None

        if body.run_capture:
            items, cap_msg = run_multi_device_capture(
                serials,
                body.session_tag,
                log=log,
                device_role_labels=role_labels,
                record_tool=body.record_tool,
            )
            result["capture_summary"] = cap_msg
            result["recorded"] = [
                {
                    "group": it.group,
                    "file": it.filename,
                    "wav": str(it.local_wav),
                    "ok": it.ok,
                    "message": it.message,
                    "device_serial": it.device_serial,
                    "device_slot": it.device_slot,
                }
                for it in items
            ]
            if not items or not any(it.ok for it in items):
                result["errors"].append("采集阶段无有效录音，请检查 ADB、音源文件与麦克风权限。")

        analysis_path: Optional[Any] = None
        label = body.test_device_name
        if len(serials) > 1:
            label = f"多设备_{len(serials)}"

        if body.run_scoring:
            analysis_path, score_msg = score_recorded_session(
                body.session_tag,
                device_label=label,
                log=log,
            )
            result["scoring_summary"] = score_msg
            result["analysis_json"] = str(analysis_path) if analysis_path else None
            if not analysis_path:
                result["errors"].append(score_msg)

        if body.run_report and analysis_path:
            ref = ", ".join(serials[:6]) + (" …" if len(serials) > 6 else "")
            doc_path, md_path, tsv_path, xlsx_path, rep_msg = build_word_from_analysis(
                analysis_path,
                test_name=body.test_name
                if len(serials) == 1
                else (body.test_name + "（多设备刺激比较）"),
                test_device=label,
                ref_device=ref or body.ref_device_name,
            )
            result["report_summary"] = rep_msg
            result["report_docx"] = str(doc_path) if doc_path else None
            result["report_markdown"] = str(md_path) if md_path else None
            result["report_tsv"] = str(tsv_path) if tsv_path else None
            result["report_xlsx"] = str(xlsx_path) if xlsx_path else None
            if not doc_path:
                result["errors"].append(rep_msg)

        result["ok"] = len(result["errors"]) == 0
        return result
    except Exception as exc:
        result["ok"] = False
        result["errors"].append(str(exc))
        if _debug_traceback_enabled():
            result["traceback"] = traceback.format_exc()
        return result


if __name__ == "__main__":
    import uvicorn

    # 传入 app 对象：作为 __main__ 运行时模块名不是 local_service，避免字符串形式导入失败
    uvicorn.run(app, host=SERVICE_HOST, port=SERVICE_PORT, reload=False)
