# -*- coding: utf-8 -*-
"""OmniMic：本机 PortAudio 路径（设备名匹配 + 增益）。"""
from __future__ import annotations

from typing import Callable

import numpy as np
import sounddevice as sd

from speaker_eval.adapters.audio.device_query import (
    find_omnimic_input_device_meta,
    remap_input_device_index_to_mme_if_enabled,
    resolve_sounddevice_input_index,
)
from speaker_eval.adapters.audio.portaudio_record import rec_with_samplerate_fallback
from speaker_eval.settings.recording import OMNIMIC_GAIN_DB


def acquire_via_portaudio(frames: int, log: Callable[[str], None]) -> np.ndarray:
    """
    双设备录制等路径历史上固定走 ``tool="omnimic"``，但侧栏麦克风可能不含 OmniMic 关键字。
    若已配置 ``SPEAKER_INPUT_DEVICE``（索引或名称子串），**优先**使用该输入，避免误用系统默认无声设备。
    """
    gain_db = float(OMNIMIC_GAIN_DB)
    resolved = resolve_sounddevice_input_index(log=log)
    if resolved is not None:
        try:
            di = sd.query_devices(resolved)
            dev_name = str(di.get("name", "?"))
        except Exception:
            dev_name = "?"
        log(
            f"[OmniMic] 已按 SPEAKER_INPUT_DEVICE 选用输入 #{resolved}: {dev_name} "
            f"（增益 {gain_db:g} dB；与侧栏/环境变量一致）"
        )
        recording = rec_with_samplerate_fallback(frames, log, device=resolved)
        if gain_db != 0.0:
            recording = recording * (10 ** (gain_db / 20.0))
            recording = np.clip(recording, -1.0, 1.0)
        return recording.astype(np.float32, copy=False)

    dev_id, match_kind = find_omnimic_input_device_meta()
    dev_name = "?"
    if dev_id is not None:
        try:
            di = sd.query_devices(dev_id)
            dev_name = str(di.get("name", "?"))
        except Exception:
            dev_name = "?"

    if dev_id is not None and match_kind in ("omnimic", "dayton"):
        brand = "OmniMic" if match_kind == "omnimic" else "Dayton"
        log(
            f"[OmniMic] 已正确识别并选用专业麦克风（{brand}，名称关键字={match_kind}），"
            f"设备索引 #{dev_id}: {dev_name}"
        )
        log(
            f"录音后端=omnimic/本机硬件 (frames={frames}, "
            f"输入#{dev_id}: {dev_name}, 增益 {gain_db:g} dB)"
        )
    elif dev_id is not None and match_kind == "usb_fallback":
        log(
            "[OmniMic] 设备名中未出现 omnimic/dayton；已按环境变量 SPEAKER_OMNIMIC_PREFER_USB=1 "
            f"选用 USB 输入（请自行确认是否为测量麦克风），设备 #{dev_id}: {dev_name}"
        )
        log(
            f"录音后端=omnimic/本机硬件 (frames={frames}, "
            f"输入#{dev_id}: {dev_name}, 增益 {gain_db:g} dB)"
        )
    else:
        try:
            di = sd.query_devices(kind="input")
            log(
                f"[OmniMic] 未识别到 OmniMic/Dayton 设备名，将使用系统默认输入（非专业路径确认）: "
                f"{di.get('name', '?')}"
            )
            log(
                f"录音后端=omnimic/本机硬件 (frames={frames}, 默认输入: {di.get('name', '?')}, "
                f"增益 {gain_db:g} dB；请连接 USB 麦或设置 SPEAKER_OMNIMIC_PREFER_USB=1 / SPEAKER_INPUT_DEVICE)"
            )
        except Exception:
            log("[OmniMic] 未识别到 OmniMic/Dayton，且无法查询默认输入设备。")
            log(
                f"录音后端=omnimic/本机硬件 (frames={frames}, 默认输入, 增益 {gain_db:g} dB)"
            )

    capture_dev = remap_input_device_index_to_mme_if_enabled(dev_id, log=log)
    recording = rec_with_samplerate_fallback(frames, log, device=capture_dev)
    if gain_db != 0.0:
        recording = recording * (10 ** (gain_db / 20.0))
        recording = np.clip(recording, -1.0, 1.0)
    return recording.astype(np.float32, copy=False)
