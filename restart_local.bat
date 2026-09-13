@echo off
setlocal enabledelayedexpansion
:: =============================================
:: restart_local.bat - switch to local dev mode
::   1) comment out NEXT_PUBLIC_API_URL in frontend/.env.local
::   2) stop docker app container (keep redis/pg/grafana/prometheus)
::   3) start local uvicorn --reload :8000 (hot reload on save)
::   4) restart frontend :3000 (NEXT_PUBLIC_* needs dev server restart)
:: switch back to gateway mode: restore that env line
::   -> stop_all.bat -> start.bat
:: =============================================
set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"
cd /d "%ROOT%"

echo.
echo [1/4] switch frontend/.env.local to local mode (comment gateway URL)...
powershell -NoProfile -Command "$f='%ROOT%\frontend\.env.local'; $t=[IO.File]::ReadAllText($f); $t=$t -replace '(?m)^NEXT_PUBLIC_API_URL=http', '# NEXT_PUBLIC_API_URL=http'; [IO.File]::WriteAllText($f, $t)"
if errorlevel 1 (
    echo [ERROR] failed to update .env.local
    exit /b 1
)

echo [2/4] stop docker app container (redis/pg/grafana stay up)...
docker compose stop app
if errorlevel 1 (
    echo [WARN] docker compose stop app failed - engine down? trying local start anyway
)

echo [3/4] start local backend uvicorn --reload :8000 ...
for /f "tokens=5" %%p in ('netstat -ano ^| %SystemRoot%\System32\find.exe ":8000" ^| %SystemRoot%\System32\find.exe "LISTENING"') do taskkill /PID %%p /F >nul 2>&1
start "backend-8000" /D "%ROOT%\backend" cmd /k ""%ROOT%\.venv\Scripts\python.exe" -m uvicorn app.server:app --host 127.0.0.1 --port 8000 --reload"

echo [4/4] restart frontend :3000 ...
for /f "tokens=5" %%p in ('netstat -ano ^| %SystemRoot%\System32\find.exe ":3000" ^| %SystemRoot%\System32\find.exe "LISTENING"') do taskkill /PID %%p /F >nul 2>&1
start "frontend-3000" /D "%ROOT%\frontend" cmd /k "npx next dev -p 3000"

echo.
echo waiting for backend health...
set /a TRIES=0
:wait_health
powershell -NoProfile -Command "try { $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8000/health' -UseBasicParsing -TimeoutSec 3; exit 0 } catch { exit 1 }" >nul 2>&1
if errorlevel 1 (
    set /a TRIES+=1
    if !TRIES! GEQ 30 (
        echo [ERROR] backend not ready in 60s, check backend-8000 window log
        exit /b 1
    )
    timeout /t 2 /nobreak >nul
    goto :wait_health
)
echo backend ready: http://localhost:8000/docs
echo frontend:      http://localhost:3000
echo local mode: python changes hot-reload on save, no build needed.
