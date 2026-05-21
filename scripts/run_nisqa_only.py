# -*- coding: utf-8 -*-
"""独立批量 NISQA 客观音质评分（无需 Dify / Web UI）。

示例::

    python scripts/run_nisqa_only.py -d output/recorded
    python scripts/run_nisqa_only.py -d output/recorded -o output/nisqa/out.json --csv output/nisqa/out.csv
    python scripts/run_nisqa_only.py --status
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nisqa_local import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
