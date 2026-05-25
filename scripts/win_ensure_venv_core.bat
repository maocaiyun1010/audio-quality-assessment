@echo off
rem Create/check .venv; sets VENV_DIR and PYTHON (caller must cd to project root)

set "VENV_DIR=%CD%\.venv"
set "PYTHON=%VENV_DIR%\Scripts\python.exe"
if exist "%PYTHON%" goto :check_venv

echo [venv] Not found: %VENV_DIR%
call :find_base_python
if errorlevel 1 exit /b 1

echo [venv] Creating .venv (prefer Python 3.10/3.11)...
%BASE_PYTHON% -m venv "%VENV_DIR%"
if errorlevel 1 (
  echo [ERROR] Failed to create .venv
  exit /b 1
)

:check_venv
"%PYTHON%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if errorlevel 1 (
  echo [ERROR] .venv Python is below 3.10. Delete .venv and reinstall Python 3.10+
  exit /b 1
)
"%PYTHON%" -c "import sys; raise SystemExit(0 if sys.version_info < (3, 13) else 1)" >nul 2>&1
if errorlevel 1 (
  echo [WARN] .venv is Python 3.13+; NISQA may not install. Use 3.10/3.11 for full features.
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
echo [ERROR] Python 3.10+ not found. Install Python 3.10 or 3.11 x64 and add to PATH.
exit /b 1
