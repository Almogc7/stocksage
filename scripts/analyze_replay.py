#!/usr/bin/env python
"""Confirmatory analysis for the setups replay
(docs/PREREGISTRATION_SETUPS_V1.md sections 6, 7, 8, 13). Reads an
existing `--run-outcomes` results DB (read-only) and the same on-disk
price cache used to produce it, builds a mechanics-matched peer control
per primary trade, runs the month-cluster bootstrap, and reports
decide()'s PASS/FAIL/INCONCLUSIVE verdict plus every section-8 secondary
table to a report file written OUTSIDE the repo.

Implements ONLY sections 6/7 plus section 8's pre-registered
`--slippage-bps 20` recompute option -- the `--peer-counts-only`
diagnostic (the one real-counts-only-data exception) lives in its own
script, scripts/peer_counts.py.

Never writes to the results DB (opened read-only, never downloads
(`--cache-only` is implied -- there is no download path in this script
at all).

Usage:
    python scripts/analyze_replay.py \\
        --results-db /home/user/stocksage_replay/run1.db \\
        --cache-dir /home/user/.stocksage_replay_cache \\
        --output /home/user/stocksage_replay/analysis_report.md
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import sqlite3
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from setups.analysis import (  # noqa: E402
    FROZEN_DRAWS,
    FROZEN_SEED,
    FROZEN_SLIPPAGE_BPS,
    Decision,
    build_control_for_trade,
    by_regime,
    by_target_source,
    by_year,
    classify_outcome,
    concentration_shares,
    decide,
    determine_label,
    mean_control_r_at_slippage,
    month_cluster_bootstrap,
    peer_atr_and_eligibility,
    per_period_table,
    recompute_primary_outcome,
    recompute_r_at_slippage,
    rejection_shares,
    sample_sufficiency,
    session_before,
    stored_vs_recomputed_mismatches,
    PairedDifference,
    all_primary_trades_mean_r,
    win_rate_and_r_stats,
)
from setups.params import V1_OUTCOME_PARAMS  # noqa: E402
from setups.reason_codes import (  # noqa: E402
    CHASE_RR_BELOW_3,
    DATA_GAP,
    EXPIRED_UNFILLED,
    INVALIDATED_BEFORE_FILL,
    OPEN_AT_END,
)

import replay_setups as rs  # noqa: E402


def _date_of(ts) -> date:
    return ts.date() if hasattr(ts, "date") else ts


def _opt_date(s):
    return date.fromisoformat(s) if s else None


def _row_from_sqlite_row(r: sqlite3.Row) -> dict:
    """Maps a replay_candidates row to the plain dict rebuild_setup_definition()/
    stored_vs_recomputed_mismatches() expect. Note the DB column is
    `trigger_price`, not `trigger`."""
    return {
        "symbol": r["symbol"], "scan_date": date.fromisoformat(r["scan_date"]),
        "regime": r["regime"],
        "sh_date": _opt_date(r["sh_date"]), "sh_price": r["sh_price"],
        "sl_date": _opt_date(r["sl_date"]), "sl_price": r["sl_price"],
        "trigger": r["trigger_price"], "stop": r["stop"], "target": r["target"],
        "risk": r["risk"], "rr": r["rr"], "atr": r["atr"],
        "validity_end": _opt_date(r["validity_end"]), "target_source": r["target_source"],
        # Stored outcome fields -- used ONLY for the §13.5 stored-vs-
        # recomputed comparison, never trusted for classification.
        "fill_date": _opt_date(r["fill_date"]), "fill_price": r["fill_price"],
        "exit_date": _opt_date(r["exit_date"]), "exit_price": r["exit_price"],
        "first_barrier_hit": r["first_barrier_hit"], "r_multiple": r["r_multiple"],
        "mae_r": r["mae_r"], "mfe_r": r["mfe_r"], "sessions_held": r["sessions_held"],
        "resolved_date": _opt_date(r["resolved_date"]),
    }


def _load_run_row(conn: sqlite3.Connection, run_id: int | None) -> sqlite3.Row:
    if run_id is None:
        rows = conn.execute("SELECT * FROM replay_runs WHERE mode = 'run_outcomes'").fetchall()
        if len(rows) != 1:
            raise RuntimeError(
                f"--run-id not given and this results DB has {len(rows)} run_outcomes runs "
                "-- pass --run-id explicitly."
            )
        return rows[0]
    row = conn.execute("SELECT * FROM replay_runs WHERE run_id = ?", (run_id,)).fetchone()
    if row is None:
        raise RuntimeError(f"no run_id {run_id} in this results DB")
    return row


def _load_accepted_rows(conn: sqlite3.Connection, run_id: int) -> list:
    rows = conn.execute(
        "SELECT * FROM replay_candidates WHERE run_id = ? AND bound = 'NA' AND accepted = 1 "
        "ORDER BY symbol, scan_date",
        (run_id,),
    ).fetchall()
    return [_row_from_sqlite_row(r) for r in rows]


def _load_manifest(conn: sqlite3.Connection, run_id: int) -> dict:
    rows = conn.execute(
        "SELECT symbol, rows, first_date, last_date, sha256 FROM replay_data_manifest "
        "WHERE run_id = ? ORDER BY symbol",
        (run_id,),
    ).fetchall()
    return {r["symbol"]: dict(r) for r in rows}


# ── Process pool (--workers): three passes, each deterministically
# ordered by collecting results into a dict and always iterating sorted
# keys downstream, regardless of actual completion order. Unlike
# scripts/replay_setups.py's --workers (which streams incremental DB
# writes and therefore needs a sorted-prefix flush), this script builds
# one in-memory report written once at the end -- no partial-write risk,
# so a simple "collect then iterate sorted" is sufficient for
# determinism. ───────────────────────────────────────────────────────────

def _run_pool_ordered(worker_fn, tasks: dict, ordered_keys: list, workers: int, executor_factory) -> dict:
    if workers <= 1:
        return {k: worker_fn(tasks[k]) for k in ordered_keys}
    pool = executor_factory(max_workers=workers)
    results: dict = {}
    try:
        futures = {pool.submit(worker_fn, tasks[k]): k for k in ordered_keys}
        for future in as_completed(futures):
            key = futures[future]
            results[key] = future.result()
    except BaseException:
        pool.shutdown(wait=False, cancel_futures=True)
        raise
    finally:
        pool.shutdown(wait=True)
    return results


def _pass1_worker(task: dict) -> dict:
    """Top-level, picklable (required for Windows `spawn`). One task per
    manifest symbol: loads its cached frame once, recomputes its sha256
    (§13.6's cache-vs-manifest check) and, for every accepted row
    belonging to it, the authoritative outcome (§13.5) -- never trusts
    the stored outcome columns."""
    symbol = task["symbol"]
    cache_dir = Path(task["cache_dir"])
    df = rs.load_from_cache(cache_dir, symbol, task["warmup_start"], task["fetch_end"])
    sha256 = hashlib.sha256(df.to_csv().encode()).hexdigest() if df is not None else None
    outcome_params = task["outcome_params"]
    spy_dates = task["spy_dates"]

    recomputed = []
    for row in task["accepted_rows"]:
        if df is None:
            recomputed.append({"row": row, "result": None, "mismatches": ["symbol_frame_missing_from_cache"]})
            continue
        result = recompute_primary_outcome(row, df, outcome_params, reference_sessions=spy_dates)
        mismatches = stored_vs_recomputed_mismatches(row, result)
        recomputed.append({"row": row, "result": result, "mismatches": mismatches})

    return {"symbol": symbol, "sha256": sha256, "recomputed": recomputed}


def _run_pass1(rows_by_symbol: dict, cache_dir: Path, warmup_start: date, fetch_end: date,
                outcome_params, spy_dates: list, workers: int, executor_factory) -> dict:
    tasks = {
        symbol: {
            "symbol": symbol, "cache_dir": str(cache_dir), "warmup_start": warmup_start,
            "fetch_end": fetch_end, "accepted_rows": rows, "outcome_params": outcome_params,
            "spy_dates": spy_dates,
        }
        for symbol, rows in rows_by_symbol.items()
    }
    ordered = sorted(tasks.keys())
    return _run_pool_ordered(_pass1_worker, tasks, ordered, workers, executor_factory)


def _pass2_worker(task: dict) -> dict:
    """Top-level, picklable. One task per universe symbol (a candidate
    peer): loads its frame once, computes eligibility+ATR (§6/§13.2) for
    every needed session-before-d date."""
    symbol = task["symbol"]
    cache_dir = Path(task["cache_dir"])
    df = rs.load_from_cache(cache_dir, symbol, task["warmup_start"], task["fetch_end"])
    out = {}
    for d in task["needed_dates"]:
        out[d] = peer_atr_and_eligibility(df, d) if df is not None else (False, None)
    return {"symbol": symbol, "eligibility": out}


def _run_pass2(universe_symbols: list, needed_dates: list, cache_dir: Path, warmup_start: date,
                fetch_end: date, workers: int, executor_factory) -> dict:
    tasks = {
        symbol: {
            "symbol": symbol, "cache_dir": str(cache_dir), "warmup_start": warmup_start,
            "fetch_end": fetch_end, "needed_dates": needed_dates,
        }
        for symbol in universe_symbols
    }
    ordered = sorted(tasks.keys())
    return _run_pool_ordered(_pass2_worker, tasks, ordered, workers, executor_factory)


# Per-process frame cache for Pass 3's peer frames, keyed by
# (cache_dir, symbol, warmup_start, fetch_end). A plain module-level dict
# works as a per-worker-process cache under ProcessPoolExecutor: each
# worker process keeps its own copy (spawned fresh, never shared across
# processes) and reuses it across every task that process is assigned,
# since warmup_start/fetch_end are constant for the whole run -- many
# trades share overlapping peer sets, so this avoids re-reading the same
# on-disk CSV once per (trade, peer) pair. Read-only: load_from_cache()
# already returns a fresh DataFrame per call and nothing downstream
# mutates it, so caching the returned frame is safe.
_PEER_FRAME_CACHE: dict = {}


def _cached_load_from_cache(cache_dir: Path, symbol: str, warmup_start: date, fetch_end: date):
    key = (str(cache_dir), symbol, warmup_start, fetch_end)
    if key not in _PEER_FRAME_CACHE:
        _PEER_FRAME_CACHE[key] = rs.load_from_cache(cache_dir, symbol, warmup_start, fetch_end)
    return _PEER_FRAME_CACHE[key]


def _pass3_worker(task: dict) -> dict:
    """Top-level, picklable. One task per PRIMARY TRADE: loads every
    peer frame this trade's eligible-peer set needs, through the
    per-process frame cache above (many trades' peer sets overlap), and
    builds its ControlResult."""
    cache_dir = Path(task["cache_dir"])
    peers = []
    for peer_symbol in task["eligible_peer_symbols"]:
        peer_df = _cached_load_from_cache(cache_dir, peer_symbol, task["warmup_start"], task["fetch_end"])
        if peer_df is not None:
            peers.append((peer_symbol, peer_df))
    control = build_control_for_trade(
        task["d"], task["session_before_d"], task["s"], task["t"], peers,
        task["eligibility_atr_lookup"], task["outcome_params"], task["spy_dates"],
    )
    return {"trade_key": task["trade_key"], "control": control}


def _run_pass3(primary_trades: list, universe_symbols: list, eligibility_atr_lookup: dict,
                cache_dir: Path, warmup_start: date, fetch_end: date, outcome_params,
                spy_dates: list, workers: int, executor_factory) -> dict:
    tasks = {}
    for trade in primary_trades:
        key = (trade["symbol"], trade["scan_date"])
        sbd = trade["session_before_d"]
        eligible_peers = [
            s for s in universe_symbols
            if s != trade["symbol"] and eligibility_atr_lookup.get((s, sbd), (False, None))[0]
        ]
        sub_lookup = {(s, sbd): eligibility_atr_lookup[(s, sbd)] for s in eligible_peers}
        tasks[key] = {
            "cache_dir": str(cache_dir), "warmup_start": warmup_start, "fetch_end": fetch_end,
            "d": trade["fill_date"], "session_before_d": sbd, "s": trade["s"], "t": trade["t"],
            "eligible_peer_symbols": eligible_peers, "eligibility_atr_lookup": sub_lookup,
            "outcome_params": outcome_params, "spy_dates": spy_dates, "trade_key": key,
        }
    ordered = sorted(tasks.keys())
    return _run_pool_ordered(_pass3_worker, tasks, ordered, workers, executor_factory)


# ── Report ────────────────────────────────────────────────────────────────

def _fmt(x, pct=False):
    if x is None:
        return "n/a"
    if pct:
        return f"{x:.2%}"
    return f"{x:.4f}" if isinstance(x, float) else str(x)


def build_report(
    label, run_row, repo_root, args, header_slippage_bps, sufficiency, unfiltered_mean_r,
    bootstrap, per_period, concentration, decision, no_peer_excluded_count,
    not_trade_status_count, excluded_status_count, recompute_mismatches, manifest_mismatches,
    by_target_source_table, by_year_table, by_regime_table, win_stats,
    rejection_shares_table, secondary_bootstrap, secondary_decision,
) -> str:
    lines = []
    lines.append("# Setup replay confirmatory analysis\n")
    lines.append(f"**Label: {label.verdict}**\n")
    if label.failed_conditions:
        lines.append("Failed CONFIRMATORY conditions (§13.6/§13.8):")
        for reason in label.failed_conditions:
            lines.append(f"- {reason}")
        lines.append("")

    lines.append("## Header\n")
    lines.append(f"- git commit: `{rs.get_git_commit(repo_root)}`")
    try:
        prereg_sha = rs.get_committed_file_sha256(repo_root, rs.PREREGISTRATION_FILE)
    except Exception:
        prereg_sha = "unavailable (file not committed in HEAD)"
    lines.append(f"- pre-registration file sha256 (HEAD): `{prereg_sha}`")
    lines.append(f"- results DB run_id: {run_row['run_id']}, mode: {run_row['mode']}, "
                  f"variants: {run_row['variants_json']}")
    lines.append(f"- results DB universe_hash: {run_row['universe_hash']}")
    lines.append(f"- results DB dates: {run_row['start_date']} .. {run_row['end_date']} "
                  f"(fetch through {run_row['fetch_end_date']})")
    lines.append(f"- library versions: {run_row['library_versions_json']}")
    lines.append(f"- seed={args.seed}, draws={args.draws}, "
                  f"run header slippage_bps={header_slippage_bps} "
                  f"(frozen: seed={FROZEN_SEED}, draws={FROZEN_DRAWS}, slippage_bps={FROZEN_SLIPPAGE_BPS})\n")

    lines.append("## Section 7 — sample sufficiency (condition 1)\n")
    lines.append(f"- trades: {sufficiency.trade_count} (need >= 100)")
    lines.append(f"- distinct symbols: {sufficiency.symbol_count} (need >= 30)")
    lines.append(f"- distinct fill months: {sufficiency.month_count} (need >= 24)")
    lines.append(f"- distinct calendar years: {sufficiency.year_count} (need >= 3)")
    lines.append(f"- meets minimum: {sufficiency.meets_minimum}")
    lines.append(f"- no-peer excluded trades (§6/§13.4): {no_peer_excluded_count}")
    lines.append(f"- mean net R, ALL primary trades incl. excluded (§13.4, unfiltered): "
                  f"{_fmt(unfiltered_mean_r)}\n")

    if bootstrap is not None:
        lines.append("## Section 7 — bootstrap (condition 3)\n")
        lines.append(f"- point estimate (mean paired difference): {_fmt(bootstrap.point_estimate)}")
        lines.append(f"- 95% interval: [{_fmt(bootstrap.lower)}, {_fmt(bootstrap.upper)}]")
        lines.append(f"- draws: {bootstrap.draws}, seed: {bootstrap.seed}\n")

    lines.append("## Section 7 — per-period table (condition 4)\n")
    lines.append("| year | trades | mean paired diff | positive | qualifies (>=30 trades) |")
    lines.append("|---|---|---|---|---|")
    for year in sorted(per_period.keys()):
        p = per_period[year]
        lines.append(f"| {year} | {p.trade_count} | {_fmt(p.mean_diff)} | {p.mean_diff_positive} | {p.qualifies} |")
    lines.append("")

    if concentration is not None:
        lines.append("## Section 7 — concentration (condition 5)\n")
        lines.append(f"- max symbol share: {_fmt(concentration.max_symbol_share, pct=True)} "
                      f"({concentration.max_symbol})")
        lines.append(f"- max month share: {_fmt(concentration.max_month_share, pct=True)} "
                      f"({concentration.max_month})\n")

    lines.append("## Section 7 — decide()\n")
    lines.append(f"**Verdict: {decision.verdict}**")
    for reason in decision.reasons:
        lines.append(f"- {reason}")
    lines.append("")

    lines.append("## Section 8 — non-confirmatory (descriptive only, never fed to decide())\n")
    total_for_shares = sum(not_trade_status_count.values()) + sum(excluded_status_count.values()) + no_peer_excluded_count
    lines.append("- not-trade counts by status (invalidated/expired/chase-rejected):")
    for status in (INVALIDATED_BEFORE_FILL, EXPIRED_UNFILLED, CHASE_RR_BELOW_3):
        count = not_trade_status_count.get(status, 0)
        share = (count / total_for_shares) if total_for_shares else 0.0
        lines.append(f"  - {status}: {count} ({share:.2%})")
    lines.append("- excluded counts by status (DATA_GAP/OPEN_AT_END):")
    for status in (DATA_GAP, OPEN_AT_END):
        count = excluded_status_count.get(status, 0)
        share = (count / total_for_shares) if total_for_shares else 0.0
        lines.append(f"  - {status}: {count} ({share:.2%})")
    no_peer_share = (no_peer_excluded_count / total_for_shares) if total_for_shares else 0.0
    lines.append(f"- no-peer excluded count: {no_peer_excluded_count} ({no_peer_share:.2%})")
    lines.append(f"- rejection/exclusion shares (raw): {rejection_shares_table}")
    lines.append(f"- win rate / R stats: {win_stats}")
    lines.append(f"- by target_source: {by_target_source_table}")
    lines.append(f"- by year: {by_year_table}")
    lines.append(f"- by regime: {by_regime_table}")
    if secondary_bootstrap is not None:
        lines.append(f"\n### Secondary: slippage 20bps (§8/§13.7, recomputed from stored prices, not resimulated)")
        lines.append(f"- point estimate: {_fmt(secondary_bootstrap.point_estimate)}, "
                      f"95% interval: [{_fmt(secondary_bootstrap.lower)}, {_fmt(secondary_bootstrap.upper)}]")
        lines.append(f"- decide() at 20bps: {secondary_decision.verdict} {secondary_decision.reasons}")

    lines.append("\n## Integrity\n")
    lines.append(f"- stored-vs-recomputed mismatches (§13.5): {len(recompute_mismatches)}")
    for m in recompute_mismatches[:50]:
        lines.append(f"  - {m}")
    lines.append(f"- cache-vs-manifest sha256 mismatches (§13.6): {manifest_mismatches}")

    return "\n".join(lines) + "\n"


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--results-db", required=True)
    p.add_argument("--run-id", type=int, default=None)
    p.add_argument("--cache-dir", default=str(rs.DEFAULT_CACHE_DIR))
    p.add_argument("--output", required=True)
    p.add_argument("--seed", type=int, default=FROZEN_SEED,
                    help="Overriding this forces an EXPLORATORY label (§13.6).")
    p.add_argument("--draws", type=int, default=FROZEN_DRAWS,
                    help="Overriding this forces an EXPLORATORY label (§13.6).")
    p.add_argument("--workers", type=int, default=1)
    return p


def main(
    argv=None, repo_root: Path | None = None, executor_factory=ProcessPoolExecutor,
    version_lookup=importlib.metadata.version,
) -> int:
    args = build_arg_parser().parse_args(argv)
    if args.workers < 1:
        print("FATAL: --workers must be >= 1", file=sys.stderr)
        return 1
    if args.draws < 1:
        print("FATAL: --draws must be >= 1", file=sys.stderr)
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
        variants = json.loads(run_row["variants_json"])
        header_outcome_params = json.loads(run_row["outcome_params_json"])
        header_slippage_bps = header_outcome_params["slippage_bps"]
        manifest = _load_manifest(conn, run_id)
        accepted_rows = _load_accepted_rows(conn, run_id)
    finally:
        conn.close()

    if not manifest:
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

    # The simulator itself always runs at the frozen 10bps cost model
    # (§13.5 re-runs "the frozen simulator") -- --slippage-bps only
    # affects the recompute-from-stored-prices step below (§13.7), never
    # this pass.
    outcome_params = V1_OUTCOME_PARAMS

    rows_by_symbol: dict = {s: [] for s in manifest}
    for row in accepted_rows:
        rows_by_symbol.setdefault(row["symbol"], []).append(row)

    t0 = time.time()
    pass1_results = _run_pass1(
        rows_by_symbol, cache_dir, warmup_start, fetch_end, outcome_params, spy_dates,
        args.workers, executor_factory,
    )
    print(f"Pass 1 (recompute + manifest check) done in {time.time() - t0:.2f}s "
          f"for {len(pass1_results)} symbols.")

    primary_trades: list = []
    not_trade_status_count: dict = {}
    excluded_status_count: dict = {}
    recompute_mismatches: list = []
    manifest_mismatches: list = []
    all_primary_rows_for_mean: list = []

    for symbol in sorted(manifest.keys()):
        p1 = pass1_results[symbol]
        if manifest[symbol]["sha256"] != p1["sha256"]:
            manifest_mismatches.append(symbol)
        for item in p1["recomputed"]:
            row, result, mismatches = item["row"], item["result"], item["mismatches"]
            if mismatches:
                recompute_mismatches.append(
                    {"symbol": symbol, "scan_date": str(row["scan_date"]), "fields": mismatches}
                )
            if result is None:
                continue
            bucket = classify_outcome(result.status)
            if bucket == "not_trade":
                not_trade_status_count[result.status] = not_trade_status_count.get(result.status, 0) + 1
            elif bucket == "excluded":
                excluded_status_count[result.status] = excluded_status_count.get(result.status, 0) + 1
            elif bucket == "primary":
                all_primary_rows_for_mean.append({"r_multiple": result.r_multiple})
                s = (row["trigger"] - row["stop"]) / row["atr"]
                t = (row["target"] - row["trigger"]) / row["atr"]
                primary_trades.append({
                    "symbol": symbol, "scan_date": row["scan_date"], "fill_date": result.fill_date,
                    "s": s, "t": t, "setup_r": result.r_multiple, "stop": row["stop"],
                    "fill_price": result.fill_price, "exit_price": result.exit_price,
                    "target_source": row["target_source"], "regime": row["regime"],
                    "first_barrier_hit": result.first_barrier_hit,
                    "mae_r": result.mae_r, "mfe_r": result.mfe_r,
                })

    print(f"Classified {len(primary_trades)} primary, {sum(not_trade_status_count.values())} not-trade, "
          f"{sum(excluded_status_count.values())} excluded.")

    for trade in primary_trades:
        trade["session_before_d"] = session_before(spy_dates, trade["fill_date"])

    needed_dates = sorted({t["session_before_d"] for t in primary_trades if t["session_before_d"] is not None})
    universe_symbols = sorted(manifest.keys())

    t0 = time.time()
    pass2_results = _run_pass2(
        universe_symbols, needed_dates, cache_dir, warmup_start, fetch_end, args.workers, executor_factory,
    )
    print(f"Pass 2 (peer eligibility/ATR) done in {time.time() - t0:.2f}s for "
          f"{len(universe_symbols)} symbols x {len(needed_dates)} dates.")

    eligibility_atr_lookup: dict = {}
    for symbol, res in pass2_results.items():
        for d, val in res["eligibility"].items():
            eligibility_atr_lookup[(symbol, d)] = val

    t0 = time.time()
    control_results = _run_pass3(
        primary_trades, universe_symbols, eligibility_atr_lookup, cache_dir, warmup_start,
        fetch_end, outcome_params, spy_dates, args.workers, executor_factory,
    )
    print(f"Pass 3 (peer simulation) done in {time.time() - t0:.2f}s for {len(primary_trades)} trades.")

    diffs: list = []
    secondary_diffs: list = []
    no_peer_excluded_count = 0
    for trade in primary_trades:
        key = (trade["symbol"], trade["scan_date"])
        control = control_results[key]["control"]
        if control.excluded or control.mean_control_r is None:
            no_peer_excluded_count += 1
            continue
        diffs.append(PairedDifference(trade["symbol"], trade["fill_date"], trade["setup_r"], control.mean_control_r))

        setup_r_20bps = recompute_r_at_slippage(trade["fill_price"], trade["exit_price"], trade["stop"], 20.0)
        control_r_20bps = mean_control_r_at_slippage(control, 20.0)
        if control_r_20bps is not None:
            secondary_diffs.append(PairedDifference(trade["symbol"], trade["fill_date"], setup_r_20bps, control_r_20bps))

    sufficiency = sample_sufficiency(diffs)
    unfiltered_mean_r = all_primary_trades_mean_r(all_primary_rows_for_mean)

    if diffs:
        bootstrap = month_cluster_bootstrap(diffs, draws=args.draws, seed=args.seed)
        per_period = per_period_table(diffs)
        concentration = concentration_shares(diffs)
        decision = decide(diffs, bootstrap, per_period, concentration, sufficiency)
    else:
        bootstrap = None
        per_period = {}
        concentration = None
        decision = Decision("INCONCLUSIVE", ["no usable paired differences"])

    secondary_bootstrap = secondary_decision = None
    if secondary_diffs:
        secondary_sufficiency = sample_sufficiency(secondary_diffs)
        secondary_bootstrap = month_cluster_bootstrap(secondary_diffs, draws=args.draws, seed=args.seed)
        secondary_per_period = per_period_table(secondary_diffs)
        secondary_concentration = concentration_shares(secondary_diffs)
        secondary_decision = decide(
            secondary_diffs, secondary_bootstrap, secondary_per_period, secondary_concentration,
            secondary_sufficiency,
        )

    prereg_committed = rs.check_preregistration_committed(repo_root)
    tree_clean, _status_output = rs.check_working_tree_clean(repo_root)
    version_mismatches = rs.check_library_versions(repo_root / rs.REQUIREMENTS_FILE, version_lookup)
    preregistration_hash_matches = (
        run_row["preregistration_file_hash"] == rs.get_committed_file_sha256(repo_root, rs.PREREGISTRATION_FILE)
    )
    label = determine_label(
        run_row["mode"], variants, header_slippage_bps, args.seed, args.draws,
        prereg_committed, tree_clean, not version_mismatches,
        not manifest_mismatches, recompute_mismatches, preregistration_hash_matches,
    )

    all_primary_dict_rows = [
        {"r_multiple": t["setup_r"], "target_source": t["target_source"], "regime": t["regime"],
         "fill_date": t["fill_date"], "first_barrier_hit": t["first_barrier_hit"],
         "mae_r": t["mae_r"], "mfe_r": t["mfe_r"]}
        for t in primary_trades
    ]
    report = build_report(
        label, run_row, repo_root, args, header_slippage_bps, sufficiency, unfiltered_mean_r,
        bootstrap, per_period, concentration, decision, no_peer_excluded_count,
        not_trade_status_count, excluded_status_count,
        recompute_mismatches, manifest_mismatches,
        by_target_source(all_primary_dict_rows), by_year(all_primary_dict_rows),
        by_regime(all_primary_dict_rows), win_rate_and_r_stats(all_primary_dict_rows),
        rejection_shares({**not_trade_status_count, **excluded_status_count}, no_peer_excluded_count),
        secondary_bootstrap, secondary_decision,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")
    print(f"Report written to {output_path} -- label: {label.verdict}, decide(): {decision.verdict}")

    main.last_decision = decision
    main.last_label = label
    main.last_diffs = diffs
    main.last_recompute_mismatches = recompute_mismatches
    main.last_manifest_mismatches = manifest_mismatches
    return 0


if __name__ == "__main__":
    sys.exit(main())
