$ErrorActionPreference = 'Stop'
$sourceRoot = if ($env:HEXGOD_SOURCE_DIR) { $env:HEXGOD_SOURCE_DIR } else { $PSScriptRoot }
$python = Join-Path $sourceRoot 'runtime\python.exe'
Push-Location $PSScriptRoot
try {
    & $python -X utf8 -B -m unittest discover -s tests -q
    if ($LASTEXITCODE -ne 0) { throw 'Python regression tests failed.' }
    & (Join-Path $PSScriptRoot 'tests\smoke_overlay.ps1')
    & (Join-Path $PSScriptRoot 'build.ps1') -OutputName hexgod-0.17.exe
    Write-Output 'PASS: regression tests, legacy/ARAMGG WPF scenes and 0.17 build.'
} finally {
    Pop-Location
}
