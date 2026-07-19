@echo off
setlocal enabledelayedexpansion

:: =============================================
:: Agent Platform - Stop Backend + Frontend
:: Kill by port (handle uvicorn worker forks)
:: =============================================

cd /d "%~dp0"

echo.
echo ==============================================
echo    Agent Platform - Stop All Services
echo ==============================================

set RETRY=0
:loop
set FOUND=0

:: 8000 - findstr /C:":8000 " avoids matching :80000
for /f "tokens=5" %%a in ('netstat -ano ^| findstr /C:":8000 "') do (
    set PID=%%a
    taskkill /F /PID !PID! /T >nul 2>&1
    if !errorlevel! equ 0 (
        echo   Stopped PID !PID! ^(port 8000^)
        set FOUND=1
    )
)

:: 3000
for /f "tokens=5" %%a in ('netstat -ano ^| findstr /C:":3000 "') do (
    set PID=%%a
    taskkill /F /PID !PID! /T >nul 2>&1
    if !errorlevel! equ 0 (
        echo   Stopped PID !PID! ^(port 3000^)
        set FOUND=1
    )
)

:: Retry up to 4 times (uvicorn workers may respawn)
if !FOUND! equ 1 (
    set /a RETRY+=1
    if !RETRY! lss 4 (
        timeout /t 2 /nobreak >nul
        goto loop
    )
)

if !RETRY! equ 0 echo   No services running on port 8000/3000

echo.
echo   Done - all services stopped
echo ==============================================
echo.

endlocal
