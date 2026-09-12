@echo off
setlocal enabledelayedexpansion

:: =============================================
:: Agent Platform - Unified One-Click Start
:: =============================================
:: Auto-detects the target mode and starts accordingly:
::
::   Gateway mode  = backend stack in Docker (compose up -d)
::                   + local frontend, requests go through
::                   API Gateway :8080
::                   (frontend\.env.local has
::                    NEXT_PUBLIC_API_URL=http://localhost:8080)
::
::   Local mode    = local uvicorn :8000 (--reload)
::                   + local frontend, requests proxied by
::                   Next.js rewrite to :8000
::                   (that env line commented out)
::
:: Conflict handling:
::   - env points local but a Docker container holds :8000
::     -> asks whether to switch to gateway mode
::   - env points gateway but Docker daemon is down -> error
:: =============================================

set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"
cd /d "%ROOT%"

echo.
echo ==============================================
echo    Agent Platform - One-Click Start
echo ==============================================

:: ---- Detect desired mode from frontend env ----
set "MODE=local"
findstr /B /C:"NEXT_PUBLIC_API_URL=http://localhost:8080" "%ROOT%\frontend\.env.local" >nul 2>&1
if not errorlevel 1 set "MODE=gateway"

:: ---- Detect whether a Docker container holds port 8000 ----
set "DOCKER_8000=0"
docker ps --format "{{.Names}} {{.Ports}}" >nul 2>&1
if not errorlevel 1 (
    docker ps --format "{{.Names}} {{.Ports}}" 2>nul | findstr /C:":8000->" >nul 2>&1
    if not errorlevel 1 set "DOCKER_8000=1"
)

if "!MODE!"=="gateway" goto :gateway
goto :local

:: =============================================
:gateway
:: =============================================
where docker >nul 2>&1
if errorlevel 1 (
    echo [ERROR] .env.local points to gateway mode ^(8080^) but docker is not in PATH.
    echo         Start Docker Desktop, or comment out NEXT_PUBLIC_API_URL in
    echo         frontend\.env.local and rerun for local mode.
    pause
    exit /b 1
)
docker info >nul 2>&1
if errorlevel 1 (
    echo [ERROR] .env.local points to gateway mode ^(8080^) but Docker daemon is not running.
    echo         Start Docker Desktop first, or comment out NEXT_PUBLIC_API_URL in
    echo         frontend\.env.local and rerun for local mode.
    pause
    exit /b 1
)

echo.
echo [MODE] Gateway mode - backend stack in Docker, frontend local
echo [1/2] Starting docker compose stack ...

:: Rebuild only when source is newer than the built image
:: (scripts\check_image_stale.py: exit 0 = fresh, >=1 = stale or detect error)
set "BUILD_FLAGS="
set "PYEXE=%ROOT%\.venv\Scripts\python.exe"
if not exist "%PYEXE%" set "PYEXE=python"
"%PYEXE%" "%ROOT%\scripts\check_image_stale.py" >nul 2>&1
if errorlevel 1 (
    set "BUILD_FLAGS=--build"
    echo        Source changed since last image build - rebuilding ...
) else (
    echo        Image is up to date - skipping build
)
docker compose up -d !BUILD_FLAGS!
if errorlevel 1 (
    echo [ERROR] docker compose up failed
    pause
    exit /b 1
)
goto :frontend

:: =============================================
:local
:: =============================================
if "!DOCKER_8000!"=="1" (
    echo.
    echo [CONFLICT] frontend\.env.local points to LOCAL mode, but the Docker
    echo            container stack is running and holds port 8000.
    echo            A local uvicorn cannot bind 8000 while the container is up.
    echo.
    echo   [G] Switch to gateway mode - keep Docker stack, start frontend only
    echo   [C] Cancel - I will handle it myself ^(docker compose down / fix .env.local^)
    choice /C GC /N /M "Select: "
    if errorlevel 2 (
        echo Cancelled. Nothing was started.
        pause
        exit /b 1
    )
    set "MODE=gateway"
    goto :gateway
)

set "VENV_PYTHON=%ROOT%\.venv\Scripts\python.exe"
if not exist "%VENV_PYTHON%" (
    echo [ERROR] venv not found: %VENV_PYTHON%
    pause
    exit /b 1
)

netstat -ano | find ":8000" >nul 2>&1
if not errorlevel 1 (
    echo [WARNING] Port 8000 in use - run stop_all.bat first
)
netstat -ano | find ":3000" >nul 2>&1
if not errorlevel 1 (
    echo [WARNING] Port 3000 in use - run stop_all.bat first
)

echo.
echo [MODE] Local mode - backend + frontend both local
echo [1/2] Starting backend on port 8000 ...
start "backend-8000" /D "%ROOT%\backend" cmd /k ""%VENV_PYTHON%" -m uvicorn app.server:app --host 127.0.0.1 --port 8000 --reload"

:: =============================================
:frontend
:: =============================================
echo [2/2] Starting frontend on port 3000 ...
start "frontend-3000" /D "%ROOT%\frontend" cmd /k "npx next dev -p 3000"

echo.
echo ==============================================
if "!MODE!"=="gateway" (
    echo    Mode:     Gateway ^(Docker backend^)
    echo    Frontend: http://localhost:3000
    echo    Gateway:  http://localhost:8080
    echo    Backend:  http://localhost:8000/docs ^(container^)
    echo.
    echo    Stop frontend: close frontend window or stop_all.bat
    echo    Stop backend stack: docker compose down
) else (
    echo    Mode:     Local ^(uvicorn --reload^)
    echo    Frontend: http://localhost:3000
    echo    Backend:  http://localhost:8000/docs
    echo.
    echo    Stop: close windows or run stop_all.bat
)
echo ==============================================
echo.
pause
