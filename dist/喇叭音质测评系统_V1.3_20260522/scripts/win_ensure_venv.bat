@echo off
rem 由 一键安装依赖.bat / 启动WebUI.bat / 启动程序.bat 调用（需已设置 VENV_DIR、PYTHON）
rem 用法: call "%~dp0scripts\win_ensure_venv.bat" install|quick
rem   install = 完整安装（一键安装脚本）
rem   quick   = 启动前快速检查（已 bootstrap 则跳过 pip）

if /i "%~1"=="install" goto :ensure_install
if /i "%~1"=="quick" goto :ensure_quick
echo 错误：win_ensure_venv.bat 需要参数 install 或 quick
exit /b 1

:ensure_quick
if not exist "%VENV_DIR%\.bootstrap_done" goto :ensure_quick_check
"%PYTHON%" -c "import streamlit" >nul 2>&1
if errorlevel 1 goto :ensure_quick_check
echo 依赖已就绪（已由「一键安装依赖」或此前安装完成），跳过 pip。
exit /b 0

:ensure_quick_check
"%PYTHON%" -m pip --version >nul 2>&1
if errorlevel 1 (
  echo 正在初始化 pip...
  "%PYTHON%" -m ensurepip --upgrade
  if errorlevel 1 exit /b 1
)

"%PYTHON%" -c "import streamlit" >nul 2>&1
if errorlevel 1 (
  echo 依赖缺失：正在安装 requirements.txt ...
  "%PYTHON%" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo 错误：安装 requirements.txt 失败。
    exit /b 1
  )
)

if exist requirements-pdf.txt (
  "%PYTHON%" -c "import markdown" >nul 2>&1
  if errorlevel 1 (
    echo 可选：安装 PDF 导出依赖 requirements-pdf.txt ...
    "%PYTHON%" -m pip install -r requirements-pdf.txt
    if errorlevel 1 (
      echo 警告：PDF 依赖安装失败；Web UI 仍可启动，但「下载 PDF」可能不可用。
    )
  )
)

if exist requirements-nisqa.txt (
  "%PYTHON%" -c "import nisqa" >nul 2>&1
  if errorlevel 1 (
    "%PYTHON%" -c "import sys; raise SystemExit(0 if sys.version_info < (3, 13) else 1)" >nul 2>&1
    if errorlevel 1 (
      echo 提示：跳过 NISQA 自动安装（Python 3.13+）。如需 NISQA，请用 Python 3.10/3.11 重建 .venv。
    ) else (
      echo 可选：安装 NISQA 依赖 requirements-nisqa.txt ...
      "%PYTHON%" -m pip install -r requirements-nisqa.txt
      if errorlevel 1 (
        echo 警告：NISQA 安装失败；Web UI 仍可启动，但本地客观评分不可用。
      )
    )
  )
)
exit /b 0

:ensure_install
"%PYTHON%" -m pip --version >nul 2>&1
if errorlevel 1 (
  "%PYTHON%" -m ensurepip --upgrade
  if errorlevel 1 exit /b 1
)

echo [3/3] 安装 requirements.txt ...
"%PYTHON%" -m pip install --upgrade pip
"%PYTHON%" -m pip install -r requirements.txt
if errorlevel 1 (
  echo 错误：安装 requirements.txt 失败。
  exit /b 1
)

if exist requirements-pdf.txt (
  echo 可选: 安装 PDF 导出依赖 requirements-pdf.txt ...
  "%PYTHON%" -m pip install -r requirements-pdf.txt
  if errorlevel 1 (
    echo 警告：PDF 依赖安装失败；不影响主流程，仅「下载 PDF」不可用。
  )
)

if exist requirements-nisqa.txt (
  "%PYTHON%" -c "import sys; raise SystemExit(0 if sys.version_info < (3, 13) else 1)" >nul 2>&1
  if errorlevel 1 (
    echo 提示：Python 3.13+ 跳过 NISQA 自动安装。
  ) else (
    echo 可选: 安装 NISQA 依赖 requirements-nisqa.txt ...
    "%PYTHON%" -m pip install -r requirements-nisqa.txt
    if errorlevel 1 (
      echo 警告：NISQA 安装失败；可稍后手动安装或仅用 Dify 主观评分。
    ) else (
      echo 可选: 下载 NISQA 权重 (约 1MB, 需联网)...
      "%PYTHON%" scripts\setup_nisqa_weights.py
      if errorlevel 1 echo 警告：NISQA 权重下载失败，可稍后重试上述命令。
    )
  )
)

echo ok>"%VENV_DIR%\.bootstrap_done"
exit /b 0
