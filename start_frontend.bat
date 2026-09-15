@echo off
setlocal

:: =============================================
:: start_frontend.bat - Next.js 前端一键启动（:3100，热更新）
::   本沙箱约定：Next 启动要清 distDir，而复用非空 dist 必失败，
::   所以每次启动用一个新的空目录 NEXT_DIST_DIR=.next-dev-XXXXX
::   注意：frontend\.next-dev-* 会逐渐堆积，定期手动删除即可
:: =============================================

set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"

if not exist "%ROOT%\frontend\package.json" (
    echo [ERROR] frontend not found: %ROOT%\frontend
    pause & exit /b 1
)

netstat -ano | findstr /C:":3100 " | findstr /C:"LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo [WARN] Port 3100 already in use - frontend likely running.
    echo        Open http://localhost:3100  or run stop_frontend.bat first.
    pause & exit /b 1
)

:: 每次启动生成一个不存在的新空目录作为 distDir
:pickdist
set "DISTDIR=.next-dev-%RANDOM%%RANDOM%"
if exist "%ROOT%\frontend\%DISTDIR%" goto pickdist

:: 网关注入（2026-09-15 APISIX 迁移后默认 APISIX:9080）
:: 回滚到 Java SCG：把下行改成 http://localhost:8080 重跑本脚本即可，无需改代码
set "AUTH_GATEWAY_URL=http://127.0.0.1:9080"

echo Starting frontend on :3100 (distDir=%DISTDIR%, auth-gateway=%AUTH_GATEWAY_URL%) ...
start "frontend-3100" /D "%ROOT%\frontend" cmd /k "set NEXT_DIST_DIR=%DISTDIR%&& set AUTH_GATEWAY_URL=%AUTH_GATEWAY_URL%&& npx next dev -p 3100"

echo.
echo   Frontend: http://localhost:3100   (login page: /login)
echo   Close the "frontend-3100" window or run stop_frontend.bat to stop.
