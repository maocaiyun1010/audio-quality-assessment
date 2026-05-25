# -*- coding: utf-8 -*-
"""
双设备模式快速测试脚本

用于验证新增模块的基本功能，无需启动Web UI。
"""
import sys
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(project_root))


def test_import_modules():
    """测试1：导入新增模块"""
    print("=" * 60)
    print("测试1：导入新增模块")
    print("=" * 60)
    
    try:
        from dual_device_recorder import DualDeviceRecorder
        print("✅ dual_device_recorder 导入成功")
    except Exception as e:
        print(f"❌ dual_device_recorder 导入失败: {e}")
        return False
    
    try:
        from dual_device_scoring import DualDeviceScorer
        print("✅ dual_device_scoring 导入成功")
    except Exception as e:
        print(f"❌ dual_device_scoring 导入失败: {e}")
        return False
    
    return True


def test_recorder_initialization():
    """测试2：录制器初始化"""
    print("\n" + "=" * 60)
    print("测试2：录制器初始化")
    print("=" * 60)
    
    try:
        from dual_device_recorder import DualDeviceRecorder
        
        recorder = DualDeviceRecorder(log=print)
        print(f"✅ 录制器初始化成功")
        print(f"   - audio_a_path: {recorder.audio_a_path}")
        print(f"   - audio_b_path: {recorder.audio_b_path}")
        print(f"   - is_complete: {recorder.is_complete}")
        
        if recorder.audio_a_path is None and recorder.audio_b_path is None and not recorder.is_complete:
            print("✅ 初始状态正确")
            return True
        else:
            print("❌ 初始状态异常")
            return False
            
    except Exception as e:
        print(f"❌ 录制器初始化失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_scorer_initialization():
    """测试3：评分器初始化"""
    print("\n" + "=" * 60)
    print("测试3：评分器初始化")
    print("=" * 60)
    
    try:
        from dual_device_scoring import DualDeviceScorer
        
        scorer = DualDeviceScorer(log=print)
        print(f"✅ 评分器初始化成功")
        print(f"   - DifyClient: {scorer.client is not None}")
        
        return True
            
    except Exception as e:
        print(f"❌ 评分器初始化失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_compute_averages():
    """测试4：平均分计算"""
    print("\n" + "=" * 60)
    print("测试4：平均分计算")
    print("=" * 60)
    
    try:
        from dual_device_scoring import DualDeviceScorer
        
        # 模拟Dify返回的评分数据
        mock_parsed = {
            "声音响度": 2,
            "人声清晰度": 1,
            "听感舒适度": 0,
            "失真与噪声": -1,
            "频响平衡": 1,
            "专业点评": "被测设备在响度和清晰度方面表现更好",
            "对比总结": "整体而言，被测设备略优于对比设备",
        }
        
        stats = DualDeviceScorer.compute_averages_and_conclusion(mock_parsed)
        
        print(f"✅ 平均分计算成功")
        print(f"   - 维度分数: {stats['dimension_scores']}")
        print(f"   - 平均分差: {stats['average_diff']}")
        print(f"   - 结论: {stats['conclusion']}")
        
        # 验证计算结果
        expected_avg = (2 + 1 + 0 + (-1) + 1) / 5  # = 0.6
        if abs(stats['average_diff'] - expected_avg) < 0.01:
            print(f"✅ 平均分计算正确（期望: {expected_avg}, 实际: {stats['average_diff']}）")
        else:
            print(f"❌ 平均分计算错误（期望: {expected_avg}, 实际: {stats['average_diff']}）")
            return False
        
        if "✅" in stats['conclusion'] or "优于" in stats['conclusion']:
            print("✅ 结论生成正确")
        else:
            print(f"⚠️  结论可能不符合预期: {stats['conclusion']}")
        
        return True
            
    except Exception as e:
        print(f"❌ 平均分计算失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_web_ui_syntax():
    """测试5：web_ui.py 语法检查"""
    print("\n" + "=" * 60)
    print("测试5：web_ui.py 语法检查")
    print("=" * 60)
    
    try:
        import py_compile
        web_ui_path = project_root / "web_ui.py"
        
        if not web_ui_path.exists():
            print(f"❌ web_ui.py 不存在: {web_ui_path}")
            return False
        
        py_compile.compile(str(web_ui_path), doraise=True)
        print(f"✅ web_ui.py 语法检查通过")
        return True
            
    except py_compile.PyCompileError as e:
        print(f"❌ web_ui.py 语法错误: {e}")
        return False
    except Exception as e:
        print(f"❌ web_ui.py 检查失败: {e}")
        return False


def test_file_structure():
    """测试6：文件结构检查"""
    print("\n" + "=" * 60)
    print("测试6：文件结构检查")
    print("=" * 60)
    
    required_files = [
        "dual_device_recorder.py",
        "dual_device_scoring.py",
        "web_ui.py",
        "双设备模式使用说明.md",
        "双设备模式实现总结.md",
    ]
    
    all_exist = True
    for filename in required_files:
        filepath = project_root / filename
        if filepath.exists():
            size = filepath.stat().st_size
            print(f"✅ {filename} 存在 ({size} 字节)")
        else:
            print(f"❌ {filename} 不存在")
            all_exist = False
    
    return all_exist


def main():
    """运行所有测试"""
    print("\n")
    print("╔" + "=" * 58 + "╗")
    print("║" + " " * 10 + "双设备模式功能测试" + " " * 28 + "║")
    print("╚" + "=" * 58 + "╝")
    print()
    
    tests = [
        ("导入新增模块", test_import_modules),
        ("录制器初始化", test_recorder_initialization),
        ("评分器初始化", test_scorer_initialization),
        ("平均分计算", test_compute_averages),
        ("web_ui.py语法", test_web_ui_syntax),
        ("文件结构", test_file_structure),
    ]
    
    results = []
    for name, test_func in tests:
        try:
            result = test_func()
            results.append((name, result))
        except Exception as e:
            print(f"\n❌ 测试 [{name}] 发生异常: {e}")
            import traceback
            traceback.print_exc()
            results.append((name, False))
    
    # 打印总结
    print("\n" + "=" * 60)
    print("测试总结")
    print("=" * 60)
    
    passed = sum(1 for _, r in results if r)
    total = len(results)
    
    for name, result in results:
        status = "✅ 通过" if result else "❌ 失败"
        print(f"{status} - {name}")
    
    print("-" * 60)
    print(f"总计: {passed}/{total} 通过")
    
    if passed == total:
        print("\n🎉 所有测试通过！双设备模式已就绪。")
        print("\n下一步：")
        print("1. 启动 Web UI: streamlit run web_ui.py")
        print("2. 在页面顶部选择「双设备单麦对比模式」")
        print("3. 按照提示进行分步录制和测评")
        return 0
    else:
        print(f"\n⚠️  有 {total - passed} 个测试失败，请检查上述错误信息。")
        return 1


if __name__ == "__main__":
    sys.exit(main())
