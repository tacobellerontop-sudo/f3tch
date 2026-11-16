<#
.SYNOPSIS
    Stops FETCH.

.DESCRIPTION
    Asks the running FETCH server to shut down cleanly. Active downloads are
    paused with their progress saved and continue the next time FETCH starts.

.PARAMETER Force
    End the process if it does not stop within the timeout.

.PARAMETER TimeoutSeconds
    How long to wait for a clean shutdown (default 40).

.EXAMPLE
    .\stop.ps1
#>
[CmdletBinding()]
param(
    [switch]$Force,
    [ValidateRange(5, 600)][int]$TimeoutSeconds = 40
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'scripts\FetchCommon.ps1')

Assert-Installed

$status = Get-FetchStatus
$process = Get-FetchProcess
if (-not $process -and -not ($status -and $status.running)) {
    Write-Host 'FETCH is not running.'
    exit 0
}

Write-Host 'Stopping FETCH (active downloads are saved)...' -ForegroundColor Cyan
if (Stop-FetchServer -TimeoutSeconds $TimeoutSeconds -Force:$Force.IsPresent) {
    Write-Host 'FETCH has stopped.' -ForegroundColor Green
    exit 0
}

Write-Host "FETCH is still shutting down after $TimeoutSeconds seconds." -ForegroundColor Yellow
Write-Host 'Wait a little longer, or run .\stop.ps1 -Force to end it. Download progress is kept either way.' -ForegroundColor Yellow
exit 1
