@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
set "APP_PY=%~dp0.venv\Scripts\python.exe"

rem Reuse a project virtual environment if one already exists.
if exist "%APP_PY%" goto :run

rem Discover a portable runtime without assuming its versioned folder names.
set "BASE_PY="
for /d %%R in ("%~dp0WinPython*") do (
  for /d %%V in ("%%~fR\WPy*") do (
    if exist "%%~fV\python\python.exe" set "BASE_PY=%%~fV\python\python.exe"
  )
)
if defined BASE_PY (
  "!BASE_PY!" -m venv "%~dp0.venv"
  if not errorlevel 1 goto :run
  echo Portable Python was found but could not create .venv. Trying installed Python...
)

rem Try supported system Python versions through the Windows Python Launcher.
py -3.13 -m venv "%~dp0.venv" >nul 2>&1
if not errorlevel 1 goto :run
py -3.11 -m venv "%~dp0.venv" >nul 2>&1
if not errorlevel 1 goto :run

rem Fallback to the Python executable available on PATH, if compatible.
python -c "import struct,sys; raise SystemExit(0 if sys.version_info[:2] in ((3,11),(3,13)) and struct.calcsize('P') == 8 else 1)" >nul 2>&1
if errorlevel 1 goto :no_python
python -m venv "%~dp0.venv"
if errorlevel 1 goto :no_python

:run
"%APP_PY%" -c "import struct,sys; raise SystemExit(0 if sys.version_info[:2] in ((3,11),(3,13)) and struct.calcsize('P') == 8 else 1)" >nul 2>&1
if errorlevel 1 (
  echo The selected Python must be 64-bit CPython 3.11 or 3.13.
  echo Remove the incompatible .venv folder and restart.
  pause
  exit /b 1
)
"%APP_PY%" -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 goto :failed
"%APP_PY%" "%~dp0scc_app.py"
if errorlevel 1 goto :failed
exit /b 0

:no_python
echo SCC needs 64-bit CPython 3.11 or 3.13.
echo Install one from python.org, then start this file again.
pause
exit /b 1

:failed
echo SCC could not start. Check the Python installation and network access for dependencies.
pause
exit /b 1
