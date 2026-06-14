# Close all Codex windows before running this script.
$ErrorActionPreference = "Stop"

$codexRoot = Join-Path $env:APPDATA "Codex"
$webProfile = Join-Path $codexRoot "web\Codex\Default"

if (-not (Test-Path -LiteralPath $webProfile)) {
    Write-Host "Codex web profile was not found: $webProfile"
    exit 0
}

$running = Get-Process | Where-Object { $_.ProcessName -ieq "Codex" -or $_.ProcessName -ieq "codex" }
if ($running) {
    Write-Host "Codex is still running. Please close all Codex windows, then run this script again."
    $running | Select-Object ProcessName,Id,Path | Format-Table -AutoSize
    exit 1
}

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$backup = "$webProfile.bak-$stamp"

Rename-Item -LiteralPath $webProfile -NewName (Split-Path -Leaf $backup)
Write-Host "Backed up the old browser profile to:"
Write-Host $backup
Write-Host ""
Write-Host "Now start Codex again. It will create a fresh browser profile."
