param([string]$OutputName = 'hexgod.exe')
$ErrorActionPreference = 'Stop'
if ($OutputName -notmatch '^[A-Za-z0-9._-]+\.exe$') { throw 'Output must be an .exe filename.' }
$projectRoot = Split-Path -Parent $PSScriptRoot
Import-Module (Join-Path $PSScriptRoot 'DesktopBuild.psm1') -Force
$spec = Get-DesktopBuildSpec -ProjectRoot $projectRoot
$outputDir = Join-Path $projectRoot 'build\bin'
New-Item -ItemType Directory -Path $outputDir -Force | Out-Null
$output = Join-Path $outputDir $OutputName
& $spec.Compiler /nologo /target:winexe /platform:x64 /optimize+ /codepage:65001 "/out:$output" "/win32manifest:$($spec.Manifest)" @($spec.References) @($spec.Sources)
if ($LASTEXITCODE -ne 0) { throw 'Desktop compilation failed.' }
Copy-Item -LiteralPath (Join-Path $projectRoot 'src\desktop\App.config') -Destination ($output + '.config') -Force
Write-Output "Built: $output"
