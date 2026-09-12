@echo off
setlocal enabledelayedexpansion

:: =============================================
:: Agent Platform - Start Microservice Mode
:: =============================================
:: Full backend stack runs in Docker (api-gateway:8080 + app:8000 +
:: business-service:8081 + rag/mcp/postgres/kafka/redis).
:: Only the frontend (Next.js, port 3000) runs locally and reaches
:: the backend through the gateway on 8080.
::
:: Prerequisite: frontend\.env.local must have
::   NEXT_PUBLIC_API_URL=http://localhost:8080
:: This script checks it and warns if missing.
:: =============================================

set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"
cd /d "%ROOT%"

echo.
echo ==============================================
echo    Agent Platform - Start Microservice Mode
echo ==============================================

:: Check docker
where docker >nul 2>&1
if errorlevel 1 (
    echo [ERROR] docker not found in PATH
    pause
    exit /b 1
)
docker info >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Docker daemon not running - start Docker Desktop first
    pause
    exit /b 1
)

:: Check frontend env points to gateway (8080)
findstr /B /C:"NEXT_PUBLIC_API_URL=http://localhost:8080" "%ROOT%\frontend\.env.local" >nul 2>&1
if errorlevel 1 (
    echo [WARNING] frontend\.env.local does NOT enable NEXT_PUBLIC_API_URL=http://localhost:8080
    echo           Uncomment that line and restart frontend, otherwise requests
    echo           still go to local backend on port 8000.
)

:: Check port conflicts
netstat -ano | find ":3000" >nul 2>&1
if not errorlevel 1 (
    echo [WARNING] Port 3000 in use - run stop_all.bat first
)

:: Docker stack (idempotent: running services untouched; missing images
:: are built automatically - first run may take a while)
:: Rebuild only when source is newer than the built image
:: (scripts\check_image_stale.py: exit 0 = fresh, >=1 = stale or detect error)
echo.
echo [1/2] Starting docker compose stack ...
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

:: Start frontend in new window
echo.
echo [2/2] Starting frontend on port 3000 ...
start "frontend-3000" /D "%ROOT%\frontend" cmd /k "npx next dev -p 3000"

echo.
echo ==============================================
echo    Frontend:      http://localhost:3000
echo    API Gateway:   http://localhost:8080
echo    Backend (AI):  http://localhost:8000/docs (container)
echo.
echo    Stop: close frontend window (docker stack keeps running)
echo    Full stop: docker compose down
echo ==============================================
echo.
pause
