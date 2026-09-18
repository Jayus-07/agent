@echo off
setlocal

:: =============================================================
:: devctl.bat - all-in-one entry (status / start / stop / restart)
::   devctl.bat status
::   devctl.bat start   [backend^|admin^|web^|all]
::   devctl.bat stop    [backend^|admin^|web^|all] [/y]
::   devctl.bat restart [backend^|admin^|web^|all] [/y]
::   shortcuts: dev-start.bat / dev-stop.bat / dev-restart.bat
::   implementation: dev-svc.bat
::   NOTE: ASCII-only (cmd parses .bat in ANSI/GBK)
:: =============================================================

set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"

call "%ROOT%\dev-svc.bat" %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" pause
endlocal & exit /b %RC%
