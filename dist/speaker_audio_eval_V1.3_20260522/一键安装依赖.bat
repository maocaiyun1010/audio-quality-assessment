@echo off
setlocal EnableExtensions
if /i not "%~1"=="_run" (
  cd /d "%~dp0"
  title Speaker Audio Eval - Install Dependencies
  cmd /k ""%~f0" _run"
  exit /b 0
)

cd /d "%~dp0"
set "INSTALL_EC=0"

echo ============================================================
echo Speaker Audio Eval V1.3 - Install .venv and dependencies
echo Working dir: %CD%
echo ============================================================
echo.

if not exist "%~dp0scripts\win_ensure_venv_core.bat" (
  echo [ERROR] Missing scripts\win_ensure_venv_core.bat
  echo Extract the full ZIP; do not copy files only.
  goto :fail
)
if not exist "%~dp0scripts\win_ensure_venv.bat" (
  echo [ERROR] Missing scripts\win_ensure_venv.bat
  goto :fail
)
if not exist "%~dp0requirements.txt" (
  echo [ERROR] Missing requirements.txt - not in project root?
  goto :fail
)

echo [1/3] Check / create virtual environment ...
call "%~dp0scripts\win_ensure_venv_core.bat"
if errorlevel 1 goto :fail

if not exist "%PYTHON%" (
  echo [ERROR] Python not found: %PYTHON%
  goto :fail
)

echo [2/3] Check pip ...
"%PYTHON%" -m pip --version >nul 2>&1
if errorlevel 1 (
  "%PYTHON%" -m ensurepip --upgrade
  if errorlevel 1 goto :fail
)

echo [3/3] Install pip packages (first run may take 5-20 min, do not close) ...
call "%~dp0scripts\win_ensure_venv.bat" install
if errorlevel 1 goto :fail

"%PYTHON%" -c "import sys; print('Python version:', sys.version.split()[0])"
if errorlevel 1 goto :fail

echo.
echo ============================================================
echo Install finished OK.
echo   Python: %PYTHON%
echo   Next: run StartWebUI.bat or StartMenu.bat
echo   Set DIFY_API_KEY in Web sidebar (see README / docs)
echo ============================================================
goto :done

:fail
set "INSTALL_EC=1"
echo.
echo ============================================================
echo Install FAILED. Common causes:
echo   1. Python 3.10/3.11 not installed or not on PATH
echo   2. Incomplete ZIP extract (missing scripts folder)
echo   3. Corporate proxy blocking pip - try manually:
echo      .venv\Scripts\python.exe -m pip install -r requirements.txt
echo ============================================================

:done
echo.
pause
endlocal & exit /b %INSTALL_EC%
