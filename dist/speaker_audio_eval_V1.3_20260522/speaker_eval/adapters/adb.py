# -*- coding: utf-8 -*-
"""ADB 设备枚举（与采集编排解耦）。"""
from __future__ import annotations

import subprocess


def list_connected_adb_devices() -> list[str]:
    """返回所有 adb 状态为 device 的序列号（稳定顺序：按首次出现）。"""
    try:
        r = subprocess.run(
            ["adb", "devices"],
            capture_output=True,
            text=True,
            timeout=20,
            encoding="utf-8",
            errors="replace",
        )
        out: list[str] = []
        for line in (r.stdout or "").splitlines():
            line = line.strip()
            if not line or line.startswith("List of"):
                continue
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                out.append(parts[0])
        return out
    except Exception:
        return []
