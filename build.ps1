param([string]$OutputName = 'hexgod.exe')
$ErrorActionPreference = 'Stop'
if ($OutputName -notmatch '^[A-Za-z0-9._-]+\.exe$') { throw '输出文件名必须是当前目录中的 .exe 文件。' }
$projectRoot = $PSScriptRoot
$frameworkPath = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319'
$compilerPath = Join-Path $frameworkPath 'csc.exe'
if (-not (Test-Path -LiteralPath $compilerPath)) { throw '需要 Windows .NET Framework 4.x。' }
$references = @('System.dll', 'System.Core.dll', 'System.Xaml.dll', 'System.Web.Extensions.dll', 'System.Windows.Forms.dll', 'System.Drawing.dll') | ForEach-Object { '/reference:' + (Join-Path $frameworkPath $_) }
$references += @('WindowsBase.dll', 'PresentationCore.dll', 'PresentationFramework.dll') | ForEach-Object { '/reference:' + (Join-Path (Join-Path $frameworkPath 'WPF') $_) }
$sources = Get-ChildItem -LiteralPath $projectRoot -Filter '*.cs' | Select-Object -ExpandProperty FullName
& $compilerPath /nologo /target:winexe /platform:x64 /optimize+ /codepage:65001 "/out:$projectRoot\$OutputName" "/win32manifest:$projectRoot\app.manifest" @references @sources
if ($LASTEXITCODE -ne 0) { throw '编译失败。' }
Copy-Item -LiteralPath (Join-Path $projectRoot 'App.config') -Destination (Join-Path $projectRoot ($OutputName + '.config')) -Force
Write-Output "Built: $projectRoot\$OutputName"
