"""End-to-end no-look-ahead property test over the FULL backtest chain —
not just detection. For seeded random series, at several truncation
points, every detection already confirmed (confirmed_on <= bar n-1) must
have an IDENTICAL stop level, entry price, and (whenever the relevant
window has already fully elapsed by bar n-1) exit and per-horizon excess
return, whether or not later bars exist.

Specifically checked, per (detection, truncation point):
  - ATR at confirmed_on is identical between the full and truncated run
    (proves stop_level() depends only on bars up to confirmed_on, never
    later ones).
  - stop_level() itself is identical.
  - Once the entry bar (the bar after confirmed_on) is visible: the
    "excluded" status, entry_date and entry_price are identical.
  - Once the barrier/timeout exit bar is visible: exit_date, exit_price,
    first_barrier_hit and r_multiple are identical.
  - For every forward horizon (5/10/20) whose own close bar is already
    visible: the signed excess return vs. the (synthetic) SPY series is
    identical.

If this test ever fails, the fix belongs in the detector/outcome code,
never in this test."""
import unittest

import pandas as pd

from app.patterns import config
from app.patterns.double_top_bottom import detect_double_top_bottom
from app.patterns.flag_breakout import detect_flag_breakout
from app.patterns.indicators import atr as atr_series
from app.patterns.outcomes import compute_forward_outcome, excess_return
from app.patterns.stops import stop_level
from app.patterns.trend_pullback import detect_trend_pullback
from app.patterns.types import Detection
from tests._pattern_fixtures import random_ohlcv

SEED_COUNT = 20
N_BARS = 450
TRUNCATION_COUNT = 6
MIN_TRUNCATION_N = 230  # past SMA150 + slope-lookback warm-up
SPY_SEED_OFFSET = 100_000  # keeps the synthetic "SPY" series independent of the symbol series


def _truncation_points(n_bars: int) -> list[int]:
    span = n_bars - MIN_TRUNCATION_N
    step = max(1, span // TRUNCATION_COUNT)
    return list(range(MIN_TRUNCATION_N, n_bars, step))[:TRUNCATION_COUNT]


def _all_detections(df: pd.DataFrame, symbol: str = "TEST") -> list[Detection]:
    dets: list[Detection] = []
    for period in config.SMA_PERIODS:
        dets.extend(detect_trend_pullback(df, symbol, sma_period=period))
    dets.extend(detect_flag_breakout(df, symbol))
    dets.extend(detect_double_top_bottom(df, symbol))
    return dets


def _score(detection: Detection, bars: pd.DataFrame, spy_bars: pd.DataFrame) -> dict | None:
    """Runs the real chain: ATR-at-confirm -> stop_level() ->
    compute_forward_outcome() -> excess_return() per horizon. Returns
    None only if ATR isn't available yet at confirmed_on (shouldn't
    happen for an already-confirmed detection, but guarded defensively)."""
    dates = bars.index.to_list()
    idx_of_date = {d: i for i, d in enumerate(dates)}
    confirm_idx = idx_of_date[detection.confirmed_on]
    atr_at_confirm = atr_series(bars).iloc[confirm_idx]
    if pd.isna(atr_at_confirm):
        return None
    atr_at_confirm = float(atr_at_confirm)

    stop = stop_level(detection, atr_at_confirm)
    outcome = compute_forward_outcome(bars, detection.confirmed_on, detection.direction, stop)

    excess_by_horizon: dict[int, float | None] = {}
    if not outcome["excluded"]:
        entry_date = outcome["entry_date"]
        entry_price = outcome["entry_price"]
        spy_dates = spy_bars.index.to_list()
        for h in config.FORWARD_HORIZONS:
            exit_close = outcome["closes"].get(h)
            if exit_close is None or entry_date not in spy_dates:
                excess_by_horizon[h] = None
                continue
            position_return = (
                (exit_close - entry_price) / entry_price
                if detection.direction == "long"
                else (entry_price - exit_close) / entry_price
            )
            spy_entry_idx = spy_dates.index(entry_date)
            spy_h_idx = spy_entry_idx + h - 1
            if spy_h_idx >= len(spy_dates):
                excess_by_horizon[h] = None
                continue
            spy_entry = float(spy_bars["close"].iloc[spy_entry_idx])
            spy_exit = float(spy_bars["close"].iloc[spy_h_idx])
            excess_by_horizon[h] = excess_return(position_return, detection.direction, spy_entry, spy_exit)

    return {"atr_at_confirm": atr_at_confirm, "stop": stop, "outcome": outcome, "excess_by_horizon": excess_by_horizon}


class RandomizedBacktestChainNoLookaheadTests(unittest.TestCase):
    def test_full_chain_stable_under_truncation(self):
        seed_n_combinations = 0
        detections_checked = 0

        for seed in range(SEED_COUNT):
            df = random_ohlcv(seed=seed, n_bars=N_BARS)
            spy_df = random_ohlcv(seed=seed + SPY_SEED_OFFSET, n_bars=N_BARS)
            dates = df.index.to_list()
            idx_of_date = {d: i for i, d in enumerate(dates)}

            full_dets = _all_detections(df)
            full_scores = {id(d): _score(d, df, spy_df) for d in full_dets}

            for n in _truncation_points(len(df)):
                seed_n_combinations += 1
                cutoff = dates[n - 1]
                df_t = df.iloc[:n]
                spy_t = spy_df.iloc[:n]
                trunc_dets = _all_detections(df_t)
                trunc_by_dict = {str(d.to_dict()): d for d in trunc_dets}

                for d_full in full_dets:
                    if d_full.confirmed_on > cutoff:
                        continue
                    key = str(d_full.to_dict())
                    self.assertIn(
                        key,
                        trunc_by_dict,
                        msg=f"seed={seed} n={n}: an already-confirmed detection vanished under truncation",
                    )
                    d_trunc = trunc_by_dict[key]
                    detections_checked += 1

                    score_full = full_scores[id(d_full)]
                    score_trunc = _score(d_trunc, df_t, spy_t)
                    self.assertIsNotNone(score_full)
                    self.assertIsNotNone(score_trunc)

                    msg_base = f"seed={seed} n={n} pattern={d_full.pattern} direction={d_full.direction} confirmed_on={d_full.confirmed_on}"

                    # ATR at confirmed_on, and therefore the stop level,
                    # must be identical -- proves stop_level() only uses
                    # ATR computed from bars up to confirmed_on.
                    self.assertEqual(score_trunc["atr_at_confirm"], score_full["atr_at_confirm"], msg=msg_base + " (atr_at_confirm)")
                    self.assertEqual(score_trunc["stop"], score_full["stop"], msg=msg_base + " (stop)")

                    confirm_idx = idx_of_date[d_full.confirmed_on]
                    entry_idx = confirm_idx + 1
                    if entry_idx > n - 1:
                        continue  # entry bar not visible yet at this truncation point

                    outcome_full = score_full["outcome"]
                    outcome_trunc = score_trunc["outcome"]
                    self.assertEqual(outcome_trunc["excluded"], outcome_full["excluded"], msg=msg_base + " (excluded)")
                    if outcome_full["excluded"]:
                        continue

                    self.assertEqual(outcome_trunc["entry_date"], outcome_full["entry_date"], msg=msg_base + " (entry_date)")
                    self.assertEqual(outcome_trunc["entry_price"], outcome_full["entry_price"], msg=msg_base + " (entry_price)")

                    if outcome_full["exit_price"] is not None:
                        exit_idx_full = idx_of_date[outcome_full["exit_date"]]
                        if exit_idx_full <= n - 1:
                            self.assertEqual(outcome_trunc["exit_date"], outcome_full["exit_date"], msg=msg_base + " (exit_date)")
                            self.assertEqual(outcome_trunc["exit_price"], outcome_full["exit_price"], msg=msg_base + " (exit_price)")
                            self.assertEqual(
                                outcome_trunc["first_barrier_hit"], outcome_full["first_barrier_hit"], msg=msg_base + " (first_barrier_hit)"
                            )
                            self.assertEqual(outcome_trunc["r_multiple"], outcome_full["r_multiple"], msg=msg_base + " (r_multiple)")

                    for h in config.FORWARD_HORIZONS:
                        h_idx = entry_idx + h - 1
                        if h_idx <= n - 1:
                            full_er = score_full["excess_by_horizon"].get(h)
                            trunc_er = score_trunc["excess_by_horizon"].get(h)
                            self.assertIsNotNone(full_er, msg=msg_base + f" (excess_return T+{h} full)")
                            self.assertEqual(trunc_er, full_er, msg=msg_base + f" (excess_return T+{h})")

        print(
            f"\n[random no-lookahead, full backtest chain] {SEED_COUNT} seeds x up to {TRUNCATION_COUNT} "
            f"truncation points = {seed_n_combinations} (seed, n) combinations; "
            f"{detections_checked} individual detection checks across them; 0 failures."
        )
        self.assertEqual(seed_n_combinations, SEED_COUNT * TRUNCATION_COUNT)
        self.assertGreater(detections_checked, 0)


if __name__ == "__main__":
    unittest.main()
