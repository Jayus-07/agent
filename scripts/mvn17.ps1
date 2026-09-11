# 用 JDK 17 运行 Maven（仅当前进程生效，不修改系统 JAVA_HOME）
# 用法示例：
#   .\scripts\mvn17.ps1 -Project business-service package
#   .\scripts\mvn17.ps1 -Project api-gateway clean package
#   .\scripts\mvn17.ps1 -Project business-service test
param(
    [Parameter(Mandatory = $true)][string]$Project,   # business-service 或 api-gateway
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$MavenArgs
)

$ErrorActionPreference = "Stop"

# JDK 17 安装目录（并存安装，系统默认仍是 JDK 8）
$Jdk17 = Get-ChildItem "D:\Program Files\Java" -Directory -Filter "jdk-17*" |
    Select-Object -First 1
if (-not $Jdk17) {
    throw "未找到 JDK 17，请确认已解压到 D:\Program Files\Java\jdk-17*"
}

$env:JAVA_HOME = $Jdk17.FullName
$env:Path = "$($Jdk17.FullName)\bin;$env:Path"

$projectDir = Join-Path $PSScriptRoot "..\$Project"
if (-not (Test-Path (Join-Path $projectDir "pom.xml"))) {
    throw "目录 $projectDir 下没有 pom.xml"
}

Write-Host "==> JAVA_HOME = $env:JAVA_HOME" -ForegroundColor Cyan
Push-Location $projectDir
try {
    & mvn @MavenArgs
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
