# ============================================================
#  RCRA Forge — Windows EXE Builder (PowerShell version)
#  Right-click → "Run with PowerShell"   OR
#  From terminal:  .\build_windows.ps1
# ============================================================

$ErrorActionPreference = "Stop"

function Write-Step($n, $msg) {
    Write-Host "`n[$n/5] $msg" -ForegroundColor Cyan
}
function Write-OK($msg) {
    Write-Host "  [OK] $msg" -ForegroundColor Green
}
function Write-Fail($msg) {
    Write-Host "`n  [ERROR] $msg" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

Clear-Host
Write-Host @"

  ██████╗  ██████╗██████╗  █████╗     ███████╗ ██████╗ ██████╗  ██████╗ ███████╗
  ██╔══██╗██╔════╝██╔══██╗██╔══██╗    ██╔════╝██╔═══██╗██╔══██╗██╔════╝ ██╔════╝
  ██████╔╝██║     ██████╔╝███████║    █████╗  ██║   ██║██████╔╝██║  ███╗█████╗
  ██╔══██╗██║     ██╔══██╗██╔══██║    ██╔══╝  ██║   ██║██╔══██╗██║   ██║██╔══╝
  ██║  ██║╚██████╗██║  ██║██║  ██║    ██║     ╚██████╔╝██║  ██║╚██████╔╝███████╗
  ╚═╝  ╚═╝ ╚═════╝╚═╝  ╚═╝╚═╝  ╚═╝   ╚═╝      ╚═════╝ ╚═╝  ╚═╝ ╚═════╝ ╚══════╝

  RCRA Forge v0.1.0 — Windows EXE Builder
  =========================================
"@ -ForegroundColor Blue

# ── Check Python ──────────────────────────────────────────────────────────────
Write-Step 1 "Checking Python installation..."
try {
    $pyver = python --version 2>&1
    Write-OK "$pyver found"
} catch {
    Write-Fail "Python not found. Install from https://python.org (check 'Add to PATH')"
}

# ── Virtual environment ───────────────────────────────────────────────────────
Write-Step 2 "Setting up virtual environment..."
if (-not (Test-Path ".venv")) {
    python -m venv .venv
    Write-OK "Virtual environment created"
} else {
    Write-OK "Virtual environment already exists"
}
& ".venv\Scripts\Activate.ps1"

# ── Install dependencies ──────────────────────────────────────────────────────
Write-Step 3 "Installing / updating dependencies..."
pip install --upgrade pip -q
pip install PyQt6 PyOpenGL PyOpenGL-accelerate numpy Pillow pyinstaller -q
if ($LASTEXITCODE -ne 0) { Write-Fail "pip install failed" }
Write-OK "All packages installed"

# ── Icon ──────────────────────────────────────────────────────────────────────
if (-not (Test-Path "assets\icon.ico")) {
    Write-Host "`n  [INFO] No assets\icon.ico found — building without custom icon" -ForegroundColor Yellow
    # Patch spec file to remove icon reference
    (Get-Content rcra_forge.spec) `
        -replace "icon='assets\\\\icon.ico' if sys\.platform == 'win32' else None", "icon=None" `
        | Set-Content rcra_forge.spec
}

# ── Clean ─────────────────────────────────────────────────────────────────────
Write-Step 4 "Cleaning previous build..."
@("build", "dist") | ForEach-Object {
    if (Test-Path $_) { Remove-Item $_ -Recurse -Force }
}
Write-OK "Clean done"

# ── PyInstaller ───────────────────────────────────────────────────────────────
Write-Step 5 "Running PyInstaller (takes 1-3 minutes)..."
pyinstaller rcra_forge.spec --noconfirm
if ($LASTEXITCODE -ne 0) {
    Write-Fail "PyInstaller failed. Check output above."
}

# ── Verify ────────────────────────────────────────────────────────────────────
$exePath = "dist\RCRA_Forge\RCRA_Forge.exe"
if (-not (Test-Path $exePath)) {
    Write-Fail "Expected EXE not found at $exePath"
}

$sizeMB = [math]::Round(
    (Get-ChildItem "dist\RCRA_Forge" -Recurse | Measure-Object -Property Length -Sum).Sum / 1MB, 1
)

Write-Host "`n" -NoNewline
Write-Host "  ✅ BUILD SUCCESSFUL" -ForegroundColor Green
Write-Host "  ─────────────────────────────────────────────" -ForegroundColor DarkGray
Write-Host "  EXE:    $exePath" -ForegroundColor White
Write-Host "  Size:   $sizeMB MB (full folder)" -ForegroundColor White
Write-Host "  To distribute: zip the dist\RCRA_Forge\ folder" -ForegroundColor White
Write-Host "  ─────────────────────────────────────────────`n" -ForegroundColor DarkGray

$launch = Read-Host "Launch RCRA_Forge.exe now? (y/n)"
if ($launch -eq "y") {
    Start-Process $exePath
}

deactivate
