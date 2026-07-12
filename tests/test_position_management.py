"""Tests for analyzers/position_management.py — staged trailing stops and
advisory exit signals. All synthetic data, no network, no DB.
"""
import unittest

import pandas as pd

from analyzers import position_management as pm
from analyzers.composite import _stop_multiplier

ENTRY, INITIAL_STOP = 100.0, 95.0   # risk = 5.0


def make_ohlcv(closes: list[float], volumes: list[float] | None = None,
               opens: list[float] | None = None,
               highs: list[float] | None = None,
               lows: list[float] | None = None,
               start: str = "2026-01-05") -> pd.DataFrame:
    n = len(closes)
    if volumes is None:
        volumes = [1_000_000.0] * n
    if opens is None:
        opens = [closes[0]] + closes[:-1]  # open at prior close
    if highs is None:
        highs = [max(o, c) * 1.005 for o, c in zip(opens, closes)]
    if lows is None:
        lows = [min(o, c) * 0.995 for o, c in zip(opens, closes)]
    dates = pd.bdate_range(start=start, periods=n)
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
        index=pd.DatetimeIndex(dates, name="Date"),
    )


def ramp(start: float, pct: float, n: int) -> list[float]:
    out, price = [], start
    for _ in range(n):
        price *= (1 + pct / 100.0)
        out.append(round(price, 4))
    return out


# ── Initial stop / R-multiple ────────────────────────────────────────────────

class TestInitialStop(unittest.TestCase):

    def test_reuses_composite_multiplier_bands(self):
        for score in (0, 65, 70, 75, 80, 90, 100):
            res = pm.compute_initial_stop(100.0, 2.0, score)
            self.assertEqual(res["multiplier"], _stop_multiplier(score))
            self.assertEqual(res["stop_price"], round(100.0 - res["multiplier"] * 2.0, 4))

    def test_r_multiple(self):
        self.assertEqual(pm.compute_r_multiple(ENTRY, INITIAL_STOP, 110.0), 2.0)
        self.assertEqual(pm.compute_r_multiple(ENTRY, INITIAL_STOP, 95.0), -1.0)

    def test_r_multiple_rejects_stop_above_entry(self):
        with self.assertRaises(ValueError):
            pm.compute_r_multiple(100.0, 101.0, 110.0)


# ── Staged trailing stop ─────────────────────────────────────────────────────

class TestTrailingStopStages(unittest.TestCase):

    def _trail(self, r, hh=120.0, atr=2.0, prev=None, tighten=False):
        return pm.compute_trailing_stop(
            ENTRY, INITIAL_STOP, hh, atr, r, previous_stop=prev, tighten=tighten
        )

    def test_stage0_holds_initial_stop(self):
        res = self._trail(r=0.5)
        self.assertEqual(res["stage"], 0)
        self.assertEqual(res["stop_price"], INITIAL_STOP)
        self.assertEqual(res["basis"], "initial")
        self.assertFalse(res["raised"])

    def test_stage1_breakeven_at_exactly_1r(self):
        res = self._trail(r=1.0)
        self.assertEqual(res["stage"], 1)
        self.assertEqual(res["stop_price"], ENTRY)
        self.assertEqual(res["basis"], "breakeven")
        self.assertTrue(res["raised"])

    def test_stage1_still_breakeven_at_1_5r(self):
        res = self._trail(r=1.5)
        self.assertEqual(res["stage"], 1)
        self.assertEqual(res["stop_price"], ENTRY)

    def test_stage2_chandelier_above_1_5r(self):
        res = self._trail(r=1.6, hh=120.0, atr=2.0)
        self.assertEqual(res["stage"], 2)
        self.assertEqual(res["basis"], "chandelier")
        self.assertEqual(res["chandelier_multiplier"], pm.CHANDELIER_MULT)
        self.assertEqual(res["stop_price"], 120.0 - 3.0 * 2.0)  # 114.0

    def test_stage2_floored_at_breakeven(self):
        # Huge ATR pushes the chandelier below entry — breakeven must win
        res = self._trail(r=1.6, hh=112.0, atr=8.0)  # chandelier = 88
        self.assertEqual(res["stage"], 2)
        self.assertEqual(res["stop_price"], ENTRY)
        self.assertEqual(res["basis"], "breakeven")

    def test_tighten_uses_smaller_multiplier(self):
        res = self._trail(r=2.0, hh=120.0, atr=2.0, tighten=True)
        self.assertEqual(res["chandelier_multiplier"], pm.CHANDELIER_MULT_TIGHT)
        self.assertEqual(res["stop_price"], 120.0 - 2.5 * 2.0)  # 115.0

    def test_monotonic_never_decreases(self):
        # Day 1: stage 2 sets a high chandelier stop
        day1 = self._trail(r=2.5, hh=120.0, atr=2.0)
        self.assertEqual(day1["stop_price"], 114.0)
        # Day 2: volatility expands — raw chandelier would be LOWER
        day2 = self._trail(r=2.2, hh=120.0, atr=4.0, prev=day1["stop_price"])
        self.assertEqual(day2["stop_price"], 114.0)   # held, not lowered
        self.assertEqual(day2["basis"], "previous")
        self.assertFalse(day2["raised"])
        # Day 3: new high raises it again
        day3 = self._trail(r=3.0, hh=126.0, atr=2.0, prev=day2["stop_price"])
        self.assertEqual(day3["stop_price"], 120.0)
        self.assertTrue(day3["raised"])

    def test_monotonic_across_r_regression(self):
        # R falls back below 1.0 after breakeven was reached — the stop must
        # NOT drop back to the initial stop.
        day1 = self._trail(r=1.2)
        self.assertEqual(day1["stop_price"], ENTRY)
        day2 = self._trail(r=0.7, prev=day1["stop_price"])
        self.assertEqual(day2["stop_price"], ENTRY)
        self.assertEqual(day2["basis"], "previous")


# ── Exit signals ─────────────────────────────────────────────────────────────

def divergence_df() -> pd.DataFrame:
    """Sharp rally to a peak (strong RSI), pullback, then a choppy grind to a
    marginal new high (weaker RSI) — textbook bearish divergence."""
    closes = [100.0] * 30                      # base
    closes += ramp(closes[-1], 2.0, 5)         # sharp rally → peak A, hot RSI
    closes += ramp(closes[-1], -0.6, 4)        # pullback
    # choppy sideways grind BELOW peak A: down days cool the RSI while the
    # price stays under the peak until the final pop
    price = closes[-1]
    for up, down in [(1.0, -0.9)] * 5:
        price *= (1 + up / 100); closes.append(round(price, 4))
        price *= (1 + down / 100); closes.append(round(price, 4))
    closes += ramp(closes[-1], 1.7, 3)         # new high in the last 3 bars
    return make_ohlcv(closes)


def climax_df(volume_mult: float = 3.5, red: bool = True,
              runup_pct: float = 2.0) -> pd.DataFrame:
    closes = [100.0] * 35 + ramp(100.0, runup_pct, 10)
    peak = closes[-1]
    last_open = round(peak * 1.001, 4)
    last_close = round(peak * (0.975 if red else 1.017), 4)
    last_high = round(peak * 1.018, 4)
    closes.append(last_close)
    n = len(closes)
    volumes = [1_000_000.0] * (n - 1) + [1_000_000.0 * volume_mult]
    df = make_ohlcv(closes, volumes=volumes)
    df.iloc[-1, df.columns.get_loc("open")] = last_open
    df.iloc[-1, df.columns.get_loc("high")] = last_high
    df.iloc[-1, df.columns.get_loc("low")] = round(last_close * 0.995, 4)
    return df


class TestExitSignals(unittest.TestCase):

    def test_bearish_rsi_divergence_fires_on_crafted_data(self):
        res = pm.check_exit_signals(divergence_df(), ENTRY, 1.8)
        self.assertIn("bearish_rsi_divergence", res["signals"])
        d = res["details"]["bearish_rsi_divergence"]
        self.assertGreater(d["recent_high"], d["earlier_high"])
        self.assertLess(d["rsi_at_recent_high"], d["rsi_at_earlier_high"])

    def test_no_divergence_without_new_high(self):
        # Rally then pure decline — no recent high above the earlier peak
        closes = [100.0] * 30 + ramp(100.0, 2.0, 5) + ramp(110.4, -0.5, 15)
        res = pm.check_exit_signals(make_ohlcv(closes), ENTRY, 1.0)
        self.assertNotIn("bearish_rsi_divergence", res["signals"])

    def test_climax_volume_fires_on_red_high_volume_bar_after_runup(self):
        res = pm.check_exit_signals(climax_df(), ENTRY, 2.5)
        self.assertIn("climax_volume", res["signals"])
        d = res["details"]["climax_volume"]
        self.assertGreaterEqual(d["rel_vol"], 3.0)
        self.assertTrue(d["red_bar"])
        self.assertGreaterEqual(d["move_pct_prior"], 15.0)

    def test_climax_needs_3x_volume(self):
        res = pm.check_exit_signals(climax_df(volume_mult=2.0), ENTRY, 2.5)
        self.assertNotIn("climax_volume", res["signals"])

    def test_climax_needs_prior_15pct_move(self):
        res = pm.check_exit_signals(climax_df(runup_pct=0.5), ENTRY, 2.5)
        self.assertNotIn("climax_volume", res["signals"])

    def test_climax_green_bar_without_wick_does_not_fire(self):
        df = climax_df(red=False)
        # make the green bar close at its high — no upper wick at all
        df.iloc[-1, df.columns.get_loc("high")] = df["close"].iloc[-1]
        res = pm.check_exit_signals(df, ENTRY, 2.5)
        self.assertNotIn("climax_volume", res["signals"])

    def test_rsi_extreme_fires_on_relentless_rally(self):
        closes = [100.0] * 25 + ramp(100.0, 3.0, 15)
        res = pm.check_exit_signals(make_ohlcv(closes), ENTRY, 3.0)
        self.assertIn("rsi_extreme", res["signals"])
        self.assertGreaterEqual(res["details"]["rsi_extreme"]["rsi"], 80.0)

    def test_rsi_not_extreme_on_quiet_tape(self):
        # Gentle uptrend WITH down days — RSI is 100 whenever the lookback
        # window contains zero losses, so a "quiet tape" must include red bars.
        closes = [100.0] * 20
        price = 100.0
        for up, down in [(0.5, -0.3)] * 10:
            price *= (1 + up / 100); closes.append(round(price, 4))
            price *= (1 + down / 100); closes.append(round(price, 4))
        res = pm.check_exit_signals(make_ohlcv(closes), ENTRY, 0.5)
        self.assertNotIn("rsi_extreme", res["signals"])

    def test_completed_bars_only_discipline(self):
        # The climax bar is the LAST row. If the market is open, that row is
        # the in-progress session and must be sliced off — no climax signal.
        df = climax_df()
        open_res = pm.check_exit_signals(df, ENTRY, 2.5, market_open=True)
        self.assertNotIn("climax_volume", open_res["signals"])
        closed_res = pm.check_exit_signals(df, ENTRY, 2.5, market_open=False)
        self.assertIn("climax_volume", closed_res["signals"])


# ── Partial exit ─────────────────────────────────────────────────────────────

class TestPartialExit(unittest.TestCase):

    def test_below_threshold(self):
        res = pm.suggest_partial_exit(1.99)
        self.assertFalse(res["suggested"])
        self.assertIsNone(res["fraction"])

    def test_at_threshold(self):
        res = pm.suggest_partial_exit(2.0)
        self.assertTrue(res["suggested"])
        self.assertEqual(res["fraction"], "33-50%")


# ── Orchestrator ─────────────────────────────────────────────────────────────

class TestEvaluatePosition(unittest.TestCase):

    def _df(self):
        # 60 quiet bars, then a 15-bar rally: entry near the start of the rally
        closes = [100.0] * 60 + ramp(100.0, 1.5, 15)
        return make_ohlcv(closes)

    def test_evaluate_with_given_stop(self):
        df = self._df()
        entry_date = pd.to_datetime(df.index[60]).date()  # first rally bar
        res = pm.evaluate_position("NVDA", df, 101.5, entry_date,
                                   initial_stop=97.0)
        self.assertEqual(res["initial_stop"], 97.0)
        self.assertFalse(res["initial_stop_reconstructed"])
        self.assertGreater(res["r_multiple"], 1.5)          # rally ≈ +25%
        self.assertEqual(res["trailing_stop"]["stage"], 2)
        # stop must be strictly above breakeven by now and below the close
        self.assertGreater(res["trailing_stop"]["stop_price"], 101.5)
        self.assertLess(res["trailing_stop"]["stop_price"], res["last_completed_close"])

    def test_evaluate_reconstructs_initial_stop(self):
        df = self._df()
        entry_date = pd.to_datetime(df.index[60]).date()
        res = pm.evaluate_position("NVDA", df, 101.5, entry_date)  # no stop/score
        self.assertTrue(res["initial_stop_reconstructed"])
        self.assertEqual(res["initial_stop_detail"]["multiplier"], 2.0)  # score None → 2.0x
        self.assertLess(res["initial_stop"], 101.5)

    def test_evaluate_respects_previous_stop(self):
        df = self._df()
        entry_date = pd.to_datetime(df.index[60]).date()
        high_prev = 999.0  # absurdly high previous stop — must be held, not lowered
        res = pm.evaluate_position("NVDA", df, 101.5, entry_date,
                                   initial_stop=97.0, previous_stop=high_prev)
        self.assertEqual(res["trailing_stop"]["stop_price"], high_prev)
        self.assertEqual(res["trailing_stop"]["basis"], "previous")

    def test_stop_breached_overrides_action(self):
        # Rally then a slide of more than 3 ATR off the high while R stays
        # above 1.5 (still stage 2): the chandelier ends up above the last
        # close — the module must say "stopped out", not quote a stop above
        # the market.
        closes = [100.0] * 60 + ramp(100.0, 1.5, 15) + ramp(125.0, -1.5, 6)
        df = make_ohlcv(closes)
        entry_date = pd.to_datetime(df.index[60]).date()
        res = pm.evaluate_position("NVDA", df, 101.5, entry_date, initial_stop=97.0)
        self.assertTrue(res["stop_breached"])
        self.assertGreaterEqual(res["trailing_stop"]["stop_price"],
                                res["last_completed_close"])
        self.assertIn("stopped out", res["recommended_action"])

    def test_partial_exit_reflected_in_action(self):
        df = self._df()
        entry_date = pd.to_datetime(df.index[60]).date()
        res = pm.evaluate_position("NVDA", df, 101.5, entry_date, initial_stop=97.0)
        if res["r_multiple"] >= 2.0:
            self.assertTrue(res["partial_exit"]["suggested"])
            self.assertIn("partial exit", res["recommended_action"])


if __name__ == "__main__":
    unittest.main()
