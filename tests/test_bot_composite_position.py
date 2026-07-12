"""Tests for the additive /composite and /position bot commands.

Formatter tests use crafted result dicts (no network); handler tests mock
every collaborator at the bot module namespace (no network, no DB, no
Telegram). Existing handlers are untouched by these commands, so no
existing-behavior tests are duplicated here.
"""
import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import bot.telegram_bot as tb


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


COMPOSITE_PASS = {
    "symbol": "NVDA", "engine": "composite_v1", "bars_completed": 250,
    "last_completed_close": 210.96,
    "regime": {"regime": "BULL", "spy_available": True, "spy_close": 620.15,
               "spy_sma150": 585.32, "required_rs": 1.0, "required_score": 70},
    "hard_gate": {"passed": True, "price_above_sma150": True,
                  "sma150_above_sma200": True, "sma150": 195.44,
                  "sma200": 180.12, "reason": None},
    "layers": {
        "trend": {"points": 22.0, "max": 25, "rsi_pts": 15.0, "extension_pts": 7.0,
                  "breakout_pts": 0.0, "rsi": 61.2, "pct_from_sma": 7.9},
        "momentum": {"points": 15.0, "max": 25, "macd_pts": 15.0, "stoch_pts": 0.0,
                     "macd_hist": 0.42},
        "volume": {"points": 10.0, "max": 25, "relvol_pts": 0.0, "obv_pts": 10.0,
                   "rel_vol": 1.2, "session_fraction": 1.0, "obv_slope": 1.0},
        "relative_strength": {"points": 25.0, "max": 25, "rs_ratio": 1.08,
                              "required_rs": 1.0, "window_days": 60},
    },
    "total_score": 72.0, "flag_buy": True,
    "stop": {"atr": 2.95, "multiplier": 2.1, "stop_price": 204.76},
}

COMPOSITE_FAIL = {
    "symbol": "XYZ", "engine": "composite_v1", "bars_completed": 250,
    "last_completed_close": 50.0,
    "regime": {"regime": "UNKNOWN", "spy_available": False, "spy_close": None,
               "spy_sma150": None, "required_rs": 1.2, "required_score": 75},
    "hard_gate": {"passed": False, "price_above_sma150": False,
                  "sma150_above_sma200": True, "sma150": 55.0, "sma200": 52.0,
                  "reason": "price<=SMA150"},
    "layers": None, "total_score": 0.0, "flag_buy": False, "stop": None,
}

POSITION_BREACHED = {
    "symbol": "NVDA", "entry_price": 150.0, "entry_date": "2026-05-01",
    "last_completed_close": 210.96, "initial_stop": 134.95,
    "initial_stop_reconstructed": True,
    "initial_stop_detail": {"stop_price": 134.95, "multiplier": 2.0, "atr": 7.52},
    "r_multiple": 4.05, "highest_high_since_entry": 236.26, "current_atr": 7.17,
    "trailing_stop": {"stop_price": 214.77, "stage": 2, "basis": "chandelier",
                      "chandelier_multiplier": 3.0, "raised": True},
    "stop_breached": True,
    "exit_signals": {"signals": [], "any_fired": False, "entry_price": 150.0,
                     "r_multiple": 4.05, "details": {}},
    "partial_exit": {"suggested": True, "fraction": "33-50%",
                     "reason": "position at 4.05R >= 2.0R -- consider selling "
                               "33-50% and trailing the remainder"},
    "recommended_action": "trailing stop already breached at the last completed "
                          "close -- the position would have been stopped out; exit",
}

POSITION_HEALTHY = {
    **POSITION_BREACHED,
    "last_completed_close": 220.0, "stop_breached": False,
    "initial_stop_reconstructed": False,
    "initial_stop_detail": {"stop_price": 134.95, "multiplier": None, "atr": None},
    "exit_signals": {"signals": ["rsi_extreme"], "any_fired": True,
                     "entry_price": 150.0, "r_multiple": 4.5,
                     "details": {"rsi_extreme": {"rsi": 83.1, "threshold": 80.0}}},
    "trailing_stop": {"stop_price": 214.77, "stage": 2, "basis": "chandelier",
                      "chandelier_multiplier": 2.5, "raised": True},
    "recommended_action": "consider partial exit (33-50%); exit signals also "
                          "firing -- chandelier tightened",
}


class TestFmtComposite(unittest.TestCase):

    def test_pass_case_contains_layers_total_and_footer(self):
        text = tb._fmt_composite(COMPOSITE_PASS)
        self.assertIn("Composite Score: NVDA", text)
        self.assertIn("Regime: BULL", text)
        self.assertIn("Hard gate: ✅ PASS", text)
        for fragment in ("Trend/ext", "Momentum", "Volume", "Rel str",
                         "72.0/100", "(bar 70, BULL)",
                         "2.1x ATR(2.95)", "$204.76"):
            self.assertIn(fragment, text)
        self.assertIn("Observation mode", text)
        self.assertIn("Not comparable to the legacy /analyze score", text)
        self.assertLessEqual(len(text), tb.TELEGRAM_SAFE_CHUNK_LIMIT)

    def test_gate_fail_shows_reason_and_no_layers(self):
        text = tb._fmt_composite(COMPOSITE_FAIL)
        self.assertIn("Hard gate: ❌ FAIL (price<=SMA150)", text)
        self.assertIn("Total: 0 — layers not scored.", text)
        self.assertNotIn("Trend/ext", text)
        self.assertIn("Regime: UNKNOWN — SPY data unavailable", text)
        self.assertIn("Observation mode", text)

    def test_rs_none_renders_na(self):
        result = {**COMPOSITE_PASS, "layers": {
            **COMPOSITE_PASS["layers"],
            "relative_strength": {"points": 0.0, "max": 25, "rs_ratio": None,
                                  "required_rs": 1.0, "window_days": 60},
        }}
        text = tb._fmt_composite(result)
        self.assertIn("RS n/a", text)


class TestFmtPosition(unittest.TestCase):

    def test_breached_shows_banner_and_action(self):
        text = tb._fmt_position(POSITION_BREACHED)
        self.assertIn("Position Check: NVDA (LONG)", text)
        self.assertIn("STOP BREACHED", text)
        self.assertIn("$214.77", text)
        self.assertIn("Stage 2 — Chandelier 3.0x ATR", text)
        self.assertIn("reconstructed, 2.0x ATR", text)
        self.assertIn("ACTION: trailing stop already breached", text)
        # monotonic re-pass hint with the current stop baked in
        self.assertIn("/position NVDA 150 2026-05-01 214.77", text)
        self.assertLessEqual(len(text), tb.TELEGRAM_SAFE_CHUNK_LIMIT)

    def test_healthy_shows_signals_no_banner(self):
        text = tb._fmt_position(POSITION_HEALTHY)
        self.assertNotIn("STOP BREACHED", text)
        self.assertIn("Exit signals: rsi_extreme", text)
        self.assertIn("rsi=83.1", text)
        self.assertIn("Chandelier 2.5x ATR", text)
        self.assertIn("as given", text)
        self.assertIn("Advisory only", text)


def _update():
    u = MagicMock()
    u.effective_chat.id = 1
    return u


class TestHandlers(unittest.TestCase):
    """Arg-parsing / wiring only — every collaborator mocked at tb namespace."""

    def _ctx(self, args):
        c = MagicMock()
        c.args = args
        return c

    def _patches(self, **extra):
        base = {
            "_check_auth": AsyncMock(return_value=True),
            "get_language": MagicMock(return_value="en"),
            "_send": AsyncMock(),
        }
        base.update(extra)
        return [patch.object(tb, k, v) for k, v in base.items()], base

    def test_composite_no_args_sends_usage(self):
        patches, mocks = self._patches(get_historical=MagicMock())
        with patches[0], patches[1], patches[2], patches[3]:
            _run(tb.cmd_composite(_update(), self._ctx([])))
        sent = mocks["_send"].call_args.args[1]
        self.assertIn("Usage: /composite", sent)
        mocks["get_historical"].assert_not_called()

    def test_composite_happy_path_formats_result(self):
        patches, mocks = self._patches(
            get_historical=MagicMock(return_value=MagicMock()),
            is_market_open=MagicMock(return_value=False),
            compute_market_context=MagicMock(return_value={"regime": "BULL"}),
            composite_score=MagicMock(return_value=COMPOSITE_PASS),
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            _run(tb.cmd_composite(_update(), self._ctx(["nvda"])))
        final = mocks["_send"].call_args_list[-1].args[1]
        self.assertIn("Composite Score: NVDA", final)
        mocks["composite_score"].assert_called_once()
        # log=False: the bot renders its own view
        self.assertFalse(mocks["composite_score"].call_args.kwargs.get("log", True))

    def test_position_bad_date_sends_usage_without_fetch(self):
        patches, mocks = self._patches(get_historical=MagicMock())
        with patches[0], patches[1], patches[2], patches[3]:
            _run(tb.cmd_position(_update(), self._ctx(["NVDA", "150", "not-a-date"])))
        sent = mocks["_send"].call_args.args[1]
        self.assertIn("Usage: /position", sent)
        mocks["get_historical"].assert_not_called()

    def test_position_stop_below_entry_is_initial_and_floor(self):
        eval_mock = MagicMock(return_value=POSITION_BREACHED)
        patches, mocks = self._patches(
            get_historical=MagicMock(return_value=MagicMock()),
            is_market_open=MagicMock(return_value=False),
            evaluate_position=eval_mock,
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            _run(tb.cmd_position(_update(), self._ctx(["NVDA", "150", "2026-05-01", "140"])))
        kwargs = eval_mock.call_args.kwargs
        self.assertEqual(kwargs["initial_stop"], 140.0)
        self.assertEqual(kwargs["previous_stop"], 140.0)

    def test_position_stop_above_entry_is_floor_only(self):
        # Re-passing a trailing stop above entry must NOT be treated as the
        # initial stop (that would make compute_r_multiple raise).
        eval_mock = MagicMock(return_value=POSITION_BREACHED)
        patches, mocks = self._patches(
            get_historical=MagicMock(return_value=MagicMock()),
            is_market_open=MagicMock(return_value=False),
            evaluate_position=eval_mock,
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            _run(tb.cmd_position(_update(), self._ctx(["NVDA", "150", "2026-05-01", "214.77"])))
        kwargs = eval_mock.call_args.kwargs
        self.assertIsNone(kwargs["initial_stop"])
        self.assertEqual(kwargs["previous_stop"], 214.77)


if __name__ == "__main__":
    unittest.main()
