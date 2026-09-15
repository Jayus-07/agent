@echo off
setlocal enabledelayedexpansion

:: =============================================
:: stop_frontend_admin.bat - 停止管理端 Next.js（按端口 3200 杀）
::   LISTENING 过滤：不会误杀连着 3200 的浏览器等客户端进程
:: =============================================

set FOUND=0
for /f "tokens=5" %%a in ('netstat -ano ^| findstr /C:":3200 " ^| findstr /C:"LISTENING"') do (
    taskkill /F /PID %%a /T >nul 2>&1
    if !errorlevel! equ 0 (
        echo   Stopped PID %%a ^(port 3200^)
        set FOUND=1
    )
)
if !FOUND! equ 0 echo   Nothing running on port 3200
echo   Done.
endlocal
