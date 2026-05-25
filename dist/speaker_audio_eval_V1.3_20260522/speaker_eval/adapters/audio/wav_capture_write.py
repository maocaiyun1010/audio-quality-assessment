# -*- coding: utf-8 -*-
"""
OmniMic / PortAudio 采集结果落盘规范（与 ``recording_config.SAMPLE_RATE`` 一致）：

- 采样率：48000 Hz
- 声道：单声道
- 容器/编码：WAV，**PCM 16-bit**（线性 PCM，无有损压缩）

输入为 float32/float64、取值约 ``[-1, 1]`` 的缓冲区（与 ``sounddevice`` / CLI 读回一致）；
写盘时由 ``soundfile`` 量化为 16-bit PCM，**不**再做有损压缩。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

from speaker_eval.settings.recording import SAMPLE_RATE


def write_standard_capture_wav(out_path: Path | str, audio: np.ndarray) -> None:
    """
    将采集缓冲区写入标准 WAV（48 kHz / mono / PCM_16）。

    Parameters
    ----------
    audio
        形状 ``(frames,)`` 或 ``(frames, channels)``；多声道时取各声道算术平均为单声道。
    """
    out_path = Path(out_path)
    x = np.asarray(audio)
    if x.dtype not in (np.float32, np.float64):
        x = x.astype(np.float64, copy=False)
    if x.ndim == 1:
        mono = np.clip(x, -1.0, 1.0)
    elif x.ndim == 2:
        if x.shape[1] == 1:
            mono = np.clip(x[:, 0], -1.0, 1.0)
        else:
            mono = np.clip(np.mean(x, axis=1), -1.0, 1.0)
    else:
        raise ValueError("audio 须为一维或二维数组")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    # (N, 1) 明确为单声道 WAV
    pcm_in = mono.reshape(-1, 1).astype(np.float32, copy=False)
    sf.write(
        str(out_path),
        pcm_in,
        int(SAMPLE_RATE),
        subtype="PCM_16",
        format="WAV",
    )
