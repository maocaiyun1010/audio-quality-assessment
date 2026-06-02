# MOSS-Audio-8B-Thinking 本地部署说明

本项目的 **MOSS-Audio Local** 评分模式只会向本地 HTTP 接口发请求，**不会**自动下载模型或启动 SGLang。

若侧栏检测显示 `WinError 10061` / 连接被拒绝，说明 **`localhost:30000` 没有 MOSS 服务在运行**。

---

## 1. 环境要求

- NVIDIA GPU（显存建议 ≥ 24GB，视量化与并发而定）
- Linux 或 **WSL2**（Windows 上推荐 WSL2 + GPU 透传）
- Python 3.12 独立环境（与评测 Web UI 的 venv 可分开）

---

## 2. 安装 MOSS 专用 SGLang

在 **WSL2 或 Linux** 终端执行：

```bash
conda create -n moss-audio python=3.12 -y
conda activate moss-audio

git clone -b moss-audio https://github.com/OpenMOSS/sglang.git
cd sglang
pip install -e "python[all]"
pip install nvidia-cudnn-cu12==9.16.0.29
```

---

## 3. 下载模型权重

```bash
pip install -U "huggingface_hub[cli]"
huggingface-cli download OpenMOSS-Team/MOSS-Audio-8B-Thinking \
  --local-dir ~/weights/MOSS-Audio-8B-Thinking
```

---

## 4. 启动服务

```bash
sglang serve \
  --model-path ~/weights/MOSS-Audio-8B-Thinking \
  --trust-remote-code \
  --host 0.0.0.0 \
  --port 30000
```

保持该终端窗口运行，直到看到服务监听 30000 端口。

---

## 5. 验证

在 **Windows PowerShell**（服务跑在 WSL 时）：

```powershell
# 查看 WSL IP
wsl hostname -I

# 用 WSL IP 测试（将 <WSL-IP> 换成上一步输出的地址）
Invoke-WebRequest -Uri "http://<WSL-IP>:30000/v1/models" -UseBasicParsing
```

若服务与 Web UI 都在同一台 Windows 本机原生启动（非 WSL），可测：

```powershell
Invoke-WebRequest -Uri "http://127.0.0.1:30000/v1/models" -UseBasicParsing
```

---

## 6. 配置 Web UI

侧栏选择 **MOSS-Audio Local**，填写：

| 项 | 值 |
|----|-----|
| MOSS_AUDIO_API_URL | `http://<服务IP>:30000/v1/chat/completions` |
| MOSS_AUDIO_MODEL | `default`（与 SGLang 默认一致） |

- 服务在 **WSL** 内：URL 用 **WSL IP**，不要用 `localhost`
- 服务在 **另一台机器**：URL 用那台机器的局域网 IP

点击 **「检测 MOSS 连接」**，显示成功后再开始评测。

---

## 7. 命令行自检

```powershell
cd d:\AI\SY\audio_evaluation_projectV1.3
python scripts\check_moss_server.py
```

可选环境变量：

- `MOSS_AUDIO_API_URL`：指定要检测的地址
- `MOSS_AUDIO_PROBE_HOSTS`：额外探测主机，逗号分隔，如 `192.168.1.10`

---

## 8. 暂不部署 MOSS 时

侧栏改选 **Dify** 或 **Seedpace Gateway**，无需本地 30000 服务。
