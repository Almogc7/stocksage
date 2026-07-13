# Auto-sync StockSage from origin/main. Invoked every 15 minutes by the
# "StockSage Auto Sync" scheduled task (via scripts\run_auto_sync.cmd).
#
# Safety rules:
#  - never touches a working tree with uncommitted changes -- skips with a
#    clear log line instead of risking a conflicting merge.
#  - only fast-forwards (git pull --ff-only); a diverged history is left
#    alone and logged as an error for manual resolution.
#  - restarts the bot by ending+re-running the "StockSage Bot" scheduled
#    task, never by killing python.exe directly -- other python processes
#    (e.g. a manually-run dashboard.py) must not be touched.

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$logsDir = Join-Path $repoRoot "logs"
if (-not (Test-Path $logsDir)) {
    New-Item -ItemType Directory -Path $logsDir | Out-Null
}
$logFile = Join-Path $logsDir "auto_sync.log"

function Write-Log($message) {
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "[$timestamp] $message" | Out-File -FilePath $logFile -Append -Encoding utf8
}

# Run a native command with stderr merged into stdout by cmd.exe, BEFORE
# PowerShell sees it. Under $ErrorActionPreference="Stop", PS 5.1 wraps
# redirected native stderr lines in NativeCommandError records and promotes
# the first one to a terminating exception -- git's informational stderr
# (e.g. "From https://github.com/...") would abort the script mid-pull.
# Success/failure is determined ONLY by $LASTEXITCODE, which cmd /c
# propagates from the wrapped command.
function Invoke-Native([string]$commandLine) {
    $output = cmd /c "$commandLine 2>&1"
    return ($output -join "`n")
}

try {
    Write-Log "check starting"

    $dirty = Invoke-Native "git status --porcelain"
    if ($LASTEXITCODE -ne 0) {
        Write-Log "ERROR: git status failed (exit $LASTEXITCODE) -- skipping. Output: $dirty"
        exit 1
    }
    if ($dirty) {
        Write-Log "ERROR: local uncommitted changes present -- skipping auto-sync to avoid conflict. Run 'git status' on the machine to resolve, then this will resume automatically next cycle."
        exit 0
    }

    Invoke-Native "git fetch origin main --quiet" | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Log "ERROR: git fetch origin main failed (exit $LASTEXITCODE) -- skipping"
        exit 1
    }

    $localHead = (Invoke-Native "git rev-parse HEAD").Trim()
    $remoteHead = (Invoke-Native "git rev-parse origin/main").Trim()

    if ($localHead -eq $remoteHead) {
        Write-Log "up to date ($($localHead.Substring(0,8)))"
        exit 0
    }

    Write-Log "new commits found: $($localHead.Substring(0,8)) -> $($remoteHead.Substring(0,8)) -- pulling"

    $pullOutput = Invoke-Native "git pull --ff-only origin main"
    if ($LASTEXITCODE -ne 0) {
        Write-Log "ERROR: git pull --ff-only failed (exit $LASTEXITCODE) -- NOT restarting bot. Manual intervention needed (possible diverged history). Output: $pullOutput"
        exit 1
    }

    $newHead = (Invoke-Native "git rev-parse HEAD").Trim()
    Write-Log "pulled successfully, now at $($newHead.Substring(0,8)) -- restarting StockSage Bot task"

    Invoke-Native 'schtasks /End /TN "StockSage Bot"' | Out-Null
    Start-Sleep -Seconds 3
    Invoke-Native 'schtasks /Run /TN "StockSage Bot"' | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Log "ERROR: schtasks /Run 'StockSage Bot' failed (exit $LASTEXITCODE) -- bot may be stopped, start it manually"
        exit 1
    }

    Write-Log "restart command issued"
}
catch {
    Write-Log "ERROR: unhandled exception -- $($_.Exception.Message)"
    exit 1
}
