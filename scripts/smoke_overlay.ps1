$ErrorActionPreference = 'Stop'
$rootPath = Split-Path -Parent $PSScriptRoot
Import-Module (Join-Path $PSScriptRoot 'DesktopBuild.psm1') -Force
$spec = Get-DesktopBuildSpec -ProjectRoot $rootPath
$outputDir = Join-Path $rootPath 'artifacts\tests\desktop'
New-Item -ItemType Directory -Path $outputDir -Force | Out-Null
$outputPath = Join-Path $outputDir 'OverlaySmokeTests.exe'
& $spec.Compiler /nologo /target:exe /main:OverlaySmokeTests /platform:x64 /codepage:65001 "/out:$outputPath" @($spec.References) @($spec.Sources) (Join-Path $rootPath 'tests\desktop\OverlaySmokeTests.cs')
if ($LASTEXITCODE -ne 0) { throw 'Desktop test compilation failed.' }
$previousLegacyFixture = $env:HEXGOD_TEST_LEGACY
try {
    $env:HEXGOD_TEST_LEGACY = '1'
    & $outputPath $rootPath
    if ($LASTEXITCODE -ne 0) { throw 'Legacy fixture overlay tests failed.' }
    Remove-Item Env:\HEXGOD_TEST_LEGACY
    & $outputPath $rootPath --production
    if ($LASTEXITCODE -ne 0) { throw 'Production overlay tests failed.' }
} finally {
    if ($null -eq $previousLegacyFixture) { Remove-Item Env:\HEXGOD_TEST_LEGACY -ErrorAction SilentlyContinue }
    else { $env:HEXGOD_TEST_LEGACY = $previousLegacyFixture }
}
