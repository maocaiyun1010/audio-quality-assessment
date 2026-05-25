# -*- coding: utf-8 -*-
"""
修复web_ui.py中重复的双设备录制按钮代码
"""

# 读取文件
with open('web_ui.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# 找到需要删除的行范围（1361-1409行，索引从0开始，所以是1360-1408）
# 删除从"# 分步录制按钮"到"# 播放预览"之前的所有行
start_idx = None
end_idx = None

for i, line in enumerate(lines):
    if '# 分步录制按钮' in line and i > 1350 and i < 1370:
        start_idx = i
    if start_idx is not None and '# 播放预览' in line and end_idx is None:
        end_idx = i
        break

if start_idx is not None and end_idx is not None:
    # 删除这些行
    new_lines = lines[:start_idx] + lines[end_idx:]
    
    # 写入文件
    with open('web_ui.py', 'w', encoding='utf-8') as f:
        f.writelines(new_lines)
    
    print(f"已删除第 {start_idx+1} 到 {end_idx} 行")
else:
    print(f"未找到需要删除的行: start_idx={start_idx}, end_idx={end_idx}")
