@echo off
REM ============================================================================
REM  CAM Studio - single-click Windows launcher.
REM  Double-click this file. On first run it sets up a Python virtualenv, builds
REM  the web UI, seeds demo data, starts every service, and opens your browser.
REM  Prerequisites: Python 3.11+ (required) and Node.js 18+ (for the web UI).
REM  Close this window (or press Ctrl-C) to stop the platform.
REM ============================================================================
setlocal
cd /d "%~dp0"

REM Prefer the Python launcher (py); fall back to python on PATH. Each candidate is
REM verified to actually run a real Python 3.10+ so the Microsoft Store "python"
REM execution-alias stub (which otherwise opens the Store) is rejected.
set "PYEXE="
py -3 -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>nul && set "PYEXE=py -3"
if not defined PYEXE (
  python -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>nul && set "PYEXE=python"
)
if not defined PYEXE (
  echo(
  echo   Python 3.10+ was not found ^(or 'python' points to the Microsoft Store stub^).
  echo   Install it from https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^),
  echo   open a NEW terminal / re-open this folder, and run this file again.
  echo(
  pause
  exit /b 1
)

%PYEXE% scripts\windows_start.py
set "RC=%ERRORLEVEL%"

echo(
echo   CAM Studio has stopped.
pause
exit /b %RC%
