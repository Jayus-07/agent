@echo off
setlocal enabledelayedexpansion

:: =============================================
:: Agent Platform - Restart Backend + Frontend
:: Self-contained: stop + start, no external deps
:: =============================================

set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"
cd /d "%ROOT%"

echo.
echo ==============================================
echo    Agent Platform - Restart All Services
echo ==============================================

:: ---- Stop (same logic as stop_all.bat) ----
echo.
echo [1/2] Stopping services ...

set RETRY=0
:kill_loop
set FOUND=0
for /f "tokens=5" %%a in ('netstat -ano ^| findstr /C:":8000 "') do (
    taskkill /F /PID %%a /T >nul 2>&1
    if !errorlevel! equ 0 (echo   Stopped PID %%a ^(port 8000^) & set FOUND=1)
)
for /f "tokens=5" %%a in ('netstat -ano ^| findstr /C:":3000 "') do (
    taskkill /F /PID %%a /T >nul 2>&1
    if !errorlevel! equ 0 (echo   Stopped PID %%a ^(port 3000^) & set FOUND=1)
)
if !FOUND! equ 1 (
    set /a RETRY+=1
    if !RETRY! lss 4 (timeout /t 2 /nobreak >nul & goto kill_loop)
)
if !RETRY! equ 0 echo   No running services found

:: ---- Start (same logic as start_all.bat) ----
echo.
echo [2/2] Starting services ...

set "VENV_PYTHON=%ROOT%\.venv\Scripts\python.exe"
if not exist "%VENV_PYTHON%" (
    echo   [ERROR] venv not found: %VENV_PYTHON%
    pause
    exit /b 1
)

start "backend-8000" /D "%ROOT%\backend" cmd /k ""%VENV_PYTHON%" -m uvicorn app.server:app --host 127.0.0.1 --port 8000 --reload"
echo   Backend starting ^(port 8000^) ...

start "frontend-3000" /D "%ROOT%\frontend" cmd /k "npx next dev -p 3000"
echo   Frontend starting ^(port 3000^) ...

echo.
echo ==============================================
echo    Backend:  http://localhost:8000/docs
echo    Frontend: http://localhost:3000
echo.
echo    Close windows to stop, or run stop_all.bat
echo ==============================================
echo.

endlocal
