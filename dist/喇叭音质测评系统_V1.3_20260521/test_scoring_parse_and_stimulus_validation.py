# -*- coding: utf-8 -*-
"""scoring：JSON 提取与刺激比较五维校验（无网络、无 Dify）。"""
from __future__ import annotations

import json
import unittest

from scoring import (
    SCORING_FIVE_DIM_KEYS,
    _extract_json_object,
    validate_and_normalize_stimulus_compare_five_dims,
)


def _sample_dims(**overrides: object) -> dict:
    base = {k: 0 for k in SCORING_FIVE_DIM_KEYS}
    base.update(overrides)
    return base


class TestScoringJsonAndStimulus(unittest.TestCase):
    def test_extract_json_nested_fences_and_preamble(self) -> None:
        raw = """Here is the result.
```json
{"声音响度": 1, "人声清晰度": -1, "听感舒适度": 0, "失真与噪声": 2, "频响平衡": -2, "专业点评": "x"}
```
"""
        got = _extract_json_object(raw)
        self.assertIsNotNone(got)
        for k in SCORING_FIVE_DIM_KEYS:
            self.assertIn(k, got)

    def test_extract_json_second_fence_when_first_is_noise(self) -> None:
        raw = """```text
not json
```
```json
{"声音响度": 3, "人声清晰度": 3, "听感舒适度": 3, "失真与噪声": 3, "频响平衡": 3}
```"""
        got = _extract_json_object(raw)
        self.assertIsNotNone(got)
        self.assertEqual(got["声音响度"], 3)

    def test_extract_json_think_wrapper_stripped(self) -> None:
        inner = json.dumps(
            _sample_dims(声音响度=1, 专业点评="ok"),
            ensure_ascii=False,
        )
        raw = f"<think>noise</think>\n```json\n{inner}\n```"
        got = _extract_json_object(raw)
        self.assertIsNotNone(got)
        self.assertEqual(got["声音响度"], 1)

    def test_validate_stimulus_all_valid_integers(self) -> None:
        d = _sample_dims(声音响度=-3, 人声清晰度=3)
        ok, err = validate_and_normalize_stimulus_compare_five_dims(d)
        self.assertIsNone(err)
        self.assertIsNotNone(ok)
        self.assertEqual(ok["声音响度"], -3)
        self.assertIsInstance(ok["声音响度"], int)

    def test_validate_stimulus_rejects_out_of_range(self) -> None:
        d = _sample_dims(声音响度=10)
        ok, err = validate_and_normalize_stimulus_compare_five_dims(d)
        self.assertIsNone(ok)
        self.assertIsNotNone(err)
        self.assertIn("异常评分", err)
        self.assertIn("10", err)

    def test_validate_stimulus_rejects_float_non_integer(self) -> None:
        d = _sample_dims(声音响度=1.5)
        ok, err = validate_and_normalize_stimulus_compare_five_dims(d)
        self.assertIsNone(ok)
        self.assertIn("须为整数", err or "")

    def test_validate_stimulus_accepts_whole_float(self) -> None:
        d = _sample_dims(声音响度=2.0)
        ok, err = validate_and_normalize_stimulus_compare_five_dims(d)
        self.assertIsNone(err)
        self.assertIsNotNone(ok)
        self.assertEqual(ok["声音响度"], 2)

    def test_validate_stimulus_string_digit(self) -> None:
        d = _sample_dims(声音响度="-1")
        ok, err = validate_and_normalize_stimulus_compare_five_dims(d)
        self.assertIsNone(err)
        self.assertIsNotNone(ok)
        self.assertEqual(ok["声音响度"], -1)


if __name__ == "__main__":
    unittest.main()
