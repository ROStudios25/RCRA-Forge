@echo off
title RCRA Forge
setlocal

REM ── Check Python ─────────────────────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found.
    echo         Install Python 3.10+ from https://python.org
    echo         Make sure to check "Add Python to PATH" during install.
    pause
    exit /b 1
)

REM ── Create virtual environment if it doesn't exist ───────────────────────────
if not exist ".venv" (
    echo [SETUP] First run - creating virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment.
        pause & exit /b 1
    )
)

REM ── Activate venv ────────────────────────────────────────────────────────────
call .venv\Scripts\activate.bat

REM ── Install / update requirements on first run ───────────────────────────────
if not exist ".venv\Lib\site-packages\PyQt6" (
    echo [SETUP] Installing requirements (one-time, may take a minute)...
    pip install --upgrade pip -q --disable-pip-version-check
    pip install PyQt6 PyOpenGL PyOpenGL-accelerate numpy Pillow imagecodecs -q
    if errorlevel 1 (
        echo [ERROR] Failed to install requirements.
        echo         Check your internet connection and try again.
        pause & exit /b 1
    )
    echo [OK] Requirements installed.
)

REM ── Launch RCRA Forge ─────────────────────────────────────────────────────────
echo [OK] Starting RCRA Forge...
python main.py

deactivate
