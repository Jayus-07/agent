@echo off
setlocal

:: =============================================================
:: dev-stop.bat - stop local dev services
::   dev-stop.bat            -> backend + admin + web  (asks to confirm)
::   dev-stop.bat admin      -> frontend-admin only (:3200)
::   dev-stop.bat web        -> frontend only (:3100)
::   dev-stop.bat backend    -> docker compose app only (:8000)
::   dev-stop.bat all /y     -> no confirmation prompt
::   implementation: dev-svc.bat
::   NOTE: ASCII-only (cmd parses .bat in ANSI/GBK)
:: =============================================================

set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"

echo ==^> stop %*
call "%ROOT%\dev-svc.bat" stop %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" pause
endlocal & exit /b %RC%
