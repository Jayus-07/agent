# ============================================================
# set_ollama_env.ps1 — Configure Ollama resource limits
# ============================================================
# Run as Administrator: right-click PowerShell → Run as Admin
# ============================================================

$ErrorActionPreference = "Stop"

# Check admin privileges
if (-NOT ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")) {
    Write-Host "[ERROR] This script requires Administrator privileges." -ForegroundColor Red
    Write-Host "        Right-click PowerShell → Run as Administrator → .\set_ollama_env.ps1" -ForegroundColor Yellow
    exit 1
}

# Target values
$envVars = @{
    "OLLAMA_NUM_THREADS"       = "4"
    "OLLAMA_NUM_PARALLEL"      = "1"
    "OLLAMA_MAX_LOADED_MODELS" = "1"
    "OLLAMA_KEEP_ALIVE"        = "300s"
}

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  Ollama Resource Limit Configurator" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# Step 1: Set system environment variables
Write-Host "[1/3] Setting system environment variables..." -ForegroundColor Green
$changed = @()

foreach ($name in $envVars.Keys) {
    $targetValue = $envVars[$name]
    $currentValue = [Environment]::GetEnvironmentVariable($name, "Machine")

    if ($currentValue -eq $targetValue) {
        Write-Host "  OK  $name = $targetValue (already set)" -ForegroundColor Gray
    } else {
        [Environment]::SetEnvironmentVariable($name, $targetValue, "Machine")
        if ($currentValue) {
            Write-Host "  SET $name : $currentValue -> $targetValue" -ForegroundColor Yellow
        } else {
            Write-Host "  SET $name = $targetValue (new)" -ForegroundColor Yellow
        }
        $changed += $name
    }
}

if ($changed.Count -eq 0) {
    Write-Host "  All variables already correct, nothing to change." -ForegroundColor Gray
}

# Step 2: Broadcast environment change
Write-Host ""
Write-Host "[2/3] Broadcasting environment change..." -ForegroundColor Green

$signature = @"
[DllImport("user32.dll", CharSet=CharSet.Auto, SetLastError=true)]
public static extern IntPtr SendMessageTimeout(
    IntPtr hWnd, uint Msg, UIntPtr wParam, string lParam,
    uint fuFlags, uint uTimeout, out UIntPtr lpdwResult);
"@
$type = Add-Type -MemberDefinition $signature -Name "Win32SendMessageTimeout" -Namespace "Win32" -PassThru
$HWND_BROADCAST = [IntPtr]0xFFFF
$WM_SETTINGCHANGE = [UInt32]0x001A
$flags = [UInt32]0x0002  # SMTO_ABORTIFHUNG
$result = [UIntPtr]::Zero
$type::SendMessageTimeout($HWND_BROADCAST, $WM_SETTINGCHANGE, [UIntPtr]::Zero, "Environment", $flags, [UInt32]5000, [ref]$result)
Write-Host "  OK  WM_SETTINGCHANGE broadcast sent" -ForegroundColor Gray

# Step 3: Restart Ollama
Write-Host ""
Write-Host "[3/3] Restarting Ollama..." -ForegroundColor Green

$ollamaService = Get-Service -Name "ollama*" -ErrorAction SilentlyContinue
$ollamaProcess = Get-Process -Name "ollama*" -ErrorAction SilentlyContinue

if ($ollamaService) {
    Write-Host "  Found Windows service: $($ollamaService.Name)" -ForegroundColor Gray
    Restart-Service $ollamaService.Name -Force
    Start-Sleep -Seconds 3
    Write-Host "  OK  Ollama service restarted" -ForegroundColor Green
} elseif ($ollamaProcess) {
    Write-Host "  Found Ollama process (PID: $($ollamaProcess.Id))" -ForegroundColor Gray
    Stop-Process -Name $ollamaProcess.Name -Force
    Start-Sleep -Seconds 2
    Write-Host "  WARN  Process killed. Please restart Ollama manually." -ForegroundColor Yellow
} else {
    Write-Host "  WARN  No Ollama service or process detected" -ForegroundColor Yellow
    Write-Host "  If Ollama is installed via Docker, env vars are set in docker-compose.yml instead." -ForegroundColor Gray
}

# Verify
Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  Verification:" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
$allOk = $true
foreach ($name in $envVars.Keys) {
    $val = [Environment]::GetEnvironmentVariable($name, "Machine")
    if ($val -eq $envVars[$name]) {
        Write-Host "  PASS  $name = $val" -ForegroundColor Green
    } else {
        Write-Host "  FAIL  $name = $val (expected: $($envVars[$name]))" -ForegroundColor Red
        $allOk = $false
    }
}

Write-Host ""
if ($allOk) {
    Write-Host "All done! Ollama is now configured for CPU-safe operation." -ForegroundColor Green
} else {
    Write-Host "Some variables failed to set. Try running as Administrator." -ForegroundColor Red
}
Write-Host ""
Write-Host "Verify with: ollama ps" -ForegroundColor Gray
