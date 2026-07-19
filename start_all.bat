@echo off
setlocal

:: =============================================
:: Agent Platform - Start Backend + Frontend
:: =============================================
:: Run this by double-clicking in Explorer.
:: Two new windows will open for backend/frontend.
:: =============================================

set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"

echo.
echo ==============================================
echo    Agent Platform - Start All Services
echo ==============================================

:: Check venv
if not exist "%ROOT%\.venv\Scripts\python.exe" (
    echo [ERROR] venv not found: .venv\Scripts\python.exe
    pause
    exit /b 1
)

:: Check port conflicts
netstat -ano | find ":8000" >nul 2>&1
if not errorlevel 1 (
    echo [WARNING] Port 8000 in use - run stop_all.bat first
)
netstat -ano | find ":3000" >nul 2>&1
if not errorlevel 1 (
    echo [WARNING] Port 3000 in use - run stop_all.bat first
)

:: Start backend in new window
echo.
echo Starting backend on port 8000 ...
start "backend-8000" /D "%ROOT%\backend" cmd /k ""%ROOT%\.venv\Scripts\python.exe" -m uvicorn app.server:app --host 127.0.0.1 --port 8000 --reload"

:: Start frontend in new window
echo Starting frontend on port 3000 ...
start "frontend-3000" /D "%ROOT%\frontend" cmd /k "npx next dev -p 3000"

echo.
echo ==============================================
echo    Backend:  http://localhost:8000/docs
echo    Frontend: http://localhost:3000
echo.
echo    Close windows or run stop_all.bat to stop
echo ==============================================
echo.
pause
