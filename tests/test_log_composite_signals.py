"""Tests for scripts/log_composite_signals.py — the nightly composite-signal
logging job (schema v10). All price data is synthetic (no network); DB
tests use a temp SQLite file via the same _reload_db pattern as the other
schema tests. The real db/stocksage.db is never touched.

Silent, observation-mode-only job: these tests also assert it never writes
to alerts/alert_signals (the live alert path's tables).
"""
import importlib
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from tests.fixtures import make_trending_df

import log_composite_signals as lcs


def _reload_db(db_path: str):
    import db.database as dbmod
    importlib.reload(dbmod)
    dbmod.DB_PATH = Path(db_path)
    return dbmod


# A strong bull uptrend (trend=0.0035, seed=41) clears the hard gate AND
# scores 79.5 total against the BULL required_score=70 bar (verified
# directly against compute_market_context(spy_df=_spy_df())) — chosen
# because the milder trend=0.002 fixture used in test_composite_score.py
# does NOT reliably clear flag_buy (volume/RS layers are seed-sensitive).
def _buy_df(seed: int = 41) -> "object":
    return make_trending_df(n=252, trend=0.0035, seed=seed)


def _spy_df() -> "object":
    return make_trending_df(n=252, trend=0.001, seed=8)


# A flat/declining series never clears the hard gate (price never sustainably
# above a rising SMA150), so flag_buy is always False.
def _no_buy_df(seed: int) -> "object":
    return make_trending_df(n=252, trend=-0.003, seed=seed)


class TestLogCompositeSignalsJob(unittest.TestCase):

    def setUp(self):
        f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        f.close()
        self.db = _reload_db(f.name)
        self.db.init_db({"AI & Semiconductors": ["AAA", "BBB"]})

    def _signals(self):
        with self.db._connect() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM composite_signals ORDER BY symbol"
            ).fetchall()]

    def test_logs_only_flag_buy_symbols(self):
        frames = {"AAA": _buy_df(), "BBB": _no_buy_df(5)}
        summary = lcs.run(
            symbols=["AAA", "BBB"], market_open=False,
            spy_df=_spy_df(), history_fn=lambda s: frames[s],
        )
        self.assertEqual(summary["scanned"], 2)
        rows = self._signals()
        symbols_logged = {r["symbol"] for r in rows}
        self.assertIn("AAA", symbols_logged)
        self.assertNotIn("BBB", symbols_logged)
        self.assertEqual(summary["logged"], len(symbols_logged))

    def test_logged_row_has_full_layer_breakdown(self):
        frames = {"AAA": _buy_df()}
        lcs.run(symbols=["AAA"], market_open=False, spy_df=_spy_df(), history_fn=lambda s: frames[s])
        row = self._signals()[0]
        self.assertEqual(row["symbol"], "AAA")
        self.assertEqual(row["regime"], "BULL")
        self.assertGreaterEqual(row["total_score"], row["required_score"])
        for col in ("trend_pts", "momentum_pts", "volume_pts", "rs_pts",
                    "required_rs", "price", "stop_price", "stop_multiplier"):
            self.assertIsNotNone(row[col], f"{col} should be populated")

    def test_idempotent_second_run_same_day_no_duplicate(self):
        frames = {"AAA": _buy_df()}
        lcs.run(symbols=["AAA"], market_open=False, spy_df=_spy_df(), history_fn=lambda s: frames[s])
        summary2 = lcs.run(symbols=["AAA"], market_open=False, spy_df=_spy_df(), history_fn=lambda s: frames[s])
        self.assertEqual(summary2["logged"], 0)
        self.assertEqual(summary2["already_logged"], 1)
        self.assertEqual(len(self._signals()), 1)

    def test_never_writes_to_alerts_table(self):
        frames = {"AAA": _buy_df()}
        lcs.run(symbols=["AAA"], market_open=False, spy_df=_spy_df(), history_fn=lambda s: frames[s])
        with self.db._connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
        self.assertEqual(count, 0)

    def test_empty_active_tier_is_noop(self):
        summary = lcs.run(symbols=[], market_open=False, spy_df=_spy_df(), history_fn=lambda s: None)
        self.assertEqual(summary["scanned"], 0)
        self.assertEqual(summary["logged"], 0)

    def test_fetch_failure_for_one_symbol_does_not_abort_others(self):
        frames = {"AAA": _buy_df()}

        def history_fn(s):
            if s == "BBB":
                raise RuntimeError("boom")
            return frames[s]

        summary = lcs.run(symbols=["AAA", "BBB"], market_open=False, spy_df=_spy_df(), history_fn=history_fn)
        self.assertEqual(summary["logged"], 1)
        self.assertEqual(len(summary["fetch_failures"]), 1)
        self.assertEqual(summary["fetch_failures"][0][0], "BBB")

    def test_spy_fetch_failure_aborts_before_scanning_any_symbol(self):
        from unittest.mock import patch

        def never_called(s):
            raise AssertionError("history_fn must not be called when SPY fetch fails")

        with patch.object(lcs, "get_historical", side_effect=RuntimeError("no SPY data")):
            summary = lcs.run(symbols=["AAA"], market_open=False, spy_df=None, history_fn=never_called)
        self.assertEqual(summary["scanned"], 0)
        self.assertEqual(len(summary["fetch_failures"]), 1)
        self.assertEqual(summary["fetch_failures"][0][0], "SPY")
        self.assertEqual(len(self._signals()), 0)


if __name__ == "__main__":
    unittest.main()
