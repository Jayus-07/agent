@echo off
setlocal enabledelayedexpansion

:: =============================================
:: stop_java.bat - 停止 Java 服务
::   原生进程：按端口杀 8080 / 8006 / 8001（容器占端口时跳过，防误杀 Docker）
::   容器：stop api-gateway + business-service + oa-auth 的 auth/system
::   基础设施（mysql/redis/nacos/postgres/kafka）保留不动 —— 重启慢且无必要
:: =============================================

set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"
set "OA=D:\Program Files\workplace\Enterprise_OA\anonymous-rating-system"

:: ---- 容器侧：只 stop 不 build ----
docker compose -f "%ROOT%\docker-compose.yml" stop api-gateway business-service >nul 2>&1
docker compose -f "%OA%\docker\oa-auth-compose.yml" stop auth-service system-service >nul 2>&1

:: ---- 原生进程：按端口杀（容器占的端口跳过）----
set RETRY=0
:loop
set FOUND=0
for %%p in (8080 8006 8001) do (
    docker ps --format "{{.Names}} {{.Ports}}" 2>nul | findstr /C:":%%p->" >nul 2>&1
    if errorlevel 1 (
        for /f "tokens=5" %%a in ('netstat -ano ^| findstr /C:":%%p "') do (
            taskkill /F /PID %%a /T >nul 2>&1
            if !errorlevel! equ 0 (
                echo   Stopped PID %%a ^(port %%p^)
                set FOUND=1
            )
        )
    )
)
if !FOUND! equ 1 (
    set /a RETRY+=1
    if !RETRY! lss 4 (
        timeout /t 2 /nobreak >nul
        goto loop
    )
)
if !RETRY! equ 0 echo   No native Java processes on 8080/8006/8001

echo   Done. Infra containers left running ^(mysql/redis/nacos/postgres/kafka^).
endlocal
