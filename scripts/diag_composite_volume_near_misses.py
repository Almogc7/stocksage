"""
AD-HOC DIAGNOSTIC — NOT part of the test suite, NOT scheduled, NOT wired
into anything live. Retroactive comparison only (per CLAUDE.md: the
composite engine / use_composite_volume flag is opt-in only and must not
be threaded into check_alerts() or any live path).

Question: over the 2026-07-15..2026-07-23 window, for the 8 symbols that
showed up as legacy/composite disagreement pairs (WAL, GS, IRM, ASML, TER,
ANET, ETN, MOD), how many symbol-days would flip from "blocked at Gate 6
(missing volume_spike)" to "clears Gate 6" if full_analysis() were run with
use_composite_volume=True instead of the live default (False)?

This does NOT modify agent/core.py or config.py. It only calls
full_analysis() twice (False, then True) on truncated historical data and
compares triggered_signals / gate outcomes. Read-only, no DB writes.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from analyzers.technical import full_analysis
from data.fetcher import get_historical
from config import ALERT_MIN_SCORE, ALERT_VERDICTS, ALERT_REQUIRE_GREEN_CANDLE

SYMBOLS = ["WAL", "GS", "IRM", "ASML", "TER", "ANET", "ETN", "MOD"]
WINDOW_START = "2026-07-15"
WINDOW_END = "2026-07-23"
REQUIRED_SIGNALS = ("rsi_healthy_range", "volume_spike")


def gate_eval(analysis: dict, last_row: pd.Series) -> dict:
    score = analysis["score"]
    verdict = analysis["verdict"]
    triggered = analysis.get("triggered_signals", [])

    gate5_pass = score >= ALERT_MIN_SCORE and verdict in ALERT_VERDICTS
    missing = [s for s in REQUIRED_SIGNALS if s not in triggered]
    gate6_pass = len(missing) == 0
    last_candle_green = float(last_row["close"]) > float(last_row["open"])
    gate7_pass = last_candle_green if ALERT_REQUIRE_GREEN_CANDLE else True

    return {
        "score": score,
        "verdict": verdict,
        "missing_signals": missing,
        "gate5_pass": gate5_pass,
        "gate6_pass": gate6_pass,
        "gate7_pass": gate7_pass,
        "would_fire": gate5_pass and gate6_pass and gate7_pass,
    }


def main() -> None:
    rows = []
    for symbol in SYMBOLS:
        df_full = get_historical(symbol, period="3y")
        if df_full is None or df_full.empty:
            print(f"{symbol}: fetch failed, skipping")
            continue

        idx_dates = df_full.index.normalize()
        window_mask = (idx_dates >= pd.Timestamp(WINDOW_START)) & (idx_dates <= pd.Timestamp(WINDOW_END))
        trading_days = df_full.index[window_mask]

        for day in trading_days:
            df_trunc = df_full[df_full.index <= day]
            if len(df_trunc) < 210:
                continue

            current_price = float(df_trunc["close"].iloc[-1])
            last_row = df_trunc.iloc[-1]

            res_false = full_analysis(symbol, df_trunc, current_price, use_composite_volume=False)
            res_true = full_analysis(symbol, df_trunc, current_price, use_composite_volume=True)

            g_false = gate_eval(res_false, last_row)
            g_true = gate_eval(res_true, last_row)

            near_miss = (
                g_false["gate5_pass"]
                and not g_false["gate6_pass"]
                and g_false["missing_signals"] == ["volume_spike"]
            )

            rows.append({
                "symbol": symbol,
                "date": day.strftime("%Y-%m-%d"),
                "score": g_false["score"],
                "verdict": g_false["verdict"],
                "near_miss_false": near_miss,
                "gate6_pass_false": g_false["gate6_pass"],
                "gate6_pass_true": g_true["gate6_pass"],
                "gate7_pass": g_false["gate7_pass"],
                "would_fire_false": g_false["would_fire"],
                "would_fire_true": g_true["would_fire"],
                "flips_gate6": near_miss and g_true["gate6_pass"],
                "flips_to_firing": near_miss and g_true["would_fire"] and not g_false["would_fire"],
            })

    df_out = pd.DataFrame(rows)
    if df_out.empty:
        print("No rows produced.")
        return

    print(f"Total symbol-days evaluated: {len(df_out)}\n")

    near_misses = df_out[df_out["near_miss_false"]]
    print(f"Near-misses under use_composite_volume=False "
          f"(Gate 5 passed, blocked ONLY by missing volume_spike at Gate 6): {len(near_misses)}\n")

    if not near_misses.empty:
        print(near_misses[["symbol", "date", "score", "verdict",
                            "gate6_pass_true", "gate7_pass", "flips_to_firing"]]
              .to_string(index=False))
        print()
        print(f"Of these, now clear Gate 6 with use_composite_volume=True: "
              f"{near_misses['gate6_pass_true'].sum()} / {len(near_misses)}")
        print(f"Of these, would fully fire (Gate 5+6+7 all pass) with the flag on: "
              f"{near_misses['flips_to_firing'].sum()} / {len(near_misses)}")

    print("\n--- Full symbol-day table (all 8 symbols x window) ---")
    print(df_out[["symbol", "date", "score", "verdict", "gate6_pass_false",
                   "gate6_pass_true", "gate7_pass", "would_fire_false", "would_fire_true"]]
          .to_string(index=False))


if __name__ == "__main__":
    main()
