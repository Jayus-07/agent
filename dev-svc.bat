@echo off
setlocal enabledelayedexpansion

:: =====================================================================
:: dev-svc.bat - shared implementation behind dev-start / dev-stop /
::               dev-restart / devctl (do not call it directly unless
::               you need the raw action form)
::
::   dev-svc.bat [status^|start^|stop^|restart^|rebuild] [backend^|admin^|web^|all] [/y]
::
::   backend = docker compose "app" service   127.0.0.1:8000  (/health)
::   admin   = frontend-admin container       :3200           (/login)
::             mirrored production image (node server.js, NO bind mount)
::   web     = frontend container             :3100           (/login)
::             mirrored production image (node server.js, NO bind mount)
::   /y      = skip the confirmation prompt of stop/restart/rebuild
::             (Git Bash / MSYS eats "/y" as a path conversion; cmd.exe /c
::              swallows it too - use "-y" here, or route via PowerShell)
::   no arg  = print status
::   rebuild = compose build + up -d all backend services AND the
::             containerized frontends (frontend-admin / frontend):
::             the ONLY way code / .env changes reach the containers
::             (2026-10-30 fix: rebuild used to be backend-only while
::             admin/web had already moved to mirrored images)
::
:: Conventions kept from start_frontend*.bat:
::   * next dev always boots from an EMPTY distDir (NEXT_DIST_DIR);
::     reusing a non-empty one always fails to boot. The dir name is
::     pinned to .next-dev and wiped before start (random-suffix
::     fallback only when the wipe is blocked), so Next's generated
::     tsconfig/next-env references stay byte-stable across boots
::     instead of appending a new fingerprint to tsconfig.json per run
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

:: Keep local resource limits on every start/rebuild. Discover the deployment
:: parent without non-ASCII literals because cmd reads this file as ANSI/GBK.
if not defined COMPOSE_FILE (
    for /d %%D in ("%ROOT%\*") do (
        if exist "%%~fD\prod-4c8g\docker-compose.local.yml" (
            set "COMPOSE_FILE=%ROOT%\docker-compose.yml;%%~fD\prod-4c8g\docker-compose.local.yml"
            set "COMPOSE_PATH_SEPARATOR=;"
        )
    )
)

:: ---- compose project name guard (shared stack = project "agent") ----
:: Compose derives the project name from the ROOT directory name. Running
:: this script from a worktree whose directory name differs (e.g. a Git
:: worktree under agent-wt-*) would otherwise build a PARALLEL compose
:: project: duplicate images and port-bind fights against the shared
:: stack. Pin the project name unless the caller already exported
:: COMPOSE_PROJECT_NAME (then respect the caller's choice).
if not defined COMPOSE_PROJECT_NAME (
    for %%I in ("%ROOT%") do set "ROOT_NAME=%%~nxI"
    if /i not "!ROOT_NAME!"=="agent" (
        set "COMPOSE_PROJECT_NAME=agent"
        echo   [dev-svc] ROOT dir "!ROOT_NAME!" differs from agent - pinning COMPOSE_PROJECT_NAME=agent to reuse the shared stack
    )
)

set "BACKEND_PORT=8000"
set "ADMIN_DIR=frontend-admin"
set "ADMIN_PORT=3200"
set "WEB_DIR=frontend"
set "WEB_PORT=3100"
set "CS_DIR=frontend-cs"
set "CS_PORT=3300"
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
if /i "!ACTION!"=="rebuild" goto do_rebuild
goto usage

:: =====================================================================
:do_status
echo Service status:
call :status_backend
call :status_next "%ADMIN_PORT%" admin
call :status_next "%CS_PORT%" cs
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

:do_rebuild
:: rebuild = backend images + containerized frontends. frontend-admin /
:: frontend run mirrored production images (node server.js, no source
:: bind mount): frontend code changes reach :3200/:3100 ONLY via this
:: path. frontend-cs still has a "next dev" fallback and stays out of
:: scope here (its container is covered by its own compose workflow).
call :confirm rebuild
if errorlevel 1 exit /b 0
call :rebuild_backend
call :rebuild_frontend
call :do_status
exit /b 0

:: =====================================================================
:: fanout: run <phase> for each selected service
:: =====================================================================
:fanout
if /i "%TARGET%"=="all" (
    call :dispatch %~1 backend
    call :dispatch %~1 admin
    call :dispatch %~1 cs
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
if /i "%NAME%"=="cs" (
    if /i "%PHASE%"=="start" call :start_next "%CS_DIR%" "%CS_PORT%" cs
    if /i "%PHASE%"=="stop"  call :stop_next "%CS_PORT%" cs
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
set "ASTAT=not running"
for /f "tokens=*" %%s in ('docker ps --filter "name=agent-apisix" --format "{{.Status}}" 2^>nul') do set "ASTAT=%%s"
curl.exe -s -o nul --max-time 5 "%GATEWAY_URL%/health"
if not errorlevel 1 (
    echo   [UP  ] apisix   "%ASTAT%"  gateway-200
) else (
    echo   [DOWN] apisix   "%ASTAT%"
)
set "MSTAT=container not running"
for /f "tokens=*" %%s in ('docker ps --filter "name=agent-business-mock-1" --format "{{.Status}}" 2^>nul') do set "MSTAT=%%s"
curl.exe -s -o nul --max-time 5 "http://127.0.0.1:8081/health"
if not errorlevel 1 (
    echo   [UP  ] business-mock "%MSTAT%" health-200
) else (
    echo   [DOWN] business-mock "%MSTAT%"
)
exit /b 0

:start_backend
echo   [backend] docker compose up -d --build app business-mock apisix rag-service mcp-service agent-worker rag-index-worker maintenance-worker report-worker beat cs-dispatcher ...
pushd "%ROOT%"
docker compose up -d --build app business-mock apisix rag-service mcp-service agent-worker rag-index-worker maintenance-worker report-worker beat cs-dispatcher
if errorlevel 1 goto start_backend_retry
goto start_backend_up_ok

:start_backend_retry
echo   [backend] up failed - prune stale containers, retry once ...
call :prune_stale_backend_containers
docker compose up -d --build app business-mock apisix rag-service mcp-service agent-worker rag-index-worker maintenance-worker report-worker beat cs-dispatcher
if errorlevel 1 (
    popd
    echo   [ERROR] docker compose up -d --build app failed after retry
    exit /b 1
)

:start_backend_up_ok
popd
call :wait_http "http://127.0.0.1:%BACKEND_PORT%/health" backend
call :wait_http "http://127.0.0.1:8081/health" business-mock
exit /b 0

:stop_backend
echo   [backend] docker compose stop app business-mock apisix rag-service mcp-service agent-worker rag-index-worker maintenance-worker report-worker beat cs-dispatcher postgres redis redis-broker auth-redis ...
pushd "%ROOT%"
docker compose stop app business-mock apisix rag-service mcp-service agent-worker rag-index-worker maintenance-worker report-worker beat cs-dispatcher postgres redis redis-broker auth-redis
set "RC=%ERRORLEVEL%"
popd
if not "%RC%"=="0" (
    echo   [ERROR] docker compose stop app failed, rc=%RC%
    exit /b 1
)
exit /b 0

:: =====================================================================
:: backend rebuild - build images + recreate ALL code-bearing backend
:: services. Plain "restart" reuses the old image and the env vars that
:: were baked in at container creation: code changes AND .env changes
:: both take effect only via this rebuild path. db-migrate one-shot is
:: re-run automatically through the compose depends_on chain.
:: =====================================================================
:rebuild_backend
set "BACKEND_SERVICES=app business-mock db-migrate rag-service mcp-service agent-worker rag-index-worker maintenance-worker report-worker beat cs-dispatcher"
echo   [backend] docker compose build %BACKEND_SERVICES% ...
pushd "%ROOT%"
docker compose build %BACKEND_SERVICES%
if errorlevel 1 goto rebuild_fail
call :prune_stale_backend_containers
echo   [backend] docker compose up -d %BACKEND_SERVICES% ...
docker compose up -d %BACKEND_SERVICES%
if errorlevel 1 goto rebuild_retry
goto rebuild_up_ok

:rebuild_retry
echo   [backend] up failed - prune stale containers, retry once ...
call :prune_stale_backend_containers
docker compose up -d %BACKEND_SERVICES%
if errorlevel 1 goto rebuild_fail

:rebuild_up_ok
popd
call :wait_http "http://127.0.0.1:%BACKEND_PORT%/health" backend
call :wait_http "http://127.0.0.1:8081/health" business-mock
exit /b 0

:rebuild_fail
set "RC=%ERRORLEVEL%"
popd
echo   [ERROR] docker compose rebuild failed, rc=%RC%
exit /b 1

:: =====================================================================
:: frontend rebuild - build + recreate the containerized frontends.
:: frontend-admin / frontend have NO source bind mount: a plain restart
:: reuses the old image, so frontend code changes take effect ONLY here.
:: =====================================================================
:rebuild_frontend
set "FRONTEND_SERVICES=frontend-admin frontend"
echo   [frontend] docker compose build %FRONTEND_SERVICES% ...
pushd "%ROOT%"
docker compose build %FRONTEND_SERVICES%
if errorlevel 1 goto rebuild_fe_fail
echo   [frontend] docker compose up -d %FRONTEND_SERVICES% ...
docker compose up -d %FRONTEND_SERVICES%
if errorlevel 1 goto rebuild_fe_fail
popd
call :wait_http "http://127.0.0.1:%ADMIN_PORT%/login" frontend-admin
call :wait_http "http://127.0.0.1:%WEB_PORT%/login" frontend
exit /b 0

:rebuild_fe_fail
set "RC=%ERRORLEVEL%"
popd
echo   [ERROR] docker compose frontend rebuild failed, rc=%RC%
exit /b 1

:: =====================================================================
:: prune_stale_backend_containers
:: A failed/interrupted "compose up" can leave leftovers that block the
:: next run with 'container name already in use':
::   - the canonical name holder left stopped but not recreated
::   - compose replace-renames like <hash>_agent-<svc>-1 (status Created)
:: Only NON-RUNNING containers (created/exited/restarting) are removed,
:: so healthy running services are never touched.
:: =====================================================================
:prune_stale_backend_containers
for /f "tokens=1,2" %%i in ('docker ps -a --filter "status=created" --filter "status=exited" --filter "status=restarting" --format "{{.ID}} {{.Names}}" 2^>nul') do (
    call :try_prune_one "%%i" "%%j"
)
exit /b 0

:try_prune_one
:: NOTE: no "exit /b" inside parenthesized blocks here - inside a
:: subroutine called from a for /f loop, block-level exit /b unwinds
:: the WHOLE call chain and silently truncates the outer loop.
set "PRUNE_ID=%~1"
set "PRUNE_NAME=%~2"
set "PRUNE_HIT="
for %%s in (%BACKEND_SERVICES%) do (
    if not defined PRUNE_HIT (
        set "PRUNE_T=!PRUNE_NAME:*agent-%%s-1=!"
        if not "!PRUNE_T!"=="!PRUNE_NAME!" set "PRUNE_HIT=1"
    )
)
if defined PRUNE_HIT (
    echo   [cleanup] removing stale container "!PRUNE_NAME!" ...
    docker rm -f "!PRUNE_ID!" >nul 2>&1
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

call :find_frontend_container "%PORT%"
if defined FRONTEND_CONTAINER (
    docker start "%FRONTEND_CONTAINER%" >nul
    call :wait_http "http://127.0.0.1:%PORT%/login" "%LABEL%"
    exit /b 0
)

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
:: Pinned distDir: a random name per start made Next append a new
:: "<distDir>/types" include line to the tracked tsconfig.json and
:: rewrite next-env.d.ts on EVERY boot (git churn). Wipe the pinned
:: dir first; fall back to a random fresh dir only if the wipe is
:: blocked by locked files.
set "DISTDIR=.next-dev"
if exist "%ROOT%\%DIR%\%DISTDIR%" rd /s /q "%ROOT%\%DIR%\%DISTDIR%" >nul 2>&1
if not exist "%ROOT%\%DIR%\%DISTDIR%" goto distok
:pickrandom
set "DISTDIR=.next-dev-%RANDOM%%RANDOM%"
if exist "%ROOT%\%DIR%\%DISTDIR%" goto pickrandom
:distok

echo   [%LABEL%] starting :%PORT% (distDir=%DISTDIR%, gateway=%GATEWAY_URL%)
start "%LABEL%-%PORT%" /D "%ROOT%\%DIR%" cmd /k "set NEXT_DIST_DIR=%DISTDIR%&& set AUTH_GATEWAY_URL=%GATEWAY_URL%&& npx next dev -p %PORT%"

call :wait_port "%PORT%" "%LABEL%"
if errorlevel 1 exit /b 1
call :wait_http "http://127.0.0.1:%PORT%/login" "%LABEL%"
exit /b 0

:stop_next
set "PORT=%~1"
set "LABEL=%~2"
call :find_frontend_container "%PORT%"
if defined FRONTEND_CONTAINER (
    docker stop "%FRONTEND_CONTAINER%"
    exit /b 0
)
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
:find_frontend_container
set "FRONTEND_CONTAINER="
set "FRONTEND_SERVICE="
if "%~1"=="3100" set "FRONTEND_SERVICE=frontend"
if "%~1"=="3200" set "FRONTEND_SERVICE=frontend-admin"
if "%~1"=="3300" set "FRONTEND_SERVICE=frontend-cs"
if not defined FRONTEND_SERVICE exit /b 0
for /f "delims=" %%c in ('docker ps -a --filter "label=com.docker.compose.project=agent" --filter "label=com.docker.compose.service=%FRONTEND_SERVICE%" --format "{{.ID}}" 2^>nul') do set "FRONTEND_CONTAINER=%%c"
exit /b 0

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
echo dev-svc.bat - shared impl of dev-start / dev-stop / dev-restart / dev-rebuild / devctl
echo.
echo   dev-svc.bat [status^|start^|stop^|restart^|rebuild] [backend^|admin^|cs^|web^|all] [/y]
echo.
echo   backend = docker compose app service   127.0.0.1:8000
echo   admin   = frontend-admin container     :3200
echo   cs      = frontend-cs next dev         :3300
echo   web     = frontend container           :3100
echo   rebuild = build images + up -d all backend services AND the
echo             containerized frontends (frontend-admin / frontend).
echo             Code or .env changes take effect ONLY via rebuild,
echo             plain restart reuses old image and old env vars.
exit /b 1
