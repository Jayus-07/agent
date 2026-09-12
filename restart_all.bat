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

:: ---- Guard: refuse to run if Docker container holds port 8000 ----
:: kill-by-port would kill com.docker.backend.exe / wslrelay.exe and break
:: Docker port forwarding for the whole container stack (incl. gateway 8080)
docker ps --format "{{.Names}} {{.Ports}}" 2>nul | findstr /C:":8000->" >nul 2>&1
if not errorlevel 1 (
    echo [ERROR] Port 8000 is held by Docker container ^(app^) - gateway mode is active.
    echo         Restart backend : docker compose restart app
    echo         Restart frontend: close frontend window, then run start_micro.bat
    echo         To switch to local mode: docker compose down first
    pause
    exit /b 1
)

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
