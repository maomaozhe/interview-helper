[CmdletBinding()]
param(
    [ValidatePattern('^[^"\r\n]+$')]
    [string]$Distribution = 'Ubuntu-22.04'
)

# Native stderr can contain WSL startup notices. Let the process exit code
# report failures without ending the persistent session on a notice.
$ErrorActionPreference = 'Continue'
$wslPath = (Get-Command wsl.exe -ErrorAction Stop).Source
& $wslPath -d $Distribution --exec sleep infinity
exit $LASTEXITCODE
