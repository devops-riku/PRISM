#Requires -Version 5.1
<#
    PRISM - start the API (port 8000) and the web client (port 5174) together.

    Creates backend\.venv if it is missing, installs backend dependencies if the
    marker file backend\.venv\.prism-deps-installed is missing or older than
    requirements.txt, runs npm install if frontend\node_modules is missing, then
    starts both servers. Ctrl+C stops both.

    Delete backend\.venv\.prism-deps-installed to force a dependency reinstall.

    Run it with:  powershell -ExecutionPolicy Bypass -File .\run.ps1
#>

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$Root         = $PSScriptRoot
$Backend      = Join-Path $Root 'backend'
$Frontend     = Join-Path $Root 'frontend'
$Venv         = Join-Path $Backend '.venv'
$VenvPy       = Join-Path $Venv 'Scripts\python.exe'
$Marker       = Join-Path $Venv '.prism-deps-installed'
$Requirements = Join-Path $Backend 'requirements.txt'
$PackageJson  = Join-Path $Frontend 'package.json'
$DotEnv       = Join-Path $Backend '.env'

$ApiPort = 8000
$WebPort = 5174

function Write-Fail {
    param([string]$Message)
    Write-Host ""
    Write-Host "run.ps1: $Message" -ForegroundColor Red
    exit 1
}

function Test-PortInUse {
    param([int]$Port)
    try {
        $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop
        return ($null -ne $conn)
    } catch {
        # Nothing is listening, or Get-NetTCPConnection is unavailable on this
        # machine. Either way, let the server itself report a real conflict.
        return $false
    }
}

function Stop-Tree {
    param([int]$ProcessId, [string]$Label)
    if ($ProcessId -le 0) { return }
    try {
        $proc = Get-Process -Id $ProcessId -ErrorAction Stop
        if ($proc.HasExited) { return }
    } catch {
        return
    }
    Write-Host "Stopping $Label"
    # uvicorn --reload and npm both fork, so kill the whole tree.
    try { & taskkill.exe /PID $ProcessId /T /F 2>$null | Out-Null } catch { }
}

# --- Preflight. Check everything before creating or installing anything. -----

if ($null -eq (Get-Command py -ErrorAction SilentlyContinue)) {
    Write-Fail "The 'py' launcher was not found on PATH. Install Python 3.10 or newer from python.org (the installer adds 'py'), then run this script again."
}

& py -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Fail "Python 3.10 or newer is required. Run 'py --version' to see what you have, install a newer Python, then run this script again."
}

if ($null -eq (Get-Command node -ErrorAction SilentlyContinue)) {
    Write-Fail "node was not found on PATH. Install Node 20 or newer from nodejs.org, then run this script again."
}
if ($null -eq (Get-Command npm -ErrorAction SilentlyContinue)) {
    Write-Fail "npm was not found on PATH. It ships with Node - reinstall Node, then run this script again."
}

if (-not (Test-Path -LiteralPath $Requirements)) {
    Write-Fail "backend\requirements.txt is missing. The backend is not set up yet - restore that file and run this script again."
}
if (-not (Test-Path -LiteralPath $PackageJson)) {
    Write-Fail "frontend\package.json is missing. The frontend is not set up yet - restore that file and run this script again."
}

if (Test-PortInUse -Port $ApiPort) {
    Write-Fail "Port $ApiPort is already in use. Stop whatever is listening on it, then run this script again."
}
if (Test-PortInUse -Port $WebPort) {
    Write-Fail "Port $WebPort is already in use. Stop whatever is listening on it, then run this script again."
}

if (-not (Test-Path -LiteralPath $DotEnv)) {
    Write-Host "Note: backend\.env is missing. PRISM will start, but generating a"
    Write-Host "      proposal will return 503 until GEMINI_API_KEY is set there."
    Write-Host ""
}

# --- Backend environment -----------------------------------------------------

if (-not (Test-Path -LiteralPath $Venv)) {
    Write-Host "Creating virtual environment in backend\.venv"
    & py -m venv $Venv
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "Creating the virtual environment failed. Check the output above, then run this script again."
    }
}
if (-not (Test-Path -LiteralPath $VenvPy)) {
    Write-Fail "The backend\.venv directory exists but has no interpreter at $VenvPy. Delete backend\.venv and run this script again."
}

$needsBackendDependencies = -not (Test-Path -LiteralPath $Marker)
if (-not $needsBackendDependencies) {
    $needsBackendDependencies = (Get-Item -LiteralPath $Requirements).LastWriteTimeUtc -gt `
        (Get-Item -LiteralPath $Marker).LastWriteTimeUtc
}
if ($needsBackendDependencies) {
    Write-Host "Installing backend dependencies"
    & $VenvPy -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "Upgrading pip failed. Check the output above, then run this script again."
    }
    & $VenvPy -m pip install -r $Requirements
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "Installing backend dependencies failed. Check the output above, then run this script again."
    }
    New-Item -ItemType File -Path $Marker -Force | Out-Null
}

# --- Frontend environment ----------------------------------------------------

if (-not (Test-Path -LiteralPath (Join-Path $Frontend 'node_modules'))) {
    Write-Host "Installing frontend dependencies"
    $npmInstall = Start-Process -FilePath $env:ComSpec -ArgumentList '/c', 'npm', 'install' `
        -WorkingDirectory $Frontend -NoNewWindow -PassThru -Wait
    if ($npmInstall.ExitCode -ne 0) {
        Write-Fail "npm install failed. Check the output above, then run this script again."
    }
}

# Apply every committed schema revision before the API opens a connection.
# SQLite needs no separate server; Docker supplies PostgreSQL and performs the
# same command in the backend container.
Write-Host "Applying database migrations"
Push-Location $Backend
try {
    & $VenvPy -m alembic -c alembic.ini upgrade head
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "Database migration failed. Check DATABASE_URL and the output above, then run this script again."
    }
} finally {
    Pop-Location
}

# --- Start both servers ------------------------------------------------------

$api = $null
$web = $null
$ctrlCAsInput = $false
$exitCode = 0

try {
    Write-Host "Starting API on port $ApiPort"
    $api = Start-Process -FilePath $VenvPy `
        -ArgumentList '-m', 'uvicorn', 'app.main:app', '--reload', '--no-access-log', '--port', "$ApiPort" `
        -WorkingDirectory $Backend -NoNewWindow -PassThru

    Write-Host "Starting web client on port $WebPort"
    $web = Start-Process -FilePath $env:ComSpec `
        -ArgumentList '/c', 'npm', 'run', 'dev', '--', '--port', "$WebPort" `
        -WorkingDirectory $Frontend -NoNewWindow -PassThru

    Write-Host ""
    Write-Host "  PRISM"
    Write-Host "  Web client   http://localhost:$WebPort"
    Write-Host "  API          http://localhost:$ApiPort"
    Write-Host "  API health   http://localhost:$ApiPort/api/health"
    Write-Host ""
    Write-Host "  Press Ctrl+C to stop both."
    Write-Host ""

    # Read Ctrl+C as a keystroke so this script decides when to shut down,
    # instead of the console killing it and orphaning the two servers.
    try {
        [Console]::TreatControlCAsInput = $true
        $ctrlCAsInput = $true
    } catch {
        $ctrlCAsInput = $false
    }

    while ($true) {
        if ($api.HasExited) {
            Write-Host "The API stopped. Shutting down the web client too."
            $exitCode = 1
            break
        }
        if ($web.HasExited) {
            Write-Host "The web client stopped. Shutting down the API too."
            $exitCode = 1
            break
        }
        if ($ctrlCAsInput) {
            try {
                if ([Console]::KeyAvailable) {
                    $key = [Console]::ReadKey($true)
                    if (($key.Modifiers -band [ConsoleModifiers]::Control) -and ($key.Key -eq [ConsoleKey]::C)) {
                        break
                    }
                }
            } catch {
                # No interactive console. Fall back to plain Ctrl+C handling.
                $ctrlCAsInput = $false
            }
        }
        Start-Sleep -Milliseconds 250
    }
}
catch {
    # A server failed to launch. Report it, then let finally take down whichever
    # one did start.
    Write-Host ""
    Write-Host "run.ps1: $($_.Exception.Message)" -ForegroundColor Red
    $exitCode = 1
}
finally {
    if ($ctrlCAsInput) {
        try { [Console]::TreatControlCAsInput = $false } catch { }
    }
    Write-Host ""
    if ($null -ne $web) { Stop-Tree -ProcessId $web.Id -Label 'web client' }
    if ($null -ne $api) { Stop-Tree -ProcessId $api.Id -Label 'API' }
    Write-Host "PRISM stopped."
    exit $exitCode
}
