# -*- coding: utf-8 -*-
"""
已废弃的「单独遍历录制」入口；录制实现已并入 ``speaker_eval.adapters.audio`` + ``sync_capture``。

若仅需本机录一段 WAV，可调用 ``record_audio()``（内部走统一后端）。
主流程请使用：``python run_all.py`` 或 ``sync_capture.run_multi_device_capture``。
"""
from __future__ import annotations

import os
import time

from speaker_eval.adapters.audio import acquire_recording_buffer, get_record_tool
from speaker_eval.adapters.audio.wav_capture_write import write_standard_capture_wav
from speaker_eval.settings import SAMPLE_RATE


def record_audio(filename: str, duration: float = 30.0, tool: str | None = None) -> str:
    """
    录制单声道 WAV，采样率与 ``config.SAMPLE_RATE`` 一致。

    ``tool``：``sounddevice`` / ``omnimic`` 或 ``None``（读 ``SPEAKER_RECORD_TOOL``）。
    """
    print(f"开始录制 {duration} 秒… (后端={get_record_tool(tool)})")
    frames = max(1, int(duration * SAMPLE_RATE))
    recording = acquire_recording_buffer(duration, frames, log=print, tool=tool)
    os.makedirs(os.path.dirname(os.path.abspath(filename)) or ".", exist_ok=True)
    write_standard_capture_wav(filename, recording)
    print(f"录制完成: {filename}")
    return filename


if __name__ == "__main__":
    import sys

    os.makedirs("recorded", exist_ok=True)
    device_label = sys.argv[1] if len(sys.argv) > 1 else "test"
    duration = float(sys.argv[2]) if len(sys.argv) > 2 else 30.0

    audio_files = sorted(
        f for f in os.listdir("test_audio") if f.endswith((".mp3", ".wav"))
    ) if os.path.isdir("test_audio") else []

    if not audio_files:
        print("未找到 test_audio 目录或其中无 .mp3/.wav；已跳过批量录制。")
        raise SystemExit(1)

    for i, file in enumerate(audio_files, 1):
        print(f"\n[{i}/{len(audio_files)}] 正在录制: {file}")
        stem = file.replace(".mp3", "").replace(".wav", "")
        output_filename = f"recorded/{device_label}_{stem}.wav"
        record_audio(output_filename, duration)
        time.sleep(1)

    print(f"\n所有音频录制完成！共 {len(audio_files)} 个文件")
