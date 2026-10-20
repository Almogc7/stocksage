"""
Post-hoc, EXPLORATORY baseline diagnostic for the v1 pattern backtest.

THIS IS A POST-HOC DIAGNOSTIC ON THE DATA THE v1 REPORT USED; IT CAN ONLY
LOWER CONFIDENCE, NOT RAISE IT; UPGRADING ANY PATTERN REQUIRES HELD-OUT
DATA. There are no PASS/NO EVIDENCE labels here.

Question: is the post-pattern excess return bigger than what the SAME
stocks earn on ORDINARY days? v1's own report showed long AND short
patterns on the same MONITOR stocks both followed by outperformance vs
SPY — a signature of generic universe drift (which symbols get
watchlisted, or just beta), not necessarily a pattern-specific edge. This
script isolates that by comparing each detection's return against two
baselines built from the same symbol's/universe's ordinary days.

Returns here are FIXED-HORIZON and have NO STOPS (app/patterns/baseline.py
has the full rationale) — deliberately not compute_forward_outcome(),
which is stop-aware. A stop mechanically changes a loser's realized
return in a way ordinary days have no equivalent for, so a stop-aware
return wouldn't be comparable to a baseline built from ordinary days.
THESE NUMBERS THEREFORE DIFFER FROM THE v1 REPORT.

Reuses (imports, never copies) backtest_patterns.py's universe/tier
plumbing, and app.patterns.{detect,dedupe,universe_prices,stats}
unmodified. All NEW baseline logic lives in app/patterns/baseline.py.
Nothing in app/patterns/{config,zigzag,trend_pullback,flag_breakout,
double_top_bottom,stops,outcomes,dedupe,stats,detect}.py is edited by
this work.

Refuses to run if app/patterns/config.py has uncommitted changes — v1
must be frozen before this diagnostic is meaningful against it.

Usage (from webapp/backend/):
    python scripts/baseline_diagnostic.py
"""
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))  # for `import backtest_patterns`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # for `app.*`

import backtest_patterns as bt  # noqa: E402

from app.patterns import config  # noqa: E402
from app.patterns.baseline import (  # noqa: E402
    build_horizon_index,
    score_detection_with_baselines,
    spy_date_index,
)
from app.patterns.dedupe import dedupe_detections  # noqa: E402
from app.patterns.detect import detect_patterns  # noqa: E402
from app.patterns.stats import bootstrap_mean_ci  # noqa: E402
from app.patterns.universe_prices import fetch_with_fallback  # noqa: E402

HEADER = (
    "This is a post-hoc diagnostic on the data the v1 report used; it can only "
    "lower confidence, not raise it; upgrading any pattern requires held-out data."
)
CONFIG_PATH = Path(__file__).resolve().parent.parent / "app" / "patterns" / "config.py"


def _require_clean_config_or_abort() -> bool:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--", str(CONFIG_PATH)],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except Exception as exc:
        print(f"FATAL: could not check app/patterns/config.py's git status ({exc}); aborting.")
        return False
    if result.stdout.strip():
        print(
            "FATAL: app/patterns/config.py has uncommitted changes. v1 must be frozen and "
            "committed before this diagnostic can run against it. Aborting.\n"
            f"{result.stdout}"
        )
        return False
    return True


def _build_horizon_indexes(
    frames: dict[str, pd.DataFrame], spy_frame: pd.DataFrame, spy_idx: dict[str, int]
) -> dict[int, "object"]:
    """One build_horizon_index() call per horizon -- O(symbols x bars),
    done ONCE per universe/horizon and shared by both the per-detection
    scoring and the unconditional-drift computation below, so neither
    rescans the price history a second time."""
    return {
        h: build_horizon_index(frames, h, spy_frame, spy_idx, warmup_bars=config.SMA_WARMUP_BARS)
        for h in config.FORWARD_HORIZONS
    }


def _print_drift(label: str, horizon_indexes: dict[int, "object"]) -> None:
    print(f"\n  --- Unconditional drift (key diagnostic number), {label} ---")
    for h in config.FORWARD_HORIZONS:
        index = horizon_indexes[h]
        all_values: list[float] = []
        values_by_year: dict[str, list[float]] = defaultdict(list)
        for symbol, bars_list in index.eligible_by_symbol.items():
            values = index.excess_by_symbol_direction.get(symbol, {}).get("long", [])
            for bar, v in zip(bars_list, values):
                if v is not None:
                    all_values.append(v)
                    values_by_year[bar["entry_date"][:4]].append(v)
        overall = sum(all_values) / len(all_values) if all_values else float("nan")
        print(f"    T+{h}: overall mean excess return (long, all eligible bars) = {overall:.2%}  (n={len(all_values)})")
        for year in sorted(values_by_year):
            vs = values_by_year[year]
            print(f"      {year}: {sum(vs) / len(vs):.2%}  (n={len(vs)})")


def _run_diagnostics_for_universe(
    label: str,
    frames: dict[str, pd.DataFrame],
    spy_frame: pd.DataFrame,
    spy_idx: dict[str, int],
    seed: int | None = config.BOOTSTRAP_SEED,
) -> None:
    print(f"\n=== {label} ({len(frames)} symbols with price history) ===")
    if not frames:
        print("  No price history; skipping.")
        return

    bar_index = bt._bar_index_lookup(frames)

    all_detections: list[dict] = []
    for symbol, df in frames.items():
        all_detections.extend(detect_patterns(bt._frame_to_payload(df), symbol))
    raw_counts: dict[tuple, int] = defaultdict(int)
    for d in all_detections:
        raw_counts[(d["pattern"], d["direction"])] += 1

    deduped = dedupe_detections(all_detections, bar_index)
    dedup_counts: dict[tuple, int] = defaultdict(int)
    for d in deduped:
        dedup_counts[(d["pattern"], d["direction"])] += 1

    print("  Reproduction check (raw / deduped detection counts, vs. the v1 report; "
          "counts may differ slightly since the fetched data window has moved):")
    for key in sorted(set(raw_counts) | set(dedup_counts)):
        print(f"    {key[0]} / {key[1]}: raw={raw_counts.get(key, 0)} deduped={dedup_counts.get(key, 0)}")

    horizon_indexes = _build_horizon_indexes(frames, spy_frame, spy_idx)

    scored_by_group_horizon: dict[tuple, list[dict]] = defaultdict(list)
    for h in config.FORWARD_HORIZONS:
        index = horizon_indexes[h]
        for det in deduped:
            result = score_detection_with_baselines(det, index, bar_index)
            if result is not None:
                scored_by_group_horizon[(det["pattern"], det["direction"], h)].append(result)

    groups = sorted({(p, d) for (p, d, _h) in scored_by_group_horizon})
    for pattern, direction in groups:
        print(f"\n  {pattern} / {direction}:")
        for h in config.FORWARD_HORIZONS:
            rows = scored_by_group_horizon.get((pattern, direction, h), [])
            n = len(rows)
            distinct_symbols = len({r["symbol"] for r in rows})
            distinct_months = len({r["month"] for r in rows})
            mean_excess = sum(r["excess_return"] for r in rows) / n if n else float("nan")
            a_vals = [r["baseline_a"] for r in rows if r["baseline_a"] is not None]
            b_vals = [r["baseline_b"] for r in rows if r["baseline_b"] is not None]
            ab_a = [r["abnormal_a"] for r in rows if r["abnormal_a"] is not None]
            ab_b = [r["abnormal_b"] for r in rows if r["abnormal_b"] is not None]
            mean_baseline_a = sum(a_vals) / len(a_vals) if a_vals else float("nan")
            mean_baseline_b = sum(b_vals) / len(b_vals) if b_vals else float("nan")
            mean_abnormal_a = sum(ab_a) / len(ab_a) if ab_a else float("nan")
            mean_abnormal_b = sum(ab_b) / len(ab_b) if ab_b else float("nan")
            hit_rate_b = (sum(1 for v in ab_b if v > 0) / len(ab_b)) if ab_b else float("nan")

            values_by_month: dict[str, list[float]] = defaultdict(list)
            values_by_symbol: dict[str, list[float]] = defaultdict(list)
            for r in rows:
                if r["abnormal_b"] is not None:
                    values_by_month[r["month"]].append(r["abnormal_b"])
                    values_by_symbol[r["symbol"]].append(r["abnormal_b"])
            _, month_lo, month_hi = bootstrap_mean_ci(values_by_month, seed=seed)
            _, symbol_lo, symbol_hi = bootstrap_mean_ci(values_by_symbol, seed=seed)

            print(
                f"    T+{h}: n={n} symbols={distinct_symbols} months={distinct_months}\n"
                f"      mean excess={mean_excess:.2%}  baseline_A={mean_baseline_a:.2%}  baseline_B={mean_baseline_b:.2%}\n"
                f"      abnormal_A={mean_abnormal_a:.2%}  abnormal_B={mean_abnormal_b:.2%}  hit_rate(abnormal_B>0)={hit_rate_b:.1%}\n"
                f"      abnormal_B 90%CI month-cluster=[{month_lo:.2%}, {month_hi:.2%}]  "
                f"symbol-cluster=[{symbol_lo:.2%}, {symbol_hi:.2%}]"
            )

    _print_drift(label, horizon_indexes)


def main() -> int:
    print("Chart-pattern baseline diagnostic (post-hoc, exploratory)")
    print(HEADER + "\n")

    if not _require_clean_config_or_abort():
        return 1

    version, git_hash = bt._config_git_info()
    print(f"config CONFIG_VERSION={version}  app/patterns/config.py committed at {git_hash}\n")

    watchlist_rows = bt.bot_db.fetch_watchlist()
    tiers = bt._group_by_tier(watchlist_rows)
    if not tiers:
        print("No enabled watchlist symbols found; nothing to diagnose.")
        return 0

    spy_frames, _spy_failed, _ = fetch_with_fallback(["SPY"], config.BACKTEST_PERIOD)
    if "SPY" not in spy_frames:
        print("FATAL: could not fetch SPY history; aborting.")
        return 1
    spy_frame = spy_frames["SPY"]
    spy_idx = spy_date_index(spy_frame)

    tier_frames: dict[str, dict[str, pd.DataFrame]] = {}
    for tier_name in sorted(tiers):
        frames, failed, skipped_count = fetch_with_fallback(tiers[tier_name], config.BACKTEST_PERIOD)
        if failed:
            preview = failed[:10]
            more = f" (+{len(failed) - 10} more)" if len(failed) > 10 else ""
            print(f"[warn] tier {tier_name}: {len(failed)} symbols failed ({skipped_count} skipped past the fallback cap): {preview}{more}")
        tier_frames[tier_name] = frames
        _run_diagnostics_for_universe(f"Tier: {tier_name}", frames, spy_frame, spy_idx)

    pooled: dict[str, pd.DataFrame] = {}
    for tier_name, frames in tier_frames.items():
        if tier_name == "ETF_INDEX_CONTEXT":
            continue
        pooled.update(frames)
    _run_diagnostics_for_universe("ALL STOCK TIERS (POOLED, excludes ETF_INDEX_CONTEXT)", pooled, spy_frame, spy_idx)

    return 0


if __name__ == "__main__":
    sys.exit(main())
