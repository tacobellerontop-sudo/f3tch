# Shared helpers for the FETCH PowerShell scripts.
# Dot-sourced by install.ps1, start.ps1, stop.ps1, update.ps1 and uninstall.ps1.
# Compatible with Windows PowerShell 5.1 and PowerShell 7+.

Set-StrictMode -Version 2.0

$script:FetchRoot = Split-Path -Parent $PSScriptRoot
$script:BackendDir = Join-Path $script:FetchRoot 'backend'
$script:FrontendDir = Join-Path $script:FetchRoot 'frontend'
$script:VenvDir = Join-Path $script:FetchRoot '.venv'
$script:VenvPython = Join-Path $script:VenvDir 'Scripts\python.exe'
$script:VenvPythonW = Join-Path $script:VenvDir 'Scripts\pythonw.exe'
$script:DataDir = Join-Path $script:FetchRoot 'data'
$script:LogDir = Join-Path $script:FetchRoot 'logs'
$script:ConfigFile = Join-Path $script:FetchRoot 'config\fetch.toml'
$script:PidFile = Join-Path $script:DataDir 'fetch.pid'
$script:StartupTaskName = 'FETCH Download Manager'
$script:MinPython = [version]'3.12'
$script:MinNode = [version]'20.0'

function Write-Step([string]$Message) {
    Write-Host ''
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Ok([string]$Message) {
    Write-Host "    [ok] $Message" -ForegroundColor Green
}

function Write-Note([string]$Message) {
    Write-Host "    $Message" -ForegroundColor Gray
}

function Write-Warn([string]$Message) {
    Write-Host "    [!] $Message" -ForegroundColor Yellow
}

# A script can set this to a script block that runs before a fatal exit
# (update.ps1 uses it to record the failure for the dashboard).
$script:OnFatal = $null

function Stop-WithError([string]$Message) {
    Write-Host ''
    Write-Host "ERROR: $Message" -ForegroundColor Red
    if ($script:OnFatal) {
        try { & $script:OnFatal $Message } catch { }
    }
    exit 1
}

function Confirm-Choice([string]$Question, [bool]$Default = $false, [bool]$AssumeYes = $false) {
    # Asks a yes/no question. In a non-interactive session the default is used.
    if ($AssumeYes) { return $true }
    if (-not [Environment]::UserInteractive) { return $Default }
    $suffix = '[y/N]'
    if ($Default) { $suffix = '[Y/n]' }
    while ($true) {
        $answer = Read-Host "$Question $suffix"
        if ([string]::IsNullOrWhiteSpace($answer)) { return $Default }
        switch -Regex ($answer.Trim()) {
            '^(y|yes)$' { return $true }
            '^(n|no)$' { return $false }
        }
    }
}

function Test-Command([string]$Name) {
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Invoke-Checked {
    # Runs a native command and stops the script when it fails.
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @(),
        [string]$WorkingDirectory = $script:FetchRoot,
        [string]$What = ''
    )
    if (-not $What) { $What = "$FilePath $($Arguments -join ' ')" }
    Push-Location $WorkingDirectory
    try {
        & $FilePath @Arguments
        $code = $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
    if ($code -ne 0) {
        Stop-WithError "$What failed (exit code $code). See the output above."
    }
}

function Get-VersionOf([string]$FilePath, [string[]]$Arguments) {
    # Returns the first x.y(.z) version printed by a command, or $null.
    # Windows PowerShell turns redirected stderr into errors; do not stop on it.
    $ErrorActionPreference = 'Continue'
    try {
        $output = & $FilePath @Arguments 2>&1 | Out-String
    }
    catch {
        return $null
    }
    if ($output -match '(\d+)\.(\d+)(\.(\d+))?') {
        $patch = 0
        if ($Matches[4]) { $patch = [int]$Matches[4] }
        return New-Object System.Version ([int]$Matches[1]), ([int]$Matches[2]), $patch
    }
    return $null
}

function Find-Python {
    # Returns @{ Path; Arguments; Version } for a Python >= 3.12, or $null.
    $candidates = @()
    if (Test-Command 'py') {
        $candidates += , @('py', @('-3'))
    }
    if (Test-Command 'python') {
        $candidates += , @('python', @())
    }
    if (Test-Command 'python3') {
        $candidates += , @('python3', @())
    }
    foreach ($candidate in $candidates) {
        $exe = $candidate[0]
        $prefix = [string[]]$candidate[1]
        $version = Get-VersionOf $exe ($prefix + @('--version'))
        if ($version -and $version -ge $script:MinPython) {
            return @{ Path = $exe; Arguments = $prefix; Version = $version }
        }
    }
    return $null
}

function Find-Aria2 {
    # Returns the path of aria2c.exe (bundled folder first, then PATH), or $null.
    $local = Join-Path $script:FetchRoot 'tools\aria2\aria2c.exe'
    if (Test-Path $local) { return $local }
    $command = Get-Command 'aria2c' -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    return $null
}

function Assert-Installed {
    if (-not (Test-Path $script:VenvPython)) {
        Stop-WithError "FETCH is not installed in this folder yet. Run .\install.ps1 first."
    }
}

function Invoke-FetchCli {
    # Runs "python -m fetch_app <args>" inside the virtual environment and
    # returns its standard output as one string. Sets $script:LastCliExit.
    param([string[]]$Arguments)
    # Windows PowerShell turns redirected stderr into errors; do not stop on it.
    $ErrorActionPreference = 'Continue'
    Push-Location $script:BackendDir
    try {
        $output = & $script:VenvPython -m fetch_app @Arguments 2>&1 | Out-String
        $script:LastCliExit = $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
    return $output
}

function Get-FetchStatus {
    # Returns the parsed JSON of "fetch_app status", or $null.
    $raw = Invoke-FetchCli @('status')
    if ($script:LastCliExit -ne 0) { return $null }
    $line = ($raw -split "`r?`n" | Where-Object { $_.Trim().StartsWith('{') } | Select-Object -Last 1)
    if (-not $line) { return $null }
    try {
        return $line | ConvertFrom-Json
    }
    catch {
        return $null
    }
}

function Get-FetchProcess {
    # Returns the running FETCH server process, or $null.
    if (-not (Test-Path $script:PidFile)) { return $null }
    $text = (Get-Content -Path $script:PidFile -Raw -ErrorAction SilentlyContinue)
    $id = 0
    if (-not [int]::TryParse(("$text").Trim(), [ref]$id)) { return $null }
    $process = Get-Process -Id $id -ErrorAction SilentlyContinue
    if ($process -and $process.ProcessName -like 'python*') { return $process }
    return $null
}

function Get-DashboardUrl($Status) {
    # FETCH listens on this computer only and answers to the name localhost.
    return "http://localhost:$($Status.port)"
}

function Stop-FetchServer {
    # Graceful stop through the local control channel; returns $true when the
    # server is no longer running.
    param([int]$TimeoutSeconds = 40, [switch]$Force)
    $process = Get-FetchProcess
    $status = Get-FetchStatus
    if (-not $process -and -not ($status -and $status.running)) { return $true }

    Invoke-FetchCli @('stop') | Out-Null
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 500
        if (-not (Get-FetchProcess)) { return $true }
    }
    if ($Force) {
        $process = Get-FetchProcess
        if ($process) {
            Write-Warn "FETCH did not stop in time; ending process $($process.Id)."
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 1
        }
        Remove-Item -Path $script:PidFile -Force -ErrorAction SilentlyContinue
        Remove-Item -Path (Join-Path $script:DataDir 'control.json') -Force -ErrorAction SilentlyContinue
        return $true
    }
    return $false
}

function Test-PrebuiltFrontend {
    # Release packages ship a ready-made web interface, so Node.js is not needed.
    return (Test-Path (Join-Path $script:FrontendDir 'dist\.prebuilt')) -and (Test-Path (Join-Path $script:FrontendDir 'dist\index.html'))
}

function Build-Frontend {
    if (-not (Test-Command 'npm')) {
        Stop-WithError "npm was not found. Install Node.js $($script:MinNode.Major) LTS or newer from https://nodejs.org and run this script again."
    }
    $lock = Join-Path $script:FrontendDir 'package-lock.json'
    if (Test-Path $lock) {
        Invoke-Checked -FilePath 'npm' -Arguments @('ci', '--no-audit', '--no-fund') -WorkingDirectory $script:FrontendDir -What 'npm ci'
    }
    else {
        Invoke-Checked -FilePath 'npm' -Arguments @('install', '--no-audit', '--no-fund') -WorkingDirectory $script:FrontendDir -What 'npm install'
    }
    Invoke-Checked -FilePath 'npm' -Arguments @('run', 'build') -WorkingDirectory $script:FrontendDir -What 'The frontend build'
    if (-not (Test-Path (Join-Path $script:FrontendDir 'dist\index.html'))) {
        Stop-WithError 'The frontend build finished without producing frontend\dist\index.html.'
    }
}

function Install-BackendPackages {
    Invoke-Checked -FilePath $script:VenvPython -Arguments @('-m', 'pip', 'install', '--disable-pip-version-check', '--upgrade', 'pip') -What 'Upgrading pip'
    Invoke-Checked -FilePath $script:VenvPython -Arguments @('-m', 'pip', 'install', '--disable-pip-version-check', '-r', (Join-Path $script:BackendDir 'requirements.txt')) -What 'Installing the Python packages'
}

function Get-StartupTask {
    if (-not (Get-Command 'Get-ScheduledTask' -ErrorAction SilentlyContinue)) { return $null }
    return Get-ScheduledTask -TaskName $script:StartupTaskName -ErrorAction SilentlyContinue
}

function Register-StartupTask {
    # Starts FETCH when the current user signs in. Runs with that user's normal
    # (non-elevated) rights; no service account and no stored password.
    if (-not (Get-Command 'Register-ScheduledTask' -ErrorAction SilentlyContinue)) {
        Write-Warn 'Scheduled tasks are not available on this system; start FETCH with .\start.ps1.'
        return $false
    }
    $action = New-ScheduledTaskAction -Execute $script:VenvPythonW -Argument '-m fetch_app serve' -WorkingDirectory $script:BackendDir
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $script:StartupTaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description 'Starts the FETCH download manager when you sign in.' -Force | Out-Null
    return $true
}

function Unregister-StartupTask {
    if (Get-StartupTask) {
        Unregister-ScheduledTask -TaskName $script:StartupTaskName -Confirm:$false
        return $true
    }
    return $false
}
