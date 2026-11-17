<#
.SYNOPSIS
    Starts FETCH.

.DESCRIPTION
    Starts the FETCH server in the background, waits until it answers, and
    prints the dashboard address. Downloads that were running when FETCH was
    last stopped are resumed automatically.

.PARAMETER Foreground
    Run in this window and show the server output. Stop with Ctrl+C.

.PARAMETER NoBrowser
    Do not open the dashboard in the default browser.

.EXAMPLE
    .\start.ps1

.EXAMPLE
    .\start.ps1 -Foreground
#>
[CmdletBinding()]
param(
    [switch]$Foreground,
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'scripts\FetchCommon.ps1')

Assert-Installed
if (-not (Test-Path $script:ConfigFile)) {
    Stop-WithError 'config\fetch.toml is missing. Run .\install.ps1 to create it.'
}
if (-not (Test-Path (Join-Path $script:FrontendDir 'dist\index.html'))) {
    Stop-WithError 'The web interface has not been built. Run .\install.ps1 (or .\update.ps1).'
}

$status = Get-FetchStatus
if (-not $status) {
    Stop-WithError 'FETCH could not read its configuration or database. Run .\install.ps1 and check the output.'
}
if ($status.running) {
    Write-Host "FETCH is already running: $(Get-DashboardUrl $status)" -ForegroundColor Green
    exit 0
}

# A pid file without a living server is left over from a crash or power loss.
if ((Test-Path $script:PidFile) -and -not (Get-FetchProcess)) {
    Remove-Item -Path $script:PidFile -Force -ErrorAction SilentlyContinue
    Remove-Item -Path (Join-Path $script:DataDir 'control.json') -Force -ErrorAction SilentlyContinue
}

if ($Foreground.IsPresent) {
    Write-Host 'Starting FETCH in this window. Press Ctrl+C to stop.' -ForegroundColor Cyan
    Push-Location $script:BackendDir
    try {
        & $script:VenvPython -m fetch_app serve
        exit $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
}

New-Item -ItemType Directory -Force -Path $script:LogDir | Out-Null
$stdoutLog = Join-Path $script:LogDir 'server.out.log'
$stderrLog = Join-Path $script:LogDir 'server.err.log'

Write-Host 'Starting FETCH...' -ForegroundColor Cyan
$process = Start-Process -FilePath $script:VenvPython -ArgumentList @('-m', 'fetch_app', 'serve') `
    -WorkingDirectory $script:BackendDir -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog

$deadline = (Get-Date).AddSeconds(45)
$status = $null
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 750
    if ($process.HasExited) { break }
    $status = Get-FetchStatus
    if ($status -and $status.running) { break }
}

if (-not $status -or -not $status.running) {
    Write-Host ''
    Write-Host 'FETCH did not start.' -ForegroundColor Red
    foreach ($log in @($stderrLog, $stdoutLog)) {
        if ((Test-Path $log) -and (Get-Item $log).Length -gt 0) {
            Write-Host "--- last lines of $log ---" -ForegroundColor Yellow
            Get-Content -Path $log -Tail 25 | ForEach-Object { Write-Host "  $_" }
        }
    }
    Write-Host ''
    Write-Host 'Common causes: the port is used by another program (change it in' -ForegroundColor Yellow
    Write-Host 'config\fetch.toml or Settings > Network), or the storage folder is not reachable.' -ForegroundColor Yellow
    Write-Host 'Run .\start.ps1 -Foreground to see the full output.' -ForegroundColor Yellow
    if (-not $process.HasExited) { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue }
    exit 1
}

$url = Get-DashboardUrl $status
Write-Host ''
Write-Host "FETCH $($status.version) is running." -ForegroundColor Green
Write-Host "  Dashboard: $url"
Write-Host "  Logs:      $script:LogDir"
Write-Host '  Stop it:   .\stop.ps1'
Write-Host ''

if (-not $NoBrowser.IsPresent -and [Environment]::UserInteractive) {
    try {
        Start-Process $url
    }
    catch {
        Write-Note "Open $url in your browser."
    }
}
