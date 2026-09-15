@echo off
setlocal enabledelayedexpansion

:: =============================================
:: stop_py.bat - Stop Python backend
::   Default (container mode): docker compose stop app (other containers untouched)
::   "native" arg: kill host uvicorn on port 8000 (with retry;
::     auto-skips when Docker holds 8000 - protects com.docker.backend)
::   NOTE: keep this file ASCII-only (cmd parses .bat in ANSI/GBK;
::         UTF-8 comments get misread and break control flow)
:: =============================================

set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"
cd /d "%ROOT%"

if /i "%~1"=="native" goto native

:: ---- container mode (default) ----
docker ps >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Docker daemon not running - start Docker Desktop first
    pause & exit /b 1
)
echo Stopping backend container ^(agent-app-1^) ...
docker compose stop app
echo   Done. APISIX^(9080^) still running - gateway requests will 502 until app restarts.
goto :eof

:: ---- native mode (stop_py.bat native) ----
:native
:: Skip when Docker holds 8000 (would kill com.docker.backend / wslrelay)
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
    for /f "tokens=5" %%a in ('netstat -ano ^| findstr /C:":8000 " ^| findstr /C:"LISTENING"') do (
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
