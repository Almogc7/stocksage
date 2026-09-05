"""
AD-HOC DIAGNOSTIC — NOT part of the test suite, NOT scheduled, NOT wired
into anything live. Component-level follow-up to
docs/composite_vs_legacy_tracking.md's 17-row signal set (10
legacy/composite disagreement pairs + 7 composite-exclusive fires from the
2026-07-15..2026-07-23 window).

For each of the 17 rows: reconstruct the 4 composite layer scores
(trend/extension, momentum, volume, relative-strength) via
composite_score() on truncated historical data (SPY truncated to the same
date for regime/RS), and pair them with a FRESH forward-return check as of
today. Read-only, no DB writes, does not touch config.py/agent/core.py.
"""
import sys
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from analyzers.composite import compute_market_context, composite_score
from data.fetcher import get_historical

# (symbol, date, group) — group: "disagree" (A, 1-10) or "exclusive" (B, 11-17)
SIGNALS = [
    ("WAL", "2026-07-22", "disagree"),
    ("WAL", "2026-07-23", "disagree"),
    ("BEP", "2026-07-23", "disagree"),
    ("ETN", "2026-07-23", "disagree"),
    ("MOD", "2026-07-23", "disagree"),
    ("GS",  "2026-07-22", "disagree"),
    ("IRM", "2026-07-21", "disagree"),
    ("TER", "2026-07-23", "disagree"),
    ("ASML","2026-07-15", "disagree"),
    ("ANET","2026-07-21", "disagree"),
    ("HBNC","2026-07-17", "exclusive"),
    ("HBNC","2026-07-20", "exclusive"),
    ("HBNC","2026-07-21", "exclusive"),
    ("IRM", "2026-07-17", "exclusive"),
    ("IRM", "2026-07-20", "exclusive"),
    ("IRM", "2026-07-22", "exclusive"),
    ("IRM", "2026-07-23", "exclusive"),
]


def main() -> None:
    spy_full = get_historical("SPY", period="3y")
    today = datetime.now(timezone.utc).date()

    rows = []
    cache: dict[str, pd.DataFrame] = {}
    for symbol, date_str, group in SIGNALS:
        if symbol not in cache:
            cache[symbol] = get_historical(symbol, period="3y")
        df_full = cache[symbol]
        if df_full is None or df_full.empty:
            print(f"{symbol}: fetch failed, skipping")
            continue

        target = pd.Timestamp(date_str)
        df_trunc = df_full[df_full.index <= target]
        spy_trunc = spy_full[spy_full.index <= target]
        if len(df_trunc) < 210 or len(spy_trunc) < 150:
            print(f"{symbol} {date_str}: insufficient history, skipping")
            continue

        entry_price = float(df_trunc["close"].iloc[-1])
        context = compute_market_context(spy_df=spy_trunc, market_open=False)
        comp = composite_score(symbol, df_trunc, context, market_open=False, log=False)

        if not comp["hard_gate"]["passed"]:
            print(f"{symbol} {date_str}: hard gate failed on reconstruction "
                  f"({comp['hard_gate']['reason']}) — unexpected, check data")
            continue

        lay = comp["layers"]

        # Forward return as of today, using the full (untruncated) series.
        future = df_full[df_full.index > target]
        if future.empty:
            fwd_return = None
            last_price = None
            days_elapsed = None
        else:
            last_price = float(future["close"].iloc[-1])
            fwd_return = round((last_price / entry_price - 1.0) * 100.0, 2)
            days_elapsed = len(future)

        rows.append({
            "symbol": symbol, "date": date_str, "group": group,
            "total_score": comp["total_score"],
            "trend": lay["trend"]["points"],
            "momentum": lay["momentum"]["points"],
            "volume": lay["volume"]["points"],
            "rs": lay["relative_strength"]["points"],
            "rs_ratio": lay["relative_strength"]["rs_ratio"],
            "required_rs": lay["relative_strength"]["required_rs"],
            "entry_price": round(entry_price, 2),
            "last_price": round(last_price, 2) if last_price else None,
            "days_elapsed": days_elapsed,
            "fwd_return": fwd_return,
        })

    df_out = pd.DataFrame(rows)
    pd.set_option("display.width", 160)
    print(f"As of {today}\n")
    print(df_out.to_string(index=False))

    print("\n--- Group averages (fresh check) ---")
    for g in ("disagree", "exclusive"):
        sub = df_out[df_out["group"] == g]
        if not sub.empty:
            print(f"{g}: n={len(sub)}  avg fwd return={sub['fwd_return'].mean():.2f}%  "
                  f"median={sub['fwd_return'].median():.2f}%  "
                  f"up={sum(sub['fwd_return']>0)}  down={sum(sub['fwd_return']<0)}")

    print("\n--- IRM composite-exclusive rows (14-17) ---")
    irm_excl = df_out[(df_out["symbol"] == "IRM") & (df_out["group"] == "exclusive")]
    print(irm_excl.to_string(index=False))

    print("\n--- Per-layer correlation with forward return (Pearson, n=%d) ---" % len(df_out))
    for layer in ("trend", "momentum", "volume", "rs"):
        corr = df_out[layer].corr(df_out["fwd_return"])
        print(f"{layer:10s}: r = {corr:.3f}")

    print("\n--- Per-layer correlation, composite-exclusive only (n=%d) ---" %
          len(df_out[df_out['group']=='exclusive']))
    excl = df_out[df_out["group"] == "exclusive"]
    for layer in ("trend", "momentum", "volume", "rs"):
        corr = excl[layer].corr(excl["fwd_return"])
        print(f"{layer:10s}: r = {corr:.3f}  (mean pts on winners vs losers below)")
        winners = excl[excl["fwd_return"] > 0][layer]
        losers = excl[excl["fwd_return"] <= 0][layer]
        w = f"{winners.mean():.1f}" if len(winners) else "n/a"
        l = f"{losers.mean():.1f}" if len(losers) else "n/a"
        print(f"             winners(n={len(winners)}) avg={w}   losers(n={len(losers)}) avg={l}")

    out_path = Path(__file__).resolve().parent.parent / "docs" / "_diag_layer_breakdown_raw.csv"
    df_out.to_csv(out_path, index=False)
    print(f"\nRaw table written to {out_path} (scratch — not referenced by any code)")


if __name__ == "__main__":
    main()
