@echo off
setlocal
chcp 65001 >nul
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
