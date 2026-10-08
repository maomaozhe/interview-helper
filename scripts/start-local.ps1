[CmdletBinding()]
param(
    [string]$Distribution = 'Ubuntu-22.04',
    [switch]$Build
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $projectRoot '.env'
if (-not (Test-Path -LiteralPath $envPath)) {
    throw 'Configure the project .env before starting the local services.'
}

$wslPath = (Get-Command wsl.exe -ErrorAction Stop).Source
$keepalivePattern = '(?:^|\s)-d\s+"?' + [regex]::Escape($Distribution) + '"?\s+--exec\s+sleep\s+infinity(?:\s|$)'
$existingKeepalive = Get-CimInstance -ClassName Win32_Process -Filter "Name = 'wsl.exe'" |
    Where-Object { $_.CommandLine -match $keepalivePattern } |
    Select-Object -First 1

if ($existingKeepalive) {
    $keepalivePid = $existingKeepalive.ProcessId
} else {
    $keepalive = Start-Process -FilePath $wslPath -ArgumentList @('-d', $Distribution, '--exec', 'sleep', 'infinity') -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru
    $keepalivePid = $keepalive.Id
}
Write-Host "WSL keepalive PID: $keepalivePid"

$composeCommand = 'cd /opt/interview-intelligence-workspace && '
if ($Build) {
    $composeCommand += 'docker compose -f compose.yaml -f compose.build-wsl.yaml build api worker pi-agent && '
}
$composeCommand += 'docker compose up -d --no-build --wait --wait-timeout 60'
& $wslPath -d $Distribution -- bash -lc $composeCommand
if ($LASTEXITCODE -ne 0) {
    throw 'Docker Compose failed. If images are missing, run this script with -Build.'
}

$apiPort = 8000
foreach ($line in Get-Content -LiteralPath $envPath) {
    if ($line -match '^\s*API_BIND_PORT\s*=\s*["'']?(\d+)["'']?\s*(?:#.*)?$') {
        $apiPort = [int]$Matches[1]
    }
}
$localUrl = "http://localhost:$apiPort/"
$health = Invoke-RestMethod -Uri "http://127.0.0.1:$apiPort/api/health" -TimeoutSec 10
Write-Host "Database: $($health.data.database); index: $($health.data.index)"
Write-Host "Local site: $localUrl"
