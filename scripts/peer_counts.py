#!/usr/bin/env python
"""Peer-count diagnostic (docs/PREREGISTRATION_SETUPS_V1.md ambiguity #7):
reads a `--counts-only`-mode results DB (no outcome fields -- outcomes
were never simulated in that mode) and the same on-disk price cache, and
reports how many eligible peers each accepted candidate would have had,
using the session before that candidate's own `scan_date` as the
eligibility reference date.

This is an APPROXIMATION of the true "fill-eve" peer count a real
outcomes run would produce -- a counts-only row has no fill_date at all,
so the closest available proxy without computing real outcomes is used,
and the report says so explicitly. No R, no bootstrap, no decide(), no
CONFIRMATORY/EXPLORATORY label -- this script structurally cannot produce
one, since it never touches outcomes. Kept as its own script, separate
from scripts/analyze_replay.py's confirmatory pipeline, so this
diagnostic tool can never affect that pipeline's label.

Never writes to the results DB (opened read-only). Never downloads.

Usage:
    python scripts/peer_counts.py \\
        --results-db /home/user/stocksage_replay/counts_only_run.db \\
        --cache-dir /home/user/.stocksage_replay_cache \\
        --output /home/user/stocksage_replay/peer_counts_report.md
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from setups.analysis import peer_atr_and_eligibility, session_before  # noqa: E402

import replay_setups as rs  # noqa: E402


def _date_of(ts) -> date:
    return ts.date() if hasattr(ts, "date") else ts


def _load_run_row(conn: sqlite3.Connection, run_id: int | None) -> sqlite3.Row:
    if run_id is None:
        rows = conn.execute("SELECT * FROM replay_runs WHERE mode = 'counts_only'").fetchall()
        if len(rows) != 1:
            raise RuntimeError(
                f"--run-id not given and this results DB has {len(rows)} counts_only runs "
                "-- pass --run-id explicitly."
            )
        return rows[0]
    row = conn.execute("SELECT * FROM replay_runs WHERE run_id = ?", (run_id,)).fetchone()
    if row is None:
        raise RuntimeError(f"no run_id {run_id} in this results DB")
    return row


def _load_accepted_scan_dates(conn: sqlite3.Connection, run_id: int, variant: str, bound: str) -> list:
    rows = conn.execute(
        "SELECT symbol, scan_date FROM replay_candidates "
        "WHERE run_id = ? AND variant = ? AND bound = ? AND accepted = 1 "
        "ORDER BY symbol, scan_date",
        (run_id, variant, bound),
    ).fetchall()
    return [{"symbol": r["symbol"], "scan_date": date.fromisoformat(r["scan_date"])} for r in rows]


def _load_manifest_symbols(conn: sqlite3.Connection, run_id: int) -> list:
    rows = conn.execute(
        "SELECT DISTINCT symbol FROM replay_data_manifest WHERE run_id = ? ORDER BY symbol",
        (run_id,),
    ).fetchall()
    return [r["symbol"] for r in rows]


def _eligibility_worker(task: dict) -> dict:
    """Top-level, picklable. One task per universe symbol (a candidate
    peer): loads its frame once, computes eligibility for every needed
    date."""
    symbol = task["symbol"]
    cache_dir = Path(task["cache_dir"])
    df = rs.load_from_cache(cache_dir, symbol, task["warmup_start"], task["fetch_end"])
    out = {}
    for d in task["needed_dates"]:
        eligible, _atr = peer_atr_and_eligibility(df, d) if df is not None else (False, None)
        out[d] = eligible
    return {"symbol": symbol, "eligibility": out}


def _run_eligibility_pool(
    universe_symbols: list, needed_dates: list, cache_dir: Path, warmup_start: date,
    fetch_end: date, workers: int, executor_factory,
) -> dict:
    tasks = {
        symbol: {
            "symbol": symbol, "cache_dir": str(cache_dir), "warmup_start": warmup_start,
            "fetch_end": fetch_end, "needed_dates": needed_dates,
        }
        for symbol in universe_symbols
    }
    ordered = sorted(tasks.keys())
    if workers <= 1:
        return {k: _eligibility_worker(tasks[k]) for k in ordered}
    pool = executor_factory(max_workers=workers)
    results: dict = {}
    try:
        futures = {pool.submit(_eligibility_worker, tasks[k]): k for k in ordered}
        for future in as_completed(futures):
            key = futures[future]
            results[key] = future.result()
    except BaseException:
        pool.shutdown(wait=False, cancel_futures=True)
        raise
    finally:
        pool.shutdown(wait=True)
    return results


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--results-db", required=True)
    p.add_argument("--run-id", type=int, default=None)
    p.add_argument("--variant", default="V3")
    p.add_argument("--bound", default="LOWER", choices=["UPPER", "LOWER"])
    p.add_argument("--cache-dir", default=str(rs.DEFAULT_CACHE_DIR))
    p.add_argument("--output", required=True)
    p.add_argument("--workers", type=int, default=1)
    return p


def main(argv=None, repo_root: Path | None = None, executor_factory=ProcessPoolExecutor) -> int:
    args = build_arg_parser().parse_args(argv)
    if args.workers < 1:
        print("FATAL: --workers must be >= 1", file=sys.stderr)
        return 1

    if repo_root is None:
        repo_root = rs.get_repo_root(Path(__file__).resolve().parent)

    output_path = rs.resolve_guarded_path(args.output, repo_root, "--output")
    cache_dir = rs.resolve_guarded_path(args.cache_dir, repo_root, "--cache-dir")
    results_db_path = Path(args.results_db).expanduser().resolve()

    conn = sqlite3.connect(f"file:{results_db_path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        run_row = _load_run_row(conn, args.run_id)
        run_id = run_row["run_id"]
        candidates = _load_accepted_scan_dates(conn, run_id, args.variant, args.bound)
        universe_symbols = _load_manifest_symbols(conn, run_id)
    finally:
        conn.close()

    if not universe_symbols:
        print("FATAL: no replay_data_manifest rows for this run_id", file=sys.stderr)
        return 1

    start = date.fromisoformat(run_row["start_date"])
    fetch_end = date.fromisoformat(run_row["fetch_end_date"])
    warmup_start = start - timedelta(days=rs.WARMUP_CALENDAR_DAYS)

    spy_df = rs.load_from_cache(cache_dir, "SPY", warmup_start, fetch_end)
    if spy_df is None:
        print("FATAL: SPY not found in --cache-dir for this run's date range", file=sys.stderr)
        return 1
    spy_dates = sorted({_date_of(ts) for ts in spy_df.index})

    for c in candidates:
        c["reference_date"] = session_before(spy_dates, c["scan_date"])

    needed_dates = sorted({c["reference_date"] for c in candidates if c["reference_date"] is not None})
    eligibility_by_symbol = _run_eligibility_pool(
        universe_symbols, needed_dates, cache_dir, warmup_start, fetch_end, args.workers, executor_factory,
    )

    lines = [
        "# Peer-count diagnostic (approximation -- not real fill-eve counts)\n",
        "This mode never computes outcomes; peer counts use the session "
        "before each candidate's own scan_date as a proxy for the real "
        "fill-eve, which can only be known once real outcomes exist.\n",
        f"- git commit: `{rs.get_git_commit(repo_root)}`",
        f"- results DB run_id: {run_id}, variant: {args.variant}, bound: {args.bound}",
        f"- universe size: {len(universe_symbols)}\n",
        "| symbol | scan_date | reference_date | eligible_peer_count |",
        "|---|---|---|---|",
    ]
    results_rows = []
    for c in candidates:
        ref = c["reference_date"]
        if ref is None:
            peer_count = 0
        else:
            peer_count = sum(
                1 for sym in universe_symbols
                if sym != c["symbol"] and eligibility_by_symbol.get(sym, {}).get("eligibility", {}).get(ref, False)
            )
        results_rows.append({"symbol": c["symbol"], "scan_date": c["scan_date"], "peer_count": peer_count})
        lines.append(f"| {c['symbol']} | {c['scan_date']} | {ref} | {peer_count} |")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Report written to {output_path} -- {len(results_rows)} candidates.")

    main.last_results = results_rows
    return 0


if __name__ == "__main__":
    sys.exit(main())
