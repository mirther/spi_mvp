param(
    [switch]$Quiet,
    # Used only by startup recovery/failure cleanup. Normal Stop-SPI disconnects HPC.
    [switch]$KeepHpcAccess
)

$ErrorActionPreference = 'Continue'
$SpiRoot = Split-Path -Parent $PSScriptRoot
$RuntimeDir = Join-Path $env:LOCALAPPDATA 'SPI\runtime'
$BackendScript = Join-Path $PSScriptRoot 'Run-SPI-Backend.ps1'
$FrontendScript = Join-Path $PSScriptRoot 'Run-SPI-Frontend.ps1'

function Write-StopMessage([string]$Message, [string]$Color = 'Gray') {
    if (-not $Quiet) {
        Write-Host $Message -ForegroundColor $Color
    }
}

function Stop-TrackedProcess([string]$PidFile, [string]$ExpectedScript) {
    if (-not (Test-Path -LiteralPath $PidFile)) { return }

    $rawPid = Get-Content -LiteralPath $PidFile -ErrorAction SilentlyContinue |
        Select-Object -First 1
    $processId = 0

    if ([int]::TryParse([string]$rawPid, [ref]$processId)) {
        $processInfo = Get-CimInstance Win32_Process `
            -Filter "ProcessId = $processId" `
            -ErrorAction SilentlyContinue

        if ($processInfo -and $processInfo.CommandLine -and
            $processInfo.CommandLine.IndexOf(
                $ExpectedScript,
                [StringComparison]::OrdinalIgnoreCase
            ) -ge 0) {
            Write-StopMessage "Stopping process tree $processId ($([IO.Path]::GetFileName($ExpectedScript)))..." 'Cyan'
            $taskkill = Join-Path $env:SystemRoot 'System32\taskkill.exe'
            & $taskkill /PID $processId /T /F 2>$null | Out-Null
        }
    }

    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
}

Write-StopMessage 'Shutting down SPI local services...' 'Cyan'
if (Test-Path -LiteralPath $RuntimeDir) {
    Stop-TrackedProcess `
        -PidFile (Join-Path $RuntimeDir 'frontend.pid') `
        -ExpectedScript $FrontendScript
    Stop-TrackedProcess `
        -PidFile (Join-Path $RuntimeDir 'backend.pid') `
        -ExpectedScript $BackendScript
}

if (-not $KeepHpcAccess) {
    # Explicit Stop-SPI is a disconnect action. Startup recovery passes -KeepHpcAccess.
    $publicKeyPath = Join-Path $HOME '.ssh\spi_hpc.pub'
    $taskName = 'SPI-Expire-HPC-SSH-Identity'
    $envFile = Join-Path $SpiRoot '.env'

    if (Test-Path -LiteralPath $envFile) {
        $envLines = Get-Content -LiteralPath $envFile -ErrorAction SilentlyContinue

        $keyLine = $envLines |
            Where-Object { $_ -match '^\s*SPI_HPC_SSH_PUBLIC_KEY_PATH\s*=' } |
            Select-Object -Last 1
        if ($keyLine) {
            $publicKeyPath = ($keyLine -replace '^\s*SPI_HPC_SSH_PUBLIC_KEY_PATH\s*=\s*', '').Trim()
            $publicKeyPath = $publicKeyPath.Trim('"').Trim("'")
            $publicKeyPath = [Environment]::ExpandEnvironmentVariables($publicKeyPath)
        }

        $taskLine = $envLines |
            Where-Object { $_ -match '^\s*SPI_HPC_ACCESS_TASK_NAME\s*=' } |
            Select-Object -Last 1
        if ($taskLine) {
            $taskName = ($taskLine -replace '^\s*SPI_HPC_ACCESS_TASK_NAME\s*=\s*', '').Trim()
            $taskName = $taskName.Trim('"').Trim("'")
        }
    }

    try {
        $sshAdd = Join-Path $env:WINDIR 'System32\OpenSSH\ssh-add.exe'
        if (-not (Test-Path -LiteralPath $sshAdd -PathType Leaf)) {
            $command = Get-Command ssh-add.exe -ErrorAction Stop
            $sshAdd = $command.Source
        }

        if (Test-Path -LiteralPath $publicKeyPath -PathType Leaf) {
            & $sshAdd -d $publicKeyPath 2>$null | Out-Null
            Write-StopMessage 'SPI SSH identity removed from the Windows OpenSSH Agent (or it was already absent).' 'Green'
        } else {
            Write-StopMessage "Configured SPI public key was not found; no agent identity was removed: $publicKeyPath" 'Yellow'
        }
    } catch {
        Write-StopMessage "Could not remove the SPI SSH identity: $($_.Exception.Message)" 'Yellow'
    }

    try {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    } catch {
        Write-StopMessage 'Could not unregister the SPI SSH expiry task.' 'Yellow'
    }
    Write-StopMessage 'SPI HPC SSH expiry task removed (if it existed).' 'Green'
} else {
    Write-StopMessage 'Startup cleanup mode: HPC identity and expiry task were left unchanged.' 'Gray'
}

Write-StopMessage ''
Write-StopMessage 'SPI local services have been stopped.' 'Green'
Write-StopMessage 'SLURM jobs already submitted to the cluster are not cancelled.' 'Gray'
