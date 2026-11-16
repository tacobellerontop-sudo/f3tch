<#
.SYNOPSIS
    Updates FETCH to the newest version in this folder.

.DESCRIPTION
    Stops FETCH, backs up the database and the configuration, optionally pulls
    the latest code with git, updates the Python packages, rebuilds the web
    interface, migrates the database, and starts FETCH again if it was running.

    Downloads, files, users and settings are kept.

.PARAMETER SkipPull
    Do not run "git pull" (use this after replacing the files yourself).

.PARAMETER NoRestart
    Leave FETCH stopped after the update.

.PARAMETER Yes
    Do not ask before pulling.

.EXAMPLE
    .\update.ps1
#>
[CmdletBinding()]
param(
    [switch]$SkipPull,
    [switch]$NoRestart,
    [switch]$Yes
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'scripts\FetchCommon.ps1')

Assert-Installed

$before = Get-FetchStatus
$wasRunning = [bool]($before -and $before.running)
$oldVersion = 'unknown'
if ($before) { $oldVersion = $before.version }

if ($wasRunning) {
    Write-Step 'Stopping FETCH'
    if (-not (Stop-FetchServer -TimeoutSeconds 60 -Force)) {
        Stop-WithError 'FETCH could not be stopped. Run .\stop.ps1 -Force and try again.'
    }
    Write-Ok 'Stopped (active downloads are saved)'
}

Write-Step 'Backing up the database and the configuration'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backupDir = Join-Path $script:DataDir "backups\$stamp"
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
foreach ($name in @('fetch.db', 'fetch.db-wal', 'fetch.db-shm')) {
    $source = Join-Path $script:DataDir $name
    if (Test-Path $source) { Copy-Item -Path $source -Destination $backupDir -Force }
}
if (Test-Path $script:ConfigFile) { Copy-Item -Path $script:ConfigFile -Destination $backupDir -Force }
Write-Ok "Backup written to $backupDir"

# Keep the ten most recent backups.
$backupRoot = Join-Path $script:DataDir 'backups'
Get-ChildItem -Path $backupRoot -Directory | Sort-Object Name -Descending | Select-Object -Skip 10 |
    ForEach-Object { Remove-Item -Path $_.FullName -Recurse -Force -ErrorAction SilentlyContinue }

if (-not $SkipPull.IsPresent) {
    Write-Step 'Getting the latest code'
    if ((Test-Path (Join-Path $script:FetchRoot '.git')) -and (Test-Command 'git')) {
        if (Confirm-Choice 'Run "git pull --ff-only" in this folder?' $true $Yes.IsPresent) {
            Invoke-Checked -FilePath 'git' -Arguments @('pull', '--ff-only') -What 'git pull'
            Write-Ok 'Code updated'
        }
        else {
            Write-Note 'Skipped; using the files that are here now.'
        }
    }
    else {
        Write-Note 'This folder is not a git checkout (or git is not installed); using the files that are here now.'
    }
}

Write-Step 'Updating the backend packages'
Install-BackendPackages
Write-Ok 'Backend packages are up to date'

Write-Step 'Rebuilding the web interface'
Build-Frontend
Write-Ok 'Web interface rebuilt'

Write-Step 'Migrating the database'
$initOutput = Invoke-FetchCli @('init')
if ($script:LastCliExit -ne 0) {
    Write-Host $initOutput
    Write-Host ''
    Write-Host "The database migration failed. Your backup is in $backupDir." -ForegroundColor Red
    Write-Host 'To go back: restore the previous code, copy fetch.db from the backup to data\, and start FETCH.' -ForegroundColor Red
    exit 1
}
$after = Get-FetchStatus
if (-not $after) {
    Stop-WithError "FETCH did not report its status after the update. Your backup is in $backupDir."
}
Write-Ok "Database is up to date (FETCH $oldVersion -> $($after.version))"

if ($wasRunning -and -not $NoRestart.IsPresent) {
    Write-Step 'Starting FETCH'
    & (Join-Path $PSScriptRoot 'start.ps1') -NoBrowser
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
else {
    Write-Host ''
    Write-Host 'Update finished. Start FETCH with .\start.ps1' -ForegroundColor Green
}
