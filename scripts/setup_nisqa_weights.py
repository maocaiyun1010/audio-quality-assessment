# -*- coding: utf-8 -*-
"""下载 NISQA v2 预训练权重到 models/nisqa/（约 1MB）。"""
from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from speaker_eval.settings.nisqa import NISQA_MODEL_DIR, NISQA_WEIGHTS_FILE, NISQA_MODEL_KIND

MIN_WEIGHT_BYTES = 100_000

_URLS = {
    "transmitted": "https://raw.githubusercontent.com/gabrielmittag/NISQA/master/weights/nisqa.tar",
    "transmitted_mos": "https://raw.githubusercontent.com/gabrielmittag/NISQA/master/weights/nisqa_mos_only.tar",
    "tts": "https://raw.githubusercontent.com/gabrielmittag/NISQA/master/weights/nisqa_tts.tar",
}


def resolve_weight_download(kind: str | None = None) -> tuple[str, Path]:
    """Return the download URL and local target path for the configured NISQA model."""
    kind = (kind or NISQA_MODEL_KIND).strip().lower()
    if kind == "tts":
        url = _URLS["tts"]
        fname = "nisqa_tts.tar"
    elif kind in ("mos", "mos_only", "transmitted_mos"):
        url = _URLS["transmitted_mos"]
        fname = "nisqa_mos_only.tar"
    else:
        url = _URLS["transmitted"]
        fname = NISQA_WEIGHTS_FILE or "nisqa.tar"

    dest_dir = NISQA_MODEL_DIR
    return url, dest_dir / fname


def ensure_nisqa_weights(*, force: bool = False) -> Path:
    """Ensure the configured NISQA weight file exists and looks complete."""
    url, dest = resolve_weight_download()
    dest_dir = dest.parent
    dest_dir.mkdir(parents=True, exist_ok=True)

    if not force and dest.is_file() and dest.stat().st_size > MIN_WEIGHT_BYTES:
        print(f"已存在: {dest}")
        return dest

    print(f"下载 {url} -> {dest}")
    tmp = dest.with_suffix(dest.suffix + ".download")
    try:
        urllib.request.urlretrieve(url, tmp)
        if tmp.stat().st_size <= MIN_WEIGHT_BYTES:
            raise RuntimeError(f"NISQA 权重下载不完整: {tmp} ({tmp.stat().st_size} bytes)")
        tmp.replace(dest)
    finally:
        if tmp.exists():
            tmp.unlink()
    print("完成。启用：设置环境变量 SPEAKER_NISQA_ENABLED=1")
    return dest


def main() -> int:
    ensure_nisqa_weights()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
