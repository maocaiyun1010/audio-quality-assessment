# -*- coding: utf-8 -*-
"""
项目自检：依赖、配置路径、音源扫描、ADB、麦克风、本地 API 模块等。
用法：
  python self_test.py           # 标准自检，有问题打印 WARN / ERROR
  python self_test.py --strict  # 无音源或未连接 adb 设备时返回非 0

建议在项目根目录执行；从别处执行时会自动切换到脚本所在目录。
"""
from __future__ import annotations

import argparse
import importlib
import os
import subprocess
import sys
from pathlib import Path


def _root_dir() -> Path:
    return Path(__file__).resolve().parent


def _ok(msg: str) -> None:
    print(f"  [OK] {msg}")


def _warn(msg: str) -> None:
    print(f"  [WARN] {msg}")


def _err(msg: str) -> None:
    print(f"  [ERROR] {msg}")


def main() -> int:
    parser = argparse.ArgumentParser(description="喇叭音效项目自检")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="无可用音源或未检测到 adb 设备时退出码为 1",
    )
    args = parser.parse_args()

    os.chdir(_root_dir())
    sys.path.insert(0, str(_root_dir()))

    print("=" * 60)
    print("喇叭音效自动化 — 自检")
    print("工作目录:", os.getcwd())
    print("=" * 60)

    errors: list[str] = []
    warnings: list[str] = []

    # --- 依赖包 ---
    print("\n[1] Python 依赖")
    pkgs = [
        "requests",
        "sounddevice",
        "soundfile",
        "numpy",
        "adbutils",
        "docx",
        "fastapi",
        "uvicorn",
    ]
    for name in pkgs:
        try:
            importlib.import_module(name if name != "docx" else "docx")
            _ok(f"import {name}")
        except Exception as exc:
            _err(f"import {name} 失败: {exc}")
            errors.append(name)

    # --- 业务模块 ---
    print("\n[2] 项目模块")
    for mod in ("config", "sync_capture", "scoring", "report_builder", "gen_report", "difyclient", "local_service"):
        try:
            importlib.import_module(mod)
            _ok(f"import {mod}")
        except Exception as exc:
            _err(f"import {mod} 失败: {exc}")
            errors.append(mod)

    if errors:
        print("\n存在 ERROR，请先修复依赖与模块。")
        return 1

    import config as cfg

    print("\n[3] 路径与输出目录")
    _ok(f"BASE_DIR = {cfg.BASE_DIR}")
    if not cfg.BASE_DIR.is_dir():
        _err("BASE_DIR 不存在，请修改 config.py")
        errors.append("BASE_DIR")
    else:
        try:
            cfg.ensure_output_dirs()
            _ok("ensure_output_dirs() 可写")
        except Exception as exc:
            _err(f"创建输出目录失败: {exc}")
            errors.append("output")

    print("\n[4] 音源扫描 (assets/test_audio)")
    try:
        tracks = cfg.discover_standard_tracks()
        if not tracks:
            w = "未发现音源；可运行 python tools\\gen_placeholder_wavs.py 生成占位文件"
            _warn(w)
            warnings.append(w)
        else:
            _ok(f"共 {len(tracks)} 条，首条: {tracks[0][0]} / {tracks[0][1]}")
            if args.strict and not tracks:
                errors.append("no_tracks")
    except Exception as exc:
        _err(f"discover_standard_tracks: {exc}")
        errors.append("discover")

    print("\n[5] ADB")
    try:
        r = subprocess.run(
            ["adb", "version"],
            capture_output=True,
            text=True,
            timeout=10,
            encoding="utf-8",
            errors="replace",
        )
        if r.returncode == 0:
            first = (r.stdout or "").strip().splitlines()[:1]
            _ok(first[0] if first else "adb version")
        else:
            _warn("adb version 非 0 退出码")
            warnings.append("adb_version")
    except FileNotFoundError:
        w = "未找到 adb 命令，请安装 Android SDK Platform-Tools 并加入 PATH"
        _warn(w)
        warnings.append("adb_missing")
    except Exception as exc:
        _warn(f"adb: {exc}")
        warnings.append("adb")

    try:
        r = subprocess.run(
            ["adb", "devices"],
            capture_output=True,
            text=True,
            timeout=15,
            encoding="utf-8",
            errors="replace",
        )
        lines = [ln for ln in (r.stdout or "").splitlines() if "\tdevice" in ln]
        if lines:
            _ok(f"已连接设备 {len(lines)} 台: {lines[0].split()[0]} …")
        else:
            w = "未检测到状态为 device 的 USB/无线调试设备"
            _warn(w)
            warnings.append("no_device")
            if args.strict:
                errors.append("no_device")
    except Exception as exc:
        _warn(f"adb devices: {exc}")

    print("\n[6] 麦克风 (sounddevice)")
    try:
        import sounddevice as sd

        d = sd.query_devices(kind="input")
        _ok(f"默认输入设备: {d.get('name', '?')}")
        print("  可用输入设备（设置 SPEAKER_INPUT_DEVICE=索引 或 名称子串）:")
        for i, dev in enumerate(sd.query_devices()):
            if int(dev.get("max_input_channels") or 0) < 1:
                continue
            mark = " *" if i == sd.default.device[0] else ""
            print(f"    #{i}{mark} {dev.get('name', '?')}")
    except Exception as exc:
        w = f"无法查询默认输入设备: {exc}"
        _warn(w)
        warnings.append("mic")

    print("\n[7] 本地 HTTP 应用 (FastAPI)")
    try:
        from fastapi.testclient import TestClient

        import local_service

        c = TestClient(local_service.app)
        h = c.get("/health")
        if h.status_code == 200 and h.json().get("ok"):
            _ok("/health " + str(h.json()))
        else:
            _warn(f"/health 异常: {h.status_code} {h.text[:200]}")
            warnings.append("health")
    except Exception as exc:
        _warn(f"本地 API 自检失败: {exc}")
        warnings.append("local_service")

    print("\n" + "=" * 60)
    if errors:
        print("结果: 未通过（存在 ERROR / strict 条件）")
        return 1
    if warnings:
        print(f"结果: 通过，但有 {len(warnings)} 项 WARN，请按需处理。")
        return 0
    print("结果: 全部检查通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
