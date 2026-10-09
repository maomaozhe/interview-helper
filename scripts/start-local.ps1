[CmdletBinding()]
param(
    [string]$Distribution = 'Ubuntu-22.04',
    [switch]$Build,
    [ValidateRange(30, 600)]
    [int]$WaitTimeoutSeconds = 120
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

$linuxProjectRoot = (& $wslPath -d $Distribution --exec wslpath -a -u $projectRoot | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or -not $linuxProjectRoot.StartsWith('/')) {
    throw 'Could not resolve this project directory inside WSL.'
}

if ($Build) {
    # Compose Bake currently puts non-ASCII project paths into a gRPC header.
    # Use the ordinary Compose builder only for affected paths.
    $buildPrefix = @()
    if ($linuxProjectRoot -cmatch '[^\x00-\x7F]') {
        $buildPrefix = @('env', 'COMPOSE_BAKE=false')
    }
    & $wslPath -d $Distribution --cd $linuxProjectRoot --exec @buildPrefix docker compose -f compose.yaml -f compose.build-wsl.yaml build api worker pi-agent
    if ($LASTEXITCODE -ne 0) {
        throw 'Docker Compose build failed; existing containers were not replaced.'
    }
}
& $wslPath -d $Distribution --cd $linuxProjectRoot --exec docker compose up -d --no-build --wait --wait-timeout $WaitTimeoutSeconds
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
if ($health.data.database -ne 'ready' -or $health.data.index -ne 'ready') {
    throw 'The API is running, but its database or search index is not ready.'
}
Write-Host "Database: $($health.data.database); index: $($health.data.index)"
Write-Host "Query prompt: $($health.data.query_prompt_version); reranker: $($health.data.reranker_version)"
Write-Host "Local site: $localUrl"
