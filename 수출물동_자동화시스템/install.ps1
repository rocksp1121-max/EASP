# install.ps1 - EASP (수출 자동 출하 계획) 설치 스크립트

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host ""
Write-Host "============================================================"
Write-Host "  EASP - Export Auto Shipment Planning - INSTALL"
Write-Host "============================================================"
Write-Host ""

# ── STEP 0. 아키텍처 감지 ─────────────────────────────────────
$arch = if ([System.Environment]::Is64BitOperatingSystem) { 64 } else { 32 }
Write-Host "[System] ${arch}-bit Windows detected"
Write-Host ""

if ($arch -eq 32) {
    $pyVersion = "3.11.9"
    $pyUrl     = "https://www.python.org/ftp/python/3.11.9/python-3.11.9.exe"
    $pyLabel   = "Python 3.11.9 (32-bit)"
    Write-Host "[Note] Python 3.12+ dropped 32-bit support. Will install Python 3.11.9"
} else {
    $pyVersion = "3.12.7"
    $pyUrl     = "https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe"
    $pyLabel   = "Python 3.12.7 (64-bit)"
}

# ── STEP 1. Python 확인 ───────────────────────────────────────
Write-Host "[1/4] Checking Python..."

$pythonOk = $false
try {
    $verOutput = & python --version 2>&1
    if ($verOutput -match "Python (\d+)\.(\d+)") {
        $major = [int]$Matches[1]
        $minor = [int]$Matches[2]
        if ($major -gt 3 -or ($major -eq 3 -and $minor -ge 10)) {
            Write-Host "  [OK] $verOutput found"
            $pythonOk = $true
        } else {
            Write-Host "  [!!] $verOutput is too old (need 3.10+). Will upgrade..."
        }
    }
} catch {
    Write-Host "  [!!] Python not found. Will install automatically..."
}

# ── STEP 2. Python 설치 ───────────────────────────────────────
if (-not $pythonOk) {
    Write-Host ""
    Write-Host "[2/4] Downloading $pyLabel ..."
    Write-Host "      URL: $pyUrl"
    Write-Host ""

    $installer = Join-Path $env:TEMP "python_setup.exe"

    try {
        $ProgressPreference = "SilentlyContinue"
        Invoke-WebRequest -Uri $pyUrl -OutFile $installer -UseBasicParsing
        Write-Host "  [OK] Download complete"
    } catch {
        Write-Host ""
        Write-Host "[ERROR] Download failed: $($_.Exception.Message)"
        Write-Host "        Install Python manually: https://www.python.org/downloads/"
        Write-Host "        Then run install.bat again."
        Read-Host "Press Enter to exit"
        exit 1
    }

    Write-Host "  Installing $pyLabel silently..."
    $proc = Start-Process -FilePath $installer `
        -ArgumentList "/quiet InstallAllUsers=0 PrependPath=1 Include_test=0" `
        -Wait -PassThru
    Remove-Item $installer -Force -ErrorAction SilentlyContinue

    if ($proc.ExitCode -ne 0) {
        Write-Host ""
        Write-Host "[ERROR] Python installation failed (exit code: $($proc.ExitCode))"
        Write-Host "        Try running install.bat as Administrator."
        Read-Host "Press Enter to exit"
        exit 1
    }

    # PATH 갱신
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "User") + ";" + `
                [System.Environment]::GetEnvironmentVariable("Path", "Machine")

    try {
        $verCheck = & python --version 2>&1
        Write-Host "  [OK] $pyLabel installed successfully ($verCheck)"
    } catch {
        Write-Host ""
        Write-Host "[NOTE] Python installed but PATH needs a restart."
        Write-Host "       Please CLOSE this window and run install.bat again."
        Read-Host "Press Enter to exit"
        exit 0
    }
} else {
    Write-Host "[2/4] Python already installed. Skipping."
}

# ── STEP 3. pip 패키지 설치 ───────────────────────────────────
Write-Host ""
Write-Host "[3/4] Installing Python packages..."
Write-Host "      flask / pandas / openpyxl / xlwings / playwright"
Write-Host ""

$reqFile = Join-Path $ScriptDir "requirements.txt"

try {
    & python -m pip install --upgrade pip --quiet
    & python -m pip install -r $reqFile
    if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
    Write-Host "  [OK] Packages installed"
} catch {
    Write-Host ""
    Write-Host "[ERROR] Package installation failed."
    Write-Host "        Check your internet connection and try again."
    Read-Host "Press Enter to exit"
    exit 1
}

# ── STEP 4. Playwright 브라우저 설치 ──────────────────────────
Write-Host ""
Write-Host "[4/4] Installing Playwright Chromium (~200MB)..."
Write-Host "      Required for portal crawling feature."
Write-Host ""

try {
    & python -m playwright install chromium
    if ($LASTEXITCODE -ne 0) { throw "playwright install failed" }
    Write-Host "  [OK] Playwright Chromium installed"
} catch {
    Write-Host "  [WARN] Playwright install failed."
    Write-Host "         You can ignore this if you do not use the crawling feature."
}

# ── 완료 ───────────────────────────────────────────────────────
Write-Host ""
Write-Host "============================================================"
Write-Host "  Installation complete!  [${arch}-bit]"
Write-Host ""
Write-Host "  run.bat       - Start server + open browser"
Write-Host "  open_html.bat - Open UI only (no Flask needed)"
Write-Host "============================================================"
Write-Host ""
