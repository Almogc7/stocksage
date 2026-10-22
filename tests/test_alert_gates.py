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
        else ["price_above_sma150", "rsi_healthy_range", "volume_spike"],
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
            triggered=["price_above_sma150", "rsi_acceptable_zone", "volume_spike"],
            rsi=67.0,
        )
        log_alert, _, _ = self._check(analysis)
        log_alert.assert_not_called()

    def test_skips_when_volume_spike_signal_missing(self):
        analysis = _analysis(
            triggered=["price_above_sma150", "rsi_healthy_range"],
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

    # ── Per-symbol isolation (PR 0a) ─────────────────────────────────────────

    def test_one_symbols_analysis_raising_does_not_abort_the_cycle(self):
        """One symbol's full_analysis() raises; the other symbol still
        alerts, and the failed symbol leaves no dedup/cooldown state."""
        bot = MagicMock()
        bot.send_message = AsyncMock()
        bot.send_photo = AsyncMock()

        prices = {
            "GOOD": {"price": 105.0, "change_pct": 2.0},
            "BAD":  {"price": 105.0, "change_pct": 2.0},
        }

        def fake_full_analysis(symbol, df, price):
            if symbol == "BAD":
                raise RuntimeError("boom")
            return _analysis()

        with patch.object(self.core, "get_active_watchlist",
                          return_value={"Cat": ["GOOD", "BAD"]}), \
             patch.object(self.core, "get_multiple_prices", return_value=prices), \
             patch.object(self.core, "was_alerted_today", return_value=False), \
             patch.object(self.core, "get_historical", return_value=_df()), \
             patch.object(self.core, "full_analysis", side_effect=fake_full_analysis), \
             patch.object(self.core, "is_market_open", return_value=False), \
             patch.object(self.core, "generate_chart_image", return_value=None), \
             patch.object(self.core, "log_alert") as log_alert:
            _run(self.core.check_alerts(bot, CHAT_ID))

        log_alert.assert_called_once()
        self.assertEqual(log_alert.call_args.args[0], "GOOD")
        good_key = self.core._session_key("GOOD")
        bad_key = self.core._session_key("BAD")
        self.assertIn(good_key, self.core._alerted_this_session)
        self.assertNotIn(bad_key, self.core._alerted_this_session)

    def test_failure_after_send_keeps_session_dedup_set(self):
        """log_alert() raises AFTER the message was sent: the in-memory
        session dedup mark stays set, so the next tick does not re-send.

        With only one symbol in this watchlist, that one symbol failing
        also means "all N (=1) symbols failed" -- the systemic-failure
        re-raise (see test_all_symbols_failing_raises below) fires on this
        first call too. That's correct, expected behavior, not a regression:
        a one-symbol cycle where the only symbol failed IS a fully-failed
        cycle. The dedup mark is still set before the raise, since marking
        happens before log_alert() is even called."""
        bot = MagicMock()
        bot.send_message = AsyncMock()
        bot.send_photo = AsyncMock()

        price_data = {"price": 105.0, "change_pct": 2.0}

        def run_once():
            with patch.object(self.core, "get_active_watchlist",
                              return_value={"Cat": ["NVDA"]}), \
                 patch.object(self.core, "get_multiple_prices",
                              return_value={"NVDA": price_data}), \
                 patch.object(self.core, "was_alerted_today", return_value=False), \
                 patch.object(self.core, "get_historical", return_value=_df()), \
                 patch.object(self.core, "full_analysis", return_value=_analysis()) as fa, \
                 patch.object(self.core, "is_market_open", return_value=False), \
                 patch.object(self.core, "generate_chart_image", return_value=None), \
                 patch.object(self.core, "log_alert",
                              side_effect=RuntimeError("db write failed")):
                _run(self.core.check_alerts(bot, CHAT_ID))
            return fa

        with self.assertRaises(RuntimeError):
            run_once()
        bot.send_message.assert_awaited_once()
        key = self.core._session_key("NVDA")
        self.assertIn(key, self.core._alerted_this_session)

        # Second tick: in-memory dedup (Gate 2) now skips before full_analysis
        # is reached again -- no raise this time, since the symbol never
        # enters the except branch (Gate 2's `continue` isn't a failure).
        fa2 = run_once()
        fa2.assert_not_called()

    def test_all_symbols_failing_raises(self):
        """Every symbol that entered the try block raised -> check_alerts()
        re-raises, so the cycle is reported as a failure exactly like before
        per-symbol isolation existed (job()'s handler logs it; the ping is
        skipped -- see test_run_checks_ping_behavior below)."""
        bot = MagicMock()
        bot.send_message = AsyncMock()
        bot.send_photo = AsyncMock()

        prices = {
            "A": {"price": 105.0, "change_pct": 2.0},
            "B": {"price": 105.0, "change_pct": 2.0},
        }

        def always_raise(symbol, df, price):
            raise RuntimeError(f"boom for {symbol}")

        with patch.object(self.core, "get_active_watchlist",
                          return_value={"Cat": ["A", "B"]}), \
             patch.object(self.core, "get_multiple_prices", return_value=prices), \
             patch.object(self.core, "was_alerted_today", return_value=False), \
             patch.object(self.core, "get_historical", return_value=_df()), \
             patch.object(self.core, "full_analysis", side_effect=always_raise), \
             patch.object(self.core, "is_market_open", return_value=False), \
             patch.object(self.core, "generate_chart_image", return_value=None), \
             patch.object(self.core, "log_alert") as log_alert:
            with self.assertRaisesRegex(RuntimeError, "all 2 symbols failed"):
                _run(self.core.check_alerts(bot, CHAT_ID))

        log_alert.assert_not_called()
        self.assertNotIn(self.core._session_key("A"), self.core._alerted_this_session)
        self.assertNotIn(self.core._session_key("B"), self.core._alerted_this_session)

    def test_zero_symbols_entered_does_not_raise(self):
        """Every symbol's price_data is None (never reaches the try block
        at all) -> entered_count stays 0 -> no systemic-failure re-raise,
        behavior identical to before this PR."""
        bot = MagicMock()
        bot.send_message = AsyncMock()
        bot.send_photo = AsyncMock()

        with patch.object(self.core, "get_active_watchlist",
                          return_value={"Cat": ["A", "B"]}), \
             patch.object(self.core, "get_multiple_prices",
                          return_value={"A": None, "B": None}), \
             patch.object(self.core, "full_analysis") as fa, \
             patch.object(self.core, "log_alert") as log_alert:
            _run(self.core.check_alerts(bot, CHAT_ID))  # must not raise

        fa.assert_not_called()
        log_alert.assert_not_called()

    def test_run_checks_ping_behavior(self):
        """Through run_checks(): the healthcheck ping is NOT sent when every
        symbol failed (the cycle itself now raises before reaching the ping
        call, agent/core.py:402-413), and IS sent in a mixed cycle where at
        least one symbol succeeded."""
        bot = MagicMock()
        bot.send_message = AsyncMock()
        bot.send_photo = AsyncMock()

        def run_checks_with(full_analysis_side_effect):
            prices = {
                "A": {"price": 105.0, "change_pct": 2.0},
                "B": {"price": 105.0, "change_pct": 2.0},
            }
            with patch.object(self.core, "get_active_watchlist",
                              return_value={"Cat": ["A", "B"]}), \
                 patch.object(self.core, "get_multiple_prices", return_value=prices), \
                 patch.object(self.core, "was_alerted_today", return_value=False), \
                 patch.object(self.core, "get_historical", return_value=_df()), \
                 patch.object(self.core, "full_analysis",
                              side_effect=full_analysis_side_effect), \
                 patch.object(self.core, "is_market_open", return_value=True), \
                 patch.object(self.core, "generate_chart_image", return_value=None), \
                 patch.object(self.core, "log_alert"), \
                 patch.object(self.core, "_ping_healthcheck") as ping:
                try:
                    _run(self.core.run_checks(bot, CHAT_ID))
                except RuntimeError:
                    pass
            return ping

        def all_fail(symbol, df, price):
            raise RuntimeError(f"boom for {symbol}")

        def mixed(symbol, df, price):
            if symbol == "B":
                raise RuntimeError("boom for B")
            return _analysis()

        ping_all_fail = run_checks_with(all_fail)
        ping_all_fail.assert_not_called()

        ping_mixed = run_checks_with(mixed)
        ping_mixed.assert_called_once()


if __name__ == "__main__":
    unittest.main()
