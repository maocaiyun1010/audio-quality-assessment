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

echo ============================================================
echo Speaker Audio Eval - Launcher
echo Working dir: %CD%
echo Python: %PYTHON%
echo ============================================================
echo   [1] CLI evaluation (run_all.py)
echo   [2] HTTP service (local_service.py, port 8765)
echo   [3] Web UI (Streamlit, http://127.0.0.1:8501) [default]
echo   [4] Exit
echo ============================================================
set /p choice=Enter 1-4 [default 3]: 
if "%choice%"=="" set choice=3
if "%choice%"=="1" goto :run_eval
if "%choice%"=="2" goto :run_http
if "%choice%"=="3" goto :run_web
if "%choice%"=="4" exit /b 0
echo Invalid choice.
pause
exit /b 1

:run_eval
"%PYTHON%" run_all.py %*
set EC=%ERRORLEVEL%
echo.
echo Done, exit code: %EC%
pause
exit /b %EC%

:run_http
echo Health: http://127.0.0.1:8765/health
"%PYTHON%" local_service.py
set EC=%ERRORLEVEL%
echo HTTP service stopped, exit code: %EC%
pause
exit /b %EC%

:run_web
echo Starting Web UI (Streamlit)...
echo Open: http://127.0.0.1:8501
"%PYTHON%" -m streamlit run web_ui.py --server.headless false
set EC=%ERRORLEVEL%
echo Web UI stopped, exit code: %EC%
pause
exit /b %EC%
