# -*- coding: utf-8 -*-
"""
在将音频上传至 Dify / 大模型前，仅做 **制式格式规范化**（便于多模态链路统一解码）：

- **固定采样率** 48000 Hz（``soxr`` HQ 重采样；仅改变时间栅格，**不做**响度/LUFS/峰值「对齐到某目标」类处理）
- **单声道**：多声道时为各声道 **算术平均** ``mean(ch)``，**不**再乘全局增益系数、**不**做 RMS/LUFS 归一化
- **16-bit PCM WAV**（无损 PCM）

**明确不做**：响度/音量归一化、自动增益 AGC、峰值拉到满刻度、EBU R128 等；尽量保留终端外放经麦克风采集后的 **相对电平与动态**，使模型听到的响度关系更接近实录。

写入 PCM 前将浮点样本限制在 ``[-1, 1]`` 仅为 **防止 int16 溢出/环绕失真**，不是「整条音频拉到统一响度」。

本地 ``output/recorded`` 等原始文件**不修改**，仅生成临时文件供上传。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import soundfile as sf

# 与 recording_config / 工程约定一致
TARGET_SAMPLE_RATE: int = 48000

# #region agent log
def _norm_debug_enabled() -> bool:
    return (os.environ.get("SPEAKER_AGENT_DEBUG_LOG") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _norm_dbg_paths() -> list[Path]:
    seen: set[str] = set()
    out: list[Path] = []
    for p in (
        Path(__file__).resolve().parent / "debug-0d224e.log",
        Path.cwd() / "debug-0d224e.log",
        Path(tempfile.gettempdir()) / "debug-0d224e.log",
    ):
        try:
            key = str(p.resolve())
        except Exception:
            key = str(p)
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def _dbg_norm(hypothesis_id: str, message: str, data: dict) -> None:
    if not _norm_debug_enabled():
        return
    rec = {
        "sessionId": "0d224e",
        "timestamp": int(time.time() * 1000),
        "hypothesisId": hypothesis_id,
        "location": "audio_llm_normalize",
        "message": message,
        "data": data,
    }
    line = json.dumps(rec, ensure_ascii=False) + "\n"
    _ok_any = False
    _first_err: str | None = None
    for _lp in _norm_dbg_paths():
        try:
            _lp.parent.mkdir(parents=True, exist_ok=True)
            with open(_lp, "a", encoding="utf-8") as f:
                f.write(line)
                f.flush()
            _ok_any = True
        except Exception as exc:
            if _first_err is None:
                _first_err = f"{type(exc).__name__}: {exc}"
    if not _ok_any and _first_err:
        try:
            sys.stderr.write(f"[debug-0d224e] audio_llm_normalize 写日志失败: {_first_err}\n")
        except Exception:
            pass


# #endregion


def write_normalized_wav_for_upload(
    src: Path,
    dst: Path,
    *,
    max_duration_sec: float | None = None,
) -> dict[str, float | bool]:
    """
    读取 ``src``（soundfile 支持的格式），写出 ``dst`` 为 **48 kHz / 单声道 / PCM_16** WAV。

    仅制式转换：**不**对波形做响度归一化或目标电平对齐；多声道下混为单声道采用算术平均且无额外增益。
    对 ``[-1, 1]`` 外的浮点样本做裁剪，仅为安全写入 16-bit PCM，避免削波以外的数值环绕。

    ``max_duration_sec``：若 >0 且源时长更长，仅保留**开头**若干秒再规范化（本地 ``src`` 不修改）。
    返回 ``{"trimmed", "duration_in_sec", "duration_out_sec"}`` 供上传日志使用。

    Raises:
        RuntimeError: 无法解码或重采样失败时。
    """
    try:
        import soxr
    except ImportError as _ie:
        # #region agent log
        _dbg_norm("H1", "soxr_import_failed", {"err": str(_ie)[:300]})
        # #endregion
        raise RuntimeError(
            "缺少依赖 soxr，请执行: pip install soxr>=0.3.7"
        ) from _ie

    src = Path(src)
    dst = Path(dst)
    # #region agent log
    try:
        _dbg_norm(
            "H1",
            "entry",
            {
                "src": str(src.name)[:200],
                "src_bytes": src.stat().st_size if src.is_file() else -1,
            },
        )
    except Exception:
        pass
    # #endregion
    try:
        data, sr = sf.read(str(src), always_2d=True, dtype="float64")
    except Exception as exc:
        # #region agent log
        _dbg_norm(
            "H1",
            "sf_read_failed",
            {"err": str(exc)[:500], "src": str(src.name)[:200]},
        )
        # #endregion
        raise RuntimeError(
            f"无法读取音频文件（请使用 WAV/FLAC 等 soundfile 支持的格式）：{src.name}: {exc}"
        ) from exc

    if data.size == 0:
        # #region agent log
        _dbg_norm("H1", "empty_audio", {"src": str(src.name)[:200]})
        # #endregion
        raise RuntimeError(f"音频为空：{src}")

    # #region agent log
    _dbg_norm(
        "H1",
        "after_read",
        {
            "sr": int(sr),
            "shape": [int(data.shape[0]), int(data.shape[1])],
            "needs_resample": int(sr) != TARGET_SAMPLE_RATE,
        },
    )
    # #endregion

    duration_in_sec = float(data.shape[0]) / float(sr) if sr else 0.0

    # 多声道 → 单声道：算术平均（不另乘增益、不做响度归一化）
    if data.shape[1] == 1:
        mono = data[:, 0].copy()
    else:
        mono = np.mean(data, axis=1)

    trimmed = False
    cap = float(max_duration_sec) if max_duration_sec is not None else 0.0
    if cap > 0.0 and duration_in_sec > cap + 1e-6:
        max_samples = max(1, int(round(cap * float(sr))))
        if mono.shape[0] > max_samples:
            mono = mono[:max_samples]
            trimmed = True

    # 仅防 float→PCM_16 写入时越界，非响度归一化
    mono = np.clip(mono, -1.0, 1.0)

    if int(sr) != TARGET_SAMPLE_RATE:
        try:
            mono = soxr.resample(
                mono.astype(np.float64, copy=False),
                float(sr),
                float(TARGET_SAMPLE_RATE),
                quality="HQ",
            )
        except Exception as _sx:
            # #region agent log
            _dbg_norm("H1", "soxr_failed", {"err": str(_sx)[:500], "sr_in": int(sr)})
            # #endregion
            raise
        mono = np.clip(mono, -1.0, 1.0)

    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        # PCM_16：保持 [-1,1] 内相对幅度关系线性映射到 int16，不做峰值归一化
        pcm = np.clip(mono, -1.0, 1.0)
        pcm_i16 = np.rint(pcm * 32767.0).astype(np.int16)
        sf.write(
            str(dst),
            pcm_i16,
            TARGET_SAMPLE_RATE,
            subtype="PCM_16",
            format="WAV",
        )
    except Exception as _we:
        # #region agent log
        _dbg_norm("H1", "sf_write_failed", {"err": str(_we)[:500]})
        # #endregion
        raise
    # #region agent log
    try:
        _db = dst.stat().st_size if dst.is_file() else 0
    except OSError:
        _db = 0
    _dbg_norm("H1", "write_ok", {"dst_bytes": _db, "trimmed": trimmed})
    # #endregion
    duration_out_sec = float(mono.shape[0]) / float(TARGET_SAMPLE_RATE)
    return {
        "trimmed": trimmed,
        "duration_in_sec": duration_in_sec,
        "duration_out_sec": duration_out_sec,
    }
