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

try {
    Write-Log "check starting"

    $dirty = git status --porcelain 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Log "ERROR: git status failed (exit $LASTEXITCODE) -- skipping. Output: $dirty"
        exit 1
    }
    if ($dirty) {
        Write-Log "ERROR: local uncommitted changes present -- skipping auto-sync to avoid conflict. Run 'git status' on the machine to resolve, then this will resume automatically next cycle."
        exit 0
    }

    git fetch origin main --quiet 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Log "ERROR: git fetch origin main failed (exit $LASTEXITCODE) -- skipping"
        exit 1
    }

    $localHead = (git rev-parse HEAD).Trim()
    $remoteHead = (git rev-parse origin/main).Trim()

    if ($localHead -eq $remoteHead) {
        Write-Log "up to date ($($localHead.Substring(0,8)))"
        exit 0
    }

    Write-Log "new commits found: $($localHead.Substring(0,8)) -> $($remoteHead.Substring(0,8)) -- pulling"

    $pullOutput = git pull --ff-only origin main 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Log "ERROR: git pull --ff-only failed (exit $LASTEXITCODE) -- NOT restarting bot. Manual intervention needed (possible diverged history). Output: $pullOutput"
        exit 1
    }

    $newHead = (git rev-parse HEAD).Trim()
    Write-Log "pulled successfully, now at $($newHead.Substring(0,8)) -- restarting StockSage Bot task"

    schtasks /End /TN "StockSage Bot" 2>&1 | Out-Null
    Start-Sleep -Seconds 3
    schtasks /Run /TN "StockSage Bot" 2>&1 | Out-Null

    Write-Log "restart command issued"
}
catch {
    Write-Log "ERROR: unhandled exception -- $($_.Exception.Message)"
    exit 1
}
