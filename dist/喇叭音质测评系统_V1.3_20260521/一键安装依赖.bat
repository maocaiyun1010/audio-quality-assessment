@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================================
echo 喇叭音质测评系统 — 一键安装虚拟环境与依赖
echo 工作目录: %CD%
echo ============================================================
echo.

call :ensure_venv
if errorlevel 1 goto :fail

call :ensure_requirements
if errorlevel 1 goto :fail

echo.
echo ============================================================
echo 安装完成。
echo   Python: %PYTHON%
echo   下一步: 双击「启动WebUI.bat」或「启动程序.bat」
echo   首次使用请在 Web 侧栏配置 DIFY_API_KEY（见「外发使用说明.md」）
echo ============================================================
pause
exit /b 0

:fail
echo.
echo 安装未完成，请根据上方错误信息处理后重试。
pause
exit /b 1

:ensure_venv
set "VENV_DIR=%CD%\.venv"
set "PYTHON=%VENV_DIR%\Scripts\python.exe"
if exist "%PYTHON%" goto :check_venv

echo [1/3] 未检测到 .venv，正在创建虚拟环境 …
call :find_base_python
if errorlevel 1 exit /b 1

%BASE_PYTHON% -m venv "%VENV_DIR%"
if errorlevel 1 (
  echo 错误：创建 .venv 失败。
  exit /b 1
)

:check_venv
"%PYTHON%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if errorlevel 1 (
  echo 错误：.venv Python 版本低于 3.10。请删除 .venv 后安装 Python 3.10+ 再运行本脚本。
  exit /b 1
)
"%PYTHON%" -c "import sys; print('当前 Python:', sys.version.split()[0])"
"%PYTHON%" -c "import sys; raise SystemExit(0 if sys.version_info < (3, 13) else 1)" >nul 2>&1
if errorlevel 1 (
  echo 警告：当前为 Python 3.13+；Web UI 可用，但 NISQA 可能无法安装。建议改用 3.10/3.11 重建 .venv。
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
echo [2/3] 检查 pip …
"%PYTHON%" -m pip --version >nul 2>&1
if errorlevel 1 (
  "%PYTHON%" -m ensurepip --upgrade
  if errorlevel 1 exit /b 1
)

echo [3/3] 安装 requirements.txt …
"%PYTHON%" -m pip install --upgrade pip
"%PYTHON%" -m pip install -r requirements.txt
if errorlevel 1 (
  echo 错误：安装 requirements.txt 失败。
  exit /b 1
)

if exist requirements-pdf.txt (
  echo 可选：安装 PDF 导出依赖 requirements-pdf.txt …
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
    echo 可选：安装 NISQA 依赖 requirements-nisqa.txt …
    "%PYTHON%" -m pip install -r requirements-nisqa.txt
    if errorlevel 1 (
      echo 警告：NISQA 安装失败；可稍后手动安装或仅用 Dify 主观评分。
    ) else (
      echo 可选：下载 NISQA 权重（约 1MB，需联网）…
      "%PYTHON%" scripts\setup_nisqa_weights.py
      if errorlevel 1 echo 警告：NISQA 权重下载失败，可稍后重试上述命令。
    )
  )
)
exit /b 0
