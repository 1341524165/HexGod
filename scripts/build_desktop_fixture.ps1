$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Import-Module (Join-Path $PSScriptRoot 'DesktopBuild.psm1') -Force
$spec = Get-DesktopBuildSpec -ProjectRoot $projectRoot
$outputDir = Join-Path $projectRoot 'artifacts\tests\desktop-fixture'
New-Item -ItemType Directory -Path $outputDir -Force | Out-Null
& $spec.Compiler /nologo /target:winexe /platform:x64 /codepage:65001 "/win32manifest:$($spec.Manifest)" "/out:$outputDir\League of Legends.exe" /reference:System.dll /reference:System.Drawing.dll /reference:System.Windows.Forms.dll (Join-Path $projectRoot 'tests\desktop\DesktopGameFixture.cs')
if ($LASTEXITCODE -ne 0) { throw 'Fixture compilation failed.' }
