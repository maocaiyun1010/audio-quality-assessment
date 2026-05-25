@echo off
rem Args: install ^| quick  (requires VENV_DIR, PYTHON from win_ensure_venv_core.bat)

if /i "%~1"=="install" goto :ensure_install
if /i "%~1"=="quick" goto :ensure_quick
echo [ERROR] win_ensure_venv.bat needs argument: install or quick
exit /b 1

:ensure_quick
if not exist "%VENV_DIR%\.bootstrap_done" goto :ensure_quick_check
"%PYTHON%" -c "import streamlit" >nul 2>&1
if errorlevel 1 goto :ensure_quick_check
echo [deps] Already installed (.bootstrap_done), skip pip.
exit /b 0

:ensure_quick_check
"%PYTHON%" -m pip --version >nul 2>&1
if errorlevel 1 (
  echo [pip] Initializing pip...
  "%PYTHON%" -m ensurepip --upgrade
  if errorlevel 1 exit /b 1
)

"%PYTHON%" -c "import streamlit" >nul 2>&1
if errorlevel 1 (
  echo [deps] Installing requirements.txt ...
  "%PYTHON%" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo [ERROR] pip install requirements.txt failed
    exit /b 1
  )
)

if exist requirements-pdf.txt (
  "%PYTHON%" -c "import markdown" >nul 2>&1
  if errorlevel 1 (
    echo [deps] Optional: requirements-pdf.txt ...
    "%PYTHON%" -m pip install -r requirements-pdf.txt
    if errorlevel 1 (
      echo [WARN] PDF deps failed; Web UI works but PDF export may be unavailable.
    )
  )
)

if exist requirements-nisqa.txt (
  "%PYTHON%" -c "import nisqa" >nul 2>&1
  if errorlevel 1 (
    "%PYTHON%" -c "import sys; raise SystemExit(0 if sys.version_info < (3, 13) else 1)" >nul 2>&1
    if errorlevel 1 (
      echo [WARN] Skip NISQA on Python 3.13+. Use 3.10/3.11 .venv for NISQA.
    ) else (
      echo [deps] Optional: requirements-nisqa.txt ...
      "%PYTHON%" -m pip install -r requirements-nisqa.txt
      if errorlevel 1 (
        echo [WARN] NISQA install failed; subjective scoring still works.
      )
    )
  )
)
exit /b 0

:ensure_install
"%PYTHON%" -m pip --version >nul 2>&1
if errorlevel 1 (
  "%PYTHON%" -m ensurepip --upgrade
  if errorlevel 1 exit /b 1
)

echo [deps] pip install -r requirements.txt ...
"%PYTHON%" -m pip install --upgrade pip
"%PYTHON%" -m pip install -r requirements.txt
if errorlevel 1 (
  echo [ERROR] pip install requirements.txt failed
  exit /b 1
)

if exist requirements-pdf.txt (
  echo [deps] Optional: requirements-pdf.txt ...
  "%PYTHON%" -m pip install -r requirements-pdf.txt
  if errorlevel 1 (
    echo [WARN] PDF deps failed; main flow OK, PDF button may be unavailable.
  )
)

if exist requirements-nisqa.txt (
  "%PYTHON%" -c "import sys; raise SystemExit(0 if sys.version_info < (3, 13) else 1)" >nul 2>&1
  if errorlevel 1 (
    echo [WARN] Skip NISQA on Python 3.13+
  ) else (
    echo [deps] Optional: requirements-nisqa.txt (may take several minutes)...
    "%PYTHON%" -m pip install -r requirements-nisqa.txt
    if errorlevel 1 (
      echo [WARN] NISQA install failed; use Dify scoring only.
    ) else (
      echo [deps] Downloading NISQA weights (~1MB, network required)...
      "%PYTHON%" scripts\setup_nisqa_weights.py
      if errorlevel 1 echo [WARN] NISQA weights download failed, retry later.
    )
  )
)

echo ok>"%VENV_DIR%\.bootstrap_done"
exit /b 0
