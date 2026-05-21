# -*- coding: utf-8 -*-
"""
独立试录脚本：底层与主流程一致，均使用 ``speaker_eval.adapters.audio.acquire_recording_buffer``。

主评测请用 ``python run_all.py --record-tool omnimic`` 或 ``--record-tool ask``。

快速调试示例::

  python omnimic_recorder.py -d 3 -o output/recorded/debug.wav --validate
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import soundfile as sf

from speaker_eval.adapters.audio import (
    acquire_recording_buffer,
    find_omnimic_input_device_meta,
    get_record_tool,
)
from speaker_eval.adapters.audio.wav_capture_write import write_standard_capture_wav
from speaker_eval.settings import SAMPLE_RATE


def extract_audio_features(audio_path: str) -> dict:
    """可选：需 ``pip install librosa``。"""
    try:
        import librosa
    except ImportError as e:
        raise ImportError("特征提取需要 librosa，请执行: pip install librosa") from e

    y, sr = librosa.load(audio_path, sr=SAMPLE_RATE)
    rms = float(np.sqrt(np.mean(np.square(y, dtype=np.float64))))
    spl = round(20 * np.log10(max(rms, 1e-12) / 2e-5), 1)
    stft = np.abs(librosa.stft(y, n_fft=512))
    sc = librosa.feature.spectral_centroid(S=stft, sr=sr)[0]
    sti = round(max(0.1, min(0.9, float(np.mean(sc)) / 5000.0)), 2)
    thd = round(float(min(max((np.std(y) * 100), 0.1), 10.0)), 2)
    return {"响度_SPL": spl, "清晰度_STI": sti, "失真_THD": thd}


def main() -> int:
    p = argparse.ArgumentParser(description="OmniMic / 本机 试录（与主工程录音适配层一致）")
    p.add_argument(
        "-o",
        "--output",
        default=str(os.environ.get("SPEAKER_OMNIMIC_TEST_WAV", r"D:\AudioTest\records\test_omnimic.wav")),
        help="输出 WAV 路径",
    )
    p.add_argument(
        "-d",
        "--duration",
        type=float,
        default=35.0,
        help="录音时长（秒）",
    )
    p.add_argument(
        "--tool",
        choices=("sounddevice", "omnimic"),
        default="omnimic",
        help="录音后端；omnimic=专业路径（默认同 run_all --record-tool omnimic）",
    )
    p.add_argument("--features", action="store_true", help="录完后尝试计算 SPL/STI/THD（需 librosa）")
    p.add_argument(
        "--validate",
        action="store_true",
        help="写盘后读回 WAV，打印时长/RMS/峰值（无需 librosa，用于确认非静音）",
    )
    args = p.parse_args()

    frames = max(1, int(args.duration * SAMPLE_RATE))
    os.makedirs(os.path.dirname(os.path.abspath(args.output)) or ".", exist_ok=True)

    print(
        f"工具: {get_record_tool(args.tool)} | 原生 {SAMPLE_RATE} Hz，无需重采样 | "
        f"时长={args.duration}s -> {args.output}"
    )
    if args.tool == "omnimic":
        did = find_omnimic_input_device()
        if did is not None:
            print(f"优先输入设备索引: {did}")

    buf = acquire_recording_buffer(args.duration, frames, log=print, tool=args.tool)
    write_standard_capture_wav(args.output, buf)
    print("已写入:", args.output)

    if args.validate:
        data, sr = sf.read(args.output, dtype="float32", always_2d=True)
        n = int(data.shape[0])
        dur = n / float(sr)
        rms = float(np.sqrt(np.mean(np.square(data, dtype=np.float64))))
        peak = float(np.max(np.abs(data)))
        print(
            f"[校验] 采样率={sr} Hz, 时长≈{dur:.3f}s, 帧数={n}, "
            f"RMS={rms:.6f}, 峰值={peak:.4f}",
            flush=True,
        )
        if rms < 1e-5:
            print(
                "[校验] WARN: 能量极低，可能未录到有效声；请检查麦克风、音量与设备选择。",
                flush=True,
            )

    if args.features:
        try:
            print("声学特征:", extract_audio_features(args.output))
        except ImportError as e:
            print(str(e), file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
