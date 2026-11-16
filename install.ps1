<#
.SYNOPSIS
    Installs FETCH in this folder.

.DESCRIPTION
    Checks the prerequisites (Python 3.12+, optionally aria2, and Node.js 20+
    when the web interface has to be built from source),
    creates a Python virtual environment in .venv, installs the backend
    packages, builds the web interface, creates the configuration and the
    database, and prints how to open the dashboard.

    Nothing is installed system-wide without asking first. Missing
    prerequisites can be installed with winget (Microsoft's package manager)
    after you confirm; otherwise the script tells you where to get them.

    Running the script again is safe: existing configuration, database and
    downloads are kept.

.PARAMETER Port
    TCP port for the dashboard (default 8787). Only used on first install.

.PARAMETER BindAddress
    Address to listen on (default 127.0.0.1 = this computer only). Only used
    on first install; change it later in Settings > Network.

.PARAMETER RegisterStartup
    Start FETCH automatically when you sign in to Windows (a per-user
    scheduled task; no administrator rights needed).

.PARAMETER SkipAria2
    Do not offer to install aria2. FETCH works without it.

.PARAMETER SkipFrontendBuild
    Keep the existing frontend\dist build.

.PARAMETER RebuildFrontend
    Build the web interface even though this release package already
    contains a built one (needs Node.js).

.PARAMETER Yes
    Answer "yes" to every question (unattended install). This includes
    installing missing prerequisites with winget.

.EXAMPLE
    .\install.ps1

.EXAMPLE
    .\install.ps1 -Port 9000 -RegisterStartup
#>
[CmdletBinding()]
param(
    [ValidateRange(1024, 65535)][int]$Port = 8787,
    [string]$BindAddress = '127.0.0.1',
    [switch]$RegisterStartup,
    [switch]$SkipAria2,
    [switch]$SkipFrontendBuild,
    [switch]$RebuildFrontend,
    [switch]$Yes
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'scripts\FetchCommon.ps1')

function Install-WithWinget([string]$PackageId, [string]$DisplayName, [string]$ManualUrl) {
    # Installs a prerequisite from the official winget source, after consent.
    if (-not (Test-Command 'winget')) {
        Write-Warn "$DisplayName is missing and winget is not available."
        Write-Note "Install it from $ManualUrl and run .\install.ps1 again."
        return $false
    }
    Write-Note "$DisplayName can be installed with: winget install --id $PackageId --source winget"
    if (-not (Confirm-Choice "Install $DisplayName now with winget?" $false $Yes.IsPresent)) {
        Write-Note "Skipped. Manual download: $ManualUrl"
        return $false
    }
    & winget install --id $PackageId --exact --source winget --accept-source-agreements --accept-package-agreements
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "winget could not install $DisplayName (exit code $LASTEXITCODE)."
        return $false
    }
    # Make the freshly installed program visible to this session.
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
    return $true
}

Write-Host ''
Write-Host 'FETCH installer' -ForegroundColor White
Write-Host "Folder: $script:FetchRoot"

if ($env:OS -ne 'Windows_NT') {
    Stop-WithError 'These scripts are for Windows. On other systems follow docs\DEVELOPMENT.md.'
}
if ($script:FetchRoot -match '[\[\]]') {
    Stop-WithError 'The install folder contains [ or ], which PowerShell paths do not handle well. Move FETCH to a simpler path.'
}

# -- Python ---------------------------------------------------------------
Write-Step 'Checking Python'
$python = Find-Python
if (-not $python) {
    Write-Warn "Python $script:MinPython or newer was not found."
    if (Install-WithWinget 'Python.Python.3.12' 'Python 3.12' 'https://www.python.org/downloads/windows/') {
        $python = Find-Python
    }
}
if (-not $python) {
    Stop-WithError "Python $script:MinPython or newer is required. Install it (tick 'Add python.exe to PATH'), open a new PowerShell window and run .\install.ps1 again."
}
Write-Ok "Python $($python.Version) ($($python.Path))"

# -- Node.js ----------------------------------------------------------------
$distIndex = Join-Path $script:FrontendDir 'dist\index.html'
$needBuild = -not $SkipFrontendBuild.IsPresent
if ($needBuild -and (Test-PrebuiltFrontend) -and -not $RebuildFrontend.IsPresent) {
    # This is a release package: the web interface is already built.
    $needBuild = $false
}
if ($SkipFrontendBuild.IsPresent -and -not (Test-Path $distIndex)) {
    Write-Warn 'There is no existing frontend build, so it has to be built.'
    $needBuild = $true
}
if ($needBuild) {
    Write-Step 'Checking Node.js'
    $nodeVersion = $null
    if (Test-Command 'node') { $nodeVersion = Get-VersionOf 'node' @('--version') }
    if (-not $nodeVersion -or $nodeVersion -lt $script:MinNode) {
        Write-Warn "Node.js $($script:MinNode.Major) or newer was not found (needed once, to build the web interface)."
        if (Install-WithWinget 'OpenJS.NodeJS.LTS' 'Node.js LTS' 'https://nodejs.org/') {
            if (Test-Command 'node') { $nodeVersion = Get-VersionOf 'node' @('--version') }
        }
    }
    if (-not $nodeVersion -or $nodeVersion -lt $script:MinNode) {
        Stop-WithError "Node.js $($script:MinNode.Major) or newer is required to build the web interface. Install it, open a new PowerShell window and run .\install.ps1 again."
    }
    Write-Ok "Node.js $nodeVersion"
}

# -- aria2 (optional) -----------------------------------------------------------
Write-Step 'Checking aria2 (optional)'
$aria2 = Find-Aria2
if ($aria2) {
    Write-Ok "aria2 found: $aria2"
}
elseif ($SkipAria2.IsPresent) {
    Write-Note 'Skipped. FETCH uses its built-in download engine.'
}
else {
    Write-Note 'aria2 speeds up large downloads by using several connections. FETCH works without it.'
    if (Install-WithWinget 'aria2.aria2' 'aria2' 'https://github.com/aria2/aria2/releases') {
        $aria2 = Find-Aria2
    }
    if ($aria2) {
        Write-Ok "aria2 found: $aria2"
    }
    else {
        Write-Note 'Continuing without aria2. To add it later, install it or place aria2c.exe in tools\aria2\ and restart FETCH.'
    }
}

# -- Python environment -----------------------------------------------------------
Write-Step 'Creating the Python environment (.venv)'
if (-not (Test-Path $script:VenvPython)) {
    $venvArgs = @($python.Arguments) + @('-m', 'venv', $script:VenvDir)
    Invoke-Checked -FilePath $python.Path -Arguments $venvArgs -What 'Creating the virtual environment'
}
$venvVersion = Get-VersionOf $script:VenvPython @('--version')
if (-not $venvVersion -or $venvVersion -lt $script:MinPython) {
    Stop-WithError "The existing .venv uses Python $venvVersion. Delete the .venv folder and run .\install.ps1 again."
}
Write-Ok "Virtual environment ready (Python $venvVersion)"

Write-Step 'Installing the backend packages'
Install-BackendPackages
Write-Ok 'Backend packages installed'

# -- Frontend ------------------------------------------------------------------------
if ($needBuild) {
    Write-Step 'Building the web interface'
    Build-Frontend
    Write-Ok 'Web interface built (frontend\dist)'
}
else {
    Write-Step 'Web interface'
    Write-Ok 'Keeping the existing build (frontend\dist)'
}

# -- Configuration and database --------------------------------------------------------
Write-Step 'Preparing the configuration and the database'
$firstInstall = -not (Test-Path $script:ConfigFile)
$initOutput = Invoke-FetchCli @('init', '--bind', $BindAddress, '--port', "$Port")
if ($script:LastCliExit -ne 0) {
    Write-Host $initOutput
    Stop-WithError 'Initialising FETCH failed. See the output above.'
}
($initOutput -split "`r?`n") | Where-Object { $_.Trim() } | ForEach-Object { Write-Note $_ }
if ($firstInstall) {
    Write-Ok 'Configuration created with new secrets (config\fetch.toml). Keep that file private.'
}
else {
    Write-Ok 'Existing configuration and data kept; database schema is up to date.'
}

# -- Verify the installation --------------------------------------------------------------
Write-Step 'Verifying the installation'
Push-Location $script:BackendDir
try {
    & $script:VenvPython -c "import fetch_app.app, fetch_app.engine.manager, fetch_app.providers.registry; print('imports ok')" | Out-Null
    $importExit = $LASTEXITCODE
}
finally {
    Pop-Location
}
if ($importExit -ne 0) {
    Stop-WithError 'The backend could not be imported. See the output above.'
}
$status = Get-FetchStatus
if (-not $status) {
    Stop-WithError 'FETCH did not report its status. See the output above.'
}
Write-Ok "FETCH $($status.version) is installed"

# -- Automatic start (optional) ------------------------------------------------------------
$startup = $RegisterStartup.IsPresent
if (-not $startup -and -not (Get-StartupTask) -and -not $Yes.IsPresent) {
    Write-Step 'Automatic start (optional)'
    $startup = Confirm-Choice 'Start FETCH automatically when you sign in to Windows?' $false
}
if ($startup) {
    if (Register-StartupTask) {
        Write-Ok "Scheduled task '$script:StartupTaskName' registered. Remove it with .\uninstall.ps1 or Task Scheduler."
    }
}

# -- Summary ---------------------------------------------------------------------------------
$url = Get-DashboardUrl $status
Write-Host ''
Write-Host 'FETCH is installed.' -ForegroundColor Green
Write-Host ''
Write-Host '  Start it:    .\start.ps1'
Write-Host "  Dashboard:   $url"
if ($status.setup_required) {
    Write-Host ''
    Write-Host '  First-time setup: the dashboard asks for this token to create the' -ForegroundColor White
    Write-Host '  administrator account. It stops working once that account exists.' -ForegroundColor White
    Write-Host ''
    Write-Host "      $($status.setup_token)" -ForegroundColor Yellow
}
Write-Host ''
if ($status.bind_address -eq '127.0.0.1') {
    Write-Host '  FETCH listens on this computer only. To let friends in, see'
    Write-Host '  "Remote access with Tailscale" in README.md.'
}
else {
    Write-Host "  FETCH will listen on $($status.bind_address). Only expose it to networks you trust;" -ForegroundColor Yellow
    Write-Host '  see "Remote access with Tailscale" in README.md.' -ForegroundColor Yellow
}
Write-Host ''
