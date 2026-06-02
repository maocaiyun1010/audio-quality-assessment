# -*- coding: utf-8 -*-
"""CLI: probe local MOSS-Audio / SGLang OpenAI-compatible server."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from moss_audio_client import check_moss_server, format_moss_deploy_help  # noqa: E402


def main() -> int:
    url = os.environ.get("MOSS_AUDIO_API_URL", "http://localhost:30000/v1/chat/completions")
    probe = check_moss_server(url)
    print(json.dumps(probe, ensure_ascii=False, indent=2))
    if not probe.get("ok"):
        print("\n" + format_moss_deploy_help(probe), file=sys.stderr)
        return 1
    if probe.get("suggested_url"):
        print(f"\n建议设置 MOSS_AUDIO_API_URL={probe['suggested_url']!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
