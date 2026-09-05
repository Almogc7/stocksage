# One-time setup: registers all six StockSage scheduled tasks on THIS
# machine. Run this yourself, interactively, in a PowerShell window --
# it prompts for your Windows account password to store the "run whether
# logged on or not" credential. The password is decrypted only in-memory
# for the duration of this script and is never written to disk or logged.
#
# Re-running is safe: existing tasks with the same names are replaced
# (-Force). If your Windows password ever changes, re-run this script --
# all six tasks will otherwise silently fail to start.
#
# Tasks registered:
#   StockSage Bot                       - at startup, crash-restart loop (main.py)
#   StockSage Log Composite Signals     - daily 01:30 (observation-mode only,
#                                          see docs/composite_vs_legacy_tracking.md)
#   StockSage Populate Outcomes         - daily 02:00
#   StockSage Populate Composite Outcomes - daily 02:15 (observation-mode only)
#   StockSage Watchlist Evaluation      - daily 02:30 (scheduled-apply mode)
#   StockSage Auto Sync                 - every 15 minutes
#
# The 01:30/02:00/02:15/02:30 spacing is deliberate: composite signals are
# logged BEFORE the 02:30 watchlist evaluation reassigns ACTIVE-tier
# membership (so "ACTIVE tier" in that job means today's session, not
# tonight's promotions/demotions), and each nightly job gets a clean,
# non-overlapping window. Same fixed-IL-local-time convention (and the
# same US/IL DST-mismatch limitation, see CLAUDE.md) as the existing jobs.

$ErrorActionPreference = "Stop"

# Registering tasks with a stored password ("run whether user is logged on
# or not") requires an elevated session -- fail loudly up front instead of
# letting the first Register-ScheduledTask throw Access-is-denied.
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Error "This script must run in an ELEVATED PowerShell session (right-click PowerShell -> Run as administrator). Nothing was registered."
    exit 1
}

$repoRoot = Split-Path -Parent $PSScriptRoot

# Resolved dynamically (not hardcoded) so this check stays valid across
# machines, accounts, and Python versions -- run_bot_supervisor.cmd resolves
# python.exe the same way, via `where python` on PATH.
$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCommand) {
    Write-Warning "python.exe not found on PATH -- run_bot_supervisor.cmd resolves python the same way (via 'where python'), so double check python is installed and on PATH for this account."
} else {
    Write-Host "Found python.exe on PATH: $($pythonCommand.Source)"
}

$user = "$env:COMPUTERNAME\$env:USERNAME"
Write-Host "Registering scheduled tasks to run as $user (whether logged on or not)."
$securePassword = Read-Host -Prompt "Enter Windows password for $env:USERNAME" -AsSecureString
$password = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto(
    [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
)

try {
    # Common settings: never time-limit (main.py runs forever), don't
    # require AC power, restart on failure as a second safety net --
    # the primary crash-recovery mechanism is run_bot_supervisor.cmd's own
    # restart loop; this only protects the supervisor task itself.
    $settings = New-ScheduledTaskSettingsSet `
        -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -RestartCount 999 `
        -RestartInterval (New-TimeSpan -Minutes 1) `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable

    # --- 1. StockSage Bot (supervisor, at startup) ---
    $botAction = New-ScheduledTaskAction -Execute (Join-Path $repoRoot "scripts\run_bot_supervisor.cmd") -WorkingDirectory $repoRoot
    $botTrigger = New-ScheduledTaskTrigger -AtStartup
    Register-ScheduledTask -TaskName "StockSage Bot" -Action $botAction -Trigger $botTrigger `
        -Settings $settings -User $user -Password $password -RunLevel Limited -Force | Out-Null
    Write-Host "Registered: StockSage Bot (at startup)"

    # --- 2. StockSage Log Composite Signals (01:30 daily, observation-mode only) ---
    $compositeSignalsAction = New-ScheduledTaskAction -Execute (Join-Path $repoRoot "scripts\run_log_composite_signals.cmd") -WorkingDirectory $repoRoot
    $compositeSignalsTrigger = New-ScheduledTaskTrigger -Daily -At "01:30"
    Register-ScheduledTask -TaskName "StockSage Log Composite Signals" -Action $compositeSignalsAction -Trigger $compositeSignalsTrigger `
        -Settings $settings -User $user -Password $password -RunLevel Limited -Force | Out-Null
    Write-Host "Registered: StockSage Log Composite Signals (daily 01:30)"

    # --- 3. StockSage Populate Outcomes (02:00 daily) ---
    $outcomesAction = New-ScheduledTaskAction -Execute (Join-Path $repoRoot "scripts\run_populate_outcomes.cmd") -WorkingDirectory $repoRoot
    $outcomesTrigger = New-ScheduledTaskTrigger -Daily -At "02:00"
    Register-ScheduledTask -TaskName "StockSage Populate Outcomes" -Action $outcomesAction -Trigger $outcomesTrigger `
        -Settings $settings -User $user -Password $password -RunLevel Limited -Force | Out-Null
    Write-Host "Registered: StockSage Populate Outcomes (daily 02:00)"

    # --- 4. StockSage Populate Composite Outcomes (02:15 daily, observation-mode only) ---
    $compositeOutcomesAction = New-ScheduledTaskAction -Execute (Join-Path $repoRoot "scripts\run_populate_composite_signal_outcomes.cmd") -WorkingDirectory $repoRoot
    $compositeOutcomesTrigger = New-ScheduledTaskTrigger -Daily -At "02:15"
    Register-ScheduledTask -TaskName "StockSage Populate Composite Outcomes" -Action $compositeOutcomesAction -Trigger $compositeOutcomesTrigger `
        -Settings $settings -User $user -Password $password -RunLevel Limited -Force | Out-Null
    Write-Host "Registered: StockSage Populate Composite Outcomes (daily 02:15)"

    # --- 5. StockSage Watchlist Evaluation (02:30 daily, scheduled-apply) ---
    $evalAction = New-ScheduledTaskAction -Execute (Join-Path $repoRoot "scripts\run_watchlist_evaluation.cmd") -WorkingDirectory $repoRoot
    $evalTrigger = New-ScheduledTaskTrigger -Daily -At "02:30"
    Register-ScheduledTask -TaskName "StockSage Watchlist Evaluation" -Action $evalAction -Trigger $evalTrigger `
        -Settings $settings -User $user -Password $password -RunLevel Limited -Force | Out-Null
    Write-Host "Registered: StockSage Watchlist Evaluation (daily 02:30)"

    # --- 6. StockSage Auto Sync (every 15 minutes, indefinitely) ---
    $syncAction = New-ScheduledTaskAction -Execute (Join-Path $repoRoot "scripts\run_auto_sync.cmd") -WorkingDirectory $repoRoot
    # NOT [TimeSpan]::MaxValue -- Task Scheduler rejects its serialized form
    # (P99999999DT23H59M59S) as out of range. 10 years is effectively forever.
    $syncTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 15) -RepetitionDuration (New-TimeSpan -Days 3650)
    Register-ScheduledTask -TaskName "StockSage Auto Sync" -Action $syncAction -Trigger $syncTrigger `
        -Settings $settings -User $user -Password $password -RunLevel Limited -Force | Out-Null
    Write-Host "Registered: StockSage Auto Sync (every 15 min)"
}
finally {
    # Zero out the plaintext password as soon as we're done with it.
    $password = $null
    [System.GC]::Collect()
}

# Verify against Task Scheduler's actual state -- never trust our own
# happy path. A task the script "registered" but Get-ScheduledTask cannot
# see is a failure.
Write-Host ""
Write-Host "Verifying registration against Get-ScheduledTask..."
$expected = @("StockSage Bot", "StockSage Log Composite Signals", "StockSage Populate Outcomes", "StockSage Populate Composite Outcomes", "StockSage Watchlist Evaluation", "StockSage Auto Sync")
$missing = @()
foreach ($name in $expected) {
    $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if ($task) {
        Write-Host ("  [OK]      {0}  (state: {1})" -f $name, $task.State)
    } else {
        Write-Host ("  [MISSING] {0}" -f $name)
        $missing += $name
    }
}
if ($missing.Count -gt 0) {
    Write-Error "VERIFICATION FAILED -- not registered: $($missing -join ', ')"
    exit 1
}
Write-Host ""
Write-Host "All six tasks registered and verified."
Write-Host "Reminder: if your Windows password ever changes, re-run this script -- the stored credential breaks silently otherwise."
