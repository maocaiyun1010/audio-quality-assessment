# -*- coding: utf-8 -*-
"""《喇叭测试报告》模块单元测试（无网络）。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from audio_test_report import (
    build_report_payload_from_analysis,
    generate_audio_test_report,
    _overview_performance_judgment,
)


def _mock_analysis() -> dict:
    return {
        "comparison_mode": True,
        "devices": [
            {"label": "测试机A", "slot": "d01"},
            {"label": "对比机B", "slot": "d02"},
        ],
        "tracks": [
            {
                "ok": True,
                "stimulus": "曲艺_曲艺/003-《船歌》.mp3",
                "parsed": {
                    "声音响度": -2,
                    "人声清晰度": -1,
                    "听感舒适度": 0,
                    "失真与噪声": 1,
                    "频响平衡": -2,
                    "专业点评": "低频浑浊，人声偏干。",
                    "综合评价": "整体劣于对比机。",
                    "综合结论": "劣于",
                },
            },
            {
                "ok": True,
                "stimulus": "语声_语声/01-诵读-赤壁怀古.mp3",
                "parsed": {
                    "声音响度": -2,
                    "人声清晰度": -1,
                    "听感舒适度": -1,
                    "失真与噪声": 0,
                    "频响平衡": -1,
                    "专业点评": "诵读场景人声闷。",
                    "综合结论": "劣于",
                },
            },
        ],
    }


class TestAudioTestReport(unittest.TestCase):
    def test_overview_judgment_core_shortcoming(self) -> None:
        t = _overview_performance_judgment(
            "声音响度",
            -2.0,
            is_best=False,
            is_worst=True,
            sole_advantage=False,
        )
        self.assertIn("核心短板", t)
        self.assertIn("弱于", t)

    def test_build_payload_from_analysis(self) -> None:
        payload = build_report_payload_from_analysis(_mock_analysis())
        self.assertAlmostEqual(payload["total_score_avg"], -0.92, places=1)
        self.assertIn("声音响度", payload["dimension_scores"])
        self.assertTrue(payload["program_details"]["advantage_programs"])
        self.assertTrue(payload["optimization_suggestions"]["dut_priorities"])

    def test_generate_docx(self) -> None:
        payload = build_report_payload_from_analysis(_mock_analysis())
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "音效对比报告_test.docx"
            generate_audio_test_report(payload, str(out))
            self.assertTrue(out.is_file())
            self.assertGreater(out.stat().st_size, 2000)


if __name__ == "__main__":
    unittest.main()
