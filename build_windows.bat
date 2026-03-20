@echo off
REM ============================================================
REM  RCRA Forge — Windows EXE Builder
REM  Run this from the rcra_forge\ folder:
REM      build_windows.bat
REM
REM  Requirements:
REM    - Python 3.10+ installed and on PATH
REM    - Internet connection (for pip installs)
REM ============================================================

title RCRA Forge Builder

echo.
echo  ██████╗  ██████╗██████╗  █████╗     ███████╗ ██████╗ ██████╗  ██████╗ ███████╗
echo  ██╔══██╗██╔════╝██╔══██╗██╔══██╗    ██╔════╝██╔═══██╗██╔══██╗██╔════╝ ██╔════╝
echo  ██████╔╝██║     ██████╔╝███████║    █████╗  ██║   ██║██████╔╝██║  ███╗█████╗
echo  ██╔══██╗██║     ██╔══██╗██╔══██║    ██╔══╝  ██║   ██║██╔══██╗██║   ██║██╔══╝
echo  ██║  ██║╚██████╗██║  ██║██║  ██║    ██║     ╚██████╔╝██║  ██║╚██████╔╝███████╗
echo  ╚═╝  ╚═╝ ╚═════╝╚═╝  ╚═╝╚═╝  ╚═╝   ╚═╝      ╚═════╝ ╚═╝  ╚═╝ ╚═════╝ ╚══════╝
echo.
echo  Rift Apart Level Editor and Asset Exporter — Windows Build Script
echo  ===================================================================
echo.

REM ── Check Python ─────────────────────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.10+ from https://python.org
    echo         Make sure to check "Add Python to PATH" during install.
    pause
    exit /b 1
)

for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo [OK] Python %PYVER% found

REM ── Create / activate virtual environment ────────────────────────────────────
if not exist ".venv" (
    echo.
    echo [1/5] Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment
        pause & exit /b 1
    )
    echo [OK] Virtual environment created
) else (
    echo [OK] Virtual environment already exists
)

call .venv\Scripts\activate.bat

REM ── Install / upgrade dependencies ───────────────────────────────────────────
echo.
echo [2/5] Installing dependencies...
pip install --upgrade pip -q
pip install PyQt6 PyOpenGL PyOpenGL-accelerate numpy Pillow pyinstaller -q
if errorlevel 1 (
    echo [ERROR] pip install failed. Check your internet connection.
    pause & exit /b 1
)
echo [OK] Dependencies installed

REM ── Optional: generate icon ──────────────────────────────────────────────────
if not exist "assets" mkdir assets
if not exist "assets\icon.ico" (
    echo [INFO] No icon found at assets\icon.ico — building without custom icon.
    echo        To add one: place a 256x256 .ico file at assets\icon.ico
    REM Patch spec to remove icon reference if file is missing
    powershell -Command "(Get-Content rcra_forge.spec) -replace \"icon='assets\\\\icon.ico' if sys.platform == 'win32' else None\", 'icon=None' | Set-Content rcra_forge.spec"
)

REM ── Clean previous build ─────────────────────────────────────────────────────
echo.
echo [3/5] Cleaning previous build output...
if exist "build"        rmdir /s /q "build"
if exist "dist"         rmdir /s /q "dist"
echo [OK] Clean done

REM ── Run PyInstaller ──────────────────────────────────────────────────────────
echo.
echo [4/5] Running PyInstaller (this may take 1-3 minutes)...
pyinstaller rcra_forge.spec --noconfirm
if errorlevel 1 (
    echo.
    echo [ERROR] PyInstaller build failed!
    echo         Check the output above for details.
    echo         Common fixes:
    echo           - Make sure all source files are present
    echo           - Try deleting __pycache__ folders and rebuilding
    pause & exit /b 1
)

REM ── Verify output ────────────────────────────────────────────────────────────
if not exist "dist\RCRA_Forge\RCRA_Forge.exe" (
    echo [ERROR] Expected EXE not found at dist\RCRA_Forge\RCRA_Forge.exe
    pause & exit /b 1
)

REM ── Report size ──────────────────────────────────────────────────────────────
echo.
echo [5/5] Build complete!
echo.
for /f %%s in ('powershell -Command "[math]::Round((Get-ChildItem dist\RCRA_Forge -Recurse | Measure-Object -Property Length -Sum).Sum / 1MB, 1)"') do set DISTSIZE=%%s
echo  Output:  dist\RCRA_Forge\RCRA_Forge.exe
echo  Folder:  dist\RCRA_Forge\  (%DISTSIZE% MB total)
echo.
echo  To run:  dist\RCRA_Forge\RCRA_Forge.exe
echo  To ship: zip up the entire dist\RCRA_Forge\ folder
echo.

REM ── Offer to launch ──────────────────────────────────────────────────────────
set /p LAUNCH="Launch RCRA_Forge.exe now? (y/n): "
if /i "%LAUNCH%"=="y" (
    start "" "dist\RCRA_Forge\RCRA_Forge.exe"
)

deactivate
echo Done.
pause
