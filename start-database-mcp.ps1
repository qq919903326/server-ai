param(
    [ValidateSet('mysql', 'postgres')][string]$Engine = 'mysql',
    [string]$BindAddress = '127.0.0.1',
    [int]$Port = 0,
    [string]$EnvFile = ''
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) { throw 'Run setup.ps1 first.' }
if (-not $EnvFile) { $EnvFile = Join-Path $PSScriptRoot 'database_mcp/.env' }
if ($Port -eq 0) { if ($Engine -eq 'mysql') { $Port = 9001 } else { $Port = 9002 } }
& './.venv/Scripts/python.exe' -m database_mcp --engine $Engine --host $BindAddress --port $Port --env-file $EnvFile
exit $LASTEXITCODE
