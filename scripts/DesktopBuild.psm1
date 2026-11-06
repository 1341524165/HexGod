function Get-DesktopBuildSpec {
    param([Parameter(Mandatory=$true)][string]$ProjectRoot)
    $framework = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319'
    $compiler = Join-Path $framework 'csc.exe'
    if (-not (Test-Path -LiteralPath $compiler)) { throw 'Windows .NET Framework 4.x compiler is required.' }
    $references = @('System.dll','System.Core.dll','System.Xml.dll','System.Xaml.dll','System.Web.Extensions.dll','System.Windows.Forms.dll','System.Drawing.dll') | ForEach-Object { '/reference:' + (Join-Path $framework $_) }
    $references += @('WindowsBase.dll','PresentationCore.dll','PresentationFramework.dll') | ForEach-Object { '/reference:' + (Join-Path (Join-Path $framework 'WPF') $_) }
    [PSCustomObject]@{
        Compiler=$compiler
        References=$references
        Sources=@(Get-ChildItem -LiteralPath (Join-Path $ProjectRoot 'src\desktop') -Recurse -File -Filter '*.cs' | Select-Object -ExpandProperty FullName)
        Manifest=(Join-Path $ProjectRoot 'src\desktop\app.manifest')
    }
}
function Update-HexgodDesktopShortcut {
    param([Parameter(Mandatory=$true)][string]$ProjectRoot)
    $root = (Resolve-Path -LiteralPath $ProjectRoot).ProviderPath
    $application = Join-Path $root 'hexgod.exe'
    if (-not (Test-Path -LiteralPath $application -PathType Leaf)) { throw 'Root executable is missing.' }
    $desktop = [Environment]::GetFolderPath('DesktopDirectory')
    if (-not $desktop -or -not (Test-Path -LiteralPath $desktop -PathType Container)) { throw 'Desktop directory is unavailable.' }
    $shortcutShell = New-Object -ComObject WScript.Shell
    $paths = @(Get-ChildItem -LiteralPath $desktop -File -Filter '*.lnk' | Where-Object {
        $link = $shortcutShell.CreateShortcut($_.FullName)
        [IO.Path]::GetFileName($link.TargetPath) -ieq 'hexgod.exe' -or $_.Name -ieq 'HexGod.lnk'
    } | Select-Object -ExpandProperty FullName)
    if ($paths.Count -eq 0) { $paths = @(Join-Path $desktop 'HexGod.lnk') }
    foreach ($path in $paths) {
        $link = $shortcutShell.CreateShortcut($path)
        $link.TargetPath = $application
        $link.WorkingDirectory = $root
        $link.IconLocation = $application + ',0'
        $link.Arguments = ''
        $link.Description = 'HexGod'
        $link.Save()
        Write-Output "Desktop shortcut: $path"
    }
}
Export-ModuleMember -Function Get-DesktopBuildSpec, Update-HexgodDesktopShortcut
