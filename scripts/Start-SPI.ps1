$ErrorActionPreference = 'Stop'

$SpiRoot = Split-Path -Parent $PSScriptRoot
$BackendScript = Join-Path $PSScriptRoot 'Run-SPI-Backend.ps1'
$FrontendScript = Join-Path $PSScriptRoot 'Run-SPI-Frontend.ps1'
$StopScript = Join-Path $PSScriptRoot 'Stop-SPI.ps1'
$BackendDir = Join-Path $SpiRoot 'backend'
$FrontendDir = Join-Path $SpiRoot 'frontend'
$BackendPython = Join-Path $BackendDir '.venv\Scripts\python.exe'
$RuntimeDir = Join-Path $env:LOCALAPPDATA 'SPI\runtime'
$PowerShellExe = Join-Path $PSHOME 'powershell.exe'

if (-not (Test-Path -LiteralPath $BackendPython)) {
    throw "Backend virtual environment not found: $BackendPython"
}
if (-not (Test-Path -LiteralPath (Join-Path $FrontendDir 'package.json'))) {
    throw 'Frontend package.json was not found.'
}
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    throw 'npm was not found in PATH. Install Node.js or repair PATH before launching SPI.'
}

New-Item -ItemType Directory -Path $RuntimeDir -Force | Out-Null
$backendPidFile = Join-Path $RuntimeDir 'backend.pid'
$frontendPidFile = Join-Path $RuntimeDir 'frontend.pid'

# Avoid starting a second copy over an existing SPI instance.
$trackedRunning = $false
foreach ($entry in @(
    @{ PidFile = $backendPidFile; Script = $BackendScript },
    @{ PidFile = $frontendPidFile; Script = $FrontendScript }
)) {
    if (Test-Path -LiteralPath $entry.PidFile) {
        $rawPid = Get-Content -LiteralPath $entry.PidFile -ErrorAction SilentlyContinue |
            Select-Object -First 1
        $processId = 0
        if ([int]::TryParse([string]$rawPid, [ref]$processId)) {
            $processInfo = Get-CimInstance Win32_Process `
                -Filter "ProcessId = $processId" `
                -ErrorAction SilentlyContinue
            if ($processInfo -and $processInfo.CommandLine -and
                $processInfo.CommandLine.IndexOf(
                    $entry.Script,
                    [StringComparison]::OrdinalIgnoreCase
                ) -ge 0) {
                $trackedRunning = $true
            }
        }
    }
}

if ($trackedRunning) {
    $backendReady = $false
    $frontendReady = $false
    try {
        $backendReady = (Invoke-WebRequest `
            -Uri 'http://127.0.0.1:8000/api/health' `
            -TimeoutSec 2 -UseBasicParsing).StatusCode -eq 200
    } catch {}
    try {
        $frontendReady = (Invoke-WebRequest `
            -Uri 'http://127.0.0.1:5173/' `
            -TimeoutSec 2 -UseBasicParsing).StatusCode -eq 200
    } catch {}

    if ($backendReady -and $frontendReady) {
        Write-Host 'SPI is already running. Opening the existing application.' -ForegroundColor Green
        Start-Process 'http://127.0.0.1:5173/'
        exit 0
    }

    Write-Warning 'Found a previous SPI process that is not healthy. Stopping local services before restarting.'
    # Recovery must not disconnect HPC or remove an active SSH identity.
    & $StopScript -Quiet -KeepHpcAccess
}

# Keep service windows hidden; capture output in RuntimeDir.
$backendOut = Join-Path $RuntimeDir 'backend.stdout.log'
$backendErr = Join-Path $RuntimeDir 'backend.stderr.log'
$frontendOut = Join-Path $RuntimeDir 'frontend.stdout.log'
$frontendErr = Join-Path $RuntimeDir 'frontend.stderr.log'
foreach ($log in @($backendOut, $backendErr, $frontendOut, $frontendErr)) {
    Remove-Item -LiteralPath $log -Force -ErrorAction SilentlyContinue
}

$backendArgs = "-NoLogo -NoProfile -ExecutionPolicy Bypass -File `"$BackendScript`""
$frontendArgs = "-NoLogo -NoProfile -ExecutionPolicy Bypass -File `"$FrontendScript`""
$backendProcess = $null
$frontendProcess = $null

try {
    $backendProcess = Start-Process -FilePath $PowerShellExe -ArgumentList $backendArgs `
        -WorkingDirectory $BackendDir -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $backendOut -RedirectStandardError $backendErr
    Set-Content -LiteralPath $backendPidFile -Value $backendProcess.Id -Encoding ASCII

    $frontendProcess = Start-Process -FilePath $PowerShellExe -ArgumentList $frontendArgs `
        -WorkingDirectory $FrontendDir -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $frontendOut -RedirectStandardError $frontendErr
    Set-Content -LiteralPath $frontendPidFile -Value $frontendProcess.Id -Encoding ASCII

    Write-Host ''
    Write-Host 'Starting SPI in the background...' -ForegroundColor Cyan
    Write-Host 'Waiting for the backend and frontend to become ready.'

    $deadline = (Get-Date).AddSeconds(120)
    $backendReady = $false
    $frontendReady = $false
    while ((Get-Date) -lt $deadline) {
        if (-not $backendReady) {
            try {
                $response = Invoke-WebRequest `
                    -Uri 'http://127.0.0.1:8000/api/health' `
                    -TimeoutSec 2 -UseBasicParsing
                $backendReady = $response.StatusCode -eq 200
            } catch {}
        }
        if (-not $frontendReady) {
            try {
                $response = Invoke-WebRequest `
                    -Uri 'http://127.0.0.1:5173/' `
                    -TimeoutSec 2 -UseBasicParsing
                $frontendReady = $response.StatusCode -eq 200
            } catch {}
        }
        if ($backendReady -and $frontendReady) { break }
        if ($backendProcess.HasExited -or $frontendProcess.HasExited) { break }
        Start-Sleep -Seconds 1
    }

    if (-not ($backendReady -and $frontendReady)) {
        throw "SPI did not become fully ready. Backend ready: $backendReady; frontend ready: $frontendReady. Check logs in $RuntimeDir"
    }

    Write-Host 'SPI backend ready:  http://127.0.0.1:8000' -ForegroundColor Green
    Write-Host 'SPI frontend ready: http://127.0.0.1:5173' -ForegroundColor Green
    Write-Host "Service logs: $RuntimeDir" -ForegroundColor DarkGray
    Start-Process 'http://127.0.0.1:5173/'
    Write-Host 'SPI opened in your default browser.' -ForegroundColor Green
    Write-Host 'To shut down SPI, double-click Stop-SPI.bat in the project folder.' -ForegroundColor Cyan
} catch {
    Write-Warning $_
    # A failed launch cleans up local services only; preserve HPC access.
    & $StopScript -Quiet -KeepHpcAccess
    throw
}
