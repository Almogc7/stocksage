"""
Walk-forward backtest for the round-1 pattern detectors
(trend_pullback, flag_breakout, double_top_bottom).

Manual CLI only — no scheduler, matching the bot's own dormant
scripts/run_strong_trend_scan.py. Outside the bot's own scripts/
directory entirely, per webapp/CLAUDE.md rule 1 (all new code under
webapp/).

NO TUNING ON THE SAME DATA IT REPORTS: every threshold lives in
app/patterns/config.py, frozen before this script is ever run against
real data (see that file's own docstring for the freeze rule). This
script only REPORTS — it must never be used to pick thresholds by
observing its own output. Re-tuning requires bumping CONFIG_VERSION in
config.py, documenting why, and running a fresh, held-out period.

Writes nothing to any database, bot or webapp — this is a report
artifact, not persisted state (webapp/CLAUDE.md rule 9: pattern
detections are informational only, never written to a bot table). The
ONE write path that can fire is incidental: fetch_with_fallback()'s
capped per-symbol retry (at most UNIVERSE_FETCH_FALLBACK_CAP symbols)
falls back to app.prices.get_prices(), which upserts the webapp's own
price_cache row in state_db — not a bot table, and capped specifically
so a bad run can't flood it.

Fetches live yfinance data for every symbol in every enabled watchlist
tier, over BACKTEST_PERIOD (5y). This is intentionally NOT exercised by
the automated test suite or by any other part of this codebase — only a
human deliberately running this file does.

Usage (from webapp/backend/):
    python scripts/backtest_patterns.py
"""
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import bot_db  # noqa: E402
from app.patterns import config  # noqa: E402
from app.patterns.dedupe import dedupe_detections  # noqa: E402
from app.patterns.detect import detect_patterns  # noqa: E402
from app.patterns.indicators import atr as atr_series  # noqa: E402
from app.patterns.outcomes import compute_forward_outcome, excess_return  # noqa: E402
from app.patterns.stats import label_pattern_direction, passes_decision_rule, summarize_pattern  # noqa: E402
from app.patterns.stops import stop_level  # noqa: E402
from app.patterns.types import Detection  # noqa: E402
from app.patterns.universe_prices import fetch_with_fallback  # noqa: E402


def _config_git_info() -> tuple[str, str]:
    config_path = Path(__file__).resolve().parent.parent / "app" / "patterns" / "config.py"
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%H", "--", str(config_path)],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        commit_hash = result.stdout.strip() or "unknown (no commit touches config.py yet)"
    except Exception:
        commit_hash = "unknown (not a git repo or git unavailable)"
    return config.CONFIG_VERSION, commit_hash


def _group_by_tier(watchlist_rows: list[dict]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = defaultdict(list)
    for row in watchlist_rows:
        groups[row["wl_state"]].append(row["symbol"])
    return dict(groups)


def _bar_index_lookup(frames: dict[str, pd.DataFrame]) -> dict[str, dict[str, int]]:
    return {symbol: {date: i for i, date in enumerate(df.index.to_list())} for symbol, df in frames.items()}


def _frame_to_payload(df: pd.DataFrame) -> dict:
    candles = [
        {"time": d, "open": float(r.open), "high": float(r.high), "low": float(r.low), "close": float(r.close)}
        for d, r in df.iterrows()
    ]
    volume = [{"time": d, "value": int(r.volume)} for d, r in df.iterrows()]
    return {"candles": candles, "volume": volume}


def _score_detections(
    detections: list[dict],
    frames: dict[str, pd.DataFrame],
    bar_index: dict[str, dict[str, int]],
    spy_frame: pd.DataFrame,
) -> tuple[list[dict], dict]:
    """Scores each (already deduplicated) detection: fixed stop (stops.py),
    forward outcome with entry-timing/gap handling (outcomes.py), signed
    excess return vs SPY per horizon (outcomes.excess_return). Returns
    (scored, excluded_counts_by_pattern_direction)."""
    scored: list[dict] = []
    excluded_counts: dict[tuple, int] = defaultdict(int)
    spy_dates = spy_frame.index.to_list()

    for det in detections:
        symbol = det["symbol"]
        bars = frames.get(symbol)
        if bars is None:
            continue
        idx = bar_index[symbol].get(det["confirmed_on"])
        if idx is None:
            continue
        atr_at_confirm = atr_series(bars).iloc[idx]
        if pd.isna(atr_at_confirm):
            continue

        det_obj = Detection(
            pattern=det["pattern"],
            direction=det["direction"],
            symbol=symbol,
            start_date=det["start_date"],
            end_date=det["end_date"],
            confirmed_on=det["confirmed_on"],
            levels=det["levels"],
            rules=det["rules"],
        )
        stop = stop_level(det_obj, float(atr_at_confirm))
        outcome = compute_forward_outcome(bars, det["confirmed_on"], det["direction"], stop)

        key = (det["pattern"], det["direction"])
        if outcome["excluded"]:
            excluded_counts[key] += 1
            continue

        entry_date = outcome["entry_date"]
        entry_price = outcome["entry_price"]
        excess_by_horizon: dict[int, float | None] = {}
        for h in config.FORWARD_HORIZONS:
            exit_close = outcome["closes"].get(h)
            if exit_close is None or entry_date not in spy_dates:
                excess_by_horizon[h] = None
                continue
            position_return = (
                (exit_close - entry_price) / entry_price
                if det["direction"] == "long"
                else (entry_price - exit_close) / entry_price
            )
            spy_entry_idx = spy_dates.index(entry_date)
            spy_h_idx = spy_entry_idx + h - 1
            if spy_h_idx >= len(spy_dates):
                excess_by_horizon[h] = None
                continue
            spy_entry = float(spy_frame["close"].iloc[spy_entry_idx])
            spy_exit = float(spy_frame["close"].iloc[spy_h_idx])
            excess_by_horizon[h] = excess_return(position_return, det["direction"], spy_entry, spy_exit)

        scored.append(
            {
                "symbol": symbol,
                "pattern": det["pattern"],
                "direction": det["direction"],
                "year": int(entry_date[:4]),
                "excess_return_by_horizon": excess_by_horizon,
            }
        )

    return scored, dict(excluded_counts)


def _group_scored(scored: list[dict]) -> dict[tuple, list[dict]]:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for s in scored:
        groups[(s["pattern"], s["direction"])].append(s)
    return dict(groups)


def run_tier(
    tier_name: str, symbols: list[str], spy_frame: pd.DataFrame, seed: int | None = config.BOOTSTRAP_SEED
) -> tuple[int, dict[tuple, bool]]:
    """Runs detect -> dedupe -> score -> summarize for one watchlist tier.
    Returns (cells, pass_by_group): `cells` is the number of (pattern,
    direction, horizon-or-year) report cells printed, for the overall
    multiple-comparisons count; `pass_by_group` is {(pattern, direction):
    passed_decision_rule_in_this_tier}, fed into the cross-tier TIER RULE
    labeling after every tier has run."""
    print(f"\n=== Tier: {tier_name} ({len(symbols)} symbols) ===")
    frames, failed, skipped_count = fetch_with_fallback(symbols, config.BACKTEST_PERIOD)
    if failed:
        preview = failed[:10]
        more = f" (+{len(failed) - 10} more)" if len(failed) > 10 else ""
        print(f"  [warn] {len(failed)} symbols failed ({skipped_count} skipped past the fallback cap): {preview}{more}")
    if not frames:
        print("  No price history fetched for this tier; skipping.")
        return 0, {}

    bar_index = _bar_index_lookup(frames)

    all_detections: list[dict] = []
    total_bars = 0
    warmup_bars_total = 0
    for symbol, df in frames.items():
        all_detections.extend(detect_patterns(_frame_to_payload(df), symbol))
        total_bars += len(df)
        warmup_bars_total += min(config.SMA_WARMUP_BARS, len(df))

    if total_bars:
        effective = total_bars - warmup_bars_total
        print(
            f"  SMA200 warm-up: {config.SMA_WARMUP_BARS} bars (~{config.SMA_WARMUP_BARS / 21:.1f} months) "
            f"excluded from the detectable range per symbol; effective detection window ~= "
            f"{effective} of {total_bars} fetched bars (~{config.BACKTEST_PERIOD})."
        )
    if tier_name == "ACTIVE":
        print(
            "  CAVEAT: The ACTIVE tier is selected using data up to today (promotion/demotion already "
            "happened), which biases toward symbols that were already trending — long-pattern hit rates on "
            "ACTIVE should be read as upper bounds, not out-of-sample results."
        )

    raw_count_by_group: dict[tuple, int] = defaultdict(int)
    for d in all_detections:
        raw_count_by_group[(d["pattern"], d["direction"])] += 1

    deduped = dedupe_detections(all_detections, bar_index)
    dedup_count_by_group: dict[tuple, int] = defaultdict(int)
    for d in deduped:
        dedup_count_by_group[(d["pattern"], d["direction"])] += 1

    scored, excluded_counts = _score_detections(deduped, frames, bar_index, spy_frame)

    cells = 0
    pass_by_group: dict[tuple, bool] = {}
    for (pattern, direction), group_scored in sorted(_group_scored(scored).items()):
        raw_n = raw_count_by_group.get((pattern, direction), 0)
        dedup_n = dedup_count_by_group.get((pattern, direction), 0)
        excluded_n = excluded_counts.get((pattern, direction), 0)
        print(f"\n  {pattern} / {direction}: raw={raw_n} deduped={dedup_n} excluded={excluded_n}")

        primary_row = None
        for h in config.FORWARD_HORIZONS:
            row = summarize_pattern(pattern, direction, group_scored, horizon=h, seed=seed)
            if h == config.PRIMARY_HORIZON:
                primary_row = row
            cells += 1
            flags = ""
            if row["inconclusive"]:
                flags += " [INCONCLUSIVE]"
            if row["concentrated"]:
                flags += " [CONCENTRATED]"
            primary_tag = " (PRIMARY)" if h == config.PRIMARY_HORIZON else ""
            print(
                f"    T+{h}{primary_tag}: n={row['n']} symbols={row['distinct_symbols']} "
                f"hit_rate={row['hit_rate']:.1%} mean={row['mean_excess_return']:.2%} "
                f"median={row['median_excess_return']:.2%} "
                f"90%CI=[{row['ci_lower']:.2%}, {row['ci_upper']:.2%}]{flags}"
            )

        yearly_rows = []
        for year in sorted({s["year"] for s in group_scored}):
            year_scored = [s for s in group_scored if s["year"] == year]
            row = summarize_pattern(pattern, direction, year_scored, horizon=config.PRIMARY_HORIZON, seed=seed)
            yearly_rows.append(row)
            flags = " [INCONCLUSIVE]" if row["inconclusive"] else ""
            print(f"    {year} (T+{config.PRIMARY_HORIZON}): n={row['n']} mean={row['mean_excess_return']:.2%}{flags}")
            cells += 1

        passed = primary_row is not None and passes_decision_rule(primary_row, yearly_rows)
        pass_by_group[(pattern, direction)] = passed
        verdict = "PASS" if passed else "NO EVIDENCE"
        print(f"    VERDICT (pre-registered rule, T+{config.PRIMARY_HORIZON}): {verdict}")

    return cells, pass_by_group


def main() -> int:
    print("Chart-pattern backtest -- round 1 (trend_pullback, flag_breakout, double_top_bottom)")
    print("NO TUNING ON THE SAME DATA IT REPORTS.\n")

    version, git_hash = _config_git_info()
    print(f"config CONFIG_VERSION={version}  app/patterns/config.py last changed at commit {git_hash}")
    print("thresholds frozen before this run.\n")

    watchlist_rows = bot_db.fetch_watchlist()
    tiers = _group_by_tier(watchlist_rows)
    if not tiers:
        print("No enabled watchlist symbols found; nothing to backtest.")
        return 0

    spy_frames, spy_failed, _ = fetch_with_fallback(["SPY"], config.BACKTEST_PERIOD)
    if "SPY" not in spy_frames:
        print("FATAL: could not fetch SPY history; aborting.")
        return 1
    spy_frame = spy_frames["SPY"]

    total_cells = 0
    pass_by_tier: dict[tuple, dict[str, bool]] = defaultdict(dict)
    for tier_name in sorted(tiers):
        cells, pass_by_group = run_tier(tier_name, tiers[tier_name], spy_frame)
        total_cells += cells
        for group, passed in pass_by_group.items():
            pass_by_tier[group][tier_name] = passed

    print(
        f"\n{total_cells} cells examined across patterns/directions/tiers/horizons-or-years -- with this "
        "many comparisons, some will look good by chance. Do not single out the best-looking cell as a "
        "discovery; only patterns that hold up consistently across years and tiers are worth a second look."
    )

    print("\n=== Summary: label per (pattern, direction), per the tier rule ===")
    if not pass_by_tier:
        print("  (no groups scored in any tier)")
    for pattern, direction in sorted(pass_by_tier):
        label = label_pattern_direction(pass_by_tier[(pattern, direction)])
        tier_detail = ", ".join(
            f"{tier}={'PASS' if passed else 'NO EVIDENCE'}" for tier, passed in sorted(pass_by_tier[(pattern, direction)].items())
        )
        print(f"  {pattern} / {direction}: {label}  [{tier_detail}]")

    return 0


if __name__ == "__main__":
    sys.exit(main())
