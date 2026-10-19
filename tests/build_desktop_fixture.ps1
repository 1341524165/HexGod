$ErrorActionPreference = 'Stop'
$rootPath = Split-Path -Parent $PSScriptRoot
$outputDir = Join-Path $rootPath '.cache\desktop-fixture'
New-Item -ItemType Directory -Path $outputDir -Force | Out-Null
$frameworkPath = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319'
& (Join-Path $frameworkPath 'csc.exe') /nologo /target:winexe /platform:x64 /codepage:65001 "/win32manifest:$rootPath\app.manifest" "/out:$outputDir\League of Legends.exe" /reference:System.dll /reference:System.Drawing.dll /reference:System.Windows.Forms.dll (Join-Path $PSScriptRoot 'DesktopGameFixture.cs')
if ($LASTEXITCODE -ne 0) { throw 'Fixture compilation failed.' }
