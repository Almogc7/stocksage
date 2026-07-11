"""
Tests for the alert loop's gate structure (agent/core.py check_alerts).

The loop must consume full_analysis() outputs (score / verdict /
triggered_signals) and never re-derive RSI, moving-average, or volume
thresholds itself. These tests drive check_alerts() with a mocked bot,
mocked fetch layer, and crafted full_analysis() return values to prove
each gate fires or skips purely on those outputs.

No network, no Telegram, no real DB — the DB cooldown gate and log_alert
are patched at the agent.core namespace.
"""
import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd

CHAT_ID = "123456789"


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _df(last_green: bool = True) -> pd.DataFrame:
    """Minimal 2-row OHLC df; full_analysis is mocked, so only the candle
    columns used by the green-candle gate matter."""
    close = [100.0, 105.0] if last_green else [100.0, 95.0]
    return pd.DataFrame({
        "open":   [99.0, 100.0],
        "close":  close,
        "high":   [101.0, 106.0],
        "low":    [98.0, 94.0],
        "volume": [1e6, 2e6],
    })


def _analysis(score=80, verdict="STRONG BUY", triggered=None, rsi=55.0) -> dict:
    return {
        "symbol": "NVDA",
        "score": score,
        "verdict": verdict,
        "rsi": rsi,
        "triggered_signals": triggered if triggered is not None
        else ["price_above_ema150", "rsi_healthy_range", "volume_spike"],
        "stop_loss": 95.0,
        "take_profit": 115.0,
    }


class TestAlertGates(unittest.TestCase):

    def setUp(self):
        import agent.core as core
        self.core = core
        core._alerted_this_session.clear()

    def tearDown(self):
        self.core._alerted_this_session.clear()

    def _check(self, analysis=None, change_pct=2.0, alerted_today=False,
               df=None, market_open=False):
        """Run check_alerts once for a single-symbol watchlist and report
        whether an alert fired (log_alert called)."""
        bot = MagicMock()
        bot.send_message = AsyncMock()
        bot.send_photo = AsyncMock()

        price_data = {"price": 105.0, "change_pct": change_pct}

        with patch.object(self.core, "get_active_watchlist",
                          return_value={"AI & Semiconductors": ["NVDA"]}), \
             patch.object(self.core, "get_multiple_prices",
                          return_value={"NVDA": price_data}), \
             patch.object(self.core, "was_alerted_today",
                          return_value=alerted_today), \
             patch.object(self.core, "get_historical",
                          return_value=df if df is not None else _df()), \
             patch.object(self.core, "full_analysis",
                          return_value=analysis or _analysis()) as fa, \
             patch.object(self.core, "is_market_open",
                          return_value=market_open), \
             patch.object(self.core, "generate_chart_image",
                          return_value=None), \
             patch.object(self.core, "log_alert") as log_alert:
            _run(self.core.check_alerts(bot, CHAT_ID))
        return log_alert, fa, bot

    # ── Firing path ──────────────────────────────────────────────────────────

    def test_alert_fires_when_all_outputs_pass(self):
        log_alert, _, bot = self._check()
        log_alert.assert_called_once()
        self.assertEqual(log_alert.call_args.args[0], "NVDA")
        self.assertEqual(log_alert.call_args.args[1], "BUY_SIGNAL")
        bot.send_message.assert_awaited_once()

    # ── Gates that consume full_analysis outputs ─────────────────────────────

    def test_skips_on_low_score(self):
        log_alert, _, _ = self._check(_analysis(score=50, verdict="WATCH"))
        log_alert.assert_not_called()

    def test_skips_on_non_alert_verdict_even_with_high_score(self):
        log_alert, _, _ = self._check(_analysis(score=70, verdict="WATCH"))
        log_alert.assert_not_called()

    def test_skips_when_rsi_healthy_signal_missing(self):
        """RSI judgment comes only from triggered_signals — a numerically
        'fine-looking' rsi field must not rescue a missing signal."""
        analysis = _analysis(
            triggered=["price_above_ema150", "rsi_acceptable_zone", "volume_spike"],
            rsi=67.0,
        )
        log_alert, _, _ = self._check(analysis)
        log_alert.assert_not_called()

    def test_skips_when_volume_spike_signal_missing(self):
        analysis = _analysis(
            triggered=["price_above_ema150", "rsi_healthy_range"],
        )
        log_alert, _, _ = self._check(analysis)
        log_alert.assert_not_called()

    def test_no_independent_rsi_threshold_in_loop(self):
        """An out-of-band rsi VALUE with the healthy signal present must
        still fire — proves the loop does not re-check raw RSI numbers.
        (full_analysis would never emit this combination; the point is that
        the loop trusts the signal, not the number.)"""
        analysis = _analysis(rsi=72.0)  # default triggered includes rsi_healthy_range
        log_alert, _, _ = self._check(analysis)
        log_alert.assert_called_once()

    # ── Non-analysis gates ───────────────────────────────────────────────────

    def test_skips_on_insufficient_price_change_without_analyzing(self):
        log_alert, fa, _ = self._check(change_pct=0.2)
        log_alert.assert_not_called()
        fa.assert_not_called()  # cheap gate must short-circuit the fetch

    def test_skips_when_already_alerted_today(self):
        log_alert, fa, _ = self._check(alerted_today=True)
        log_alert.assert_not_called()
        fa.assert_not_called()

    def test_skips_on_red_candle_when_market_closed(self):
        log_alert, _, _ = self._check(df=_df(last_green=False))
        log_alert.assert_not_called()

    def test_uses_previous_candle_when_market_open(self):
        """Market open → last row is in-progress; gate must judge iloc[-2].
        Here iloc[-1] is red but iloc[-2] (close 100 > open 99) is green."""
        log_alert, _, _ = self._check(df=_df(last_green=False), market_open=True)
        log_alert.assert_called_once()

    def test_session_dedup_blocks_second_pass(self):
        log_alert1, _, _ = self._check()
        log_alert1.assert_called_once()
        # _alerted_this_session now holds today's key; DB gate still False
        log_alert2, fa2, _ = self._check()
        log_alert2.assert_not_called()
        fa2.assert_not_called()


if __name__ == "__main__":
    unittest.main()
