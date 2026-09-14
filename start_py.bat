@echo off
setlocal

:: =============================================
:: start_py.bat - Python 后端一键启动（本地原生，免 Docker 构建）
::   uvicorn --reload :8000，热更新，改代码即生效
:: 前端请另用 start_all.bat / start.bat；Java 服务用 start_java.bat
:: =============================================

set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"

if not exist "%ROOT%\.venv\Scripts\python.exe" (
    echo [ERROR] venv not found: %ROOT%\.venv\Scripts\python.exe
    pause & exit /b 1
)

:: Docker 容器占着 8000 时绝不能按端口杀（会误杀 Docker 端口转发）
docker ps --format "{{.Names}} {{.Ports}}" 2>nul | findstr /C:":8000->" >nul 2>&1
if not errorlevel 1 (
    echo [ERROR] Port 8000 is held by Docker container ^(app^).
    echo         Run: docker compose stop app   then retry.
    pause & exit /b 1
)

netstat -ano | findstr /C:":8000 " | findstr /C:"LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo [WARN] Port 8000 already in use. Run stop_py.bat first if needed.
    pause & exit /b 1
)

echo Starting Python backend on :8000 ...
:: --reload-dir 限定只监听 backend/app：data/、日志等文件变更不再误触发重载
start "py-backend-8000" /D "%ROOT%\backend" cmd /k ""%ROOT%\.venv\Scripts\python.exe" -m uvicorn app.server:app --host 127.0.0.1 --port 8000 --reload --reload-dir app"

echo.
echo   Backend : http://localhost:8000/docs
echo   Close the "py-backend-8000" window or run stop_py.bat to stop.
