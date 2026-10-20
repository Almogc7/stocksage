# One-time setup: registers the "StockSage Webapp Snapshot" scheduled
# task (every 10 minutes) on THIS machine. Run elevated -- same account,
# same credential-prompt pattern as install_webapp_task.ps1 and the
# bot's own register_scheduled_tasks.ps1 (not touched by this script).
#
# -MultipleInstances IgnoreNew: if one run overruns the 10-minute
# interval (e.g. a slow/locked os.replace retry), Task Scheduler skips
# the next trigger instead of running two copies concurrently against
# the same destination file.

$ErrorActionPreference = "Stop"

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Error "This script must run in an ELEVATED PowerShell session (right-click PowerShell -> Run as administrator). Nothing was registered."
    exit 1
}

$deployDir = $PSScriptRoot

$user = "$env:COMPUTERNAME\$env:USERNAME"
Write-Host "Registering 'StockSage Webapp Snapshot' to run as $user (whether logged on or not)."
$securePassword = Read-Host -Prompt "Enter Windows password for $env:USERNAME" -AsSecureString
$password = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto(
    [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
)

try {
    $settings = New-ScheduledTaskSettingsSet `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
        -MultipleInstances IgnoreNew `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable

    $action = New-ScheduledTaskAction -Execute (Join-Path $deployDir "run_snapshot_job.cmd") -WorkingDirectory $deployDir
    # Not [TimeSpan]::MaxValue -- Task Scheduler rejects its serialized
    # form as out of range. 10 years is effectively forever (same trick
    # as the bot's own "StockSage Auto Sync" task).
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 10) -RepetitionDuration (New-TimeSpan -Days 3650)
    Register-ScheduledTask -TaskName "StockSage Webapp Snapshot" -Action $action -Trigger $trigger `
        -Settings $settings -User $user -Password $password -RunLevel Limited -Force | Out-Null
    Write-Host "Registered: StockSage Webapp Snapshot (every 10 min)"
}
finally {
    $password = $null
    [System.GC]::Collect()
}

Write-Host ""
Write-Host "Verifying registration against Get-ScheduledTask..."
$task = Get-ScheduledTask -TaskName "StockSage Webapp Snapshot" -ErrorAction SilentlyContinue
if ($task) {
    Write-Host ("  [OK]      StockSage Webapp Snapshot  (state: {0})" -f $task.State)
} else {
    Write-Error "VERIFICATION FAILED -- 'StockSage Webapp Snapshot' is not registered"
    exit 1
}
Write-Host ""
Write-Host "Reminder: if your Windows password ever changes, re-run this script -- the stored credential breaks silently otherwise."
