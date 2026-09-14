@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul

:: =============================================
:: start_java.bat - Java 全家桶一键启动（本地原生 jar，免 Docker 构建）
::
::   原生进程 : auth-service :8006 / system-service :8001 / api-gateway :8080
::   容器保留 : oa-auth 的 mysql/redis/nacos（基础设施）+ business-service
::              （依赖 docker 网络内的 postgres/kafka，容器化最省事）
::
::   网关读 api-gateway/config/application.yml（cwd 相对路径）——
::   改路由只需 docker compose restart 换成重开本脚本，免 build。
::   改了 Java 代码 → 先 build_java.bat 再重跑本脚本。
:: =============================================

set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"
set "OA=D:\Program Files\workplace\Enterprise_OA\anonymous-rating-system"
set "OA_ENV=%OA%\docker\.env.oaauth"

:: ---- jar 就位检查 ----
if not exist "%ROOT%\api-gateway\target\api-gateway-1.0.0.jar" (
    echo [ERROR] api-gateway jar missing - run build_java.bat first
    pause & exit /b 1
)
if not exist "%OA%\auth\auth-service\target\auth-service-1.0.0-SNAPSHOT.jar" (
    echo [ERROR] auth-service jar missing - run build_java.bat first
    pause & exit /b 1
)
if not exist "%OA%\services\system-service\target\system-service-1.0.0-SNAPSHOT.jar" (
    echo [ERROR] system-service jar missing - run build_java.bat first
    pause & exit /b 1
)
if not exist "%OA_ENV%" (
    echo [ERROR] %OA_ENV% missing
    pause & exit /b 1
)

:: ---- 释放被容器占住的端口（只 stop 不 build，秒级）----
echo [1/4] Freeing ports held by containers ...
docker compose -f "%OA%\docker\oa-auth-compose.yml" stop auth-service system-service >nul 2>&1
docker compose -f "%ROOT%\docker-compose.yml" stop api-gateway >nul 2>&1

:: ---- 启动基础设施容器（镜像已存在，不触发构建）----
echo [2/4] Starting infra containers (mysql/redis/nacos/postgres/kafka) ...
docker compose -f "%OA%\docker\oa-auth-compose.yml" up -d mysql redis nacos
docker compose -f "%ROOT%\docker-compose.yml" up -d postgres kafka business-service

:: ---- 加载 .env.oaauth（口令只留在 env 文件，脚本不内联）----
echo [3/4] Loading env from .env.oaauth ...
for /f "usebackq eol=# tokens=1,* delims==" %%a in ("%OA_ENV%") do set "%%a=%%b"
set "JWT_SECRET=%JWT_SECRET_KEY%"

:: 原生进程指向宿主机映射端口 / Nacos 宿主机端口 18848
set "MYSQL_HOST=127.0.0.1"
set "MYSQL_PORT=13306"
set "MYSQL_USERNAME=oaauth"
set "REDIS_HOST=127.0.0.1"
set "REDIS_PORT=16379"
set "SPRING_CLOUD_NACOS_DISCOVERY_SERVER_ADDR=127.0.0.1:18848"
set "SPRING_CLOUD_NACOS_CONFIG_SERVER_ADDR=127.0.0.1:18848"

:: ---- 启动三个原生 Java 进程（各自独立窗口，env 由本进程继承下去）----
echo [4/4] Starting native Java services ...
set "SERVER_PORT=8006"
start "java-auth-8006" /D "%OA%\auth\auth-service" cmd /k "java -jar target\auth-service-1.0.0-SNAPSHOT.jar"

set "SERVER_PORT=8001"
start "java-system-8001" /D "%OA%\services\system-service" cmd /k "java -jar target\system-service-1.0.0-SNAPSHOT.jar"

set "AUTH_SERVICE_URL=http://127.0.0.1:8006"
set "SYS_SERVICE_URL=http://127.0.0.1:8001"
start "java-gateway-8080" /D "%ROOT%\api-gateway" cmd /k "java -jar target\api-gateway-1.0.0.jar"

echo.
echo   auth-service    : http://localhost:8006/auth/  (window: java-auth-8006)
echo   system-service  : http://localhost:8001/system/  (window: java-system-8001)
echo   api-gateway     : http://localhost:8080  (window: java-gateway-8080)
echo   business-service: http://localhost:8081  (docker container)
echo.
echo   NOTE: Python 后端请另跑 start_py.bat（:8000），前端 start_all.bat / start.bat
echo   热路由（可选）：先 set GATEWAY_HOT_ROUTES=true 再跑本脚本，
echo     即可 POST /actuator/gateway/routes/{id} 运行时增改路由（零重启，仅限本机开发）
echo   首次使用如服务起不来：Nacos 配置需先导入一次（oa-auth docker\nacos_import.sh）
echo   Stop: stop_java.bat / restart_java.bat
