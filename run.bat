@echo off
title RCRA Forge
setlocal

REM ── Find Python ───────────────────────────────────────────────────────────────
REM  Windows Python installs as one of: py (launcher), python3, or python.
REM  Try each in order so users don't need to fix their PATH manually.

set PYTHON=

REM Try the Windows Python Launcher first (most reliable on Windows 10/11)
py --version >nul 2>&1
if not errorlevel 1 (
    set PYTHON=py
    goto :found_python
)

REM Try python3
python3 --version >nul 2>&1
if not errorlevel 1 (
    set PYTHON=python3
    goto :found_python
)

REM Try plain python
python --version >nul 2>&1
if not errorlevel 1 (
    set PYTHON=python
    goto :found_python
)

REM Nothing worked
echo.
echo [ERROR] Python not found.
echo.
echo   Install Python 3.10+ from https://python.org
echo   During install, check "Add Python to PATH".
echo.
echo   If Python IS installed but this still appears:
echo     - Open a NEW terminal window and run:  py --version
echo     - If that works, just run:             py main.py
echo     - Otherwise reinstall Python and check "Add Python to PATH"
echo.
pause
exit /b 1

:found_python
echo [OK] Found Python: %PYTHON%

REM ── Create virtual environment if it doesn't exist ───────────────────────────
if not exist ".venv" (
    echo [SETUP] First run - creating virtual environment...
    %PYTHON% -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment.
        echo         Try running as Administrator, or reinstall Python.
        pause & exit /b 1
    )
)

REM ── Activate venv ────────────────────────────────────────────────────────────
call .venv\Scripts\activate.bat

REM ── Install / update requirements on first run ───────────────────────────────
if not exist ".venv\Lib\site-packages\PyQt6" (
    echo [SETUP] Installing requirements - one-time setup, may take a minute...
    python -m pip install --upgrade pip -q --disable-pip-version-check
    python -m pip install PyQt6 PyOpenGL PyOpenGL-accelerate numpy Pillow imagecodecs -q
    if errorlevel 1 (
        echo.
        echo [ERROR] Failed to install packages.
        echo         Check your internet connection and try again.
        echo         If it persists, run manually:
        echo           pip install PyQt6 PyOpenGL numpy Pillow imagecodecs
        echo.
        pause & exit /b 1
    )
    echo [OK] Requirements installed.
)

REM ── Launch RCRA Forge ─────────────────────────────────────────────────────────
echo [OK] Starting RCRA Forge...
python main.py

deactivate
