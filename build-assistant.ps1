param([switch]$InstallBuildTools)
$ErrorActionPreference = 'Stop'
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Run setup.ps1 before building the assistant.' }
$builder = Join-Path $PSScriptRoot 'tools\build_desktop_assistant.py'
$arguments = @($builder)
if ($InstallBuildTools) { $arguments += '--install-build-tools' }
& $python @arguments
if ($LASTEXITCODE -ne 0) { throw 'Assistant build or frozen-executable verification failed.' }
