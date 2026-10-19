$ErrorActionPreference = 'Stop'
$rootPath = Split-Path -Parent $PSScriptRoot
$frameworkPath = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319'
$compilerPath = Join-Path $frameworkPath 'csc.exe'
$references = @('System.dll', 'System.Core.dll', 'System.Xaml.dll', 'System.Web.Extensions.dll', 'System.Windows.Forms.dll', 'System.Drawing.dll') | ForEach-Object { '/reference:' + (Join-Path $frameworkPath $_) }
$references += @('WindowsBase.dll', 'PresentationCore.dll', 'PresentationFramework.dll') | ForEach-Object { '/reference:' + (Join-Path (Join-Path $frameworkPath 'WPF') $_) }
$cachePath = Join-Path $rootPath '.cache'
New-Item -ItemType Directory -Path $cachePath -Force | Out-Null
$outputPath = Join-Path $cachePath 'OverlaySmokeTests.exe'
$sources = Get-ChildItem -LiteralPath $rootPath -Filter '*.cs' | Select-Object -ExpandProperty FullName
& $compilerPath /nologo /target:exe /main:OverlaySmokeTests /platform:x64 /codepage:65001 "/out:$outputPath" @references @sources (Join-Path $PSScriptRoot 'OverlaySmokeTests.cs')
if ($LASTEXITCODE -ne 0) { throw 'Overlay smoke test compilation failed.' }
$previousLegacyFixture = $env:HEXGOD_TEST_LEGACY
try {
    $env:HEXGOD_TEST_LEGACY = '1'
    & $outputPath $rootPath
    if ($LASTEXITCODE -ne 0) { throw 'Legacy fixture overlay tests failed.' }
    Remove-Item Env:\HEXGOD_TEST_LEGACY
    & $outputPath $rootPath --production
    if ($LASTEXITCODE -ne 0) { throw 'ARAMGG production overlay tests failed.' }
} finally {
    if ($null -eq $previousLegacyFixture) { Remove-Item Env:\HEXGOD_TEST_LEGACY -ErrorAction SilentlyContinue }
    else { $env:HEXGOD_TEST_LEGACY = $previousLegacyFixture }
}
