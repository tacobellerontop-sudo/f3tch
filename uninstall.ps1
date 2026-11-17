<#
.SYNOPSIS
    Removes FETCH from this computer.

.DESCRIPTION
    Stops FETCH, removes the automatic-start task, and deletes what the
    installer created (.venv, frontend\node_modules, frontend\dist).

    Your data is kept unless you explicitly ask for it to be deleted:
      -RemoveData       deletes the database, configuration and logs
                        (users, history, settings, provider credentials)
      -RemoveDownloads  deletes the default storage folder with every
                        downloaded file

    Each of those asks for confirmation. Programs installed with winget
    (Python, Node.js, aria2) are not removed; use "Apps" in Windows Settings.
    The FETCH folder itself is left in place; delete it afterwards if you want.

.PARAMETER RemoveData
    Also delete data\, logs\ and config\fetch.toml.

.PARAMETER RemoveDownloads
    Also delete storage\ (downloaded files in the default location).

.PARAMETER Yes
    Do not ask for confirmation. Use with care.

.EXAMPLE
    .\uninstall.ps1

.EXAMPLE
    .\uninstall.ps1 -RemoveData -RemoveDownloads
#>
[CmdletBinding()]
param(
    [switch]$RemoveData,
    [switch]$RemoveDownloads,
    [switch]$Yes
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'scripts\FetchCommon.ps1')

function Remove-Folder([string]$RelativePath) {
    # Deletes a folder that must be inside the FETCH folder.
    $target = Join-Path $script:FetchRoot $RelativePath
    if (-not (Test-Path $target)) { return }
    $resolved = (Resolve-Path $target).Path
    $root = (Resolve-Path $script:FetchRoot).Path.TrimEnd('\') + '\'
    if (-not $resolved.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) {
        Write-Warn "Not deleting $resolved (outside the FETCH folder)."
        return
    }
    Remove-Item -Path $resolved -Recurse -Force
    Write-Ok "Removed $RelativePath"
}

Write-Host ''
Write-Host 'FETCH uninstaller' -ForegroundColor White
Write-Host "Folder: $script:FetchRoot"

if (-not (Confirm-Choice 'Remove FETCH from this folder?' $false $Yes.IsPresent)) {
    Write-Host 'Nothing was changed.'
    exit 0
}

Write-Step 'Stopping FETCH'
if (Test-Path $script:VenvPython) {
    if (Stop-FetchServer -TimeoutSeconds 40 -Force) { Write-Ok 'FETCH is not running' }
}
else {
    Write-Note 'No installation found in .venv; nothing to stop.'
}

Write-Step 'Removing the automatic-start task'
if (Unregister-StartupTask) {
    Write-Ok "Scheduled task '$script:StartupTaskName' removed"
}
else {
    Write-Note 'No scheduled task was registered.'
}

Write-Step 'Removing installed components'
Remove-Folder '.venv'
Remove-Folder 'frontend\node_modules'
Remove-Folder 'frontend\dist'

if ($RemoveData.IsPresent) {
    Write-Step 'Deleting data'
    Write-Warn 'This deletes all users, download history, settings and stored provider credentials.'
    if (Confirm-Choice 'Delete data\, logs\ and config\fetch.toml permanently?' $false $Yes.IsPresent) {
        Remove-Folder 'data'
        Remove-Folder 'logs'
        if (Test-Path $script:ConfigFile) {
            Remove-Item -Path $script:ConfigFile -Force
            Write-Ok 'Removed config\fetch.toml'
        }
    }
    else {
        Write-Note 'Data kept.'
    }
}

if ($RemoveDownloads.IsPresent) {
    Write-Step 'Deleting downloaded files'
    $storage = Join-Path $script:FetchRoot 'storage'
    if (Test-Path $storage) {
        $files = @(Get-ChildItem -Path $storage -Recurse -File -ErrorAction SilentlyContinue)
        $sum = ($files | Measure-Object -Property Length -Sum).Sum
        if (-not $sum) { $sum = 0 }
        $gb = [math]::Round($sum / 1GB, 2)
        Write-Warn "storage\ contains $($files.Count) file(s), $gb GB."
        if (Confirm-Choice 'Delete every downloaded file in storage\ permanently?' $false $Yes.IsPresent) {
            Remove-Folder 'storage'
        }
        else {
            Write-Note 'Downloads kept.'
        }
    }
    else {
        Write-Note 'There is no storage\ folder here.'
    }
    Write-Note 'If you moved the storage location in Settings, that folder is not touched.'
}

Write-Host ''
Write-Host 'FETCH has been removed.' -ForegroundColor Green
$kept = @()
foreach ($name in @('data', 'logs', 'storage', 'config\fetch.toml')) {
    if (Test-Path (Join-Path $script:FetchRoot $name)) { $kept += $name }
}
if ($kept.Count -gt 0) {
    Write-Host "Kept: $($kept -join ', ')"
    Write-Host 'Run .\install.ps1 to install FETCH again with this data, or delete the folder to remove everything.'
}
Write-Host ''
