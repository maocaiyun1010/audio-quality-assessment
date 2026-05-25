# Android 喇叭音效自动化评测

本地一键完成：**ADB 设备枚举 → 多设备同步采集 → Dify AI 评分 → Word/Markdown/TSV/Excel 报告**。支持命令行与可选 **FastAPI** 供 Dify 工作流 HTTP 调用。

---

## 支持设备

- **Android 手机 / 平板**（需开启 USB 调试或无线 ADB，且 `adb devices` 中为 `device` 状态）

## 支持麦克风

- **本机麦克风**：通过 `sounddevice`（可用环境变量 `SPEAKER_INPUT_DEVICE` 指定设备）
- **OmniMic 专业输入**：48 kHz 路径、增益与防爆音等逻辑见 `omnimic_recorder.py` 与 `speaker_eval/adapters/audio/`；可通过环境变量 `SPEAKER_RECORD_TOOL=omnimic` 或 `--record-tool omnimic` 选用；若使用外部 Dayton 程序可配置 `SPEAKER_OMNIMIC_EXE`

---

## 项目结构（主模块）

```
audio_evaluation_project/
├── run_all.py               # 主入口：一键执行 设备→录制→评分→报告
├── config.py                # 全局配置（路径、麦克风、Dify、音源）
├── device_roles.py          # 设备角色（被测/对比）确认
├── push_and_play.py         # 旧版 ADB 推送+播放（已废弃，保留兼容）
├── sync_capture.py          # 多设备同步采集（当前主路径）
├── omnimic_recorder.py      # OmniMic 专业录音相关
├── difyclient.py            # Dify API 评分调用
├── scoring.py               # AI 评分编排
├── markdown_report.py       # Markdown 评测表
├── report_builder.py        # 报告构建
├── gen_report.py            # Word 报告导出与美化
├── excel_summary.py         # Excel 汇总（report_builder 依赖）
├── tsv_report.py            # TSV 导出（report_builder 依赖）
├── local_service.py         # FastAPI 本地服务
├── speaker_eval/            # 包：CLI、流水线、ADB/音频适配、设置
├── assets/test_audio/       # 标准音源目录（按子目录分组）
├── requirements.txt         # 运行时依赖
├── README.md
├── 启动程序.bat             # 双击：菜单启动评测或 HTTP 服务
└── tools/                   # 可选工具（如占位音源生成）
```

---

## 使用步骤（从零到运行）

1. 安装 **Python 3.10 或 3.11（64 位，推荐）**，安装时勾选 **Add python.exe to PATH**。Python 3.13 可运行基础 Web UI，但 NISQA 依赖兼容性较弱，不建议作为项目 `.venv`。
2. 安装 **Android 平台工具**，将 **`adb.exe` 所在目录** 加入系统 **PATH**；连接设备后执行 `adb devices` 确认在线。
3. 将测试音频放入 **`assets/test_audio/`**。
4. 配置 Dify 相关环境变量（见 `speaker_eval/settings/dify.py` 或通过 `config` 导出项）。
5. 运行方式任选其一：
   - 双击 **`启动WebUI.bat`** 直接启动 Web 界面；
   - 双击 **`启动程序.bat`**，按菜单选择「一键评测」「HTTP 服务」或「Web 界面」；
   - 命令行使用项目虚拟环境：`.\.venv\Scripts\python.exe run_all.py` 或 `.\.venv\Scripts\python.exe -m speaker_eval`；
   - HTTP：`.\.venv\Scripts\python.exe local_service.py`，默认 `http://127.0.0.1:8765`（`SPEAKER_AI_HOST` / `SPEAKER_AI_PORT` 可改）。
6. 命令行评测可选参数：多设备跳过确认加 **`--yes`**；录音方式 **`--record-tool sounddevice`** 或 **`omnimic`**。

### Python 虚拟环境与跨电脑运行

- 两个 Windows 启动脚本会**强制使用项目目录下的 `.venv\Scripts\python.exe`**；若 `.venv` 不存在，会自动优先用 `py -3.10`、再尝试 `py -3.11`、最后尝试 PATH 上的 `python` 创建 `.venv`。
- 首次运行或依赖缺失时，启动脚本会自动执行 `pip install -r requirements.txt`；若存在 `requirements-nisqa.txt` 且当前 `.venv` 低于 Python 3.13，会尝试补装 NISQA 依赖。
- 若把项目复制到另一台 PC，建议**不要复制旧 `.venv`**，只复制源码、配置、音频与模型文件；在新 PC 安装 Python 3.10/3.11 后双击启动脚本，让它在本机重新创建 `.venv`。
- 如果已经存在的 `.venv` 是 Python 3.13，脚本会提示 NISQA 兼容性风险。需要完整 NISQA 功能时，请删除 `.venv`，安装 Python 3.10/3.11 后重新运行启动脚本。

---

## ADB 开启方法（摘要）

1. 手机 **设置 → 关于手机**，连续点击 **版本号** 开启开发者选项。
2. **设置 → 系统 → 开发者选项**：打开 **USB 调试**；若需无线调试，打开 **无线调试** 并按系统提示配对。
3. USB 连接电脑，手机上允许调试授权；命令行执行 **`adb devices`**，应显示序列号且状态为 **`device`**。
4. 若仅 Wi‑Fi：在开发者选项中获取 IP/端口，使用 **`adb connect IP:端口`** 后再 **`adb devices`** 确认。

---

## 录音摆放位置（实践建议）

- **麦克风主轴**尽量对准被测设备 **扬声器开孔区域**，距离约 **15～30 cm**（按实验室桌面与声压级微调）。
- **对比机**与 **被测机** 到麦克风的距离尽量一致，减少几何差异带来的误差。
- **避免**出风口直吹麦克风、桌面共振；关闭无关外放；多机时保持摆放稳定至整条会话结束。
- 使用 **OmniMic** 时，按硬件说明书固定支架与指向，增益可通过环境变量或工具链默认策略调整。

---

## 报告输出位置

- 默认在项目根目录下的 **`output/`**：
  - **`output/recorded/`**：录音与播放列表 JSON  
  - **`output/analysis/`**：评分结果 JSON  
  - **`output/reports/`**：Word、Markdown、TSV、Excel 等  
  - **`output/logs/`**：运行日志  
- 可通过环境变量 **`SPEAKER_BASE_DIR`** 将整棵目录树迁移到其他磁盘（需自行创建 **`assets/test_audio`** 与可写 **`output`** 结构）。

---

## 常见问题

| 现象 | 处理 |
|------|------|
| 找不到设备 | 检查数据线、驱动、USB 调试授权；`adb kill-server` 后重试 `adb devices` |
| 无音源 / 扫描为空 | 确认 **`assets/test_audio/`** 下存在 **wav/mp3** 等支持的格式 |
| 多设备交互想跳过 | 使用 **`--yes`**（命令行）或 HTTP 请求体指定序列号 |
| 录音无波形或全静音 | 检查系统默认输入设备、权限；OmniMic 检查驱动与 `SPEAKER_RECORD_TOOL` |
| Dify 调用失败 | 检查 API Key、工作流 URL、网络；查看 **`output/logs`** |

---

## 许可证与声明

业务数据与 API 密钥请勿提交版本库；生产环境请使用 HTTPS 与访问控制保护 **`local_service`** 暴露面。
