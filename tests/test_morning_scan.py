"""
Characterization tests for run_morning_scan() (PR 0a, docs/SETUP_SPEC.md §14).

Not previously covered by any test (flagged in docs/CURRENT_ALERT_LOGIC.md
§10). Locks in current behavior: the SCAN_MIN_SCORE filter, descending-score
ordering, SCAN_TOP_N truncation, and the existing per-symbol exception skip
-- before anything in docs/SETUP_SPEC.md's later PRs touches shared code.

No network, no Telegram, no real DB -- fetchers and full_analysis are mocked
at the agent.core namespace, mirroring tests/test_alert_gates.py's convention.
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd

from config import SCAN_MIN_SCORE, SCAN_TOP_N

CHAT_ID = "123456789"


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _df() -> pd.DataFrame:
    return pd.DataFrame({
        "open": [99.0], "close": [100.0], "high": [101.0], "low": [98.0], "volume": [1e6],
    })


def _analysis(symbol: str, score: int) -> dict:
    return {
        "symbol": symbol,
        "score": score,
        "verdict": "BUY",
        "rsi": 55.0,
        "triggered_signals": ["price_above_sma150", "rsi_healthy_range"],
        "stop_loss": 95.0,
        "take_profit": 115.0,
    }


class TestMorningScan(unittest.TestCase):

    def _run_scan(self, scores: dict[str, int], raise_for: set[str] | None = None):
        """Run run_morning_scan() once against a watchlist built from
        `scores`' keys. `raise_for` names symbols whose full_analysis()
        call raises instead of returning a score."""
        import agent.core as core
        raise_for = raise_for or set()

        def fake_full_analysis(symbol, df, price):
            if symbol in raise_for:
                raise RuntimeError(f"boom for {symbol}")
            return _analysis(symbol, scores[symbol])

        bot = MagicMock()
        bot.send_message = AsyncMock()

        with patch.object(core, "get_active_watchlist",
                          return_value={"Cat": list(scores.keys())}), \
             patch.object(core, "get_historical", return_value=_df()), \
             patch.object(core, "get_current_price",
                          return_value={"price": 100.0, "change_pct": 1.0,
                                        "volume": 1_000_000, "high": 101.0,
                                        "low": 98.0, "open": 99.0}), \
             patch.object(core, "full_analysis", side_effect=fake_full_analysis), \
             patch.object(core, "get_language", return_value="en"), \
             patch.object(core, "send_morning_scan", new=AsyncMock()) as send_scan:
            _run(core.run_morning_scan(bot, CHAT_ID))

        return send_scan

    def test_scan_min_score_filter(self):
        """Symbols below SCAN_MIN_SCORE are excluded; at/above it, included."""
        scores = {"LOW": SCAN_MIN_SCORE - 5, "HIGH": SCAN_MIN_SCORE + 5}
        send_scan = self._run_scan(scores)
        results = send_scan.call_args.args[2]
        symbols = [r["symbol"] for r in results]
        self.assertNotIn("LOW", symbols)
        self.assertIn("HIGH", symbols)

    def test_descending_score_order(self):
        scores = {"MID": SCAN_MIN_SCORE, "TOP": SCAN_MIN_SCORE + 20, "LOWEST": SCAN_MIN_SCORE + 5}
        send_scan = self._run_scan(scores)
        results = send_scan.call_args.args[2]
        symbols_in_order = [r["symbol"] for r in results]
        self.assertEqual(symbols_in_order, ["TOP", "LOWEST", "MID"])

    def test_scan_top_n_truncation(self):
        scores = {f"SYM{i}": SCAN_MIN_SCORE + i for i in range(SCAN_TOP_N + 3)}
        send_scan = self._run_scan(scores)
        results = send_scan.call_args.args[2]
        self.assertEqual(len(results), SCAN_TOP_N)
        # Truncation keeps the highest scores (descending order already proven above).
        expected_top_symbols = {
            f"SYM{i}" for i in range(SCAN_TOP_N + 3)
        }
        kept_scores = sorted((r["score"] for r in results), reverse=True)
        all_scores_sorted = sorted(scores.values(), reverse=True)
        self.assertEqual(kept_scores, all_scores_sorted[:SCAN_TOP_N])

    def test_per_symbol_exception_is_skipped_not_fatal(self):
        """Existing behavior (agent/core.py:206-218): one symbol's
        full_analysis() raising does not abort the scan for the others."""
        scores = {"GOOD": SCAN_MIN_SCORE + 10, "BAD": SCAN_MIN_SCORE + 10}
        send_scan = self._run_scan(scores, raise_for={"BAD"})
        results = send_scan.call_args.args[2]
        symbols = [r["symbol"] for r in results]
        self.assertIn("GOOD", symbols)
        self.assertNotIn("BAD", symbols)


if __name__ == "__main__":
    unittest.main()
