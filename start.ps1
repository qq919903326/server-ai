param([string]$BindAddress = '127.0.0.1', [int]$Port = 8000)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) { throw 'Run setup.ps1 first.' }
if (-not (Test-Path -LiteralPath 'frontend/dist/index.html')) { throw 'Run setup.ps1 to build the frontend.' }
Write-Host "AI Studio: http://${BindAddress}:$Port" -ForegroundColor Cyan
& './.venv/Scripts/python.exe' -m uvicorn backend.app:app --host $BindAddress --port $Port
exit $LASTEXITCODE
