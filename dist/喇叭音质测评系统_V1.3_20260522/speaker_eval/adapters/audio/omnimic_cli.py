# -*- coding: utf-8 -*-
"""OmniMic：外部可执行程序录音并读回 WAV。"""
from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
from typing import Callable

import numpy as np
import soundfile as sf

from speaker_eval.settings.recording import RECORD_CHANNELS, SAMPLE_RATE


def acquire_via_cli(total_seconds: float, frames: int, log: Callable[[str], None]) -> np.ndarray:
    exe = os.environ.get("SPEAKER_OMNIMIC_EXE", "").strip().strip('"')
    if not exe:
        raise RuntimeError("内部错误：CLI 路径未设置 SPEAKER_OMNIMIC_EXE。")

    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".wav", prefix="omnimic_cli_")
    os.close(tmp_fd)
    dur_s = f"{total_seconds:.3f}"
    try:
        argline = (os.environ.get("SPEAKER_OMNIMIC_ARGLINE") or "").strip()
        if not argline:
            argv = [exe, "--output", tmp_path, "--duration", dur_s]
        else:
            expanded = argline.format(out=tmp_path, dur=dur_s, sr=str(SAMPLE_RATE))
            argv = [exe] + shlex.split(expanded, posix=os.name != "nt")

        log(
            f"录音后端=omnimic/外部程序，时长≈{total_seconds:.2f}s，命令: {exe} "
            + " ".join(shlex.quote(a) for a in argv[1:6])
            + (" …" if len(argv) > 6 else "")
        )
        run_kw: dict = {
            "timeout": max(300.0, float(total_seconds) * 1.5 + 120.0),
            "check": True,
        }
        if os.name == "nt":
            run_kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.run(argv, **run_kw)
        data, sr = sf.read(tmp_path, dtype="float32", always_2d=True)
        log(
            "[OmniMic] 外部录音程序已执行完毕，WAV 已读入；工程要求原生 "
            f"{SAMPLE_RATE} Hz，不做重采样（CLI 路径）。"
        )
        if data.ndim == 1:
            data = data.reshape(-1, 1)
        if sr != SAMPLE_RATE:
            msg = (
                f"OmniMic CLI 输出采样率为 {sr} Hz，与工程 {SAMPLE_RATE} Hz 不一致；"
                "请在外部程序或 SPEAKER_OMNIMIC_ARGLINE 中指定 48000 Hz 输出。"
            )
            log(msg)
            raise RuntimeError(msg)
        if data.shape[1] > RECORD_CHANNELS:
            if RECORD_CHANNELS == 1:
                data = np.mean(data, axis=1, keepdims=True, dtype=np.float64).astype(np.float32)
        elif data.shape[1] < RECORD_CHANNELS:
            pad = RECORD_CHANNELS - data.shape[1]
            data = np.pad(data, ((0, 0), (0, pad)), mode="constant")

        n = data.shape[0]
        if n < frames:
            data = np.pad(data, ((0, frames - n), (0, 0)), mode="constant")
        elif n > frames:
            data = data[:frames, :]
        return data.astype(np.float32, copy=False)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
