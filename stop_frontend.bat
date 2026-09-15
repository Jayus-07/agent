@echo off
setlocal enabledelayedexpansion

:: =============================================
:: stop_frontend.bat - 停止 Next.js 前端（按端口 3100 杀）
::   LISTENING 过滤：不会误杀连着 3100 的浏览器等客户端进程
:: =============================================

set FOUND=0
for /f "tokens=5" %%a in ('netstat -ano ^| findstr /C:":3100 " ^| findstr /C:"LISTENING"') do (
    taskkill /F /PID %%a /T >nul 2>&1
    if !errorlevel! equ 0 (
        echo   Stopped PID %%a ^(port 3100^)
        set FOUND=1
    )
)
if !FOUND! equ 0 echo   Nothing running on port 3100
echo   Done.
endlocal
