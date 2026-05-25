@echo off
rem 虚拟环境创建与 Python 版本检查（需调用方已 cd 到项目根）
rem 成功后设置 VENV_DIR、PYTHON

set "VENV_DIR=%CD%\.venv"
set "PYTHON=%VENV_DIR%\Scripts\python.exe"
if exist "%PYTHON%" goto :check_venv

echo 未检测到项目虚拟环境：%VENV_DIR%
call :find_base_python
if errorlevel 1 exit /b 1

echo 正在创建 .venv (优先 Python 3.10/3.11)...
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
