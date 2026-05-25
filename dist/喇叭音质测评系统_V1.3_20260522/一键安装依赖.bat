@echo off
setlocal EnableExtensions
rem 双击时用 cmd /k 保持窗口，避免报错后闪退看不清信息
if /i not "%~1"=="_run" (
  cd /d "%~dp0"
  title 喇叭音质测评系统 - 一键安装依赖
  cmd /k ""%~f0" _run"
  exit /b 0
)

cd /d "%~dp0"
chcp 65001 >nul 2>&1
set "INSTALL_EC=0"

echo ============================================================
echo 喇叭音质测评系统 - 一键安装虚拟环境与依赖
echo 工作目录: %CD%
echo ============================================================
echo.

if not exist "%~dp0scripts\win_ensure_venv_core.bat" (
  echo [错误] 缺少 scripts\win_ensure_venv_core.bat
  echo 请完整解压 ZIP，不要只复制部分文件；或重新获取外发包。
  goto :fail
)
if not exist "%~dp0scripts\win_ensure_venv.bat" (
  echo [错误] 缺少 scripts\win_ensure_venv.bat
  goto :fail
)
if not exist "%~dp0requirements.txt" (
  echo [错误] 缺少 requirements.txt，当前目录可能不是项目根。
  goto :fail
)

echo [1/3] 检查 / 创建虚拟环境 ...
call "%~dp0scripts\win_ensure_venv_core.bat"
if errorlevel 1 goto :fail

if not exist "%PYTHON%" (
  echo [错误] 未找到虚拟环境 Python: %PYTHON%
  goto :fail
)

echo [2/3] 检查 pip ...
"%PYTHON%" -m pip --version >nul 2>&1
if errorlevel 1 (
  "%PYTHON%" -m ensurepip --upgrade
  if errorlevel 1 goto :fail
)

echo [3/3] 安装 pip 依赖（首次可能需 5~20 分钟，请勿关闭窗口）...
call "%~dp0scripts\win_ensure_venv.bat" install
if errorlevel 1 goto :fail

"%PYTHON%" -c "import sys; print('当前 Python:', sys.version.split()[0])"
if errorlevel 1 goto :fail

echo.
echo ============================================================
echo 安装完成。
echo   Python: %PYTHON%
echo   下一步: 双击「启动WebUI.bat」或「启动程序.bat」
echo   首次使用请在 Web 侧栏配置 DIFY_API_KEY（见「外发使用说明.md」）
echo ============================================================
goto :done

:fail
set "INSTALL_EC=1"
echo.
echo ============================================================
echo 安装未完成。常见原因:
echo   1. 未安装 Python 3.10/3.11 或未勾选 Add to PATH
echo   2. 未完整解压 ZIP（缺少 scripts 目录）
echo   3. pip 被公司代理拦截 — 可手动运行:
echo      .venv\Scripts\python.exe -m pip install -r requirements.txt
echo ============================================================

:done
echo.
pause
endlocal & exit /b %INSTALL_EC%
