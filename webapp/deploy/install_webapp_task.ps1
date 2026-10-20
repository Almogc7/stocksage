# One-time setup: registers the "StockSage Webapp" scheduled task on
# THIS machine. Run this yourself, interactively, in an ELEVATED
# PowerShell window -- it prompts for your Windows account password to
# store the "run whether logged on or not" credential, same as the
# bot's own scripts\register_scheduled_tasks.ps1 (NOT touched by this
# script -- this is a separate, new file, registering a separate,
# distinctly-named task).
#
# Runs as the SAME Windows account the bot tasks already use
# ($env:COMPUTERNAME\$env:USERNAME) -- that account already owns the
# bot's file/DB permissions and its stored Task Scheduler credential,
# so reusing it avoids a second credential prompt and any cross-account
# file-permission mismatch on the shared repo checkout.
#
# Re-running is safe: an existing task with the same name is replaced
# (-Force).

$ErrorActionPreference = "Stop"

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Error "This script must run in an ELEVATED PowerShell session (right-click PowerShell -> Run as administrator). Nothing was registered."
    exit 1
}

$deployDir = $PSScriptRoot

$user = "$env:COMPUTERNAME\$env:USERNAME"
Write-Host "Registering 'StockSage Webapp' to run as $user (whether logged on or not)."
$securePassword = Read-Host -Prompt "Enter Windows password for $env:USERNAME" -AsSecureString
$password = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto(
    [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
)

try {
    $settings = New-ScheduledTaskSettingsSet `
        -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -RestartCount 999 `
        -RestartInterval (New-TimeSpan -Minutes 1) `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable

    $action = New-ScheduledTaskAction -Execute (Join-Path $deployDir "run_webapp_supervisor.cmd") -WorkingDirectory $deployDir
    $trigger = New-ScheduledTaskTrigger -AtStartup
    Register-ScheduledTask -TaskName "StockSage Webapp" -Action $action -Trigger $trigger `
        -Settings $settings -User $user -Password $password -RunLevel Limited -Force | Out-Null
    Write-Host "Registered: StockSage Webapp (at startup)"
}
finally {
    $password = $null
    [System.GC]::Collect()
}

Write-Host ""
Write-Host "Verifying registration against Get-ScheduledTask..."
$task = Get-ScheduledTask -TaskName "StockSage Webapp" -ErrorAction SilentlyContinue
if ($task) {
    Write-Host ("  [OK]      StockSage Webapp  (state: {0})" -f $task.State)
} else {
    Write-Error "VERIFICATION FAILED -- 'StockSage Webapp' is not registered"
    exit 1
}
Write-Host ""
Write-Host "Reminder: if your Windows password ever changes, re-run this script -- the stored credential breaks silently otherwise."
