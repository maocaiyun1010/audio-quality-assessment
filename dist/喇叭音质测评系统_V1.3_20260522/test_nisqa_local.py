# -*- coding: utf-8 -*-
"""NISQA 旁路模块单元测试（无 PyTorch 推理）。"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from eval_source_summary import build_per_track_rows
from nisqa_local import (
    _nisqa_score_explanation,
    build_nisqa_only_payload,
    discover_audio_files,
    enrich_track_row_with_nisqa,
    has_nisqa_report_data,
    is_enabled,
    resolve_track_wav_paths,
    render_nisqa_appendix_markdown,
    strip_nisqa_appendix_from_section_six,
    weights_ready,
)


class TestNisqaLocal(unittest.TestCase):
    def test_disabled_by_default(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SPEAKER_NISQA_ENABLED", None)
            self.assertFalse(is_enabled())

    def test_resolve_wav_paths(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            rd = Path(td)
            wav = rd / "sess_01_d01_test.wav"
            wav.write_bytes(b"RIFF")
            row = {"file": wav.name, "wav_paths": [str(wav)]}
            paths = resolve_track_wav_paths(row, rd)
            self.assertEqual(len(paths), 1)
            self.assertEqual(paths[0].name, wav.name)

    def test_weights_ready_rejects_partial_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            weight = Path(td) / "nisqa.tar"
            weight.write_bytes(b"partial")
            with patch("nisqa_local.weights_path", return_value=weight):
                self.assertFalse(weights_ready())

            weight.write_bytes(b"x" * 100_001)
            with patch("nisqa_local.weights_path", return_value=weight):
                self.assertTrue(weights_ready())

    def test_enrich_mock_score(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            rd = Path(td)
            wav = rd / "a.wav"
            wav.write_bytes(b"RIFF")
            row = {"file": wav.name}
            fake = {
                "engine": "nisqa",
                "metrics": {"mos_pred": 4.12, "file": wav.name},
            }
            with patch("nisqa_local.is_enabled", return_value=True):
                with patch("nisqa_local.score_wav", return_value=fake):
                    enrich_track_row_with_nisqa(row, recorded_dir=rd)
            self.assertIn("objective_scores", row)
            self.assertEqual(
                row["objective_scores"]["per_file"][0]["metrics"]["mos_pred"],
                4.12,
            )

    def test_appendix_markdown(self) -> None:
        md = render_nisqa_appendix_markdown(
            [
                {
                    "stimulus": "船歌",
                    "objective_scores": {
                        "per_file": [
                            {"metrics": {"mos_pred": 3.5, "file": "a.wav"}},
                        ]
                    },
                }
            ]
        )
        self.assertIn("NISQA", md)
        self.assertIn("3.50", md)
        self.assertIn("船歌", md)

    def test_appendix_markdown_splits_device_a_b_and_diff_summary(self) -> None:
        md = render_nisqa_appendix_markdown(
            [
                {
                    "stimulus": "曲艺/001.wav",
                    "objective_scores": {
                        "per_file": [
                            {
                                "metrics": {
                                    "file": "session_A_01.wav",
                                    "mos_pred": 4.0,
                                    "noi_pred": 3.0,
                                    "dis_pred": 3.5,
                                    "col_pred": 4.1,
                                    "loud_pred": 3.8,
                                }
                            },
                            {
                                "metrics": {
                                    "file": "session_B_01.wav",
                                    "mos_pred": 3.0,
                                    "noi_pred": 2.5,
                                    "dis_pred": 3.4,
                                    "col_pred": 3.0,
                                    "loud_pred": 3.6,
                                }
                            },
                        ]
                    },
                }
            ]
        )
        self.assertIn("#### 录音明细", md)
        self.assertNotIn("#### 设备 A 明细", md)
        self.assertNotIn("#### 设备 B 明细", md)
        self.assertIn("session_A_01.wav", md)
        self.assertIn("session_B_01.wav", md)
        self.assertIn("#### 设备 A 维度平均与分数说明", md)
        self.assertIn("#### 设备 B 维度平均与分数说明", md)
        self.assertIn("#### 设备 A vs B 核心差异汇总", md)
        self.assertIn("| MOS | 4.00 | 3.00 | +1.00 |", md)
        self.assertIn("设备 A 更好", md)
        self.assertIn("差值", md)

    def test_discover_audio_files(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.wav").write_bytes(b"RIFF")
            (root / "readme.txt").write_text("x", encoding="utf-8")
            sub = root / "sub"
            sub.mkdir()
            (sub / "b.mp3").write_bytes(b"ID3")
            found = discover_audio_files(root, recursive=True)
            names = {p.name for p in found}
            self.assertEqual(names, {"a.wav", "b.mp3"})
            found_flat = discover_audio_files(root, recursive=False)
            self.assertEqual([p.name for p in found_flat], ["a.wav"])

    def test_build_nisqa_only_payload(self) -> None:
        tracks = [
            {"ok": True, "file": "a.wav", "objective_scores": {"per_file": []}},
            {"ok": False, "file": "b.wav", "error": "fail"},
        ]
        payload = build_nisqa_only_payload(tracks, source="/tmp")
        self.assertEqual(payload["mode"], "nisqa_only")
        self.assertEqual(payload["summary"]["total"], 2)
        self.assertEqual(payload["summary"]["ok"], 1)

    def test_score_explanation_is_detailed(self) -> None:
        from nisqa_local import nisqa_scale_disclaimer_text

        text = _nisqa_score_explanation(3.2, metric_label="MOS")
        self.assertIn("【中等】", text)
        self.assertIn("3.20", text)
        self.assertNotIn("不可直接换算", text)
        self.assertIn("不可直接换算", nisqa_scale_disclaimer_text())

    def test_device_diff_explanation_is_detailed(self) -> None:
        from nisqa_local import _device_diff_explanation

        text = _device_diff_explanation(
            0.5, metric_label="噪声", avg_a=3.5, avg_b=3.0
        )
        self.assertIn("设备 A 更好", text)
        self.assertIn("0.50", text)

    def test_strip_nisqa_appendix_from_section_six(self) -> None:
        md = "### 综合评价\n\nok\n\n### NISQA 客观音质（本地）\n\n| x |"
        stripped = strip_nisqa_appendix_from_section_six(md)
        self.assertIn("综合评价", stripped)
        self.assertNotIn("NISQA", stripped)

    def test_has_nisqa_report_data(self) -> None:
        self.assertFalse(has_nisqa_report_data([]))
        self.assertTrue(
            has_nisqa_report_data(
                [
                    {
                        "objective_scores": {
                            "per_file": [{"metrics": {"mos_pred": 3.0, "file": "a.wav"}}]
                        }
                    }
                ]
            )
        )

    def test_build_per_track_rows_keeps_objective_scores(self) -> None:
        analysis = {
            "tracks": [
                {
                    "ok": True,
                    "stimulus": "曲艺/001.wav",
                    "file": "001.wav",
                    "parsed": {
                        "声音响度": 1,
                        "人声清晰度": 0,
                        "听感舒适度": 0,
                        "失真与噪声": 0,
                        "频响平衡": 0,
                    },
                    "objective_scores": {
                        "engine": "nisqa",
                        "ok": True,
                        "per_file": [{"metrics": {"mos_pred": 4.1}}],
                    },
                }
            ]
        }
        rows = build_per_track_rows(analysis)
        self.assertEqual(len(rows), 1)
        self.assertIn("objective_scores", rows[0])
        md = render_nisqa_appendix_markdown(rows)
        self.assertIn("4.10", md)


if __name__ == "__main__":
    unittest.main()
