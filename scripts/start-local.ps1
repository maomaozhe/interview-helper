[CmdletBinding()]
param(
    [ValidatePattern('^[^"\r\n]+$')]
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
$taskIdentityHash = [System.Security.Cryptography.SHA256]::Create()
try {
    $taskIdentityBytes = $taskIdentityHash.ComputeHash([System.Text.Encoding]::UTF8.GetBytes("$projectRoot|$Distribution"))
} finally {
    $taskIdentityHash.Dispose()
}
$taskSuffix = ([BitConverter]::ToString($taskIdentityBytes) -replace '-', '').Substring(0, 12).ToLowerInvariant()
$keepaliveTaskName = "InterviewIntelligence-WSL-$taskSuffix"
$keepaliveShell = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
$keepaliveScript = Join-Path $PSScriptRoot 'wsl-keepalive.ps1'
$keepaliveArguments = '-NoProfile -NonInteractive -WindowStyle Hidden -File "' + $keepaliveScript + '" -Distribution "' + $Distribution + '"'
$keepaliveIdentity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
$keepaliveTask = Get-ScheduledTask -TaskName $keepaliveTaskName -ErrorAction SilentlyContinue
if ($keepaliveTask) {
    $keepaliveOwner = $keepaliveTask.Principal.UserId
    if ($keepaliveOwner -notmatch '^S-1-') {
        $keepaliveOwner = ([System.Security.Principal.NTAccount]$keepaliveOwner).Translate([System.Security.Principal.SecurityIdentifier]).Value
    }
    if ($keepaliveTask.Actions.Count -ne 1 -or $keepaliveTask.Actions[0].Execute -ne $keepaliveShell -or
        $keepaliveTask.Actions[0].Arguments -ne $keepaliveArguments -or
        $keepaliveTask.Actions[0].WorkingDirectory -ne $projectRoot -or
        $keepaliveOwner -ne $keepaliveIdentity.User.Value -or
        $keepaliveTask.Principal.LogonType -ne 'Interactive' -or
        $keepaliveTask.Principal.RunLevel -ne 'Limited' -or
        $keepaliveTask.Settings.ExecutionTimeLimit -ne 'PT0S' -or
        $keepaliveTask.Settings.MultipleInstances -ne 'IgnoreNew' -or
        $keepaliveTask.Triggers.Count -ne 0) {
        throw "The existing task $keepaliveTaskName has a different configuration."
    }
} else {
    $keepalivePrincipal = New-ScheduledTaskPrincipal -UserId $keepaliveIdentity.Name -LogonType Interactive -RunLevel Limited
    $keepaliveAction = New-ScheduledTaskAction -Execute $keepaliveShell -Argument $keepaliveArguments -WorkingDirectory $projectRoot
    $keepaliveSettings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -Hidden -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    $keepaliveTask = Register-ScheduledTask -TaskName $keepaliveTaskName -Action $keepaliveAction -Principal $keepalivePrincipal -Settings $keepaliveSettings -Description 'Keep this local interview workspace running in WSL until stopped; started on demand.' -ErrorAction Stop
}
if ($keepaliveTask.State -ne 'Running') {
    Start-ScheduledTask -TaskName $keepaliveTaskName -ErrorAction Stop
}
Write-Host "WSL keepalive task: $keepaliveTaskName"

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
$keepaliveStatus = Get-ScheduledTask -TaskName $keepaliveTaskName -ErrorAction Stop
if ($keepaliveStatus.State -ne 'Running') {
    $keepaliveResult = (Get-ScheduledTaskInfo -TaskName $keepaliveTaskName -ErrorAction Stop).LastTaskResult
    throw "The WSL keepalive task stopped (result $keepaliveResult); the local service may shut down."
}
Write-Host "Database: $($health.data.database); index: $($health.data.index)"
Write-Host "Query prompt: $($health.data.query_prompt_version); reranker: $($health.data.reranker_version)"
Write-Host "Local site: $localUrl"
