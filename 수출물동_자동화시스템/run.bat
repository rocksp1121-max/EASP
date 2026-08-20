@echo off
setlocal enabledelayedexpansion

echo.
echo ============================================================
echo   EASP - Export Auto Shipment Planning
echo ============================================================
echo.

:: Refresh PATH (in case install.bat just finished)
for /f "usebackq delims=" %%P in (`powershell -NoProfile -Command "[System.Environment]::GetEnvironmentVariable('PATH','Machine') + ';' + [System.Environment]::GetEnvironmentVariable('PATH','User')"`) do set "PATH=%%P"

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found.
    echo         1. Run install.bat first.
    echo         2. If install just finished, close this window and reopen run.bat.
    pause
    exit /b 1
)

:: If already running, just open browser and exit
powershell -NoProfile -Command "try { Invoke-WebRequest -Uri 'http://127.0.0.1:5000' -UseBasicParsing -TimeoutSec 1 ^| Out-Null; exit 0 } catch { exit 1 }" >nul 2>&1
if !errorlevel! equ 0 (
    echo [Info] EASP is already running. Opening browser...
    start http://localhost:5000
    timeout /t 2 >nul
    exit /b 0
)

:: Start Flask - browser will auto-open once ready (handled by app.py)
echo Starting Flask server... (browser opens automatically when ready)
echo Keep this window open. Ctrl+C or close this window to stop.
echo.
cd /d "%~dp0"
python "app\app.py"

pause
