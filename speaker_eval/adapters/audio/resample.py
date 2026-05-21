# -*- coding: utf-8 -*-
"""仅 numpy 的线性重采样工具。"""
from __future__ import annotations

import numpy as np


def resample_linear(buf: np.ndarray, sr_from: int, sr_to: int) -> np.ndarray:
    if sr_from == sr_to or buf.shape[0] < 2:
        return buf.astype(np.float32, copy=False)
    n_in, ch = buf.shape
    duration = n_in / float(sr_from)
    n_out = max(2, int(round(duration * sr_to)))
    t_in = np.linspace(0.0, duration, n_in, endpoint=False, dtype=np.float64)
    t_out = np.linspace(0.0, duration, n_out, endpoint=False, dtype=np.float64)
    out = np.zeros((n_out, ch), dtype=np.float32)
    for c in range(ch):
        out[:, c] = np.interp(t_out, t_in, buf[:, c].astype(np.float64)).astype(np.float32)
    return out
