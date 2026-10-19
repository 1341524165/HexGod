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
Export-ModuleMember -Function Get-DesktopBuildSpec
