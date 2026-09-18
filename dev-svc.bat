@echo off
setlocal enabledelayedexpansion

:: =====================================================================
:: dev-svc.bat - shared implementation behind dev-start / dev-stop /
::               dev-restart / devctl (do not call it directly unless
::               you need the raw action form)
::
::   dev-svc.bat [status^|start^|stop^|restart] [backend^|admin^|web^|all] [/y]
::
::   backend = docker compose "app" service   127.0.0.1:8000  (/health)
::   admin   = frontend-admin  next dev       :3200           (/login)
::   web     = frontend        next dev       :3100           (/login)
::   /y      = skip the confirmation prompt of stop/restart
::   no arg  = print status
::
:: Conventions kept from start_frontend*.bat:
::   * every next dev start uses a NEW EMPTY distDir (NEXT_DIST_DIR),
::     reusing a non-empty one always fails to boot
::   * stopping a frontend only kills the LISTENING pid on its port,
::     never the browsers/clients that merely connected to it
::   * backend runs in docker compose (the only complete runtime);
::     APISIX :9080 self-heals 1-2 min after an app restart
::
:: Batch gotchas already handled here - do NOT reintroduce them:
::   * a label line must not carry "arguments" (call :label fails)
::   * never echo literal parentheses inside an if (...) block,
::     the ")" closes the block early and runs the lines behind it
::   * external text such as docker "Up 5 minutes (healthy)" must be
::     echoed inside double quotes
::
:: NOTE: this file is intentionally ASCII-only (cmd parses .bat as ANSI/GBK)
:: =====================================================================

set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"

set "BACKEND_PORT=8000"
set "ADMIN_DIR=frontend-admin"
set "ADMIN_PORT=3200"
set "WEB_DIR=frontend"
set "WEB_PORT=3100"
set "GATEWAY_URL=http://127.0.0.1:9080"

:: ---- parse args: flags anywhere, first bare token = action, 2nd = target ----
set "ACTION="
set "TARGET="
set "YES=0"
for %%a in (%*) do (
    set "TOK=%%~a"
    if /i "!TOK!"=="/y" set "YES=1"
    if /i "!TOK!"=="-y" set "YES=1"
    if /i "!TOK!"=="--yes" set "YES=1"
    if not "!TOK:~0,1!"=="/" (
        if "!ACTION!"=="" (
            set "ACTION=!TOK!"
        ) else (
            if "!TARGET!"=="" set "TARGET=!TOK!"
        )
    )
)
if "!ACTION!"=="" set "ACTION=status"
if "!TARGET!"=="" set "TARGET=all"

if /i "!ACTION!"=="status"  goto do_status
if /i "!ACTION!"=="start"   goto do_start
if /i "!ACTION!"=="stop"    goto do_stop
if /i "!ACTION!"=="restart" goto do_restart
goto usage

:: =====================================================================
:do_status
echo Service status:
call :status_backend
call :status_next "%ADMIN_PORT%" admin
call :status_next "%WEB_PORT%" web
echo.
echo   gateway: http://127.0.0.1:9080  (APISIX, python entry)
exit /b 0

:do_start
call :fanout start
call :do_status
exit /b 0

:do_stop
call :confirm stop
if errorlevel 1 exit /b 0
call :fanout stop
call :do_status
exit /b 0

:do_restart
call :confirm restart
if errorlevel 1 exit /b 0
call :fanout stop
call :fanout start
call :do_status
exit /b 0

:: =====================================================================
:: fanout: run <phase> for each selected service
:: =====================================================================
:fanout
if /i "%TARGET%"=="all" (
    call :dispatch %~1 backend
    call :dispatch %~1 admin
    call :dispatch %~1 web
    exit /b 0
)
call :dispatch %~1 %TARGET%
exit /b 0

:dispatch
set "PHASE=%~1"
set "NAME=%~2"
if /i "%NAME%"=="backend" (
    if /i "%PHASE%"=="start" call :start_backend
    if /i "%PHASE%"=="stop"  call :stop_backend
    exit /b 0
)
if /i "%NAME%"=="admin" (
    if /i "%PHASE%"=="start" call :start_next "%ADMIN_DIR%" "%ADMIN_PORT%" admin
    if /i "%PHASE%"=="stop"  call :stop_next "%ADMIN_PORT%" admin
    exit /b 0
)
if /i "%NAME%"=="web" (
    if /i "%PHASE%"=="start" call :start_next "%WEB_DIR%" "%WEB_PORT%" web
    if /i "%PHASE%"=="stop"  call :stop_next "%WEB_PORT%" web
    exit /b 0
)
echo   [ERROR] unknown service: %NAME%
exit /b 1

:: =====================================================================
:: backend (docker compose)
:: =====================================================================
:status_backend
set "CSTAT=container not running"
for /f "tokens=*" %%s in ('docker ps --filter "name=agent-app-1" --format "{{.Status}}" 2^>nul') do set "CSTAT=%%s"
curl.exe -s -o nul --max-time 5 "http://127.0.0.1:%BACKEND_PORT%/health"
if not errorlevel 1 (
    echo   [UP  ] backend  "%CSTAT%"  health-200
) else (
    echo   [DOWN] backend  "%CSTAT%"
)
exit /b 0

:start_backend
echo   [backend] docker compose up -d app ...
pushd "%ROOT%"
docker compose up -d app
set "RC=%ERRORLEVEL%"
popd
if not "%RC%"=="0" (
    echo   [ERROR] docker compose up -d app failed, rc=%RC%
    exit /b 1
)
call :wait_http "http://127.0.0.1:%BACKEND_PORT%/health" backend
exit /b 0

:stop_backend
echo   [backend] docker compose stop app ...
pushd "%ROOT%"
docker compose stop app
set "RC=%ERRORLEVEL%"
popd
if not "%RC%"=="0" (
    echo   [ERROR] docker compose stop app failed, rc=%RC%
    exit /b 1
)
exit /b 0

:: =====================================================================
:: next dev (admin / web)
:: =====================================================================
:status_next
set "PORT=%~1"
set "LABEL=%~2"
set "PID="
for /f "tokens=5" %%a in ('netstat -ano ^| findstr /C:":%PORT% " ^| findstr /C:"LISTENING"') do (
    if not defined PID set "PID=%%a"
)
if defined PID (
    echo   [UP  ] %LABEL%  :%PORT%  pid=%PID%
) else (
    echo   [DOWN] %LABEL%  :%PORT%  no listener
)
exit /b 0

:start_next
set "DIR=%~1"
set "PORT=%~2"
set "LABEL=%~3"

if not exist "%ROOT%\%DIR%\package.json" (
    echo   [ERROR] not found: %ROOT%\%DIR%
    exit /b 1
)

netstat -ano | findstr /C:":%PORT% " | findstr /C:"LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo   [%LABEL%] already running on :%PORT% - skip
    exit /b 0
)

:pickdist
set "DISTDIR=.next-dev-%RANDOM%%RANDOM%"
if exist "%ROOT%\%DIR%\%DISTDIR%" goto pickdist

echo   [%LABEL%] starting :%PORT% (distDir=%DISTDIR%, gateway=%GATEWAY_URL%)
start "%LABEL%-%PORT%" /D "%ROOT%\%DIR%" cmd /k "set NEXT_DIST_DIR=%DISTDIR%&& set AUTH_GATEWAY_URL=%GATEWAY_URL%&& npx next dev -p %PORT%"

call :wait_port "%PORT%" "%LABEL%"
if errorlevel 1 exit /b 1
call :wait_http "http://127.0.0.1:%PORT%/login" "%LABEL%"
exit /b 0

:stop_next
set "PORT=%~1"
set "LABEL=%~2"
set "FOUND=0"
for /f "tokens=5" %%a in ('netstat -ano ^| findstr /C:":%PORT% " ^| findstr /C:"LISTENING"') do (
    taskkill /F /PID %%a /T >nul 2>&1
    if not errorlevel 1 (
        echo   [%LABEL%] stopped PID %%a on port !PORT!
        set "FOUND=1"
    )
)
if "!FOUND!"=="0" (
    echo   [%LABEL%] nothing listening on :%PORT%
    exit /b 0
)
for /l %%i in (1,1,15) do (
    netstat -ano | findstr /C:":%PORT% " | findstr /C:"LISTENING" >nul 2>&1
    if errorlevel 1 exit /b 0
    ping -n 2 127.0.0.1 >nul
)
echo   [WARN] :%PORT% still listening
exit /b 0

:: =====================================================================
:: helpers
:: =====================================================================
:wait_port
set "PORT=%~1"
set "LABEL=%~2"
for /l %%i in (1,1,45) do (
    ping -n 3 127.0.0.1 >nul
    netstat -ano | findstr /C:":%PORT% " | findstr /C:"LISTENING" >nul 2>&1
    if not errorlevel 1 (
        echo   [%LABEL%] listening on :%PORT%
        exit /b 0
    )
)
echo   [WARN] %LABEL% not listening on :%PORT% after ~90s
exit /b 1

:wait_http
set "URL=%~1"
set "LABEL=%~2"
for /l %%i in (1,1,30) do (
    curl.exe -s -o nul --max-time 5 "%URL%"
    if not errorlevel 1 (
        echo   [%LABEL%] ready -^> %URL%
        exit /b 0
    )
    ping -n 3 127.0.0.1 >nul
)
echo   [WARN] %LABEL% http not ready: %URL%
exit /b 1

:confirm
if "!YES!"=="1" exit /b 0
set "ANS="
set /p "ANS=%~1 %TARGET% ? [y/N] "
if /i "%ANS%"=="y" exit /b 0
if /i "%ANS%"=="yes" exit /b 0
echo   cancelled
exit /b 1

:: =====================================================================
:usage
echo dev-svc.bat - shared impl of dev-start / dev-stop / dev-restart / devctl
echo.
echo   dev-svc.bat [status^|start^|stop^|restart] [backend^|admin^|web^|all] [/y]
echo.
echo   backend = docker compose app service   127.0.0.1:8000
echo   admin   = frontend-admin next dev      :3200
echo   web     = frontend next dev            :3100
exit /b 1
