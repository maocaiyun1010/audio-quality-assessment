# -*- coding: utf-8 -*-
"""生成占位 WAV（静音），用于试跑；写入子目录以匹配自动扫描规则（子文件夹优先）。"""
from __future__ import annotations

import struct
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import ASSETS_AUDIO_DIR, SAMPLE_RATE

# 与常见「曲艺 + 语声」结构一致；若仅需扁平目录，可改为在根目录生成文件名列表
PLACEHOLDER_TRACKS: list[tuple[str, str]] = [
    ("曲艺", "quyi_01.wav"),
    ("曲艺", "quyi_02.wav"),
    ("曲艺", "quyi_03.wav"),
    ("曲艺", "quyi_04.wav"),
    ("曲艺", "quyi_05.wav"),
    ("曲艺", "quyi_06.wav"),
    ("曲艺", "quyi_07.wav"),
    ("曲艺", "quyi_08.wav"),
    ("语声", "yusheng_01.wav"),
    ("语声", "yusheng_02.wav"),
    ("语声", "yusheng_03.wav"),
    ("语声", "yusheng_04.wav"),
    ("语声", "yusheng_05.wav"),
    ("语声", "yusheng_06.wav"),
    ("语声", "yusheng_07.wav"),
    ("语声", "yusheng_08.wav"),
]


def main() -> None:
    ASSETS_AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    nframes = int(SAMPLE_RATE * 1.0)
    frames = struct.pack("<" + "h" * nframes, *([0] * nframes))

    for group, name in PLACEHOLDER_TRACKS:
        path = ASSETS_AUDIO_DIR / group / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(frames)
        print("OK", group, path.relative_to(ASSETS_AUDIO_DIR.parent.parent))

    print("完成，共", len(PLACEHOLDER_TRACKS), "个文件 ->", ASSETS_AUDIO_DIR)


if __name__ == "__main__":
    main()
