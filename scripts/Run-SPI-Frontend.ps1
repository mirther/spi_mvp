$ErrorActionPreference = 'Stop'

$SpiRoot = Split-Path -Parent $PSScriptRoot
$FrontendDir = Join-Path $SpiRoot 'frontend'

if (-not (Test-Path -LiteralPath (Join-Path $FrontendDir 'package.json'))) {
    throw "Frontend package.json was not found: $FrontendDir"
}

Set-Location -LiteralPath $FrontendDir
npm run dev -- --host 127.0.0.1
