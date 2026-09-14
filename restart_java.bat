@echo off
setlocal

:: =============================================
:: restart_java.bat - 重启 Java 服务 = stop_java + start_java
:: 改了 api-gateway/config/application.yml（路由/白名单）→ 跑这个即可
:: 改了 Java 源码 → 先 build_java.bat 再跑这个
:: =============================================

call "%~dp0stop_java.bat"
echo.
call "%~dp0start_java.bat"
