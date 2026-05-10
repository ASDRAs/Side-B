$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$BackendDir = Join-Path $Root "backend"
$FrontendDir = Join-Path $Root "frontend"
$VenvDir = Join-Path $BackendDir ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$Requirements = Join-Path $BackendDir "requirements.txt"
$RequirementsStamp = Join-Path $VenvDir ".requirements.stamp"
$NodeModules = Join-Path $FrontendDir "node_modules"

function Quote-PowerShellPath {
    param([string]$Path)
    return "'" + ($Path -replace "'", "''") + "'"
}

function Get-Python314 {
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        $exe = & py -3.14 -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $exe) {
            return $exe.Trim()
        }
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        $version = & $python.Source -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
        if ($LASTEXITCODE -eq 0 -and $version -eq "3.14") {
            return $python.Source
        }
    }

    throw "Python 3.14 was not found. Install Python 3.14, then run this launcher again."
}

function Ensure-Backend {
    $python314 = Get-Python314

    if (-not (Test-Path $VenvPython)) {
        Write-Host "Creating backend virtual environment..."
        & $python314 -m venv $VenvDir
    }

    $needsInstall = -not (Test-Path $RequirementsStamp)
    if (-not $needsInstall) {
        $needsInstall = (Get-Item $RequirementsStamp).LastWriteTimeUtc -lt (Get-Item $Requirements).LastWriteTimeUtc
    }

    if ($needsInstall) {
        Write-Host "Installing backend dependencies..."
        & $VenvPython -m pip install --upgrade pip
        & $VenvPython -m pip install -r $Requirements
        Set-Content -Path $RequirementsStamp -Value (Get-Date).ToUniversalTime().ToString("o")
    }
}

function Ensure-Frontend {
    $node = Get-Command node -ErrorAction SilentlyContinue
    if (-not $node) {
        throw "Node.js was not found in PATH. Install Node.js, then run this launcher again."
    }

    $npm = Get-Command npm -ErrorAction SilentlyContinue
    if (-not $npm) {
        throw "npm was not found in PATH. Install Node.js, then run this launcher again."
    }

    if (-not (Test-Path $NodeModules)) {
        Write-Host "Installing frontend dependencies..."
        Push-Location $FrontendDir
        try {
            & $npm.Source install
        }
        finally {
            Pop-Location
        }
    }
}

try {
    Write-Host "Preparing Side-B localhost launcher..."
    Ensure-Backend
    Ensure-Frontend

    $backendCommand = "Set-Location -LiteralPath $(Quote-PowerShellPath $BackendDir); `$env:PYTHONPATH=$(Quote-PowerShellPath $BackendDir); & $(Quote-PowerShellPath $VenvPython) -m uvicorn main:app --reload --host 127.0.0.1 --port 8000"
    $frontendCommand = "Set-Location -LiteralPath $(Quote-PowerShellPath $FrontendDir); `$env:VITE_API_BASE_URL='http://127.0.0.1:8000'; npm run dev -- --host 127.0.0.1 --port 3000"

    Start-Process powershell.exe -ArgumentList @("-NoExit", "-ExecutionPolicy", "Bypass", "-Command", $backendCommand) -WindowStyle Normal
    Start-Sleep -Seconds 2
    Start-Process powershell.exe -ArgumentList @("-NoExit", "-ExecutionPolicy", "Bypass", "-Command", $frontendCommand) -WindowStyle Normal
    Start-Sleep -Seconds 8
    Start-Process "http://localhost:3000"

    Write-Host ""
    Write-Host "Side-B is starting on http://localhost:3000"
    Write-Host "Backend health: http://127.0.0.1:8000/health"
}
catch {
    Write-Host ""
    Write-Host "Failed to start Side-B localhost." -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    Write-Host ""
    Read-Host "Press Enter to close"
    exit 1
}
