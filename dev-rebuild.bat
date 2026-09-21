@echo off
setlocal

:: =============================================================
:: dev-rebuild.bat - one-click backend rebuild + recreate
::   dev-rebuild.bat         -> build images + up -d ALL backend
::                              services (asks to confirm)
::   dev-rebuild.bat /y      -> no confirmation prompt
::
::   WHY it exists: plain dev-restart reuses the OLD image and the
::   env vars baked in at container creation. Code changes AND
::   .env changes take effect ONLY via rebuild.
::   covers: app rag-service mcp-service worker beat
::           cs-dispatcher metadata-shadow-worker
::           (db-migrate one-shot re-runs via depends_on chain)
::   implementation: dev-svc.bat
::   NOTE: ASCII-only (cmd parses .bat in ANSI/GBK)
:: =============================================================

set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"

echo ==^> rebuild %*
call "%ROOT%\dev-svc.bat" rebuild %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" pause
endlocal & exit /b %RC%
