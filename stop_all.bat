@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion

:: =============================================
:: 一键停止后端 (FastAPI) + 前端 (Next.js)
:: =============================================

set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"
cd /d "%ROOT%"

echo.
echo ==============================================
echo      Agent Platform - 停止所有服务
echo ==============================================

:: ---- 关闭前端 (Next.js / Node) ----
echo.
echo [1/2] 停止前端 (Node 进程)...
taskkill /F /IM node.exe /T >nul 2>&1
if errorlevel 1 (
    echo   !  没有发现 Node 进程
) else (
    echo   OK Node 进程已停止
)

:: ---- 关闭后端 (uvicorn / Python) ----
echo.
echo [2/2] 停止后端 (Python 进程)...
:: 查找占用 8000 端口的 PID
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000.*LISTENING"') do (
    echo   关闭 PID %%a (端口 8000)
    taskkill /F /PID %%a /T >nul 2>&1
)
:: 兜底：杀掉所有由 start_all.bat 启动的 uvicorn 进程
taskkill /F /FI "WINDOWTITLE eq Agent-Backend-8000*" /T >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq Agent-Frontend-3000*" /T >nul 2>&1
echo   OK 后端进程已停止

:: ---- 完成 ----
echo.
echo ==============================================
echo   *** 所有服务已停止 ***
echo ==============================================
echo.

endlocal
pause