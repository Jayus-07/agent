@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion

:: =============================================
:: 重启 Agent Platform (后端 + 前端)
:: =============================================

set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"
cd /d "%ROOT%"

echo.
echo ==============================================
echo      Agent Platform - 重启所有服务
echo ==============================================

:: 调用 stop_all.bat
echo.
echo [1/3] 停止现有服务...
call "%ROOT%\stop_all.bat" <nul >nul 2>&1

:: 等待端口释放
echo.
echo [2/3] 等待端口释放...
timeout /t 3 /nobreak >nul
echo   OK

:: 调用 start_all.bat
echo.
echo [3/3] 启动服务...
echo   (将打开新的控制台窗口)
echo.
call "%ROOT%\start_all.bat" <nul

endlocal