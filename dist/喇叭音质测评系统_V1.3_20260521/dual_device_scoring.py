# -*- coding: utf-8 -*-
"""
双设备分步对比测评模式（Web）：封装结果打包；Dify 调用与常规流水线共用 ``scoring.run_pairwise_stimulus_dify_compare``。
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from audio_model_client import create_audio_model_client
from config import ANALYSIS_DIR, ensure_output_dirs
from scoring import (
    compose_stimulus_compare_extra_instruction,
    eval_model_tags_for_track_row,
    run_pairwise_stimulus_dify_compare,
    stimulus_compare_prompt_mode,
)


class DualDeviceScorer:
    """双设备分步对比评分器（使用刺激比较-3+3规则）。"""

    def __init__(self, log: Optional[Callable[[str], None]] = None):
        self.log = log or (lambda _m: None)
        self.client = create_audio_model_client(log=log)

    def score_dual_device_comparison(
        self,
        audio_a_path: str,
        audio_b_path: str,
        device_a_label: str = "被测设备A",
        device_b_label: str = "对比设备B",
        stimulus_label: str = "双设备对比音源",
        persist_analysis: bool = True,
    ) -> tuple[Optional[Path], dict[str, Any]]:
        """
        对两段音频进行刺激比较评分（-3～+3分差规则）。

        Args:
            audio_a_path: 被测设备A的WAV路径
            audio_b_path: 对比设备B的WAV路径
            device_a_label: 被测设备标签
            device_b_label: 对比设备标签
            stimulus_label: 音源标识

        Returns:
            (analysis_json_path, result_dict)
        """
        ensure_output_dirs()

        self.log(f"🧠 开始双设备对比评分（刺激比较 -3～+3）...")
        self.log(f"   被测设备A: {device_a_label}")
        self.log(f"   对比设备B: {device_b_label}")

        if stimulus_compare_prompt_mode() == "final":
            runtime = (
                f"【本轮附件角色】\n"
                f"- 第 1 个音频 = 被测（{device_a_label}）\n"
                f"- 第 2 个音频 = 对比（{device_b_label}）\n"
            )
        else:
            from scoring import build_dual_stepwise_pairwise_extra_instruction

            runtime = build_dual_stepwise_pairwise_extra_instruction(
                device_a_label, device_b_label
            )
        extra_instruction, prompt_mode = compose_stimulus_compare_extra_instruction(
            runtime
        )

        paths_ordered = [audio_a_path, audio_b_path]
        slots_order = ["被测设备A", "对比设备B"]

        text, parsed, err = run_pairwise_stimulus_dify_compare(
            self.client,
            paths_ordered=paths_ordered,
            slots_order=slots_order,
            stimulus_label=stimulus_label,
            extra_instruction=extra_instruction,
            comparison_variant="dual_device_stepwise",
            log=self.log,
            log_prefix="[双设备对比]",
            prompt_mode=prompt_mode,
        )

        if err:
            self.log(f"❌ 评分失败: {err}")
            _tags = eval_model_tags_for_track_row()
            return None, {
                "ok": False,
                "error": err,
                "raw": text,
                "parsed": None,
                **_tags,
            }

        self.log("✅ 本轨评分成功：JSON 已解析且五维校验通过")
        self.log("📎 正在写入本轨结果并刷新界面（若长时间无新日志，请看上方状态条或是否在生成总报告）…")

        # 构建结果数据
        session_tag = datetime.now().strftime("%Y%m%d_%H%M%S")
        _tags = eval_model_tags_for_track_row()
        result = {
            "session_tag": session_tag,
            "comparison_mode": True,
            "scoring_rule_set": "pairwise_minus3_to_plus3_dual_device_stepwise",
            "devices": [
                {"slot": "A", "label": device_a_label},
                {"slot": "B", "label": device_b_label},
            ],
            "tracks": [
                {
                    "track_index": 1,
                    "stimulus": stimulus_label,
                    "scoring_mode": "stimulus_compare_dual_device",
                    "file": Path(audio_a_path).name,
                    "wav_paths": paths_ordered,
                    "ok": True,
                    "error": None,
                    "raw": text,
                    "parsed": parsed,
                    **_tags,
                }
            ],
        }
        try:
            from nisqa_local import enrich_track_row_with_nisqa, is_enabled

            if is_enabled():
                from config import RECORDED_DIR

                enrich_track_row_with_nisqa(
                    result["tracks"][0], recorded_dir=RECORDED_DIR
                )
        except Exception:
            pass
        if _tags.get("eval_model"):
            result["eval_model"] = _tags["eval_model"]
        if _tags.get("dify_selected_model"):
            result["dify_selected_model"] = _tags["dify_selected_model"]

        # 写入 analysis JSON（可选；Web UI 批量评测时可关闭以减少 I/O）
        if not persist_analysis:
            return None, result

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = ANALYSIS_DIR / f"analysis_dual_device_{session_tag}_{ts}.json"
        try:
            out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            self.log(f"📄 分析结果已保存: {out_path}")
        except Exception as exc:
            self.log(f"⚠️ 写入JSON失败: {exc}")
            return None, result

        return out_path, result

    @staticmethod
    def compute_averages_and_conclusion(parsed: dict[str, Any]) -> dict[str, Any]:
        """
        计算五维度平均分和总体结论。

        Args:
            parsed: Dify返回的评分JSON

        Returns:
            包含各维度分数、平均分、结论的字典
        """
        dimensions = [
            "声音响度",
            "人声清晰度",
            "听感舒适度",
            "失真与噪声",
            "频响平衡",
        ]

        scores = {}
        for dim in dimensions:
            val = parsed.get(dim)
            try:
                scores[dim] = int(val) if val is not None else 0
            except (TypeError, ValueError):
                scores[dim] = 0

        # 计算平均分差
        avg_diff = sum(scores.values()) / len(scores)

        # 生成结论
        if avg_diff > 1.0:
            conclusion = f"✅ 被测设备显著优于对比设备，领先 {avg_diff:.2f} 分"
        elif avg_diff > 0.3:
            conclusion = f"✅ 被测设备优于对比设备"
        elif abs(avg_diff) <= 0.3:
            conclusion = f"⚖️ 两设备音质相当"
        else:
            conclusion = f"⚠️ 被测设备略逊于对比设备"

        return {
            "dimension_scores": scores,
            "average_diff": round(avg_diff, 2),
            "conclusion": conclusion,
            "professional_comment": parsed.get("专业点评", ""),
            "comparison_summary": parsed.get("对比总结") or parsed.get("综合评价") or "",
        }
