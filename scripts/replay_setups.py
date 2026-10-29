#!/usr/bin/env python
"""Replay harness I/O (docs/SETUP_SPEC.md section 13, rollout step 5): reads
a historical universe, fetches/caches OHLCV, and writes replay results to
their own sqlite file. The actual simulation logic lives in the pure
setups/replay.py module this script imports -- nothing here computes a
setup or an outcome itself.

Usage examples (always an absolute path OUTSIDE this repo for --output and
--cache-dir -- the script refuses any path that resolves inside the repo
working tree):

    python scripts/replay_setups.py \\
        --db /home/user/readonly_copy.db \\
        --start 2022-01-01 --end 2025-01-01 \\
        --output /home/user/stocksage_replay/run1.db \\
        --counts-only

    python scripts/replay_setups.py \\
        --db /home/user/readonly_copy.db \\
        --start 2022-01-01 --end 2025-01-01 \\
        --output /home/user/stocksage_replay/run1.db \\
        --cache-dir /home/user/.stocksage_replay_cache \\
        --run-outcomes --slippage-bps 10

    python scripts/replay_setups.py \\
        --symbols-file /home/user/symbols.txt --limit-symbols 5 \\
        --start 2022-01-01 --end 2022-06-01 \\
        --output /home/user/stocksage_replay/trial.db \\
        --counts-only

Resume an interrupted run:

    python scripts/replay_setups.py --db ... --start ... --end ... \\
        --output /home/user/stocksage_replay/run1.db \\
        --counts-only --resume-run-id 1

Frozen full run against an already-populated cache, never touching the
network (fails loudly if anything is missing):

    python scripts/replay_setups.py --db /home/user/readonly_copy.db \\
        --start 2022-01-01 --end 2025-01-01 \\
        --output /home/user/stocksage_replay/run1.db \\
        --cache-dir /home/user/.stocksage_replay_cache \\
        --run-outcomes --cache-only
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from setups.frame_utils import drop_all_nan_ohlc_rows, normalize_frame  # noqa: E402
from setups.params import OutcomeParams, SetupParams, V1_OUTCOME_PARAMS, V1_PARAMS, V3_PARAMS  # noqa: E402
from setups.replay import (  # noqa: E402
    CandidateRow, build_regime_by_date, run_replay_core,
    run_replay_core_counts_only_both_bounds,
)
from setups.universe import get_setup_universe  # noqa: E402

PREREGISTRATION_FILE = "docs/PREREGISTRATION_SETUPS_V1.md"
REQUIREMENTS_FILE = "requirements.txt"
WARMUP_CALENDAR_DAYS = 400  # reasoned default, comfortably >= MIN_HISTORY_BARS sessions
RUN_OUTCOMES_FETCH_EXTENSION_DAYS = 70  # lets a late-scan-window setup still resolve
OPEN_AT_END_NOTE_SESSIONS = 35  # roughly timeout_sessions + validity_window_sessions
V1_THRESHOLDS = {"setups": 100, "symbols": 30, "years": 3}
DEFAULT_CACHE_DIR = Path.home() / ".stocksage_replay_cache"
UPPER, LOWER, NA_BOUND = "UPPER", "LOWER", "NA"


# ── git helpers (every one takes repo_root explicitly -- never re-derives
# its own notion of "the repo", so tests can point these at a disposable
# temporary git repository instead of this repo's real working tree) ────

def get_repo_root(cwd: Path) -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=str(cwd), capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "git is required to run this script (repo-root detection failed): "
            + result.stderr.strip()
        )
    return Path(result.stdout.strip())


def path_is_inside(path: Path, root: Path) -> bool:
    path = path.resolve()
    root = root.resolve()
    return path == root or root in path.parents


def check_preregistration_committed(repo_root: Path, filename: str = PREREGISTRATION_FILE) -> bool:
    result = subprocess.run(
        ["git", "cat-file", "-e", f"HEAD:{filename}"],
        cwd=str(repo_root), capture_output=True, text=True,
    )
    return result.returncode == 0


def check_working_tree_clean(repo_root: Path) -> tuple[bool, str]:
    result = subprocess.run(
        ["git", "status", "--porcelain"], cwd=str(repo_root), capture_output=True, text=True,
    )
    return result.stdout.strip() == "", result.stdout


def get_git_commit(repo_root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(repo_root), capture_output=True, text=True,
    )
    return result.stdout.strip()


def get_committed_file_sha256(repo_root: Path, filename: str) -> str:
    result = subprocess.run(
        ["git", "show", f"HEAD:{filename}"], cwd=str(repo_root), capture_output=True,
    )
    return hashlib.sha256(result.stdout).hexdigest()


def check_library_versions(requirements_path: Path, version_lookup=importlib.metadata.version) -> dict:
    """Returns {package: (pinned, installed)} for every mismatch.
    version_lookup is injectable so tests can simulate a mismatch without
    a real broken environment."""
    mismatches = {}
    for line in requirements_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, _, pinned = line.partition("==")
        name, pinned = name.strip(), pinned.strip()
        if not name:
            continue
        try:
            installed = version_lookup(name)
        except Exception as exc:
            installed = f"NOT INSTALLED ({exc})"
        if installed != pinned:
            mismatches[name] = (pinned, installed)
    return mismatches


def all_library_versions(requirements_path: Path, version_lookup=importlib.metadata.version) -> dict:
    versions = {}
    for line in requirements_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name = line.partition("==")[0].strip()
        if name:
            try:
                versions[name] = version_lookup(name)
            except Exception as exc:
                versions[name] = f"NOT INSTALLED ({exc})"
    return versions


# ── Output/cache path guards ─────────────────────────────────────────────

def resolve_guarded_path(raw_path: str, repo_root: Path, label: str) -> Path:
    path = Path(raw_path).expanduser().resolve()
    if path_is_inside(path, repo_root):
        raise RuntimeError(f"{label} must not be inside the repo working tree: {path}")
    return path


# ── Universe ──────────────────────────────────────────────────────────────

def load_universe_from_db(db_path: Path) -> list[str]:
    uri = f"file:{db_path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        result = get_setup_universe(conn)
    finally:
        conn.close()
    return sorted(set(result.included))


def load_universe_from_file(symbols_file: Path) -> list[str]:
    symbols = []
    for line in symbols_file.read_text().splitlines():
        line = line.strip()
        if line:
            symbols.append(line.upper())
    return sorted(set(symbols))


def universe_hash(symbols: list[str]) -> str:
    return hashlib.sha256("\n".join(sorted(symbols)).encode()).hexdigest()


# ── Chunked downloader (I/O-level; independently implemented and tested,
# not importing data/market_data_validator.MarketDataClient, per section
# 13's own "bootstrap/fetch infrastructure is copied in with its own
# tests" rule) ────────────────────────────────────────────────────────────

def default_downloader(symbols: list[str], start: date, end: date) -> dict:
    """Real yfinance wrapper. Thin by design: group_by ticker,
    normalize via setups.frame_utils.normalize_frame, None for a symbol
    that came back empty. Never raises -- a whole-chunk failure here is
    caught by the retry loop in fetch_history_chunked()."""
    import yfinance as yf
    raw = yf.download(
        symbols, start=start, end=end, auto_adjust=True,
        group_by="ticker", progress=False,
    )
    return normalize_multi_ticker_frame(raw, symbols)


def normalize_multi_ticker_frame(raw: pd.DataFrame, symbols: list[str]) -> dict:
    """Splits yfinance's multi-ticker download() output (MultiIndex
    columns, level 0 = ticker for group_by='ticker') into one normalized
    frame per symbol. None for a symbol with no data at all. A single
    symbol's download() returns plain (non-MultiIndex) columns -- handled
    too, for the len(symbols)==1 case."""
    result = {}
    is_multi = isinstance(raw.columns, pd.MultiIndex)
    for symbol in symbols:
        try:
            if is_multi:
                if symbol not in raw.columns.get_level_values(0):
                    result[symbol] = None
                    continue
                sub = raw[symbol]
            else:
                sub = raw
            sub = normalize_frame(sub)
            missing = {"open", "high", "low", "close"} - set(sub.columns)
            if missing:
                result[symbol] = None
                continue
            # Drop yfinance's all-NaN padding rows (a late-listed symbol
            # gets one for every calendar date before it existed, for the
            # whole requested range) BEFORE the emptiness check below --
            # a symbol with genuinely no real data at all (every row was
            # padding) correctly becomes None here, same as before.
            sub = drop_all_nan_ohlc_rows(sub)
            if sub.empty:
                result[symbol] = None
            else:
                result[symbol] = sub
        except Exception:
            result[symbol] = None
    return result


def fetch_history_chunked(
    symbols: list[str], start: date, end: date,
    chunk_size: int = 50, max_retries: int = 3, downloader=default_downloader,
) -> dict:
    """Chunked fetch with retries, degrading to a per-symbol re-fetch for
    any symbol missing from an otherwise-successful chunk, or for every
    symbol in a chunk whose call failed outright after all retries.
    Never raises on a partial chunk failure."""
    results: dict = {}
    for i in range(0, len(symbols), chunk_size):
        chunk = symbols[i:i + chunk_size]
        chunk_result = None
        for _ in range(max_retries):
            try:
                chunk_result = downloader(chunk, start, end)
                break
            except Exception:
                chunk_result = None
        if chunk_result is None:
            chunk_result = {}
        for symbol in chunk:
            if chunk_result.get(symbol) is not None:
                results[symbol] = chunk_result[symbol]
                continue
            retried = None
            for _ in range(max_retries):
                try:
                    one = downloader([symbol], start, end)
                    if one.get(symbol) is not None:
                        retried = one[symbol]
                        break
                except Exception:
                    retried = None
            results[symbol] = retried
    return results


# ── Raw-data cache (per symbol; a sidecar .json records the REQUESTED
# range, not the data's own date bounds -- a hit means the cached fetch
# already covered [start, end], regardless of where the symbol's actual
# first/last bar happens to fall within it. What makes a run reproducible
# despite auto_adjust=True retroactively rebasing historical prices on
# later ex-dividend events.) ─────────────────────────────────────────────

def cache_file(cache_dir: Path, symbol: str) -> Path:
    return cache_dir / f"{symbol}.csv"


def cache_sidecar_file(cache_dir: Path, symbol: str) -> Path:
    return cache_dir / f"{symbol}.json"


def load_from_cache(cache_dir: Path, symbol: str, start: date, end: date) -> pd.DataFrame | None:
    data_path = cache_file(cache_dir, symbol)
    sidecar_path = cache_sidecar_file(cache_dir, symbol)
    if not data_path.exists() or not sidecar_path.exists():
        return None
    try:
        meta = json.loads(sidecar_path.read_text())
        requested_start = date.fromisoformat(meta["requested_start"])
        requested_end = date.fromisoformat(meta["requested_end"])
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return None
    if not (requested_start <= start and requested_end >= end):
        return None  # the cached fetch didn't cover what's being asked for now
    df = pd.read_csv(data_path, index_col=0, parse_dates=True)
    # Clean on load, never on save -- the cache file's bytes are left
    # exactly as fetched (reproducible, inspectable), but a cache file
    # written before this fix (or one that otherwise still has yfinance's
    # all-NaN padding rows baked in) must still come back clean here.
    return drop_all_nan_ohlc_rows(df)


def save_to_cache(cache_dir: Path, symbol: str, df: pd.DataFrame, start: date, end: date) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(cache_file(cache_dir, symbol))
    meta = {
        "requested_start": str(start), "requested_end": str(end),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "sha256": hashlib.sha256(df.to_csv().encode()).hexdigest(),
    }
    cache_sidecar_file(cache_dir, symbol).write_text(json.dumps(meta))


def fetch_universe_history(
    symbols: list[str], start: date, end: date, cache_dir: Path,
    chunk_size: int = 50, max_retries: int = 3, downloader=default_downloader,
    cache_only: bool = False,
) -> dict:
    """Per-symbol cache check first (by requested range, see above); only
    the symbols missing/stale in the cache are actually fetched (chunked).
    cache_only=True never downloads -- raises RuntimeError listing every
    symbol missing from the cache instead."""
    result: dict = {}
    to_fetch = []
    for symbol in symbols:
        cached = load_from_cache(cache_dir, symbol, start, end)
        if cached is not None:
            result[symbol] = cached
        else:
            to_fetch.append(symbol)
    if to_fetch:
        if cache_only:
            raise RuntimeError(
                f"--cache-only: missing from cache for [{start}, {end}]: {sorted(to_fetch)}"
            )
        fetched = fetch_history_chunked(to_fetch, start, end, chunk_size, max_retries, downloader)
        for symbol, df in fetched.items():
            if df is not None:
                save_to_cache(cache_dir, symbol, df, start, end)
            result[symbol] = df
    return result


# ── Scan dates / fetch-range extension ───────────────────────────────────

def build_scan_dates(spy_df: pd.DataFrame, start: date, end: date) -> list[date]:
    """SPY's own index IS the trading calendar for this replay -- its
    dates between start and end inclusive, not a separate calendar walk
    that could drift from what was actually fetched."""
    dates = []
    for ts in spy_df.index:
        d = ts.date() if hasattr(ts, "date") else ts
        if start <= d <= end:
            dates.append(d)
    return sorted(dates)


def resolve_fetch_end(end: date, mode: str, today: date | None = None) -> date:
    """--run-outcomes fetches further than --end so a setup scanned near
    the end of the window can still resolve; --counts-only never needs
    more than the scan window itself (it never looks at outcomes)."""
    if mode != "run_outcomes":
        return end
    today = today or date.today()
    yesterday = today - timedelta(days=1)
    return min(end + timedelta(days=RUN_OUTCOMES_FETCH_EXTENSION_DAYS), yesterday)


# ── Results DB schema ─────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS replay_runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    mode TEXT NOT NULL,
    variants_json TEXT NOT NULL,
    git_commit TEXT,
    params_json TEXT NOT NULL,
    outcome_params_json TEXT NOT NULL,
    universe_hash TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    fetch_end_date TEXT NOT NULL,
    library_versions_json TEXT,
    preregistration_file_hash TEXT,
    status TEXT NOT NULL DEFAULT 'started',
    workers INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS replay_candidates (
    run_id INTEGER NOT NULL,
    variant TEXT NOT NULL,
    bound TEXT NOT NULL,
    symbol TEXT NOT NULL,
    scan_date TEXT NOT NULL,
    regime TEXT,
    accepted INTEGER NOT NULL,
    reason_code TEXT,
    setup_identity TEXT,
    sh_date TEXT, sh_price REAL, sl_date TEXT, sl_price REAL,
    trigger_price REAL, stop REAL, target REAL, risk REAL, rr REAL, atr REAL,
    validity_end TEXT, target_source TEXT,
    fill_date TEXT, fill_price REAL, exit_date TEXT, exit_price REAL,
    first_barrier_hit TEXT, r_multiple REAL, mae_r REAL, mfe_r REAL,
    sessions_held INTEGER, resolved_date TEXT,
    UNIQUE(run_id, variant, bound, symbol, scan_date)
);
CREATE TABLE IF NOT EXISTS replay_data_manifest (
    run_id INTEGER NOT NULL, symbol TEXT NOT NULL, rows INTEGER,
    first_date TEXT, last_date TEXT, sha256 TEXT,
    UNIQUE(run_id, symbol)
);
CREATE TABLE IF NOT EXISTS replay_progress (
    run_id INTEGER NOT NULL, symbol TEXT NOT NULL, status TEXT NOT NULL,
    completed_at TEXT, PRIMARY KEY (run_id, symbol)
);
"""


def open_results_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    _check_schema_compatible(conn)
    conn.executescript(_SCHEMA)
    _migrate_replay_runs_workers_column(conn)
    conn.commit()
    return conn


def _migrate_replay_runs_workers_column(conn: sqlite3.Connection) -> None:
    """A results DB created before --workers existed has a replay_runs
    table without the workers column -- CREATE TABLE IF NOT EXISTS never
    retroactively alters an existing table, so add it here. Additive and
    safe (matches db/database.py's migrate_db() convention): existing rows
    default to workers=1, the same single-process behavior they actually
    ran with."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(replay_runs)").fetchall()}
    if columns and "workers" not in columns:
        conn.execute("ALTER TABLE replay_runs ADD COLUMN workers INTEGER NOT NULL DEFAULT 1")


def _check_schema_compatible(conn: sqlite3.Connection) -> None:
    """A results DB from before target_source existed has a
    replay_candidates table that CREATE TABLE IF NOT EXISTS will never
    retroactively alter -- caught here, at open time, with a clear
    message, rather than failing obscurely on the first insert."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(replay_candidates)").fetchall()}
    if columns and "target_source" not in columns:
        raise RuntimeError(
            "This results DB was created by an older version of this script and "
            "is missing the replay_candidates.target_source column; use a new --output path."
        )


def _s(value):
    return str(value) if value is not None else None


def _row_to_db_tuple(run_id: int, bound: str, row: CandidateRow) -> tuple:
    d = asdict(row)
    identity = "|".join(str(x) for x in d["setup_identity"]) if d["setup_identity"] else None
    return (
        run_id, d["variant"], bound, d["symbol"], str(d["scan_date"]), d["regime"],
        1 if d["accepted"] else 0, d["reason_code"], identity,
        _s(d["sh_date"]), d["sh_price"], _s(d["sl_date"]), d["sl_price"],
        d["trigger"], d["stop"], d["target"], d["risk"], d["rr"], d["atr"],
        _s(d["validity_end"]), d["target_source"],
        _s(d["fill_date"]), d["fill_price"], _s(d["exit_date"]), d["exit_price"],
        d["first_barrier_hit"], d["r_multiple"], d["mae_r"], d["mfe_r"],
        d["sessions_held"], _s(d["resolved_date"]),
    )


def write_candidate_rows(
    conn: sqlite3.Connection, run_id: int, bound: str, rows: list[CandidateRow], commit: bool = True,
) -> None:
    conn.executemany(
        """INSERT OR REPLACE INTO replay_candidates (
            run_id, variant, bound, symbol, scan_date, regime, accepted, reason_code,
            setup_identity, sh_date, sh_price, sl_date, sl_price, trigger_price,
            stop, target, risk, rr, atr, validity_end, target_source, fill_date, fill_price,
            exit_date, exit_price, first_barrier_hit, r_multiple, mae_r, mfe_r,
            sessions_held, resolved_date
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [_row_to_db_tuple(run_id, bound, row) for row in rows],
    )
    if commit:
        conn.commit()


def _manifest_fields_from_frame(df: pd.DataFrame | None) -> dict:
    """Extracted from write_manifest() so a worker process (which can't
    share a sqlite3.Connection across the process boundary) can compute the
    same fields and hand them back to the main process for the actual
    INSERT via write_manifest_fields()."""
    if df is None or df.empty:
        return {"rows": 0, "first_date": None, "last_date": None, "sha256": None}
    return {
        "rows": len(df),
        "first_date": str(df.index.min().date()),
        "last_date": str(df.index.max().date()),
        "sha256": hashlib.sha256(df.to_csv().encode()).hexdigest(),
    }


def write_manifest_fields(
    conn: sqlite3.Connection, run_id: int, symbol: str, fields: dict, commit: bool = True,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO replay_data_manifest (run_id, symbol, rows, first_date, last_date, sha256) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (run_id, symbol, fields["rows"], fields["first_date"], fields["last_date"], fields["sha256"]),
    )
    if commit:
        conn.commit()


def write_manifest(
    conn: sqlite3.Connection, run_id: int, symbol: str, df: pd.DataFrame | None, commit: bool = True,
) -> None:
    write_manifest_fields(conn, run_id, symbol, _manifest_fields_from_frame(df), commit=commit)


def mark_symbol_done(conn: sqlite3.Connection, run_id: int, symbol: str, commit: bool = True) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO replay_progress (run_id, symbol, status, completed_at) VALUES (?, ?, 'done', ?)",
        (run_id, symbol, datetime.now(timezone.utc).isoformat()),
    )
    if commit:
        conn.commit()


def get_done_symbols(conn: sqlite3.Connection, run_id: int) -> set:
    rows = conn.execute(
        "SELECT symbol FROM replay_progress WHERE run_id = ? AND status = 'done'", (run_id,)
    ).fetchall()
    return {r["symbol"] for r in rows}


# ── Run header / resume ──────────────────────────────────────────────────

def create_run(
    conn: sqlite3.Connection, mode: str, variants: list[str], git_commit: str | None,
    params_by_variant: dict, outcome_params: OutcomeParams, u_hash: str,
    start: date, end: date, fetch_end: date, library_versions: dict,
    preregistration_hash: str | None, workers: int = 1,
) -> int:
    cur = conn.execute(
        """INSERT INTO replay_runs (
            started_at, mode, variants_json, git_commit, params_json,
            outcome_params_json, universe_hash, start_date, end_date, fetch_end_date,
            library_versions_json, preregistration_file_hash, status, workers
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'started',?)""",
        (
            datetime.now(timezone.utc).isoformat(), mode, json.dumps(variants), git_commit,
            json.dumps({v: asdict(p) for v, p in params_by_variant.items()}),
            json.dumps(asdict(outcome_params)), u_hash,
            str(start), str(end), str(fetch_end), json.dumps(library_versions), preregistration_hash,
            workers,
        ),
    )
    conn.commit()
    return cur.lastrowid


def resume_run(
    conn: sqlite3.Connection, run_id: int, mode: str, variants: list[str],
    params_by_variant: dict, outcome_params: OutcomeParams, u_hash: str,
    start: date, end: date,
) -> dict:
    row = conn.execute("SELECT * FROM replay_runs WHERE run_id = ?", (run_id,)).fetchone()
    if row is None:
        raise RuntimeError(f"--resume-run-id {run_id}: no such run in this results DB")

    def _refuse(label, expected, actual):
        raise RuntimeError(
            f"--resume-run-id {run_id}: {label} differs from the original run "
            f"(was {actual!r}, now {expected!r}) -- start a fresh run instead."
        )

    if row["universe_hash"] != u_hash:
        _refuse("universe", u_hash, row["universe_hash"])
    if row["start_date"] != str(start) or row["end_date"] != str(end):
        _refuse("start/end date", (str(start), str(end)), (row["start_date"], row["end_date"]))
    if row["mode"] != mode:
        _refuse("mode", mode, row["mode"])
    if json.loads(row["variants_json"]) != variants:
        _refuse("variants", variants, json.loads(row["variants_json"]))
    expected_params = {v: asdict(p) for v, p in params_by_variant.items()}
    if json.loads(row["params_json"]) != expected_params:
        _refuse("params (including V2/V3)", expected_params, json.loads(row["params_json"]))
    if json.loads(row["outcome_params_json"]) != asdict(outcome_params):
        _refuse("outcome params / slippage", asdict(outcome_params), json.loads(row["outcome_params_json"]))
    # NOTE: args.workers is intentionally excluded from this comparison --
    # it is a performance knob (process-pool width), not a semantics knob.
    # A resumed run may legally use a different --workers value than the
    # run it's resuming; see docs/SETUP_SPEC.md section 14.
    return dict(row)


def complete_run(conn: sqlite3.Connection, run_id: int) -> None:
    conn.execute(
        "UPDATE replay_runs SET completed_at = ?, status = 'success' WHERE run_id = ?",
        (datetime.now(timezone.utc).isoformat(), run_id),
    )
    conn.commit()


# ── Per-symbol processing ────────────────────────────────────────────────

def process_symbol(
    symbol: str, df: pd.DataFrame | None, spy_df: pd.DataFrame, scan_dates: list[date],
    params: SetupParams, outcome_params: OutcomeParams, compute_outcomes: bool,
    counts_only_block_extra_sessions: int, variant: str, regime_by_date: dict | None = None,
) -> list[CandidateRow]:
    if df is None:
        return [
            CandidateRow(symbol=symbol, scan_date=d, regime="UNKNOWN", accepted=False,
                         reason_code="DATA_FETCH_FAILED", variant=variant)
            for d in scan_dates
        ]
    return run_replay_core(
        {symbol: df}, spy_df, scan_dates, params=params, outcome_params=outcome_params,
        compute_outcomes=compute_outcomes,
        counts_only_block_extra_sessions=counts_only_block_extra_sessions,
        variant=variant, regime_by_date=regime_by_date,
    )


def process_symbol_counts_only_both_bounds(
    symbol: str, df: pd.DataFrame | None, spy_df: pd.DataFrame, scan_dates: list[date],
    params: SetupParams, outcome_params: OutcomeParams, variant: str,
    regime_by_date: dict | None = None,
) -> tuple[list[CandidateRow], list[CandidateRow]]:
    """Counts-only UPPER+LOWER for one symbol, computing
    evaluate_candidate()/define_setup() once instead of once per bound.
    Used by both the sequential main loop and _worker_process_symbol_variants
    (so the --workers speed-up and this speed-up compose automatically)."""
    if df is None:
        rows = [
            CandidateRow(symbol=symbol, scan_date=d, regime="UNKNOWN", accepted=False,
                         reason_code="DATA_FETCH_FAILED", variant=variant)
            for d in scan_dates
        ]
        return rows, [replace(r) for r in rows]
    return run_replay_core_counts_only_both_bounds(
        {symbol: df}, spy_df, scan_dates, params=params, outcome_params=outcome_params,
        variant=variant, regime_by_date=regime_by_date,
    )


# ── Counts-only summary (SQL aggregation over the whole run, not an
# in-memory list -- resume-safe: a report taken after a crash + resume
# aggregates every row now in the table, from before AND after the
# interruption, identically to an uninterrupted run) ─────────────────────

def _summarize_tuples(rows: list[tuple]) -> dict:
    """rows: (scan_date: date, symbol: str, accepted: bool, reason_code: str | None)."""
    accepted = [(d, s) for d, s, a, _ in rows if a]
    per_year: dict = {}
    for d, s in accepted:
        per_year.setdefault(d.year, {"setups": 0, "symbols": set()})
        per_year[d.year]["setups"] += 1
        per_year[d.year]["symbols"].add(s)
    reason_per_year: dict = {}
    for d, _s_, _a, r in rows:
        if r:
            reason_per_year.setdefault(d.year, {}).setdefault(r, 0)
            reason_per_year[d.year][r] += 1
    total_setups = len(accepted)
    total_symbols = len({s for _, s in accepted})
    total_years = len({d.year for d, _ in accepted})
    thresholds_met = (
        total_setups >= V1_THRESHOLDS["setups"]
        and total_symbols >= V1_THRESHOLDS["symbols"]
        and total_years >= V1_THRESHOLDS["years"]
    )
    return {
        "total_setups": total_setups, "total_symbols": total_symbols,
        "total_years": total_years, "thresholds_met": thresholds_met,
        "per_year": {y: {"setups": v["setups"], "symbols": len(v["symbols"])} for y, v in per_year.items()},
        "reason_per_year": reason_per_year,
    }


def summarize_counts_from_db(conn: sqlite3.Connection, run_id: int, variant: str, bound: str) -> dict:
    cursor = conn.execute(
        "SELECT scan_date, symbol, accepted, reason_code FROM replay_candidates "
        "WHERE run_id = ? AND variant = ? AND bound = ?",
        (run_id, variant, bound),
    )
    rows = [(date.fromisoformat(r[0]), r[1], bool(r[2]), r[3]) for r in cursor.fetchall()]
    return _summarize_tuples(rows)


def print_counts_only_report(variant: str, upper: dict, lower: dict) -> None:
    label = f"{variant} (PRIMARY)" if variant == "V3" else variant
    print(f"--- Variant {label} (outcome-blind) ---")
    print(f"  UPPER bound (blocked until validity_end): "
          f"{upper['total_setups']} setups, {upper['total_symbols']} symbols, {upper['total_years']} years")
    print(f"  LOWER bound (blocked until validity_end + timeout_sessions): "
          f"{lower['total_setups']} setups, {lower['total_symbols']} symbols, {lower['total_years']} years")
    print(f"  Thresholds (100 setups / 30 symbols / 3 years) MET: {lower['thresholds_met']} "
          f"(judged on the LOWER bound only)")
    for year in sorted(lower["reason_per_year"]):
        print(f"  {year} reason-code breakdown: {lower['reason_per_year'][year]}")
    if variant == "V3":
        print("  V4 (highest-high-252 target) is NOT implemented -- would need a new "
              "TARGET_MODE in setups/definition.py / setups/params.py.")


# ── Process pool (--workers) ─────────────────────────────────────────────
# Each worker receives ONLY JSON-primitive data -- no DataFrame crosses the
# process boundary inbound -- and loads its own symbol/SPY frames straight
# from the on-disk cache via load_from_cache(), the same function the main
# process uses. Workers never fetch (no downloader parameter exists to call)
# and never write to the results DB; the main process does all writes, one
# transaction per symbol, in sorted-symbol order, exactly as the sequential
# path does.

def _worker_process_symbol_variants(task: dict) -> dict:
    """Top-level (module-level) function -- required for Windows `spawn`
    safety: ProcessPoolExecutor must be able to pickle a reference to this
    function by import path, which a closure or lambda cannot provide."""
    symbol = task["symbol"]
    cache_dir = Path(task["cache_dir"])
    warmup_start = date.fromisoformat(task["warmup_start"])
    fetch_end = date.fromisoformat(task["fetch_end"])
    scan_dates = [date.fromisoformat(d) for d in task["scan_dates"]]
    mode = task["mode"]
    variants = task["variants"]
    params_by_variant = {v: SetupParams(**p) for v, p in task["params_by_variant"].items()}
    outcome_params = OutcomeParams(**task["outcome_params"])

    df = load_from_cache(cache_dir, symbol, warmup_start, fetch_end)
    # SPY is cached identically to any other symbol (same cache_file()/
    # cache_sidecar_file() functions, symbol="SPY") -- not a separate path.
    spy_df = load_from_cache(cache_dir, "SPY", warmup_start, fetch_end)
    # regime_by_date is a pure function of (spy_df, scan_dates) only
    # (setups/replay.py build_regime_by_date) -- recomputed here per worker
    # rather than pickled through, since that's cheaper than pickling a
    # DataFrame or a dict of comparable size across the process boundary.
    regime_by_date = (
        build_regime_by_date(spy_df, scan_dates) if spy_df is not None
        else {d: "UNKNOWN" for d in scan_dates}
    )

    manifest_fields = _manifest_fields_from_frame(df)
    rows_by_variant_bound: dict[str, dict[str, list]] = {}
    for variant in variants:
        params = params_by_variant[variant]
        if mode == "run_outcomes":
            rows = process_symbol(symbol, df, spy_df, scan_dates, params, outcome_params,
                                   True, 0, variant, regime_by_date=regime_by_date)
            rows_by_variant_bound[variant] = {NA_BOUND: rows}
        else:
            upper_rows, lower_rows = process_symbol_counts_only_both_bounds(
                symbol, df, spy_df, scan_dates, params, outcome_params, variant,
                regime_by_date=regime_by_date,
            )
            rows_by_variant_bound[variant] = {UPPER: upper_rows, LOWER: lower_rows}

    return {"symbol": symbol, "manifest_fields": manifest_fields, "rows": rows_by_variant_bound}


def _flush_symbol_result(conn: sqlite3.Connection, run_id: int, symbol: str, result: dict) -> None:
    write_manifest_fields(conn, run_id, symbol, result["manifest_fields"], commit=False)
    for _variant, by_bound in result["rows"].items():
        for bound, rows in by_bound.items():
            write_candidate_rows(conn, run_id, bound, rows, commit=False)
    mark_symbol_done(conn, run_id, symbol, commit=False)
    conn.commit()  # one transaction for this symbol's candidates + manifest + progress


def _run_pool(
    conn: sqlite3.Connection, run_id: int, pending: list, cache_dir: Path,
    warmup_start: date, fetch_end: date, scan_dates: list, mode: str, variants: list,
    params_by_variant: dict, outcome_params: OutcomeParams, workers: int, executor_factory,
) -> None:
    scan_dates_iso = [str(d) for d in scan_dates]
    tasks = {
        symbol: {
            "symbol": symbol, "cache_dir": str(cache_dir),
            "warmup_start": str(warmup_start), "fetch_end": str(fetch_end),
            "scan_dates": scan_dates_iso, "mode": mode, "variants": variants,
            "params_by_variant": {v: asdict(p) for v, p in params_by_variant.items()},
            "outcome_params": asdict(outcome_params),
        }
        for symbol in pending
    }

    results: dict[str, dict] = {}
    next_idx = 0
    run_start_time = time.time()
    last_flush_time = run_start_time

    pool = executor_factory(max_workers=workers)
    try:
        futures = {pool.submit(_worker_process_symbol_variants, tasks[s]): s for s in pending}
        for future in as_completed(futures):
            symbol = futures[future]
            results[symbol] = future.result()  # re-raises a worker's exception HERE, in the main process

            # Flush only the contiguous sorted-order prefix that's ready --
            # preserves the exact sorted-symbol write order of the
            # sequential path even though completion is out of order
            # across workers.
            while next_idx < len(pending) and pending[next_idx] in results:
                sym = pending[next_idx]
                _flush_symbol_result(conn, run_id, sym, results.pop(sym))
                next_idx += 1
                now = time.time()
                elapsed = now - last_flush_time
                last_flush_time = now
                avg = (now - run_start_time) / next_idx
                remaining = len(pending) - next_idx
                print(f"[{next_idx}/{len(pending)}] {sym} done in {elapsed:.2f}s "
                      f"(avg {avg:.2f}s/symbol, ~{avg * remaining:.0f}s remaining)")
    except BaseException:
        pool.shutdown(wait=False, cancel_futures=True)
        raise
    finally:
        pool.shutdown(wait=True)


# ── CLI ───────────────────────────────────────────────────────────────────

def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Replay harness I/O for the setups system (docs/SETUP_SPEC.md section 13).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Example (always an ABSOLUTE path outside this repo for --output/--cache-dir):\n"
            "  python scripts/replay_setups.py --db /home/user/readonly_copy.db \\\n"
            "    --start 2022-01-01 --end 2025-01-01 \\\n"
            "    --output /home/user/stocksage_replay/run1.db --counts-only"
        ),
    )
    p.add_argument("--db", help="Path to a (read-only opened) copy of the bot's sqlite DB")
    p.add_argument("--symbols-file", help="Alternative to --db: one symbol per line")
    p.add_argument("--limit-symbols", type=int, default=None)
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    p.add_argument("--cache-only", action="store_true",
                    help="Never download; fail and list any symbol missing from the cache.")
    p.add_argument("--resume-run-id", type=int, default=None)
    p.add_argument("--chunk-size", type=int, default=50)
    p.add_argument("--max-retries", type=int, default=3)
    p.add_argument("--slippage-bps", type=float, default=V1_OUTCOME_PARAMS.slippage_bps)
    p.add_argument("--variants", default=None,
                    help="Comma-separated subset of V1,V2,V3 to run. Default: V1,V2,V3 for "
                         "--counts-only, V3 only for --run-outcomes.")
    p.add_argument("--workers", type=int, default=1,
                    help="Process-pool width for the CPU-bound process_symbol work. "
                         "Default 1 = sequential, today's exact behavior. The on-disk "
                         "cache is always fully pre-populated in the main process before "
                         "workers start -- workers never fetch or write to the DB.")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--counts-only", action="store_true")
    mode.add_argument("--run-outcomes", action="store_true")
    return p


def main(
    argv=None, downloader=default_downloader, repo_root: Path | None = None,
    version_lookup=importlib.metadata.version, executor_factory=ProcessPoolExecutor,
) -> int:
    """repo_root is injectable (tests point it at a disposable temporary
    git repository, per the approved implementation conditions -- no test
    may read or modify this repo's own real working tree); real CLI
    invocations leave it None and get this script's own repo auto-detected.
    executor_factory is injectable the same way, so tests can substitute a
    synchronous fake pool instead of spawning real OS processes."""
    args = build_arg_parser().parse_args(argv)
    if args.workers < 1:
        print("FATAL: --workers must be >= 1", file=sys.stderr)
        return 1
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    if repo_root is None:
        script_dir = Path(__file__).resolve().parent
        repo_root = get_repo_root(script_dir)

    output_path = resolve_guarded_path(args.output, repo_root, "--output")
    cache_dir = resolve_guarded_path(args.cache_dir, repo_root, "--cache-dir")

    if args.symbols_file:
        symbols = load_universe_from_file(Path(args.symbols_file))
    else:
        if not args.db:
            print("FATAL: one of --db or --symbols-file is required", file=sys.stderr)
            return 1
        symbols = load_universe_from_db(Path(args.db))
    if args.limit_symbols is not None:
        symbols = symbols[: args.limit_symbols]
    u_hash = universe_hash(symbols)

    outcome_params = replace(V1_OUTCOME_PARAMS, slippage_bps=args.slippage_bps)
    mode = "counts_only" if args.counts_only else "run_outcomes"
    # V3 (measured-3R target) is primary (SETUP_SPEC.md §13) -- V1/V2 are
    # sensitivity variants only, run in counts-only mode alongside it by
    # default; --run-outcomes computes returns for V3 alone by default.
    # --variants overrides either default with an explicit subset.
    _ALL_VARIANTS = ["V1", "V2", "V3"]
    _PARAMS_BY_VARIANT_NAME = {"V1": V1_PARAMS, "V2": replace(V1_PARAMS, swing_lookback_bars=120), "V3": V3_PARAMS}
    if args.variants is not None:
        requested = {v.strip().upper() for v in args.variants.split(",") if v.strip()}
        unknown = requested - set(_ALL_VARIANTS)
        if unknown:
            print(f"FATAL: --variants: unknown variant(s) {sorted(unknown)}; "
                  f"must be a subset of {_ALL_VARIANTS}", file=sys.stderr)
            return 1
        if not requested:
            print("FATAL: --variants: empty list", file=sys.stderr)
            return 1
        variants = [v for v in _ALL_VARIANTS if v in requested]
    else:
        variants = _ALL_VARIANTS if mode == "counts_only" else ["V3"]

    if mode == "run_outcomes" and variants != ["V3"]:
        print(
            f"FATAL: --run-outcomes only accepts --variants V3 (or the default) -- got "
            f"{','.join(variants)}. Sensitivity runs of V1/V2 in outcome mode are not "
            f"allowed until a separately pre-registered run names them.",
            file=sys.stderr,
        )
        return 1

    params_by_variant = {v: _PARAMS_BY_VARIANT_NAME[v] for v in variants}
    fetch_end = resolve_fetch_end(end, mode)

    preregistration_hash = None
    git_commit = None
    library_versions = all_library_versions(repo_root / REQUIREMENTS_FILE, version_lookup)
    if mode == "run_outcomes":
        if not check_preregistration_committed(repo_root):
            print(f"FATAL: {PREREGISTRATION_FILE} does not exist in git HEAD -- "
                  "freeze it before running --run-outcomes.", file=sys.stderr)
            return 1
        clean, status_output = check_working_tree_clean(repo_root)
        if not clean:
            print(f"FATAL: working tree is not clean:\n{status_output}", file=sys.stderr)
            return 1
        mismatches = check_library_versions(repo_root / REQUIREMENTS_FILE, version_lookup)
        if mismatches:
            print(f"FATAL: installed library versions don't match {REQUIREMENTS_FILE}: "
                  f"{mismatches}", file=sys.stderr)
            return 1
        git_commit = get_git_commit(repo_root)
        preregistration_hash = get_committed_file_sha256(repo_root, PREREGISTRATION_FILE)

    conn = open_results_db(output_path)
    try:
        if args.resume_run_id is not None:
            resume_run(conn, args.resume_run_id, mode, variants, params_by_variant,
                       outcome_params, u_hash, start, end)
            run_id = args.resume_run_id
        else:
            run_id = create_run(
                conn, mode, variants, git_commit, params_by_variant, outcome_params, u_hash,
                start, end, fetch_end, library_versions, preregistration_hash,
                workers=args.workers,
            )

        done = get_done_symbols(conn, run_id)
        pending = [s for s in symbols if s not in done]
        print(f"Run {run_id}: {len(symbols)} symbols, {len(pending)} pending "
              f"({len(done)} already done).")

        warmup_start = start - timedelta(days=WARMUP_CALENDAR_DAYS)

        spy_map = fetch_universe_history(["SPY"], warmup_start, fetch_end, cache_dir,
                                          args.chunk_size, args.max_retries, downloader,
                                          cache_only=args.cache_only)
        spy_df = spy_map.get("SPY")
        if spy_df is None:
            print("FATAL: could not fetch SPY -- required for every candidate's regime.", file=sys.stderr)
            return 1

        scan_dates = build_scan_dates(spy_df, start, end)
        # Built ONCE, shared across every symbol x variant x bound call
        # below -- regime depends only on spy_df + scan_date, so without
        # this every one of those calls would otherwise silently rebuild
        # it from scratch.
        regime_by_date = build_regime_by_date(spy_df, scan_dates)

        # Download in chunks up front, for every pending symbol at once --
        # the per-symbol loop below only ever reads from the cache.
        fetch_universe_history(pending, warmup_start, fetch_end, cache_dir,
                                args.chunk_size, args.max_retries, downloader,
                                cache_only=args.cache_only)

        if mode == "run_outcomes":
            print(f"Fetching through {fetch_end} (scan window ends {end}) so a setup scanned near "
                  f"the end of the window can still resolve. Setups scanned within "
                  f"~{OPEN_AT_END_NOTE_SESSIONS} sessions of the data end can still be OPEN_AT_END.")

        run_start_time = time.time()
        elapsed_total = 0.0

        if args.workers <= 1:
            for idx, symbol in enumerate(pending, start=1):
                symbol_start = time.time()
                df = load_from_cache(cache_dir, symbol, warmup_start, fetch_end)
                write_manifest(conn, run_id, symbol, df, commit=False)

                for variant in variants:
                    params = params_by_variant[variant]
                    if mode == "run_outcomes":
                        rows = process_symbol(symbol, df, spy_df, scan_dates, params, outcome_params,
                                               True, 0, variant, regime_by_date=regime_by_date)
                        write_candidate_rows(conn, run_id, NA_BOUND, rows, commit=False)
                    else:
                        upper_rows, lower_rows = process_symbol_counts_only_both_bounds(
                            symbol, df, spy_df, scan_dates, params, outcome_params, variant,
                            regime_by_date=regime_by_date,
                        )
                        write_candidate_rows(conn, run_id, UPPER, upper_rows, commit=False)
                        write_candidate_rows(conn, run_id, LOWER, lower_rows, commit=False)

                mark_symbol_done(conn, run_id, symbol, commit=False)
                conn.commit()  # one transaction for this symbol's candidates + manifest + progress

                elapsed = time.time() - symbol_start
                elapsed_total += elapsed
                avg = elapsed_total / idx
                remaining = len(pending) - idx
                print(f"[{idx}/{len(pending)}] {symbol} done in {elapsed:.2f}s "
                      f"(avg {avg:.2f}s/symbol, ~{avg * remaining:.0f}s remaining)")
        else:
            _run_pool(conn, run_id, pending, cache_dir, warmup_start, fetch_end, scan_dates,
                      mode, variants, params_by_variant, outcome_params, args.workers,
                      executor_factory)

        complete_run(conn, run_id)
        total_elapsed = time.time() - run_start_time
        print(f"Run {run_id} complete in {total_elapsed:.2f}s.")

        summary = {}
        if mode == "counts_only":
            for variant in variants:
                upper = summarize_counts_from_db(conn, run_id, variant, UPPER)
                lower = summarize_counts_from_db(conn, run_id, variant, LOWER)
                print_counts_only_report(variant, upper, lower)
                summary[variant] = {"upper": upper, "lower": lower}

        main.last_summary = summary  # exposed for tests without parsing stdout
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
