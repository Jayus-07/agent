@echo off
setlocal

:: =============================================
:: build_java.bat - 本地 mvn 打包全部 Java jar（免 Docker 构建，改代码后跑一次）
::   产物：
::     api-gateway\target\api-gateway-1.0.0.jar
::     business-service\target\business-service-1.0.0.jar（可选，容器模式用镜像）
::     Enterprise_OA auth-service / system-service jar
::   打完包：start_java.bat 或 restart_java.bat 即生效
:: =============================================

set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"
set "OA=D:\Program Files\workplace\Enterprise_OA\anonymous-rating-system"
set "MVN=mvn.cmd"

echo [1/3] Building api-gateway ...
cd /d "%ROOT%\api-gateway"
call %MVN% -q package -DskipTests
if errorlevel 1 ( echo [ERROR] api-gateway build failed & pause & exit /b 1 )
echo   OK: target\api-gateway-1.0.0.jar

echo [2/3] Building business-service jar (docker 镜像更新仍需 docker compose build) ...
cd /d "%ROOT%\business-service"
call %MVN% -q package -DskipTests
if errorlevel 1 ( echo [ERROR] business-service build failed & pause & exit /b 1 )
echo   OK: target\business-service-1.0.0.jar

echo [3/3] Building auth-service + system-service (Enterprise_OA) ...
cd /d "%OA%"
call %MVN% -q package -DskipTests -pl common/common-auth,common/common-core,common/common-api,auth/auth-service,services/system-service -am
if errorlevel 1 ( echo [ERROR] Enterprise_OA build failed & pause & exit /b 1 )
echo   OK: auth-service / system-service jars updated

echo.
echo   All jars built. Now run restart_java.bat to pick them up.
