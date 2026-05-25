# -*- coding: utf-8 -*-
"""工程化可信度优化的低风险单元测试（无硬件、无网络）。"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

import difyclient as _difyclient

from local_service import _assert_authorized
from scoring import compose_stimulus_compare_extra_instruction, stimulus_compare_prompt_mode
from markdown_report import (
    DIMENSION_KEYS,
    _pairwise_comprehensive_text,
    build_dim_conclusion_notes,
    build_section_six_markdown,
    compute_dimension_statistics,
    render_pairwise_comprehensive_evaluation_md,
    template_dim_verdict,
)
from eval_source_summary import (
    _pick_source_name,
    build_per_track_rows,
    display_source_name_from_stimulus,
    stamp_parsed_with_stimulus,
)
from web_ui_multi_model_reports import write_multi_model_consistency_report


class TestEngineeringTrustOptimizations(unittest.TestCase):
    def tearDown(self) -> None:
        _difyclient._provider_alias_map_cache = None

    def test_optional_service_token_allows_default_compatibility(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SPEAKER_SERVICE_TOKEN", None)
            _assert_authorized(None)

    def test_optional_service_token_rejects_wrong_header(self) -> None:
        with patch.dict(os.environ, {"SPEAKER_SERVICE_TOKEN": "secret"}, clear=False):
            with self.assertRaises(HTTPException):
                _assert_authorized("wrong")
            _assert_authorized("secret")

    def test_write_multi_model_consistency_report(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            score_a = root / "web_ui_scores_demo.json"
            score_b = root / "web_ui_scores_demo__B.json"
            dims_a = {
                "声音响度": 7.0,
                "人声清晰度": 8.0,
                "听感舒适度": 7.5,
                "失真与噪声": 7.0,
                "频响平衡": 7.5,
            }
            dims_b = {
                "声音响度": 7.2,
                "人声清晰度": 7.8,
                "听感舒适度": 7.4,
                "失真与噪声": 7.1,
                "频响平衡": 7.6,
            }
            for path, dims, model in ((score_a, dims_a, "A"), (score_b, dims_b, "B")):
                path.write_text(
                    json.dumps(
                        {
                            "dut_scores": dims,
                            "ref_scores": {k: 7.0 for k in dims},
                            "web_ui_eval_model": model,
                            "scoring_quality": {
                                "total_tracks": 3,
                                "ok_tracks": 3,
                                "failed_tracks": 0,
                            },
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
            with patch("config.REPORT_DIR", root):
                out = write_multi_model_consistency_report(
                    primary_score_json=str(score_a),
                    extra_reports=[{"model": "B", "score_json": str(score_b)}],
                    primary_model="A",
                )
            self.assertTrue(Path(out["markdown"]).is_file())
            self.assertTrue(Path(out["tsv"]).is_file())
            self.assertTrue(Path(out["json"]).is_file())
            md = Path(out["markdown"]).read_text(encoding="utf-8")
            self.assertIn("多模型一致性统计", md)
            self.assertIn("五维一致性统计", md)

    def test_doubao_selected_model_alias_lowercase(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SPEAKER_DISABLE_PROVIDER_MODEL_ALIAS", None)
            os.environ.pop("SPEAKER_PROVIDER_MODEL_MAP_PATH", None)
        _difyclient._provider_alias_map_cache = None
        self.assertEqual(
            _difyclient.resolve_selected_model_for_dify_inputs("Doubao-Seed-2.0-pro"),
            "doubao-seed-2.0-pro",
        )

    def test_selected_model_alias_respect_disable_env(self) -> None:
        with patch.dict(os.environ, {"SPEAKER_DISABLE_PROVIDER_MODEL_ALIAS": "1"}):
            self.assertEqual(
                _difyclient.resolve_selected_model_for_dify_inputs("Doubao-Seed-2.0-pro"),
                "Doubao-Seed-2.0-pro",
            )

    def test_user_provider_model_map_overrides_builtin(self) -> None:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump({"Doubao-Seed-2.0-pro": "ep-test-override"}, f, ensure_ascii=False)
            p = f.name
        try:
            with patch.dict(os.environ, {"SPEAKER_PROVIDER_MODEL_MAP_PATH": p}):
                _difyclient._provider_alias_map_cache = None
                self.assertEqual(
                    _difyclient.resolve_selected_model_for_dify_inputs("Doubao-Seed-2.0-pro"),
                    "ep-test-override",
                )
        finally:
            Path(p).unlink(missing_ok=True)
            os.environ.pop("SPEAKER_PROVIDER_MODEL_MAP_PATH", None)

    def test_compose_stimulus_final_mode_no_supplement_wrapper(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SPEAKER_DIFY_APPEND_BUILTIN_PROMPT", None)
        with patch(
            "scoring._read_prompt_overrides_json",
            return_value={
                "stimulus_compare_extras": "【用户完整提示词】仅输出 JSON",
                "stimulus_compare_prompt_mode": "final",
            },
        ):
            extra, mode = compose_stimulus_compare_extra_instruction(
                "【角色】第1附件=被测"
            )
            self.assertEqual(mode, "final")
            self.assertIn("【用户完整提示词】", extra)
            self.assertIn("【角色】", extra)
            self.assertNotIn("【自 Web UI 保存的补充说明】", extra)

    def test_display_source_name_short_title(self) -> None:
        stim = "曲艺_曲艺/003-低音-《船歌》-赵鹏 60'' Nb.mp3"
        self.assertEqual(display_source_name_from_stimulus(stim), "船歌")
        self.assertEqual(
            display_source_name_from_stimulus(
                "语声_语声/01-诵读-赤壁怀古-苏轼 1'49'' Nb.mp3"
            ),
            "赤壁怀古",
        )

    def test_source_name_prefers_stimulus_over_model_hallucination(self) -> None:
        stim = "曲艺_曲艺/003-低音-《船歌》-赵鹏 60'' Nb.mp3"
        parsed = {"音源名称": "古风女声朗读", "分组": "曲艺_曲艺", "声音响度": -1}
        track = {"ok": True, "stimulus": stim, "parsed": parsed}
        self.assertEqual(_pick_source_name(parsed, track), "船歌")
        rows = build_per_track_rows(
            {
                "tracks": [
                    {
                        **track,
                        "parsed": {
                            **parsed,
                            "人声清晰度": -1,
                            "听感舒适度": -2,
                            "失真与噪声": -1,
                            "频响平衡": -2,
                        },
                    }
                ]
            }
        )
        self.assertEqual(rows[0]["音源名称"], "船歌")

    def test_section_six_table_includes_conclusion_notes(self) -> None:
        dim_avgs = {
            "声音响度": -1.2,
            "人声清晰度": 1.5,
            "听感舒适度": -1.8,
            "失真与噪声": 0.1,
            "频响平衡": -0.2,
        }
        _, grand = compute_dimension_statistics(
            [
                {
                    "声音响度": -1,
                    "人声清晰度": 2,
                    "听感舒适度": -2,
                    "失真与噪声": 0,
                    "频响平衡": 0,
                    "综合结论": "劣于",
                }
            ]
        )
        notes = build_dim_conclusion_notes(dim_avgs, comparison_mode=True)
        self.assertEqual(notes["人声清晰度"], "为核心优势维度")
        self.assertEqual(notes["听感舒适度"], "为主要短板维度")
        md = build_section_six_markdown(
            comparison_mode=True,
            dim_avgs=dim_avgs,
            grand=grand,
            rows=[{"综合结论": "劣于"}],
        )
        self.assertIn("| 结论说明 |", md)
        self.assertIn("显著弱于对比机", md)
        self.assertIn("为核心优势维度", md)

    def test_comparison_summary_falls_back_to_comprehensive_eval(self) -> None:
        rows = build_per_track_rows(
            {
                "tracks": [
                    {
                        "ok": True,
                        "stimulus": "曲艺_曲艺/003-低音-《船歌》-赵鹏.mp3",
                        "parsed": {
                            "分组": "曲艺_曲艺",
                            "声音响度": -1,
                            "人声清晰度": 2,
                            "听感舒适度": -2,
                            "失真与噪声": -1,
                            "频响平衡": -2,
                            "综合结论": "劣于",
                            "综合评价": "被测低频浑浊，整体听感劣于对比机。",
                            "专业点评": "人声解析较好。",
                        },
                    }
                ]
            }
        )
        self.assertEqual(rows[0]["综合结论"], "劣于")
        self.assertEqual(rows[0]["对比总结"], "被测低频浑浊，整体听感劣于对比机。")

    def test_pairwise_comprehensive_no_placeholder_when_all_dims_negative(self) -> None:
        rows = [
            {
                "音源名称": "船歌",
                "分组": "曲艺_曲艺",
                "综合结论": "劣于",
                "声音响度": -1,
                "人声清晰度": -1,
                "听感舒适度": -2,
                "失真与噪声": -1,
                "频响平衡": -2,
            },
            {
                "音源名称": "月亮代表我的心",
                "分组": "曲艺_曲艺",
                "综合结论": "相当",
                "声音响度": 0,
                "人声清晰度": -1,
                "听感舒适度": -1,
                "失真与噪声": 0,
                "频响平衡": -1,
            },
        ]
        avgs, grand = compute_dimension_statistics(rows)
        text = _pairwise_comprehensive_text(avgs, grand, rows)
        self.assertNotIn("——", text)
        self.assertIn("## 一、分维度表现（全节目平均分差）", text)
        self.assertIn("综合判定：测试机相对对比机整体：", text)
        self.assertIn("## 四、调音与复测建议", text)
        for k in DIMENSION_KEYS:
            self.assertIn(k, text)

    def test_template_dim_verdict_words(self) -> None:
        self.assertEqual(template_dim_verdict(-1.5), "显著弱于对比机")
        self.assertEqual(template_dim_verdict(-0.5), "略弱于对比机")
        self.assertEqual(template_dim_verdict(0.1), "持平")
        self.assertEqual(template_dim_verdict(0.5), "略优于对比机")
        self.assertEqual(template_dim_verdict(1.2), "显著优于对比机")

    def test_fixed_comprehensive_template_structure(self) -> None:
        md = render_pairwise_comprehensive_evaluation_md(
            {
                "声音响度": -1.5,
                "人声清晰度": 0.83,
                "听感舒适度": -1.5,
                "失真与噪声": -0.33,
                "频响平衡": -0.83,
            },
            -0.67,
            [{"音源名称": "船歌", "声音响度": -2, "人声清晰度": 1, "听感舒适度": -2, "失真与噪声": 0, "频响平衡": -2}],
        )
        self.assertIn("本次评测共 1 个标准节目", md)
        self.assertIn("全节目平均分差（测试机相对对比机）", md)
        self.assertIn("| 结论说明 |", md)
        self.assertIn("所有维度总平均分差", md)
        self.assertIn("## 五、报告说明", md)
        sec6 = build_section_six_markdown(
            comparison_mode=True,
            dim_avgs={"声音响度": -1.5, "人声清晰度": 0.83, "听感舒适度": -1.5, "失真与噪声": -0.33, "频响平衡": -0.83},
            grand=-0.67,
            rows=[],
        )
        self.assertNotIn("### 优化建议", sec6)
        self.assertNotIn("### 五维评分结果表", sec6)
        self.assertIn("### 综合评价", sec6)

    def test_stamp_parsed_with_stimulus_overwrites_model_name(self) -> None:
        stim = "曲艺_曲艺/003-低音-《船歌》-赵鹏 60'' Nb.mp3"
        out = stamp_parsed_with_stimulus({"音源名称": "古风女声朗读"}, stim)
        self.assertEqual(out["音源名称"], "船歌")
        self.assertEqual(out["分组"], "曲艺_曲艺")


if __name__ == "__main__":
    unittest.main()
