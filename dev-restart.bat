@echo off
setlocal

:: =============================================================
:: dev-restart.bat - restart local dev services
::   dev-restart.bat            -> backend + admin + web  (asks to confirm)
::   dev-restart.bat backend    -> docker compose app only (:8000)
::   dev-restart.bat admin      -> frontend-admin only (:3200)
::   dev-restart.bat web        -> frontend only (:3100)
::   dev-restart.bat all /y     -> no confirmation prompt
::   implementation: dev-svc.bat
::   NOTE: ASCII-only (cmd parses .bat in ANSI/GBK)
:: =============================================================

set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"

echo ==^> restart %*
call "%ROOT%\dev-svc.bat" restart %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" pause
endlocal & exit /b %RC%
