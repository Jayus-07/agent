@echo off
setlocal

:: =============================================================
:: dev-start.bat - start local dev services
::   dev-start.bat            -> backend + admin + cs + web
::   dev-start.bat backend    -> docker compose app only (:8000)
::   dev-start.bat admin      -> frontend-admin next dev (:3200)
::   dev-start.bat cs         -> frontend-cs next dev (:3300)
::   dev-start.bat web        -> frontend next dev (:3100)
::   already running services are skipped
::   implementation: dev-svc.bat
::   NOTE: ASCII-only (cmd parses .bat in ANSI/GBK)
:: =============================================================

set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"

echo ==^> start %*
call "%ROOT%\dev-svc.bat" start %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" pause
endlocal & exit /b %RC%
