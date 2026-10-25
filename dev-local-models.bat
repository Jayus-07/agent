@echo off
setlocal
:: Keep this wrapper ASCII-only for cmd.exe.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\local_models.ps1" %*
exit /b %ERRORLEVEL%
