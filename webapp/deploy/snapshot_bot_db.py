"""Copies the live bot DB to a read-only snapshot the webapp can safely
open, run every 10 minutes by the "StockSage Webapp Snapshot" scheduled
task (webapp/deploy/install_snapshot_task.ps1).

Standalone script -- NOT imported by the FastAPI app, has no dependency
on app.settings. Reads its own three env vars from webapp/deploy/.env
(see .env.example).

Safety:
- The source is opened read-only (file:...?mode=ro, same idiom as
  app/bot_db.py) and is only ever passed to sqlite3.Connection.backup(),
  which reads from it -- it is never written to.
- The copy is written to a fresh temp file, integrity-checked, and only
  then atomically swapped into place via os.replace() -- the webapp
  (itself read-only against the destination) never observes a
  half-written file.
- os.replace() is retried (PermissionError only) for a few seconds,
  since a momentarily locked/AV-scanned destination on Windows is a
  realistic transient failure, not a reason to leave a stale snapshot
  forever or crash the scheduled task loudly for no good reason.
- Every destination/log path's parent directory is created if missing
  (the outside-repo C:\\stocksage-deploy\\... tree may not exist yet on
  a fresh setup).
"""
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

STALE_TMP_MAX_AGE_SECONDS = 3600

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dotenv ships with the backend venv
    load_dotenv = None

DEPLOY_DIR = Path(__file__).resolve().parent
DEFAULT_DEST_DB_PATH = Path(r"C:\stocksage-deploy\snapshot\bot.db")
DEFAULT_LOG_PATH = Path(r"C:\stocksage-deploy\logs\snapshot.log")

REPLACE_MAX_ATTEMPTS = 10
REPLACE_RETRY_DELAY_SECONDS = 0.5


class SnapshotError(RuntimeError):
    pass


def load_config() -> tuple[Path, Path, Path]:
    if load_dotenv is not None:
        load_dotenv(DEPLOY_DIR / ".env", override=False)

    raw_source = os.environ.get("SNAPSHOT_SOURCE_DB_PATH", "").strip()
    if not raw_source:
        raise SnapshotError(
            "SNAPSHOT_SOURCE_DB_PATH is not set. Define it in webapp/deploy/.env "
            "(see .env.example)."
        )
    source = Path(raw_source).expanduser().resolve()

    raw_dest = os.environ.get("SNAPSHOT_DEST_DB_PATH", "").strip()
    dest = Path(raw_dest).expanduser().resolve() if raw_dest else DEFAULT_DEST_DB_PATH

    raw_log = os.environ.get("SNAPSHOT_LOG_PATH", "").strip()
    log_path = Path(raw_log).expanduser().resolve() if raw_log else DEFAULT_LOG_PATH

    return source, dest, log_path


def _log(log_path: Path, message: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(f"[{timestamp}] {message}\n")


def _integrity_check(path: Path) -> bool:
    conn = sqlite3.connect(str(path))
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
        return bool(row) and row[0] == "ok"
    finally:
        conn.close()


def _replace_with_retry(tmp_path: Path, dest: Path) -> None:
    last_error: PermissionError | None = None
    for attempt in range(REPLACE_MAX_ATTEMPTS):
        try:
            os.replace(tmp_path, dest)
            return
        except PermissionError as exc:
            last_error = exc
            if attempt < REPLACE_MAX_ATTEMPTS - 1:
                time.sleep(REPLACE_RETRY_DELAY_SECONDS)
    assert last_error is not None
    raise last_error


def _clean_stale_tmp_files(dest: Path, log_path: Path) -> None:
    """A tmp file left behind by a prior run that crashed or was killed
    before cleanup (e.g. os.replace never reached) would otherwise sit
    forever -- remove ones old enough that no in-flight run could still
    own them."""
    now = time.time()
    pattern = f".{dest.name}.tmp-*"
    for stale in dest.parent.glob(pattern):
        try:
            age = now - stale.stat().st_mtime
            if age > STALE_TMP_MAX_AGE_SECONDS:
                stale.unlink(missing_ok=True)
                _log(log_path, f"removed stale tmp file -- {stale} (age {age:.0f}s)")
        except OSError as exc:
            _log(log_path, f"WARNING: failed to clean stale tmp file {stale} -- {exc}")


def run_snapshot(source: Path, dest: Path, log_path: Path) -> int:
    dest.parent.mkdir(parents=True, exist_ok=True)
    _clean_stale_tmp_files(dest, log_path)
    tmp_path = dest.parent / f".{dest.name}.tmp-{os.getpid()}"

    source_conn: sqlite3.Connection | None = None
    tmp_conn: sqlite3.Connection | None = None
    try:
        source_uri = source.resolve().as_uri() + "?mode=ro"
        source_conn = sqlite3.connect(source_uri, uri=True, timeout=10.0)
        tmp_conn = sqlite3.connect(str(tmp_path))
        source_conn.backup(tmp_conn)
    except Exception as exc:
        if source_conn is not None:
            source_conn.close()
        if tmp_conn is not None:
            tmp_conn.close()
        tmp_path.unlink(missing_ok=True)
        _log(log_path, f"ERROR: backup failed -- {exc}")
        return 1

    tmp_conn.close()
    source_conn.close()

    if not _integrity_check(tmp_path):
        tmp_path.unlink(missing_ok=True)
        _log(log_path, "ERROR: integrity_check failed on the new copy -- keeping previous snapshot")
        return 1

    try:
        _replace_with_retry(tmp_path, dest)
    except PermissionError as exc:
        tmp_path.unlink(missing_ok=True)
        _log(log_path, f"ERROR: os.replace failed after {REPLACE_MAX_ATTEMPTS} attempts -- {exc}")
        return 1

    _log(log_path, f"snapshot ok -- {dest}")
    return 0


def main() -> int:
    try:
        source, dest, log_path = load_config()
    except SnapshotError as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        return 1
    return run_snapshot(source, dest, log_path)


if __name__ == "__main__":
    sys.exit(main())
