@echo off
setlocal

:: =============================================
:: restart_py.bat - 重启 Python 后端 = stop_py + start_py
:: =============================================

call "%~dp0stop_py.bat"
echo.
call "%~dp0start_py.bat"
