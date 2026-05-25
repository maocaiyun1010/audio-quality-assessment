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

echo ============================================================
echo 喇叭音效评测 — 启动器（Python 源码）
echo 工作目录: %CD%
echo Python: %PYTHON%
echo ============================================================
echo   [1] 一键评测 CLI（.venv Python: run_all.py）
echo   [2] 本地 HTTP 服务（.venv Python: local_service.py，默认 8765）
echo   [3] Web 界面（Streamlit：web_ui.py，默认 http://127.0.0.1:8501）
echo   [4] 退出
echo ============================================================
set /p choice=请输入 1-4 [默认 3]: 
if "%choice%"=="" set choice=3
if "%choice%"=="1" goto :run_eval
if "%choice%"=="2" goto :run_http
if "%choice%"=="3" goto :run_web
if "%choice%"=="4" exit /b 0
echo 无效选择。
pause
exit /b 1

:run_eval
"%PYTHON%" run_all.py %*
set EC=%ERRORLEVEL%
echo.
echo 结束，退出码: %EC%
pause
exit /b %EC%

:run_http
echo 服务: http://127.0.0.1:8765/health
"%PYTHON%" local_service.py
set EC=%ERRORLEVEL%
echo HTTP 服务已退出，退出码: %EC%
pause
exit /b %EC%

:run_web
echo 正在启动 Web UI（Streamlit）…
echo 浏览器打开: http://127.0.0.1:8501  （若未自动打开请手动访问）
"%PYTHON%" -m streamlit run web_ui.py --server.headless false
set EC=%ERRORLEVEL%
echo Web UI 已退出，退出码: %EC%
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
