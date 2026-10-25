param(
    [switch]$Authorize,
    [string]$Server = '',
    [string]$Device = 'my-pc',
    [string[]]$Allow = @(),
    [string]$Config = '',
    [string]$EnvFile = ''
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) { throw 'Install file_mcp/requirements.txt in .venv first.' }
if (-not $Config) { $Config = Join-Path $PSScriptRoot 'file_mcp/agent.local.json' }
if (-not $EnvFile) { $EnvFile = Join-Path $PSScriptRoot 'file_mcp/agent.env' }
$agentArgs = @('-m', 'file_mcp.agent', '--config', $Config, '--env-file', $EnvFile)
if ($Authorize) {
    if (-not $Server) { throw 'Provide -Server https://your-file-mcp-host when authorizing.' }
    $agentArgs += @('--authorize', '--server', $Server, '--device', $Device)
    foreach ($root in $Allow) { $agentArgs += @('--allow', $root) }
}
& './.venv/Scripts/python.exe' @agentArgs
exit $LASTEXITCODE
