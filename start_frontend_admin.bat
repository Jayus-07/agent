@echo off
setlocal

:: =============================================
:: start_frontend_admin.bat - 管理端 Next.js 一键启动（:3200，热更新）
::   与 start_frontend.bat 同款约定：每次启动用一个新的空目录
::   NEXT_DIST_DIR=.next-dev-XXXXX；frontend-admin\.next-dev-* 定期手动删除
::   业务用户端在 frontend/（:3100），见 start_frontend.bat
:: =============================================

set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"

if not exist "%ROOT%\frontend-admin\package.json" (
    echo [ERROR] frontend-admin not found: %ROOT%\frontend-admin
    pause & exit /b 1
)

netstat -ano | findstr /C:":3200 " | findstr /C:"LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo [WARN] Port 3200 already in use - admin frontend likely running.
    echo        Open http://localhost:3200  or run stop_frontend_admin.bat first.
    pause & exit /b 1
)

:: 每次启动生成一个不存在的新空目录作为 distDir
:pickdist
set "DISTDIR=.next-dev-%RANDOM%%RANDOM%"
if exist "%ROOT%\frontend-admin\%DISTDIR%" goto pickdist

:: 网关注入（与用户端一致：APISIX:9080；回滚 SCG 改 http://localhost:8080）
set "AUTH_GATEWAY_URL=http://127.0.0.1:9080"

echo Starting admin frontend on :3200 (distDir=%DISTDIR%, auth-gateway=%AUTH_GATEWAY_URL%) ...
start "frontend-admin-3200" /D "%ROOT%\frontend-admin" cmd /k "set NEXT_DIST_DIR=%DISTDIR%&& set AUTH_GATEWAY_URL=%AUTH_GATEWAY_URL%&& npx next dev -p 3200"

echo.
echo   Admin frontend: http://localhost:3200   (login page: /login)
echo   Close the "frontend-admin-3200" window or run stop_frontend_admin.bat to stop.
