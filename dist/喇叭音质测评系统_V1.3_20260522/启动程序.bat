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
