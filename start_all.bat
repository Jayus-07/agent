@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion

:: =============================================
:: 一键启动后端 (FastAPI) + 前端 (Next.js)
:: =============================================

set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"
cd /d "%ROOT%"

echo.
echo ==============================================
echo      Agent Platform - 全栈启动
echo ==============================================

:: ---- 检查 Ollama ----
echo.
echo [0/3] 检查 Ollama 服务...
ollama list >nul 2>&1
if errorlevel 1 (
    echo [警告] Ollama 未运行，LLM 推理将不可用
    echo        请先启动 Ollama 或在另一个窗口运行: ollama serve
) else (
    echo   OK Ollama 运行中
)

:: ---- 加载 .env ----
echo.
echo [1/3] 加载配置...
if exist "%ROOT%\.env" (
    for /f "usebackq delims=" %%a in ("%ROOT%\.env") do (
        set "line=%%a"
        if not "!line!"=="" (
            if not "!line:~0,1!"=="#" (
                set "%%a" 2>nul
            )
        )
    )
    echo   OK .env 已加载
) else (
    echo   !  .env 文件不存在，使用默认配置
)

:: ---- 检查端口占用 ----
echo.
echo [2/3] 检查端口占用...
netstat -ano | findstr ":8000" >nul 2>&1
if not errorlevel 1 (
    echo [警告] 端口 8000 已被占用，请先运行 stop_all.bat
)
netstat -ano | findstr ":3000" >nul 2>&1
if not errorlevel 1 (
    echo [警告] 端口 3000 已被占用，请先运行 stop_all.bat
)
echo   OK 端口检查完成

:: ---- 启动后端 ----
echo.
echo [3/3] 启动后端 FastAPI (端口 8000)...
echo        浏览器打开: http://localhost:8000/docs

start "Agent-Backend-8000" /D "%ROOT%\backend" cmd /k "..\.venv\Scripts\python.exe -m uvicorn app.server:app --port 8000"

:: ---- 启动前端 ----
echo.
echo [+] 启动前端 Next.js (端口 3000)...
echo        浏览器打开: http://localhost:3000

start "Agent-Frontend-3000" /D "%ROOT%\frontend" cmd /k "npm run dev"

:: ---- 完成 ----
echo.
echo ==============================================
echo   *** 全栈服务已启动（两个新窗口） ***
echo.
echo   前端: http://localhost:3000
echo   后端: http://localhost:8000
echo   文档: http://localhost:8000/docs
echo.
echo   关闭对应窗口即可停止服务
echo   或运行 stop_all.bat 一键停止
echo ==============================================
echo.

endlocal
pause