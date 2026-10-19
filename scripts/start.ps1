$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot 'runtime\python.exe'
& $python -X utf8 -B (Join-Path $PSScriptRoot 'bootstrap_runtime.py')
if ($LASTEXITCODE -ne 0) { throw 'Runtime setup failed.' }
$application = Join-Path $projectRoot 'hexgod.exe'
if (-not (Test-Path -LiteralPath $application)) { & (Join-Path $PSScriptRoot 'build.ps1') }
Start-Process -FilePath $application -WorkingDirectory $projectRoot -WindowStyle Hidden
