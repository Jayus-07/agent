@echo off
setlocal enabledelayedexpansion

:: =============================================
:: stop_py.bat - 停止 Python 后端（按端口 8000 杀，含 uvicorn worker 重试）
:: =============================================

set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"
cd /d "%ROOT%"

:: Docker 容器占着 8000 时跳过（杀端口会误杀 com.docker.backend / wslrelay）
set SKIP=0
docker ps --format "{{.Names}} {{.Ports}}" 2>nul | findstr /C:":8000->" >nul 2>&1
if not errorlevel 1 (
    set SKIP=1
    echo   [SKIP] Port 8000 held by Docker container ^(app^) - untouched
)

set RETRY=0
:loop
set FOUND=0
if "!SKIP!"=="0" (
    for /f "tokens=5" %%a in ('netstat -ano ^| findstr /C:":8000 "') do (
        taskkill /F /PID %%a /T >nul 2>&1
        if !errorlevel! equ 0 (
            echo   Stopped PID %%a ^(port 8000^)
            set FOUND=1
        )
    )
)
if !FOUND! equ 1 (
    set /a RETRY+=1
    if !RETRY! lss 4 (
        timeout /t 2 /nobreak >nul
        goto loop
    )
)
if !RETRY! equ 0 if !SKIP! equ 0 echo   Nothing running on port 8000

echo   Done.
endlocal
