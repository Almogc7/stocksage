"""
Round B: pre-registered, HELD-OUT evaluation of trend_pullback/long.

Evaluates the single hypothesis pre-registered in app/patterns/prereg_v2.py
(read that file first — the docstring there is the actual pre-registration
document, including the exact held-out dates, every threshold and why,
the survivorship caveat, and the fixed PASS-1/PASS-2/classification
consequences). This script is the orchestration layer only; it contains
no thresholds, no dates, and no decision logic of its own.

SPY: fetched ONCE, for the held-out range specifically
(prereg_v2.HELD_OUT_PERIOD_START..HELD_OUT_PERIOD_END), via the exact same
fetch_with_fallback_ranged() used for every other symbol. The single
resulting frame is reused for every excess_return() call in this run
(baseline B, baseline B-trend, and the drift computation) — never
refetched, never a different range.

NO STOPS, FIXED HORIZON: reuses app.patterns.baseline unmodified — same
rationale as that module's own docstring (ordinary peer-days have no
stop-loss equivalent, so a stop-aware return wouldn't be comparable).

Refuses to run if app/patterns/config.py OR app/patterns/prereg_v2.py has
uncommitted changes — both must be frozen and committed before a held-out
run is meaningful.

Writes nothing to any database, bot or webapp — not even incidentally.
Unlike universe_prices.py's capped fallback (which calls
app.prices.get_prices(), a caching/DB-writing path), this script's ranged
fallback (app.patterns.universe_prices_ranged) talks to yfinance directly
and caches nothing, so this entire run is 100% read-only/stateless.

Usage (from webapp/backend/):
    python scripts/eval_trend_pullback_holdout.py
"""
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))  # for `import backtest_patterns`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # for `app.*`

import backtest_patterns as bt  # noqa: E402

from app.patterns import config, prereg_v2  # noqa: E402
from app.patterns.baseline import build_horizon_index, score_detection_with_baselines  # noqa: E402
from app.patterns.dedupe import dedupe_detections  # noqa: E402
from app.patterns.detect import detect_patterns  # noqa: E402
from app.patterns.stats import bootstrap_mean_ci  # noqa: E402
from app.patterns.trend_state_baseline import (  # noqa: E402
    baseline_date_matched_trend,
    build_trend_restricted_date_agg,
    compute_trend_state,
    is_trend_peer,
)
from app.patterns.universe_prices_ranged import fetch_with_fallback_ranged  # noqa: E402

TARGET_PATTERN = "trend_pullback"
TARGET_DIRECTION = "long"
HORIZONS = config.FORWARD_HORIZONS  # (5, 10, 20) -- PRIMARY_HORIZON=10 decides; rest are context

SURVIVORSHIP_NOTE = (
    "Same watchlist symbols as the round-1 diagnostic -- survivorship bias remains, and is "
    "stronger going backward: only symbols on today's watchlist are evaluated against "
    f"{prereg_v2.HELD_OUT_PERIOD_START}..{prereg_v2.HELD_OUT_PERIOD_END}, and baselines "
    "B/B-trend only partly absorb this (see prereg_v2.py's docstring)."
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CONFIG_PATH = _REPO_ROOT / "app" / "patterns" / "config.py"
_PREREG_PATH = _REPO_ROOT / "app" / "patterns" / "prereg_v2.py"


def _git_commit_hash(path: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%H", "--", str(path)],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        return result.stdout.strip() or "unknown (no commit touches this file yet)"
    except Exception:
        return "unknown (not a git repo or git unavailable)"


def _require_clean_or_abort(path: Path, label: str) -> bool:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--", str(path)],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except Exception as exc:
        print(f"FATAL: could not check {label}'s git status ({exc}); aborting.")
        return False
    if result.stdout.strip():
        print(f"FATAL: {label} has uncommitted changes. It must be frozen and committed before this run.\n{result.stdout}")
        return False
    return True


def _year_of(date: str) -> str:
    return date[:4]


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else float("nan")


def _summary_row(values_by_month: dict[str, list[float]], values_by_symbol: dict[str, list[float]], seed: int) -> dict:
    all_values = [v for vs in values_by_month.values() for v in vs]
    n = len(all_values)
    distinct_symbols = len(values_by_symbol)
    distinct_months = len(values_by_month)
    mean_er, ci_lower, ci_upper = bootstrap_mean_ci(
        values_by_month, n_resamples=prereg_v2.PREREG_BOOTSTRAP_RESAMPLES, seed=seed
    )
    _, sym_lo, sym_hi = bootstrap_mean_ci(values_by_symbol, n_resamples=prereg_v2.PREREG_BOOTSTRAP_RESAMPLES, seed=seed)
    sorted_vals = sorted(all_values)
    median = sorted_vals[len(sorted_vals) // 2] if n % 2 else (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2 if n else float("nan")
    hit_rate = (sum(1 for v in all_values if v > 0) / n) if n else float("nan")
    return {
        "n": n,
        "distinct_symbols": distinct_symbols,
        "distinct_months": distinct_months,
        "mean": mean_er,
        "median": median,
        "hit_rate": hit_rate,
        "ci_lower": ci_lower,
        "ci_upper": ci_upper,
        "symbol_ci_lower": sym_lo,
        "symbol_ci_upper": sym_hi,
    }


def main() -> int:
    print("Round B: pre-registered, held-out evaluation of trend_pullback/long")
    print(f"PREREG_VERSION={prereg_v2.PREREG_VERSION}\n")

    if not _require_clean_or_abort(_CONFIG_PATH, "app/patterns/config.py"):
        return 1
    if not _require_clean_or_abort(_PREREG_PATH, "app/patterns/prereg_v2.py"):
        return 1

    print(f"config.py committed at {_git_commit_hash(_CONFIG_PATH)}")
    print(f"prereg_v2.py committed at {_git_commit_hash(_PREREG_PATH)}")
    print(f"Held-out window: {prereg_v2.HELD_OUT_PERIOD_START} .. {prereg_v2.HELD_OUT_PERIOD_END}\n")

    watchlist_rows = bt.bot_db.fetch_watchlist()
    tiers = bt._group_by_tier(watchlist_rows)
    symbols: list[str] = []
    for tier_name, tier_symbols in tiers.items():
        if tier_name == "ETF_INDEX_CONTEXT":
            continue
        symbols.extend(tier_symbols)
    if not symbols:
        print("No enabled stock-tier watchlist symbols found; nothing to evaluate.")
        return 0
    print(SURVIVORSHIP_NOTE + "\n")

    spy_frames, spy_failed, _ = fetch_with_fallback_ranged(
        ["SPY"], prereg_v2.HELD_OUT_PERIOD_START, prereg_v2.HELD_OUT_PERIOD_END
    )
    if "SPY" not in spy_frames:
        print("FATAL: could not fetch SPY history for the held-out range; aborting.")
        return 1
    spy_frame = spy_frames["SPY"]
    spy_idx = {d: i for i, d in enumerate(spy_frame.index.to_list())}

    frames, failed, skipped_count = fetch_with_fallback_ranged(
        symbols, prereg_v2.HELD_OUT_PERIOD_START, prereg_v2.HELD_OUT_PERIOD_END
    )
    if failed:
        preview = failed[:10]
        more = f" (+{len(failed) - 10} more)" if len(failed) > 10 else ""
        print(f"[warn] {len(failed)} symbols failed ({skipped_count} skipped past the fallback cap): {preview}{more}")
    if not frames:
        print("No price history fetched; aborting.")
        return 1

    bar_index = bt._bar_index_lookup(frames)

    all_detections: list[dict] = []
    for symbol, df in frames.items():
        all_detections.extend(detect_patterns(bt._frame_to_payload(df), symbol))
    target_detections = [d for d in all_detections if d["pattern"] == TARGET_PATTERN and d["direction"] == TARGET_DIRECTION]
    raw_n = len(target_detections)
    deduped = dedupe_detections(target_detections, bar_index)
    print(f"{TARGET_PATTERN} / {TARGET_DIRECTION}: raw={raw_n} deduped={len(deduped)}\n")

    trend_state = compute_trend_state(frames)

    rows_b_by_horizon: dict[int, list[dict]] = defaultdict(list)
    excluded_peers_b: dict[str, int] = defaultdict(int)
    excluded_peers_b_trend: dict[str, int] = defaultdict(int)

    for h in HORIZONS:
        index = build_horizon_index(frames, h, spy_frame, spy_idx, warmup_bars=config.SMA_WARMUP_BARS)
        trend_agg = build_trend_restricted_date_agg(index, trend_state, TARGET_DIRECTION)

        for det in deduped:
            result = score_detection_with_baselines(det, index, bar_index)
            if result is None:
                continue
            symbol = result["symbol"]
            entry_date = result["entry_date"]
            det_excess = result["excess_return"]
            year = _year_of(entry_date)

            # Baseline B peer count, straight from the public date_agg field.
            b_total_sum, b_total_count = index.date_agg[TARGET_DIRECTION].get(entry_date, (0.0, 0))
            b_peer_count = b_total_count - 1 if result["baseline_b"] is not None else b_total_count
            abnormal_b = result["abnormal_b"]
            if b_peer_count < prereg_v2.PREREG_MIN_PEERS:
                abnormal_b = None
                excluded_peers_b[year] += 1

            # Baseline B-trend: own contribution only if the symbol itself qualifies.
            confirm_idx = bar_index[symbol][det["confirmed_on"]]
            entry_idx = confirm_idx + 1
            own_excess_trend = det_excess if is_trend_peer(trend_state, symbol, entry_idx) else None
            baseline_b_trend, b_trend_peer_count = baseline_date_matched_trend(trend_agg, entry_date, own_excess_trend)
            abnormal_b_trend = (det_excess - baseline_b_trend) if baseline_b_trend is not None else None
            if b_trend_peer_count < prereg_v2.PREREG_MIN_PEERS:
                abnormal_b_trend = None
                excluded_peers_b_trend[year] += 1

            rows_b_by_horizon[h].append(
                {
                    "symbol": symbol,
                    "year": year,
                    "month": entry_date[:7],
                    "excess_return": det_excess,
                    "abnormal_b": abnormal_b,
                    "abnormal_b_trend": abnormal_b_trend,
                }
            )

    def _grouped(rows: list[dict], key: str) -> tuple[dict, dict]:
        by_month: dict[str, list[float]] = defaultdict(list)
        by_symbol: dict[str, list[float]] = defaultdict(list)
        for r in rows:
            v = r[key]
            if v is not None:
                by_month[r["month"]].append(v)
                by_symbol[r["symbol"]].append(v)
        return by_month, by_symbol

    primary_row_b = primary_row_b_trend = None
    yearly_rows_b: list[dict] = []
    yearly_rows_b_trend: list[dict] = []

    for h in HORIZONS:
        rows = rows_b_by_horizon.get(h, [])
        by_month_b, by_symbol_b = _grouped(rows, "abnormal_b")
        by_month_bt, by_symbol_bt = _grouped(rows, "abnormal_b_trend")
        row_b = _summary_row(by_month_b, by_symbol_b, seed=config.BOOTSTRAP_SEED)
        row_bt = _summary_row(by_month_bt, by_symbol_bt, seed=config.BOOTSTRAP_SEED)

        raw_excess_values = [r["excess_return"] for r in rows]
        tag = " (PRIMARY)" if h == prereg_v2.PRIMARY_HORIZON else ""
        print(f"T+{h}{tag}:")
        print(f"  n={row_b['n']} symbols={row_b['distinct_symbols']} months={row_b['distinct_months']}")
        print(f"  mean raw excess={_mean(raw_excess_values):.2%}")
        print(
            f"  B:       mean abnormal={row_b['mean']:.2%} median={row_b['median']:.2%} "
            f"hit_rate={row_b['hit_rate']:.1%} month-CI=[{row_b['ci_lower']:.2%}, {row_b['ci_upper']:.2%}] "
            f"symbol-CI=[{row_b['symbol_ci_lower']:.2%}, {row_b['symbol_ci_upper']:.2%}]"
        )
        print(
            f"  B-trend: n={row_bt['n']} symbols={row_bt['distinct_symbols']} months={row_bt['distinct_months']} "
            f"mean abnormal={row_bt['mean']:.2%} median={row_bt['median']:.2%} "
            f"hit_rate={row_bt['hit_rate']:.1%} month-CI=[{row_bt['ci_lower']:.2%}, {row_bt['ci_upper']:.2%}] "
            f"symbol-CI=[{row_bt['symbol_ci_lower']:.2%}, {row_bt['symbol_ci_upper']:.2%}]"
        )

        years = sorted({r["year"] for r in rows})
        for year in years:
            year_rows = [r for r in rows if r["year"] == year]
            _, by_symbol_yb = _grouped(year_rows, "abnormal_b")
            _, by_symbol_ybt = _grouped(year_rows, "abnormal_b_trend")
            vals_b = [v for vs in by_symbol_yb.values() for v in vs]
            vals_bt = [v for vs in by_symbol_ybt.values() for v in vs]
            year_row_b = {"n": len(vals_b), "mean_abnormal": _mean(vals_b)}
            year_row_bt = {"n": len(vals_bt), "mean_abnormal": _mean(vals_bt)}
            print(f"    {year}: B n={year_row_b['n']} mean={year_row_b['mean_abnormal']:.2%} | "
                  f"B-trend n={year_row_bt['n']} mean={year_row_bt['mean_abnormal']:.2%}")
            if h == prereg_v2.PRIMARY_HORIZON:
                yearly_rows_b.append(year_row_b)
                yearly_rows_b_trend.append(year_row_bt)

        if h == prereg_v2.PRIMARY_HORIZON:
            primary_row_b = row_b
            primary_row_b_trend = row_bt
        print()

    print("Peer-exclusion counts (fewer than PREREG_MIN_PEERS=%d same-date peers):" % prereg_v2.PREREG_MIN_PEERS)
    for year in sorted(set(excluded_peers_b) | set(excluded_peers_b_trend)):
        print(f"  {year}: B={excluded_peers_b.get(year, 0)} B-trend={excluded_peers_b_trend.get(year, 0)}")
    print()

    # Unconditional drift (context, not decision-making).
    print("Unconditional drift (all eligible bars, direction=long):")
    for h in HORIZONS:
        index = build_horizon_index(frames, h, spy_frame, spy_idx, warmup_bars=config.SMA_WARMUP_BARS)
        all_values: list[float] = []
        by_year: dict[str, list[float]] = defaultdict(list)
        for symbol, bars_list in index.eligible_by_symbol.items():
            values = index.excess_by_symbol_direction.get(symbol, {}).get("long", [])
            for bar, v in zip(bars_list, values):
                if v is not None:
                    all_values.append(v)
                    by_year[_year_of(bar["entry_date"])].append(v)
        print(f"  T+{h}: overall={_mean(all_values):.2%} (n={len(all_values)})")
        for year in sorted(by_year):
            print(f"    {year}: {_mean(by_year[year]):.2%} (n={len(by_year[year])})")
    print()

    pass1 = primary_row_b is not None and prereg_v2.passes_pass1(primary_row_b, yearly_rows_b)
    pass2 = primary_row_b_trend is not None and prereg_v2.passes_pass2(primary_row_b_trend, yearly_rows_b_trend)
    verdict = prereg_v2.classify_result(pass1, pass2)

    print(f"PASS-1 (B): {'PASS' if pass1 else 'FAIL'}")
    print(f"PASS-2 (B-trend): {'PASS' if pass2 else 'FAIL'}")
    print(f"VERDICT: {verdict}")
    print("2 tests examined (1 primary hypothesis x 2 baselines: B, B-trend)")
    print()
    print(prereg_v2.INFORMATIONAL_ONLY_NOTE)

    return 0


if __name__ == "__main__":
    sys.exit(main())
