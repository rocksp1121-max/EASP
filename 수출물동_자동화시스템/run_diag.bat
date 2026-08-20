@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul

set LOG=%~dp0diag.log
echo === %DATE% %TIME% === > "%LOG%"
echo. >> "%LOG%"

echo [1] Refreshing PATH... >> "%LOG%"
for /f "usebackq delims=" %%P in (`powershell -NoProfile -Command "[System.Environment]::GetEnvironmentVariable('PATH','Machine') + ';' + [System.Environment]::GetEnvironmentVariable('PATH','User')"`) do set "PATH=%%P"
echo PATH OK >> "%LOG%"
echo. >> "%LOG%"

echo [2] Python version check... >> "%LOG%"
python --version >> "%LOG%" 2>&1
echo. >> "%LOG%"

echo [3] Python location... >> "%LOG%"
where python >> "%LOG%" 2>&1
echo. >> "%LOG%"

echo [4] Current directory... >> "%LOG%"
echo %~dp0 >> "%LOG%"
echo. >> "%LOG%"

echo [5] Files check... >> "%LOG%"
if exist "%~dp0app\app.py" (echo app/app.py FOUND >> "%LOG%") else (echo app/app.py MISSING >> "%LOG%")
if exist "%~dp0step_processor.py" (echo step_processor.py FOUND >> "%LOG%") else (echo step_processor.py MISSING >> "%LOG%")
if exist "%~dp0step5_optimizer.py" (echo step5_optimizer.py FOUND >> "%LOG%") else (echo step5_optimizer.py MISSING >> "%LOG%")
if exist "%~dp0data\ref_fdest.csv" (echo data/ref_fdest.csv FOUND >> "%LOG%") else (echo data/ref_fdest.csv MISSING >> "%LOG%")
echo. >> "%LOG%"

echo [6] Required packages import test... >> "%LOG%"
python -c "import flask, pandas, openpyxl, pyxlsb; print('imports OK')" >> "%LOG%" 2>&1
echo. >> "%LOG%"

echo [7] Port 5000 check... >> "%LOG%"
netstat -ano | findstr :5000 >> "%LOG%" 2>&1
echo. >> "%LOG%"

echo [8] Starting Flask (will show first 30 sec or error)... >> "%LOG%"
cd /d "%~dp0"
start "" /B cmd /c "timeout /t 30 /nobreak >nul && taskkill /F /IM python.exe >nul 2>&1"
python app\app.py >> "%LOG%" 2>&1
echo. >> "%LOG%"

echo === END %TIME% === >> "%LOG%"
echo.
echo Diagnostics complete. Open this file and share content:
echo   %LOG%
echo.
notepad "%LOG%"
pause
