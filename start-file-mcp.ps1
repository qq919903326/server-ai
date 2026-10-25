param(
    [string]$BindAddress = '127.0.0.1',
    [int]$Port = 9003,
    [string]$EnvFile = ''
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) { throw 'Run setup.ps1 first.' }
if (-not $EnvFile) { $EnvFile = Join-Path $PSScriptRoot 'file_mcp/.env' }
& './.venv/Scripts/python.exe' -m file_mcp --host $BindAddress --port $Port --env-file $EnvFile
exit $LASTEXITCODE
