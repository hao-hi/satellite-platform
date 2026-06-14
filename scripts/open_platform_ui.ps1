param(
    [string]$BindHost = "127.0.0.1",
    [int]$PreferredPort = 8765,
    [int]$MaxPort = 8775,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"

$workspace = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $workspace

function Find-PythonCommand {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        return @{
            FilePath = $python.Source
            Prefix = @()
        }
    }

    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        return @{
            FilePath = $py.Source
            Prefix = @("-3")
        }
    }

    $fallback = "D:\anaconda3\python.exe"
    if (Test-Path $fallback) {
        return @{
            FilePath = $fallback
            Prefix = @()
        }
    }

    throw "Python was not found. Please install Python or add it to PATH."
}
$python = Find-PythonCommand
$args = @()
$args += $python.Prefix
$args += @("scripts/open_platform_ui.py", "--host", $BindHost, "--preferred-port", "$PreferredPort", "--max-port", "$MaxPort")
if ($NoBrowser) {
    $args += "--no-browser"
}
& $python.FilePath @args
