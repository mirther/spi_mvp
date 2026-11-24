$ErrorActionPreference = 'Stop'

$SpiRoot = Split-Path -Parent $PSScriptRoot
$BackendDir = Join-Path $SpiRoot 'backend'
$Activate = Join-Path $BackendDir '.venv\Scripts\Activate.ps1'

if (-not (Test-Path -LiteralPath $Activate)) {
    throw "Backend activation script not found: $Activate"
}

Set-Location -LiteralPath $BackendDir
. $Activate

python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
