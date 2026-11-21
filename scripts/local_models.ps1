param(
    [ValidateSet('start', 'stop', 'status', 'restart')]
    [string]$Action = 'status'
)
$ErrorActionPreference = 'Stop'
$modelRoot = Split-Path -Parent $PSScriptRoot
$modelScript = Join-Path $PSScriptRoot 'local_model_server.py'
$modelLogs = Join-Path $modelRoot 'logs'
$modelPort = 8088
if ($env:LOCAL_MODEL_PORT) { $modelPort = [int]$env:LOCAL_MODEL_PORT }
if ($modelPort -ne 8088) { $modelLogs = Join-Path $modelLogs "local-model-$modelPort" }
# 本机模型服务是生产组件：embedding/rerank 容器经 Tailscale
# (http://100.117.4.3:8088) 访问，127.0.0.1 只够本机自检。未显式设置时
# 固化监听所有网卡，暴露面由 Windows 防火墙（入站规则）控制。
if (-not $env:LOCAL_MODEL_HOST) { $env:LOCAL_MODEL_HOST = '0.0.0.0' }
$modelPidFile = Join-Path $modelLogs 'local_model_server.pid'
$modelLog = Join-Path $modelLogs 'local_model_server.log'
$modelPython = $env:LOCAL_MODEL_PY
if (-not $modelPython) {
    $modelPython = 'D:\Python\python.exe'
    if (-not (Test-Path -LiteralPath $modelPython)) {
        $modelPython = Join-Path $modelRoot '.venv\Scripts\python.exe'
    }
}

function Get-ModelProcess {
    if (-not (Test-Path -LiteralPath $modelPidFile)) { return $null }
    $modelPidText = (Get-Content -LiteralPath $modelPidFile -Raw).Trim()
    if ($modelPidText -notmatch '^\d+$') { return $null }
    $modelProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$modelPidText"
    if ($modelProcess -and $modelProcess.CommandLine -and
        $modelProcess.CommandLine.IndexOf($modelScript, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
        return $modelProcess
    }
    return $null
}

function Test-ModelReady {
    try {
        $modelHealth = Invoke-RestMethod "http://127.0.0.1:$modelPort/healthz" -TimeoutSec 2
        return ($modelHealth.status -eq 'ok' -and
            $modelHealth.models_loaded -contains 'embedder' -and
            $modelHealth.models_loaded -contains 'reranker')
    } catch { return $false }
}

function Stop-Model {
    $modelProcess = Get-ModelProcess
    if ($modelProcess) {
        Stop-Process -Id $modelProcess.ProcessId -ErrorAction Stop
        Wait-Process -Id $modelProcess.ProcessId -Timeout 15 -ErrorAction SilentlyContinue
        Write-Output "[local-models] stopped pid=$($modelProcess.ProcessId)"
    } else { Write-Output '[local-models] not running' }
    if (Test-Path -LiteralPath $modelPidFile) {
        Remove-Item -LiteralPath $modelPidFile
    }
}

try {
    if ($Action -eq 'stop' -or $Action -eq 'restart') { Stop-Model }
    if ($Action -eq 'stop') { exit 0 }
    $modelProcess = Get-ModelProcess
    if ($Action -eq 'status') {
        if (-not $modelProcess) {
            Write-Output '[local-models] not running'
            exit 1
        }
        if (Test-ModelReady) {
            Write-Output "[local-models] ready pid=$($modelProcess.ProcessId) port=$modelPort"
            exit 0
        }
        Write-Output "[local-models] process alive but not ready pid=$($modelProcess.ProcessId)"
        exit 2
    }
    if (-not $modelProcess) {
        if (Get-NetTCPConnection -LocalPort $modelPort -State Listen -ErrorAction SilentlyContinue) {
            throw "Port $modelPort is occupied by an unmanaged process; no process was stopped."
        }
        if (-not (Test-Path -LiteralPath $modelPython)) {
            throw "Python not found: $modelPython"
        }
        New-Item -ItemType Directory -Path $modelLogs -Force | Out-Null
        $env:PYTHONIOENCODING = 'utf-8'
        $modelStarted = Start-Process -FilePath $modelPython -ArgumentList @(
            '-u', '-X', 'faulthandler', ('"' + $modelScript + '"')
        ) -WorkingDirectory $modelRoot -WindowStyle Hidden -PassThru `
          -RedirectStandardOutput $modelLog -RedirectStandardError "$modelLog.err"
        $null = $modelStarted.Handle
        Set-Content -LiteralPath $modelPidFile -Value $modelStarted.Id
        Write-Output "[local-models] loading pid=$($modelStarted.Id); log=$modelLog"
    }
    $modelDeadline = [datetime]::UtcNow.AddSeconds(180)
    while ([datetime]::UtcNow -lt $modelDeadline) {
        if (-not (Get-ModelProcess)) {
            $modelExit = 'unknown'
            if ($modelStarted) {
                $modelStarted.WaitForExit()
                $modelExit = $modelStarted.ExitCode
            }
            throw "Process exited (code=$modelExit). See $modelLog.err"
        }
        if (Test-ModelReady) {
            Write-Output "[local-models] ready port=$modelPort"
            exit 0
        }
        Start-Sleep -Seconds 1
    }
    throw "Readiness timeout after 180 seconds. Process retained; see $modelLog.err"
} catch {
    Write-Output "[local-models] ERROR: $($_.Exception.Message)"
    exit 1
}
