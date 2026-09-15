@echo off
setlocal

:: =============================================
:: start_py.bat - Start Python backend
::   Default (container mode): docker compose up agent-app-1
::     (the ONLY complete runtime since the APISIX migration -
::      compose env has RAG_MODE=remote / PG / Redis / Kafka)
::   "native" arg: host uvicorn --reload :8000 (temporary debug only -
::     degraded without compose env; ALSO switch apisix/apisix.yaml
::     app upstream from "app:8000" back to "host.docker.internal:8000")
::   Since 2026-09-15 APISIX(:9080) is the Python project entry.
::     After app container restart, gateway self-heals in ~1-2min
::     (dns_resolver_valid=5); urgent: docker compose restart apisix
::   Frontend: start_frontend.bat   Java: start_java.bat
::   NOTE: keep this file ASCII-only (cmd parses .bat in ANSI/GBK;
::         UTF-8 Chinese comments get misread and break control flow)
:: =============================================

set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"

if /i "%~1"=="native" goto native

:: ---- container mode (default) ----
docker ps >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Docker daemon not running - start Docker Desktop first
    pause & exit /b 1
)

netstat -ano | findstr /C:":8000 " | findstr /C:"LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo [WARN] Port 8000 already listening - likely the app container or a
    echo        stray process. For native mode run: stop_py.bat native
)

echo [1/2] Starting backend container ^(agent-app-1^) ...
docker compose up -d app
if errorlevel 1 (
    echo [ERROR] docker compose up -d app failed
    pause & exit /b 1
)

echo [2/2] Waiting for health check ^(max 2 min^) ...
:: ping delay instead of timeout.exe - Git Bash PATH resolves GNU timeout
set /a TRIES=0
:waithealth
ping -n 6 127.0.0.1 >nul
curl.exe -s -o nul --max-time 5 http://127.0.0.1:8000/health
if not errorlevel 1 goto healthy
set /a TRIES+=1
if %TRIES% lss 24 goto waithealth
echo [ERROR] Backend not healthy after 2 min - check: docker logs agent-app-1 --tail 30
pause & exit /b 1

:healthy
echo.
echo   Backend : http://localhost:8000/docs  ^(container: agent-app-1^)
echo   Gateway : http://localhost:9080       ^(APISIX = Python entry; SCG 8080 fallback^)
echo   Frontend: run start_frontend.bat      Java: run start_java.bat
goto :eof

:: ---- native mode (start_py.bat native) ----
:native
if not exist "%ROOT%\.venv\Scripts\python.exe" (
    echo [ERROR] venv not found: %ROOT%\.venv\Scripts\python.exe
    pause & exit /b 1
)

:: Never kill port 8000 when held by Docker (would hit com.docker.backend)
docker ps --format "{{.Names}} {{.Ports}}" 2>nul | findstr /C:":8000->" >nul 2>&1
if not errorlevel 1 (
    echo [ERROR] Port 8000 is held by Docker container ^(app^).
    echo         Fix: docker compose stop app   then retry.
    pause & exit /b 1
)

netstat -ano | findstr /C:":8000 " | findstr /C:"LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo [WARN] Port 8000 already in use. Run stop_py.bat native first if needed.
    pause & exit /b 1
)

echo Starting Python backend on :8000 ^(native, hot-reload^) ...
start "py-backend-8000" /D "%ROOT%\backend" cmd /k ""%ROOT%\.venv\Scripts\python.exe" -m uvicorn app.server:app --host 127.0.0.1 --port 8000 --reload --reload-dir app"

echo.
echo   Backend : http://localhost:8000/docs
echo   Close the "py-backend-8000" window or run stop_py.bat native to stop.
