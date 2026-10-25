@echo off
setlocal

:: =============================================================
:: dev-local-models.bat - local embedding/rerank model server
::   dev-local-models.bat start|stop|status|restart
::
:: Serves on 127.0.0.1:8088 (containers reach it via host.docker.internal):
::   POST /embeddings                                OpenAI format (bge-m3)
::   POST /services/rerank/text-rerank/text-rerank   DashScope format (bge-reranker-v2-m3)
::   GET  /healthz
::
:: Logs:  logs\local_model_server.log
:: PID:   logs\local_model_server.pid
:: NOTE: ASCII-only (cmd parses .bat in ANSI/GBK)
:: =============================================================

set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"
set "SERVER=%ROOT%\scripts\local_model_server.py"
set "PY=%ROOT%\.venv\Scripts\python.exe"
set "LOG=%ROOT%\logs\local_model_server.log"
set "PIDFILE=%ROOT%\logs\local_model_server.pid"
set "PORT=8088"

if /i "%~1"=="start"   goto start
if /i "%~1"=="stop"    goto stop
if /i "%~1"=="status"  goto status
if /i "%~1"=="restart" goto restart
echo Usage: dev-local-models.bat start^|stop^|status^|restart
exit /b 1

:findpid
:: %1 = out var name; find listener PID on %PORT% (fallback: pidfile)
set "%~1="
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":%PORT% " ^| findstr "LISTENING"') do (
    if not defined %~1 set "%~1=%%p"
)
if not defined %~1 if exist "%PIDFILE%" set /p %~1=<"%PIDFILE%"
exit /b 0

:status
call :findpid RUNNING
if defined RUNNING (
    echo [local-models] running pid=%RUNNING% port=%PORT%
) else (
    echo [local-models] not running
)
exit /b 0

:start
call :findpid RUNNING
if defined RUNNING (
    echo [local-models] already running pid=%RUNNING%
    exit /b 0
)
if not exist "%ROOT%\logs" mkdir "%ROOT%\logs"
if not exist "%PY%" (
    echo [local-models] ERROR: venv python not found: %PY%
    exit /b 1
)
echo [local-models] starting on 127.0.0.1:%PORT% (first model load takes a while)...
powershell -NoProfile -Command "$p = Start-Process -FilePath '%PY%' -ArgumentList '\"%SERVER%\"' -WorkingDirectory '%ROOT%' -RedirectStandardOutput '%LOG%' -RedirectStandardError '%LOG%.err' -WindowStyle Hidden -PassThru; Set-Content -Path '%PIDFILE%' -Value $p.Id" 
timeout /t 3 /nobreak >nul
call :findpid RUNNING
if defined RUNNING (
    echo [local-models] started pid=%RUNNING%, log=%LOG%
    echo [local-models] verify: curl http://127.0.0.1:%PORT%/healthz
    exit /b 0
)
echo [local-models] ERROR: failed to start, check %LOG%
exit /b 1

:stop
call :findpid RUNNING
if not defined RUNNING (
    echo [local-models] not running
    if exist "%PIDFILE%" del /q "%PIDFILE%"
    exit /b 0
)
echo [local-models] stopping pid=%RUNNING% ...
taskkill /PID %RUNNING% /T /F >nul 2>&1
if exist "%PIDFILE%" del /q "%PIDFILE%"
timeout /t 2 /nobreak >nul
call :findpid RUNNING
if defined RUNNING (
    echo [local-models] WARNING: port %PORT% still held by pid=%RUNNING%
    exit /b 1
)
echo [local-models] stopped
exit /b 0

:restart
call :stop
call :start
exit /b %ERRORLEVEL%
