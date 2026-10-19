$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot 'runtime\python.exe'
Push-Location $projectRoot
try {
    & $python -X utf8 -B (Join-Path $PSScriptRoot 'bootstrap_runtime.py')
    if ($LASTEXITCODE -ne 0) { throw 'Runtime setup failed.' }
    & $python -X utf8 -B -m unittest discover -s tests -q
    if ($LASTEXITCODE -ne 0) { throw 'Python regression tests failed.' }
    & (Join-Path $PSScriptRoot 'smoke_overlay.ps1')
    & (Join-Path $PSScriptRoot 'build.ps1') -OutputName hexgod-verify.exe
    Write-Output 'PASS: Python, desktop rendering and build.'
} finally { Pop-Location }
