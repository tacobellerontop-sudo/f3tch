<#
.SYNOPSIS
    Updates FETCH to the newest version.

.DESCRIPTION
    Stops FETCH, backs up the database and the configuration, gets the new
    version, updates the Python packages, migrates the database, and starts
    FETCH again if it was running. Downloads, files, users and settings are kept.

    Where the new version comes from:
      - A git checkout is updated with "git pull --ff-only".
      - Otherwise the latest release package is downloaded from the project's
        GitHub releases page, its SHA-256 checksum is verified against the
        checksum published with the release, and the files are copied over
        this folder.

    The same script runs when an administrator chooses "Install update" in the
    dashboard (Settings > Updates).

.PARAMETER SkipPull
    Do not fetch anything; use the files that are in the folder now (for
    example after replacing them yourself).

.PARAMETER FromRelease
    Use the release package even when this folder is a git checkout.

.PARAMETER Force
    Reinstall the latest release even if this version is already installed.

.PARAMETER NoRestart
    Leave FETCH stopped after the update.

.PARAMETER Yes
    Do not ask for confirmation.

.PARAMETER Unattended
    Used by the dashboard: never prompt and never open a browser.

.EXAMPLE
    .\update.ps1
#>
[CmdletBinding()]
param(
    [switch]$SkipPull,
    [switch]$FromRelease,
    [switch]$Force,
    [switch]$NoRestart,
    [switch]$Yes,
    [switch]$Unattended
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'scripts\FetchCommon.ps1')

Assert-Installed
$assumeYes = $Yes.IsPresent -or $Unattended.IsPresent

# -- Status for the dashboard -----------------------------------------------------
$statusFile = Join-Path $script:DataDir 'update-status.json'
$startedAt = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
$script:FromVersion = ''
$script:ToVersion = ''

function Set-UpdateStatus([string]$State, [string]$Message) {
    $finished = ''
    if ($State -ne 'running') { $finished = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ') }
    $record = [ordered]@{
        state        = $State
        message      = $Message
        from_version = $script:FromVersion
        to_version   = $script:ToVersion
        started_at   = $startedAt
        finished_at  = $finished
    }
    try {
        New-Item -ItemType Directory -Force -Path $script:DataDir | Out-Null
        $json = $record | ConvertTo-Json -Compress
        [System.IO.File]::WriteAllText($statusFile, $json, (New-Object System.Text.UTF8Encoding $false))
    }
    catch {
        Write-Warn "Could not write $statusFile"
    }
}

$script:WasRunning = $false
$script:OnFatal = {
    param($Message)
    Set-UpdateStatus 'failed' $Message
    # Do not leave people without a server: bring the previous state back up.
    if ($script:WasRunning -and -not $NoRestart.IsPresent) {
        Write-Warn 'Starting FETCH again with the files that are in place.'
        try { & (Join-Path $PSScriptRoot 'start.ps1') -NoBrowser } catch { }
    }
}
trap {
    Write-Host ''
    Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
    & $script:OnFatal $_.Exception.Message
    exit 1
}

function Get-LatestRelease([string]$Repository) {
    # Returns @{ Version; Tag; ZipUrl; ZipName; ChecksumUrl } for the latest release.
    if ($Repository -notmatch '^[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9._-]+$') {
        Stop-WithError 'No update repository is configured (see [updates] in config\fetch.toml).'
    }
    # Windows PowerShell 5.1 does not enable TLS 1.2 by default.
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    $headers = @{ 'User-Agent' = 'FETCH-updater'; 'Accept' = 'application/vnd.github+json' }
    try {
        $release = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repository/releases/latest" -Headers $headers -UseBasicParsing
    }
    catch {
        Stop-WithError "The latest release could not be read from GitHub ($($_.Exception.Message)). Is there a published release, and is this computer online?"
    }
    $tag = "$($release.tag_name)"
    if ($tag -notmatch '^v?(\d+\.\d+(\.\d+)?)') {
        Stop-WithError "The latest release ($tag) has no recognisable version number."
    }
    $version = $Matches[1]
    $zip = $release.assets | Where-Object { $_.name -match '^FETCH-[\w.-]+\.zip$' } | Select-Object -First 1
    if (-not $zip) {
        Stop-WithError "Release $tag has no FETCH-<version>.zip package attached."
    }
    $sum = $release.assets | Where-Object { $_.name -eq "$($zip.name).sha256" } | Select-Object -First 1
    if (-not $sum) {
        Stop-WithError "Release $tag has no checksum file for $($zip.name); refusing to install an unverifiable package."
    }
    foreach ($url in @($zip.browser_download_url, $sum.browser_download_url)) {
        if ("$url" -notmatch '^https://github\.com/') {
            Stop-WithError "Release $tag points to an unexpected download address; refusing to continue."
        }
    }
    return @{ Version = $version; Tag = $tag; ZipName = $zip.name; ZipUrl = $zip.browser_download_url; ChecksumUrl = $sum.browser_download_url }
}

function Install-ReleasePackage($Release) {
    # Downloads, verifies and unpacks the package over the FETCH folder.
    $work = Join-Path $script:DataDir 'updates'
    if (Test-Path $work) { Remove-Item -Path $work -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $work | Out-Null
    $zipPath = Join-Path $work $Release.ZipName
    $sumPath = "$zipPath.sha256"
    $headers = @{ 'User-Agent' = 'FETCH-updater' }
    $oldProgress = $ProgressPreference
    $ProgressPreference = 'SilentlyContinue'   # the progress bar makes downloads very slow
    try {
        Invoke-WebRequest -Uri $Release.ZipUrl -OutFile $zipPath -Headers $headers -UseBasicParsing
        Invoke-WebRequest -Uri $Release.ChecksumUrl -OutFile $sumPath -Headers $headers -UseBasicParsing
    }
    catch {
        Stop-WithError "The release package could not be downloaded ($($_.Exception.Message))."
    }
    finally {
        $ProgressPreference = $oldProgress
    }

    $expected = ((Get-Content -Path $sumPath -Raw) -split '\s+')[0].Trim().ToLowerInvariant()
    $actual = (Get-FileHash -Path $zipPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($expected -notmatch '^[0-9a-f]{64}$' -or $expected -ne $actual) {
        Remove-Item -Path $work -Recurse -Force -ErrorAction SilentlyContinue
        Stop-WithError 'The downloaded package does not match its published SHA-256 checksum. Nothing was changed.'
    }
    Write-Ok "Package verified (SHA-256 $($actual.Substring(0, 16))...)"

    $unpacked = Join-Path $work 'unpacked'
    Expand-Archive -Path $zipPath -DestinationPath $unpacked -Force
    $source = $unpacked
    if (-not (Test-Path (Join-Path $source 'install.ps1'))) {
        $inner = @(Get-ChildItem -Path $unpacked -Directory)
        if ($inner.Count -eq 1 -and (Test-Path (Join-Path $inner[0].FullName 'install.ps1'))) {
            $source = $inner[0].FullName
        }
        else {
            Stop-WithError 'The release package does not look like FETCH (install.ps1 is missing). Nothing was changed.'
        }
    }
    foreach ($required in @('backend\fetch_app\__init__.py', 'backend\requirements.txt', 'frontend\dist\index.html')) {
        if (-not (Test-Path (Join-Path $source $required))) {
            Stop-WithError "The release package is incomplete ($required is missing). Nothing was changed."
        }
    }

    # Replace program files only. Data, configuration, downloads, logs, the
    # Python environment and tools are never part of a package.
    $protected = @('data', 'logs', 'storage', '.venv', 'tools', '.git')
    $oldDist = Join-Path $script:FrontendDir 'dist'
    if (Test-Path $oldDist) { Remove-Item -Path $oldDist -Recurse -Force }
    foreach ($item in Get-ChildItem -Path $source -Force) {
        if ($protected -contains $item.Name) { continue }
        Copy-Item -Path $item.FullName -Destination $script:FetchRoot -Recurse -Force
    }
    Remove-Item -Path $work -Recurse -Force -ErrorAction SilentlyContinue
}

# -- Start ---------------------------------------------------------------------------
Write-Host ''
Write-Host "FETCH updater ($(Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))" -ForegroundColor White

$before = Get-FetchStatus
if (-not $before) {
    Stop-WithError 'FETCH could not report its status. Run .\install.ps1 to repair the installation.'
}
$script:WasRunning = [bool]$before.running
$script:FromVersion = "$($before.version)"
Set-UpdateStatus 'running' 'Preparing.'

$useGit = (-not $FromRelease.IsPresent) -and (Test-Path (Join-Path $script:FetchRoot '.git')) -and (Test-Command 'git')
$release = $null
if (-not $SkipPull.IsPresent -and -not $useGit) {
    Write-Step 'Looking for the latest release'
    $repository = ''
    if ($before.PSObject.Properties.Name -contains 'update_repository') { $repository = "$($before.update_repository)" }
    $release = Get-LatestRelease $repository
    $script:ToVersion = $release.Version
    Write-Ok "Latest release: $($release.Tag) (installed: $($script:FromVersion))"
    if (([version]$release.Version) -le ([version]$script:FromVersion) -and -not $Force.IsPresent) {
        Write-Host ''
        Write-Host 'FETCH is already up to date.' -ForegroundColor Green
        Set-UpdateStatus 'succeeded' "FETCH $($script:FromVersion) is already the latest version."
        exit 0
    }
    if (-not (Confirm-Choice "Install FETCH $($release.Version) now?" $true $assumeYes)) {
        Set-UpdateStatus 'cancelled' 'The update was cancelled.'
        Write-Host 'Nothing was changed.'
        exit 0
    }
}
elseif ($useGit -and -not $SkipPull.IsPresent) {
    if (-not (Confirm-Choice 'Run "git pull --ff-only" in this folder?' $true $assumeYes)) {
        Set-UpdateStatus 'cancelled' 'The update was cancelled.'
        Write-Host 'Nothing was changed.'
        exit 0
    }
}

if ($script:WasRunning) {
    Write-Step 'Stopping FETCH'
    Set-UpdateStatus 'running' 'Stopping FETCH.'
    if (-not (Stop-FetchServer -TimeoutSeconds 60 -Force)) {
        Stop-WithError 'FETCH could not be stopped. Run .\stop.ps1 -Force and try again.'
    }
    Write-Ok 'Stopped (active downloads are saved)'
}

Write-Step 'Backing up the database and the configuration'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backupRoot = Join-Path $script:DataDir 'backups'
$backupDir = Join-Path $backupRoot $stamp
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
foreach ($name in @('fetch.db', 'fetch.db-wal', 'fetch.db-shm')) {
    $sourceFile = Join-Path $script:DataDir $name
    if (Test-Path $sourceFile) { Copy-Item -Path $sourceFile -Destination $backupDir -Force }
}
if (Test-Path $script:ConfigFile) { Copy-Item -Path $script:ConfigFile -Destination $backupDir -Force }
Write-Ok "Backup written to $backupDir"
# Keep the ten most recent backups.
Get-ChildItem -Path $backupRoot -Directory | Sort-Object Name -Descending | Select-Object -Skip 10 |
    ForEach-Object { Remove-Item -Path $_.FullName -Recurse -Force -ErrorAction SilentlyContinue }

if ($SkipPull.IsPresent) {
    Write-Step 'Using the files that are in the folder now'
}
elseif ($useGit) {
    Write-Step 'Getting the latest code with git'
    Set-UpdateStatus 'running' 'Getting the latest code.'
    Invoke-Checked -FilePath 'git' -Arguments @('pull', '--ff-only') -What 'git pull'
    Write-Ok 'Code updated'
}
else {
    Write-Step "Downloading FETCH $($release.Version)"
    Set-UpdateStatus 'running' "Downloading FETCH $($release.Version)."
    Install-ReleasePackage $release
    Write-Ok 'New files are in place'
}

Write-Step 'Updating the backend packages'
Set-UpdateStatus 'running' 'Updating the Python packages.'
Install-BackendPackages
Write-Ok 'Backend packages are up to date'

if (Test-PrebuiltFrontend) {
    Write-Step 'Web interface'
    Write-Ok 'Using the web interface included in the release'
}
else {
    Write-Step 'Rebuilding the web interface'
    Set-UpdateStatus 'running' 'Building the web interface.'
    Build-Frontend
    Write-Ok 'Web interface rebuilt'
}

Write-Step 'Migrating the database'
Set-UpdateStatus 'running' 'Migrating the database.'
$initOutput = Invoke-FetchCli @('init')
if ($script:LastCliExit -ne 0) {
    Write-Host $initOutput
    Stop-WithError "The database migration failed. Your backup is in $backupDir. To go back, restore the previous files and copy fetch.db from the backup to data\."
}
$after = Get-FetchStatus
if (-not $after) {
    Stop-WithError "FETCH did not report its status after the update. Your backup is in $backupDir."
}
$script:ToVersion = "$($after.version)"
Write-Ok "Database is up to date (FETCH $($script:FromVersion) -> $($script:ToVersion))"

if ($script:WasRunning -and -not $NoRestart.IsPresent) {
    Write-Step 'Starting FETCH'
    & (Join-Path $PSScriptRoot 'start.ps1') -NoBrowser
    if ($LASTEXITCODE -ne 0) {
        Set-UpdateStatus 'failed' "FETCH $($script:ToVersion) was installed but did not start. Run .\start.ps1 -Foreground to see why."
        exit $LASTEXITCODE
    }
}
Set-UpdateStatus 'succeeded' "Updated from $($script:FromVersion) to $($script:ToVersion)."
Write-Host ''
Write-Host "Update finished: FETCH $($script:ToVersion)." -ForegroundColor Green
if (-not $script:WasRunning -or $NoRestart.IsPresent) {
    Write-Host 'Start FETCH with .\start.ps1'
}
