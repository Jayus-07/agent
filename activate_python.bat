@echo off
setlocal

:: =============================================
:: 快速激活项目虚拟环境
:: =============================================
:: 双击此脚本即可在当前终端窗口激活 .venv 虚拟环境
:: =============================================

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] 找不到虚拟环境
    pause
    exit /b 1
)

:: 检查是否为 PowerShell
where powershell >nul 2>&1
if errorlevel 1 (
    :: PowerShell 不可用，使用 CMD 方式
    echo [INFO] 在 CMD 中激活虚拟环境...
    call "%~dp0\.venv\Scripts\activate.bat"
    goto :end
)

:: PowerShell 可用，优先使用 PowerShell 激活
echo [INFO] 在 PowerShell 中激活虚拟环境...
powershell -Command "& { cd '%~dp0'; . '.venv\Scripts\Activate.ps1'; Write-Host '[OK] 虚拟环境已激活' }"
goto :end

:end
pause
