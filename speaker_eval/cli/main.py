# -*- coding: utf-8 -*-
"""
统一 CLI 入口：解析参数 → 初始化日志 → 调用评测流水线。

根目录 ``run_all.py`` 仅转发至此模块的 ``main``。
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime

from speaker_eval.adapters.adb import list_connected_adb_devices
from speaker_eval.adapters.audio import get_record_tool
from speaker_eval.logging_config import setup_app_logging
from speaker_eval.pipelines.evaluation import resolve_role_labels_for_serials, run_evaluation_pipeline
from speaker_eval.settings import LOG_DIR


def _record_tool_env_set() -> bool:
    return bool(os.environ.get("SPEAKER_RECORD_TOOL", "").strip())


def _prompt_record_tool_choice() -> str:
    print("", flush=True)
    print("请选择录音方式:", flush=True)
    print("  1 — 本机麦克风 (sounddevice，可用环境变量 SPEAKER_INPUT_DEVICE 指定设备)", flush=True)
    print(
        "  2 — OmniMic 专业 (优先 OmniMic/Dayton 输入 + 默认 -6dB 增益；"
        "若配置 SPEAKER_OMNIMIC_EXE 则改为外部录音程序)",
        flush=True,
    )
    choice = input("请输入 1 或 2 [默认 1]: ").strip() or "1"
    return "omnimic" if choice == "2" else "sounddevice"


def main() -> int:
    parser = argparse.ArgumentParser(description="喇叭音效：多设备采集 + 刺激比较/单设备评分 + Word")
    parser.add_argument(
        "-d",
        "--device",
        action="append",
        default=None,
        help="只评指定序列号；可重复传入多台。不传则使用 adb devices 中全部 device",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="试跑：仅略缩预滚/尾缓冲，不缩短每条有效录音（避免误成 2s）",
    )
    parser.add_argument(
        "--seconds",
        type=float,
        default=None,
        help="每条有效录音秒数（默认 30，等同环境变量 SPEAKER_PER_TRACK_SEC）",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="多设备时跳过「被测机/对比机」交互确认；同时跳过录音方式询问（顺序=当前列表：首台=DUT）",
    )
    parser.add_argument(
        "--record-tool",
        dest="record_tool",
        choices=("sounddevice", "omnimic", "ask"),
        default=None,
        help="录音：sounddevice / omnimic / ask；不传时交互终端会询问（见 run_all 模块说明）",
    )
    args = parser.parse_args()

    logger = setup_app_logging(LOG_DIR, name="speaker_eval", file_prefix="cli_run")

    if args.seconds is not None and args.seconds > 0:
        os.environ["SPEAKER_PER_TRACK_SEC"] = str(args.seconds)

    if args.quick:
        os.environ.setdefault("SPEAKER_PRE_ROLL_SEC", "0.25")
        os.environ.setdefault("SPEAKER_POST_TAIL_SEC", "0.5")

    if args.record_tool == "ask":
        if sys.stdin.isatty():
            args.record_tool = _prompt_record_tool_choice()
        else:
            print("提示: --record-tool ask 在非交互终端下等同于 sounddevice。", flush=True)
            args.record_tool = "sounddevice"
    elif args.record_tool is None and not _record_tool_env_set():
        if args.yes:
            print("提示: 已使用 --yes，跳过录音方式询问，默认本机 sounddevice。", flush=True)
            args.record_tool = "sounddevice"
        elif sys.stdin.isatty():
            args.record_tool = _prompt_record_tool_choice()
        else:
            print("提示: 非交互终端且未设置 SPEAKER_RECORD_TOOL，使用默认本机 sounddevice。", flush=True)
            args.record_tool = "sounddevice"

    if args.record_tool:
        os.environ["SPEAKER_RECORD_TOOL"] = args.record_tool

    serials = [s.strip() for s in (args.device or []) if s and str(s).strip()]
    if not serials:
        serials = list_connected_adb_devices()

    if not serials:
        logger.error("未找到已连接 ADB 设备")
        print("错误：未找到已连接设备。请连接 USB/无线调试，或用 -d 指定序列号。", file=sys.stderr)
        return 1

    role_labels = None
    if len(serials) >= 2:
        skip_confirm = bool(args.yes) or (not sys.stdin.isatty())
        if not sys.stdin.isatty() and not args.yes:
            print(
                "提示：标准输入非 TTY，已跳过多设备「被测/对比」交互确认；"
                "当前枚举顺序即 d01=DUT、d02=REF。无人值守请加参数：--yes",
                flush=True,
            )

        def _clog(m: str) -> None:
            print(m, flush=True)

        ordered, role_labels = resolve_role_labels_for_serials(serials, skip_confirm=skip_confirm, clog=_clog)
        if ordered is None:
            print("已取消运行（未开始采集）。", flush=True)
            return 5
        serials = ordered

    session_tag = datetime.now().strftime("manual_%Y%m%d_%H%M%S")
    test_device_name = "多设备对比" if len(serials) > 1 else "单设备"
    dev_summary = ", ".join(serials[:4]) + (" …" if len(serials) > 4 else "")

    print("=" * 60)
    print("喇叭音效自动化 AI 评测（本地 Python）全流程")
    print("设备数:", len(serials), "|", dev_summary)
    print("会话:", session_tag)
    print("试跑模式:", "是 (--quick)" if args.quick else "否")
    print("录音方式:", get_record_tool(args.record_tool))
    print("运行日志:", LOG_DIR)
    print("=" * 60)

    def user_print(m: str) -> None:
        print(m, flush=True)

    code, detail = run_evaluation_pipeline(
        serials,
        session_tag,
        test_device_name=test_device_name,
        dev_summary=dev_summary,
        record_tool=args.record_tool,
        role_labels=role_labels,
        user_print=user_print,
        logger=logger,
    )
    if detail and code != 0:
        print(detail, file=sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
