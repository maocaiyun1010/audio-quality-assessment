# -*- coding: utf-8 -*-
"""查询 PortAudio 设备：OmniMic 匹配与本机输入解析。"""
from __future__ import annotations

import os
import re
from typing import Callable

import sounddevice as sd

from speaker_eval.settings.recording import INPUT_DEVICE_ID, INPUT_DEVICE_NAME_SUBSTR


def prefer_mme_hostapi_for_capture() -> bool:
    """
    Windows 下默认 **将录音解析到 MME 主机 API 设备索引**，以绕开不稳定的 WDM-KS。

    设 ``SPEAKER_SD_PREFER_MME=0/false/off`` 可关闭（仍使用 PortAudio 枚举到的原索引，可能是 WASAPI/WDM-KS）。
    非 Windows 恒为 False。
    """
    if os.name != "nt":
        return False
    raw = (os.environ.get("SPEAKER_SD_PREFER_MME") or "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    if raw in ("1", "true", "yes", "on"):
        return True
    return True


def _mme_hostapi_index() -> int | None:
    try:
        for hi, api in enumerate(sd.query_hostapis()):
            name = str(api.get("name", "")).lower()
            if "mme" in name and "wdm" not in name:
                return int(hi)
    except Exception:
        return None
    return None


def _normalize_cross_host_input_name(name: str) -> str:
    """去掉常见 Host API 前缀并折叠空白，便于在 MME / WASAPI / WDM-KS 等条目间配对。"""
    s = str(name).strip().lower()
    s = re.sub(
        r"^(mme|directsound|ds|wasapi|wdm-ks|wdm ks)\s*:\s*",
        "",
        s,
        flags=re.IGNORECASE,
    )
    return " ".join(s.split())


def _remap_device_index_to_mme(
    device_index: int,
    *,
    log: Callable[[str], None] | None,
) -> int:
    """将任意 Host API 上的输入设备索引映射到 **同一显示名** 对应的 MME 条目（若存在）。"""
    lg = log or (lambda _m: None)
    mme_hi = _mme_hostapi_index()
    if mme_hi is None:
        lg("[WARN] 未找到 MME 主机 API（PortAudio），保持原输入设备索引")
        return int(device_index)
    try:
        devices = sd.query_devices()
        ref = devices[int(device_index)]
    except Exception:
        return int(device_index)
    if int(ref.get("max_input_channels") or 0) < 1:
        return int(device_index)
    try:
        if int(ref.get("hostapi", -1)) == mme_hi:
            return int(device_index)
    except Exception:
        pass
    ref_key = _normalize_cross_host_input_name(str(ref.get("name") or ""))
    if not ref_key:
        return int(device_index)
    best: list[int] = []
    try:
        for i, d in enumerate(devices):
            if int(d.get("max_input_channels") or 0) < 1:
                continue
            if int(d.get("hostapi", -1)) != mme_hi:
                continue
            cand_key = _normalize_cross_host_input_name(str(d.get("name") or ""))
            if cand_key == ref_key:
                best.append(int(i))
    except Exception:
        return int(device_index)
    if not best:
        lg(
            f"[WARN] 无法在 MME 下匹配输入设备「{ref.get('name', '?')}」"
            f"（已规范化键「{ref_key}」），保持原索引 #{device_index}"
        )
        return int(device_index)
    chosen = best[0]
    if chosen != int(device_index):
        try:
            mme_name = str(devices[chosen].get("name", "?"))
        except Exception:
            mme_name = "?"
        lg(
            f"录音：已按 SPEAKER_SD_PREFER_MME 使用 **MME** 主机 API 设备 "
            f"#{chosen}（{mme_name}），替代原 #{device_index}"
        )
    return int(chosen)


def remap_input_device_index_to_mme_if_enabled(
    device_index: int | None,
    *,
    log: Callable[[str], None] | None = None,
) -> int | None:
    """
    在启用 ``prefer_mme_hostapi_for_capture()`` 时，把解析到的索引（或系统默认输入索引）映射到 MME 侧。
    """
    if not prefer_mme_hostapi_for_capture():
        return device_index
    if device_index is not None:
        return _remap_device_index_to_mme(int(device_index), log=log)
    try:
        pair = sd.default.device
        base = int(pair[0])
    except Exception:
        return None
    if base < 0:
        return None
    return _remap_device_index_to_mme(base, log=log)


def input_device_allows_wasapi_extra_settings(device_index: int | None) -> bool:
    """仅 WASAPI 主机 API 上的设备适合配合 ``WasapiSettings``；MME / DirectSound 等应跳过。"""
    if device_index is None:
        return True
    try:
        hi = int(sd.query_devices(int(device_index)).get("hostapi", -1))
        name = str(sd.query_hostapis(hi).get("name", "")).lower()
        return "wasapi" in name
    except Exception:
        return True


def find_omnimic_input_device_meta() -> tuple[int | None, str]:
    try:
        devices = sd.query_devices()
    except Exception:
        return None, ""
    for i, dev in enumerate(devices):
        if int(dev.get("max_input_channels") or 0) < 1:
            continue
        name = str(dev.get("name") or "").lower()
        if "omnimic" in name:
            return int(i), "omnimic"
        if "dayton" in name:
            return int(i), "dayton"
    prefer_usb = os.environ.get("SPEAKER_OMNIMIC_PREFER_USB", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    if prefer_usb:
        for i, dev in enumerate(devices):
            if int(dev.get("max_input_channels") or 0) < 1:
                continue
            name = str(dev.get("name") or "").lower()
            if "usb" in name:
                return int(i), "usb_fallback"
    return None, ""


def find_omnimic_input_device() -> int | None:
    return find_omnimic_input_device_meta()[0]


def resolve_sounddevice_input_index(
    *,
    log: Callable[[str], None] | None = None,
) -> int | None:
    """
    优先读当前进程 ``os.environ["SPEAKER_INPUT_DEVICE"]``（Web UI 在录制前会写入），
    再回退到模块级 ``INPUT_DEVICE_ID`` / ``INPUT_DEVICE_NAME_SUBSTR``（兼容 import 时已固定的环境）。

    Windows 下默认再将索引 **映射到 MME 主机 API**（``SPEAKER_SD_PREFER_MME``，见 ``prefer_mme_hostapi_for_capture``）。
    """
    env = (os.environ.get("SPEAKER_INPUT_DEVICE") or "").strip()
    idx: int | None = None
    if env.isdigit():
        idx = int(env)
    elif env:
        try:
            for i, d in enumerate(sd.query_devices()):
                if int(d.get("max_input_channels") or 0) < 1:
                    continue
                name = str(d.get("name") or "")
                if env.lower() in name.lower():
                    idx = int(i)
                    break
        except Exception:
            idx = None
    elif INPUT_DEVICE_ID is not None:
        idx = int(INPUT_DEVICE_ID)
    else:
        sub = (INPUT_DEVICE_NAME_SUBSTR or "").strip()
        if sub:
            try:
                for i, d in enumerate(sd.query_devices()):
                    if int(d.get("max_input_channels") or 0) < 1:
                        continue
                    name = str(d.get("name") or "")
                    if sub.lower() in name.lower():
                        idx = int(i)
                        break
            except Exception:
                idx = None

    return remap_input_device_index_to_mme_if_enabled(idx, log=log)
