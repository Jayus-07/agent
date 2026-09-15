@echo off
setlocal

:: =============================================
:: restart_py.bat - 重启 Python 后端 = stop_py + start_py
::   透传参数：restart_py.bat native = 原生模式重启
::   容器模式重启后 APISIX 最长 ~1-2min 内自愈（dns_resolver_valid=5）
:: =============================================

call "%~dp0stop_py.bat" %~1
echo.
call "%~dp0start_py.bat" %~1
