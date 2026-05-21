# -*- coding: utf-8 -*-
"""
临时脚本：修复web_ui.py中双设备录制按钮的log_box引用问题
"""
import re

# 读取文件
with open('web_ui.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 替换step1_col中的log_box调用
content = re.sub(
    r'(with step1_col:.*?try:\s*)log_box\.info\("▶ 开始录制【被测设备A】\.\.\."\)\s*path_a = recorder\.record_device_a\(duration=float\(duration\), gain_db=float\(gain\)\)\s*log_box\.success\(f"✅ 【被测设备A】录制完成：\{Path\(path_a\)\.name\}"\)\s*st\.rerun\(\)\s*except Exception as e:\s*log_box\.error\(f"❌ 录制失败：\{e\}"\)',
    r'\1st.info("▶ 开始录制【被测设备A】...")\n                # 应用麦克风配置\n                _patch_input_device(_mic_spec)\n                path_a = recorder.record_device_a(duration=float(duration), gain_db=float(gain))\n                st.success(f"✅ 【被测设备A】录制完成：{Path(path_a).name}")\n                st.rerun()\n            except Exception as e:\n                st.error(f"❌ 录制失败：{e}")',
    content,
    flags=re.DOTALL
)

# 替换step2_col中的log_box调用
content = re.sub(
    r'(with step2_col:.*?try:\s*)log_box\.info\("▶ 开始录制【对比设备B】\.\.\."\)\s*path_b = recorder\.record_device_b\(duration=float\(duration\), gain_db=float\(gain\)\)\s*log_box\.success\(f"✅ 【对比设备B】录制完成：\{Path\(path_b\)\.name\}"\)\s*st\.rerun\(\)\s*except Exception as e:\s*log_box\.error\(f"❌ 录制失败：\{e\}"\)',
    r'\1st.info("▶ 开始录制【对比设备B】...")\n                # 应用麦克风配置\n                _patch_input_device(_mic_spec)\n                path_b = recorder.record_device_b(duration=float(duration), gain_db=float(gain))\n                st.success(f"✅ 【对比设备B】录制完成：{Path(path_b).name}")\n                st.rerun()\n            except Exception as e:\n                st.error(f"❌ 录制失败：{e}")',
    content,
    flags=re.DOTALL
)

# 替换clear_col中的log_box调用
content = re.sub(
    r'(recorder\.clear_recordings\(\)\s*)log_box\.info\("🗑️ 已清除所有录制文件"\)',
    r'\1st.info("🗑️ 已清除所有录制文件")',
    content
)

# 写入文件
with open('web_ui.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("修复完成！")
