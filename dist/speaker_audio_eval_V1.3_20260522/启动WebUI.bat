@echo off
setlocal
cd /d "%~dp0"

call "%~dp0scripts\win_ensure_venv_core.bat"
if errorlevel 1 (
  pause
  exit /b 1
)

call "%~dp0scripts\win_ensure_venv.bat" quick
if errorlevel 1 (
  pause
  exit /b 1
)

echo Speaker Audio Eval - Web UI (Streamlit)
echo Working dir: %CD%
echo Python: %PYTHON%
echo URL: http://127.0.0.1:8501
echo Press Ctrl+C to stop
echo.
"%PYTHON%" -m streamlit run web_ui.py --server.headless false
set EC=%ERRORLEVEL%
if not "%EC%"=="0" echo Exit code: %EC%
pause
exit /b %EC%
