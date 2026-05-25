@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

call :ensure_venv
if errorlevel 1 (
  pause
  exit /b 1
)

call :ensure_requirements
if errorlevel 1 (
  pause
  exit /b 1
)

echo 喇叭音效评测 — Web 界面（Streamlit）
echo 工作目录: %CD%
echo Python: %PYTHON%
echo 地址: http://127.0.0.1:8501
echo 按 Ctrl+C 可停止服务
echo.
"%PYTHON%" -m streamlit run web_ui.py --server.headless false
set EC=%ERRORLEVEL%
if not "%EC%"=="0" echo 退出码: %EC%
pause
exit /b %EC%

:ensure_venv
set "VENV_DIR=%CD%\.venv"
set "PYTHON=%VENV_DIR%\Scripts\python.exe"
if exist "%PYTHON%" goto :check_venv

echo 未检测到项目虚拟环境：%VENV_DIR%
call :find_base_python
if errorlevel 1 exit /b 1

echo 正在创建 .venv（优先建议 Python 3.10/3.11）...
%BASE_PYTHON% -m venv "%VENV_DIR%"
if errorlevel 1 (
  echo 错误：创建 .venv 失败。
  exit /b 1
)

:check_venv
"%PYTHON%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if errorlevel 1 (
  echo 错误：.venv Python 版本低于 3.10，请删除 .venv 后使用 Python 3.10+ 重新运行。
  exit /b 1
)
"%PYTHON%" -c "import sys; raise SystemExit(0 if sys.version_info < (3, 13) else 1)" >nul 2>&1
if errorlevel 1 (
  echo 警告：当前 .venv 为 Python 3.13+；基础 Web UI 可运行，但 NISQA 依赖可能不兼容。建议用 Python 3.10/3.11 重建 .venv。
)
exit /b 0

:find_base_python
set "BASE_PYTHON="
py -3.10 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if not errorlevel 1 (
  set "BASE_PYTHON=py -3.10"
  exit /b 0
)
py -3.11 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if not errorlevel 1 (
  set "BASE_PYTHON=py -3.11"
  exit /b 0
)
python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if not errorlevel 1 (
  set "BASE_PYTHON=python"
  exit /b 0
)
echo 错误：未检测到 Python 3.10+。请先安装 Python 3.10 或 3.11（64 位），并勾选 Add python.exe to PATH。
exit /b 1

:ensure_requirements
"%PYTHON%" -m pip --version >nul 2>&1
if errorlevel 1 (
  echo 正在初始化 pip...
  "%PYTHON%" -m ensurepip --upgrade
  if errorlevel 1 exit /b 1
)

"%PYTHON%" -c "import streamlit" >nul 2>&1
if errorlevel 1 (
  echo 首次运行或依赖缺失：正在安装 requirements.txt ...
  "%PYTHON%" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo 错误：安装 requirements.txt 失败。
    exit /b 1
  )
)

if exist requirements-pdf.txt (
  "%PYTHON%" -c "import markdown; from web_ui_report_pdf import pdf_export_available; raise SystemExit(0 if pdf_export_available() else 1)" >nul 2>&1
  if errorlevel 1 (
    echo 检测到 PDF 导出依赖缺失：正在安装 requirements-pdf.txt ...
    "%PYTHON%" -m pip install -r requirements-pdf.txt
    if errorlevel 1 (
      echo 警告：PDF 依赖安装失败；Web UI 仍可启动，但「下载 PDF」不可用。
    )
  )
)

if exist requirements-nisqa.txt (
  "%PYTHON%" -c "import nisqa" >nul 2>&1
  if errorlevel 1 (
    "%PYTHON%" -c "import sys; raise SystemExit(0 if sys.version_info < (3, 13) else 1)" >nul 2>&1
    if errorlevel 1 (
      echo 提示：跳过 NISQA 依赖自动安装（Python 3.13+ 兼容性不足）。如需 NISQA，请用 Python 3.10/3.11 重建 .venv。
    ) else (
      echo 检测到 NISQA 依赖缺失：正在安装 requirements-nisqa.txt ...
      "%PYTHON%" -m pip install -r requirements-nisqa.txt
      if errorlevel 1 (
        echo 警告：NISQA 依赖安装失败；Web UI 仍可启动，但本地客观评分不可用。
      )
    )
  )
)
exit /b 0
