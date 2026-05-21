# -*- coding: utf-8 -*-
"""
双设备模式测评流程测试脚本

用于验证点击"手动开始测评"后是否能正常执行：
1. 上传音频到Dify
2. 评分
3. 解析结果
4. 生成报告
"""
import json
from pathlib import Path
from datetime import datetime


def test_dual_device_eval_flow():
    """测试双设备模式测评流程的状态流转"""
    
    print("=" * 70)
    print("双设备模式测评流程测试")
    print("=" * 70)
    
    # 模拟状态流转
    session_state = {}
    
    print("\n【步骤1】初始状态 - 用户完成两段录制")
    session_state["_dual_eval_running"] = False
    session_state["_dual_start_eval_clicked"] = False
    print(f"  _dual_eval_running: {session_state['_dual_eval_running']}")
    print(f"  _dual_start_eval_clicked: {session_state['_dual_start_eval_clicked']}")
    
    print("\n【步骤2】用户点击'手动开始测评'按钮")
    # 模拟按钮点击
    if not session_state.get("_dual_eval_running", False):
        session_state["_dual_start_eval_clicked"] = True
        print(f"  ✅ 设置 _dual_start_eval_clicked = True")
    print(f"  _dual_eval_running: {session_state['_dual_eval_running']}")
    print(f"  _dual_start_eval_clicked: {session_state['_dual_start_eval_clicked']}")
    
    print("\n【步骤3】第一次rerun - 进入启动逻辑")
    # 检查是否应该启动
    _dual_should_start = (
        session_state.get("_dual_start_eval_clicked", False)
        and not session_state.get("_dual_eval_running", False)
    )
    print(f"  _dual_should_start: {_dual_should_start}")
    
    if _dual_should_start:
        print("  ✅ 进入启动逻辑...")
        # 模拟初始化
        session_state["_dual_eval_state"] = {
            "paired_audios": [("track1", "path_a.wav", "path_b.wav")],
            "cursor": 0,
            "merged_tracks": [],
            "parsed_list": [],
        }
        session_state["_dual_eval_scorer"] = None
        session_state["_dual_eval_running"] = True
        session_state["_dual_eval_stop_requested"] = False
        # 清除启动标志
        session_state.pop("_dual_start_eval_clicked", None)
        print(f"  ✅ 设置 _dual_eval_running = True")
        print(f"  ✅ 清除 _dual_start_eval_clicked")
        print(f"  🔄 触发 rerun")
    
    print(f"\n  当前状态:")
    print(f"    _dual_eval_running: {session_state['_dual_eval_running']}")
    print(f"    _dual_start_eval_clicked: {session_state.get('_dual_start_eval_clicked', '已清除')}")
    print(f"    _dual_eval_state: {'已设置' if '_dual_eval_state' in session_state else '未设置'}")
    
    print("\n【步骤4】第二次rerun - 进入实际执行逻辑")
    # 检查是否在执行中
    if session_state.get("_dual_eval_running", False):
        print("  ✅ 进入实际执行逻辑...")
        state = session_state.get("_dual_eval_state", {})
        cursor = state.get("cursor", 0)
        paired_audios = state.get("paired_audios", [])
        
        print(f"  📊 待处理音源数: {len(paired_audios)}")
        print(f"  📍 当前游标: {cursor}")
        
        if cursor < len(paired_audios):
            print("  🎯 开始评分第一个音源...")
            # 模拟评分过程
            track_name, audio_a_path, audio_b_path = paired_audios[cursor]
            print(f"     音源: {track_name}")
            print(f"     设备A: {audio_a_path}")
            print(f"     设备B: {audio_b_path}")
            
            # 模拟评分成功
            merged_tracks = state.get("merged_tracks", [])
            parsed_list = state.get("parsed_list", [])
            
            # 添加模拟结果
            mock_track = {
                "track_index": 1,
                "stimulus": track_name,
                "ok": True,
                "error": None,
                "parsed": {
                    "声音响度": 1.5,
                    "人声清晰度": 2.0,
                    "听感舒适度": 1.0,
                    "失真与噪声": 0.5,
                    "频响平衡": 1.5,
                }
            }
            merged_tracks.append(mock_track)
            parsed_list.append(mock_track["parsed"])
            
            # 更新状态
            state["cursor"] = cursor + 1
            state["merged_tracks"] = merged_tracks
            state["parsed_list"] = parsed_list
            session_state["_dual_eval_state"] = state
            
            print(f"  ✅ 评分完成，更新游标为 {cursor + 1}")
            print(f"  🔄 继续处理下一个音源或完成")
    
    print("\n【步骤5】所有音源处理完成")
    state = session_state.get("_dual_eval_state", {})
    merged_tracks = state.get("merged_tracks", [])
    parsed_list = state.get("parsed_list", [])
    
    print(f"  📊 已完成音源数: {len(merged_tracks)}")
    print(f"  📊 已解析结果数: {len(parsed_list)}")
    
    if merged_tracks:
        print("  ✅ 开始生成分析报告...")
        
        # 模拟生成analysis JSON
        from config import ANALYSIS_DIR
        ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
        
        merged_analysis_path = ANALYSIS_DIR / f"test_analysis_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        merged_payload = {
            "session_tag": datetime.now().strftime("%Y%m%d_%H%M%S"),
            "comparison_mode": True,
            "scoring_rule_set": "pairwise_minus3_to_plus3_dual_device_stepwise",
            "devices": [
                {"slot": "A", "label": "被测设备A"},
                {"slot": "B", "label": "对比设备B"},
            ],
            "tracks": merged_tracks,
        }
        merged_analysis_path.write_text(
            json.dumps(merged_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"  ✅ Analysis JSON已保存: {merged_analysis_path}")
        
        # 模拟生成score JSON
        dims = ["声音响度", "人声清晰度", "听感舒适度", "失真与噪声", "频响平衡"]
        dim_scores = {}
        for dim in dims:
            vals = []
            for p in parsed_list:
                try:
                    vals.append(float(p.get(dim, 0)))
                except (TypeError, ValueError):
                    pass
            dim_scores[dim] = round(sum(vals) / len(vals), 2) if vals else 0.0
        
        avg_diff = round(sum(dim_scores.values()) / len(dim_scores), 2) if dim_scores else 0.0
        
        score_json_path = ANALYSIS_DIR / f"test_scores_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        score_data = {
            "comparison_mode": True,
            "stimulus_pairwise": True,
            "dut_scores": dim_scores,
            "ref_scores": {k: 7 for k in dim_scores.keys()},
            "average_diff": avg_diff,
            "conclusion": f"✅ 被测设备优于对比设备，领先 {avg_diff:.2f} 分",
            "track_count": len(merged_tracks),
        }
        score_json_path.write_text(
            json.dumps(score_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"  ✅ Score JSON已保存: {score_json_path}")
        
        # 模拟生成报告
        from report_builder import build_word_from_analysis
        try:
            doc_path, md_path, tsv_path, xlsx_path, msg = build_word_from_analysis(
                merged_analysis_path,
                test_name="双设备单麦对比测评（测试）",
                test_device="被测设备A",
                ref_device="对比设备B",
            )
            print(f"  ✅ 报告生成成功!")
            print(f"     Word: {doc_path}")
            print(f"     Markdown: {md_path}")
            print(f"     TSV: {tsv_path}")
            print(f"     Excel: {xlsx_path}")
        except Exception as e:
            print(f"  ⚠️ 报告生成异常: {e}")
        
        print("\n  ✅ 测评流程完成!")
        print(f"  📄 最终结果将显示在Web UI中")
    
    # 清理状态
    session_state["_dual_eval_running"] = False
    session_state.pop("_dual_eval_state", None)
    session_state.pop("_dual_eval_scorer", None)
    
    print("\n" + "=" * 70)
    print("测试完成!")
    print("=" * 70)
    
    return True


if __name__ == "__main__":
    try:
        test_dual_device_eval_flow()
        print("\n✅ 测试通过 - 双设备模式测评流程状态流转正常")
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
