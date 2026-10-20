# Unregisters the "StockSage Webapp Snapshot" scheduled task. Run
# elevated. No-op (with a message) if the task doesn't exist. Does not
# touch any bot task.

$ErrorActionPreference = "Stop"

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Error "This script must run in an ELEVATED PowerShell session (right-click PowerShell -> Run as administrator)."
    exit 1
}

$task = Get-ScheduledTask -TaskName "StockSage Webapp Snapshot" -ErrorAction SilentlyContinue
if (-not $task) {
    Write-Host "'StockSage Webapp Snapshot' is not registered -- nothing to do."
    exit 0
}

Unregister-ScheduledTask -TaskName "StockSage Webapp Snapshot" -Confirm:$false
Write-Host "Unregistered: StockSage Webapp Snapshot"
