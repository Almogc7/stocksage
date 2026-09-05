"""Tests for scripts/populate_composite_signal_outcomes.py — the nightly
composite-signal-outcome population job (schema v10).

Mirrors tests/test_populate_outcomes.py's structure and FakeClient pattern.
All price data is synthetic (no network); DB tests use a temp SQLite file
via the same _reload_db pattern as the other schema tests. The real
db/stocksage.db is never touched. compute_outcome() itself is already
covered by test_populate_outcomes.py — these tests exercise the
composite_signals-specific plumbing (take_profit=inf, signal_id instead of
alert_id, composite_signal_outcomes schema) end to end.
"""
import importlib
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import populate_composite_signal_outcomes as pco


def _reload_db(db_path: str):
    import db.database as dbmod
    importlib.reload(dbmod)
    dbmod.DB_PATH = Path(db_path)
    return dbmod


ENTRY, STOP = 100.0, 97.0  # risk = 3

BENIGN = (101.0, 102.0, 99.0)


def make_bars(specs: list[tuple], start: str = "2026-06-16") -> pd.DataFrame:
    dates = pd.bdate_range(start=start, periods=len(specs))
    return pd.DataFrame(
        {
            "close": [s[0] for s in specs],
            "high":  [s[1] for s in specs],
            "low":   [s[2] for s in specs],
            "open":  [s[0] for s in specs],
            "volume": [1_000_000] * len(specs),
        },
        index=pd.DatetimeIndex(dates, name="Date"),
    )


def full_df(after_specs: list[tuple]) -> pd.DataFrame:
    """A realistic fetch result: the signal-day (T+0) bar plus the bars after."""
    t0 = make_bars([(100.5, 103.0, 95.0)], start="2026-06-15")
    rest = make_bars(after_specs, start="2026-06-16")
    return pd.concat([t0, rest])


SIGNAL_DATE = date(2026, 6, 15)  # Monday
TODAY = date(2026, 7, 10)


class FakeClient:
    def __init__(self, frames: dict):
        self.frames = frames
        self.calls = 0

    def get_history(self, symbol: str):
        self.calls += 1
        return self.frames.get(symbol.upper()), None


class TestPopulateCompositeSignalOutcomesJob(unittest.TestCase):

    def setUp(self):
        f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        f.close()
        self.db = _reload_db(f.name)
        self.db.init_db({"AI & Semiconductors": ["NVDA"]})

    def _insert_signal(self, symbol="NVDA", signal_date="2026-06-15") -> int:
        signal_id = self.db.log_composite_signal(
            symbol=symbol, signal_date=signal_date, regime="BULL",
            total_score=79.0, required_score=70,
            trend_pts=25.0, momentum_pts=25.0, volume_pts=15.0, rs_pts=14.0,
            rs_ratio=0.9957, required_rs=1.0,
            price=ENTRY, atr=2.0, stop_price=STOP, stop_multiplier=1.5,
        )
        self.assertIsNotNone(signal_id)
        return signal_id

    def _outcome_row(self, signal_id):
        with self.db._connect() as conn:
            return conn.execute(
                "SELECT * FROM composite_signal_outcomes WHERE signal_id = ?",
                (signal_id,),
            ).fetchone()

    def test_full_run_completes_old_signal_never_hits_take_profit_branch(self):
        signal_id = self._insert_signal()
        # A big rally that would trip a normal take-profit barrier —
        # must NOT be reported as 'take_profit' (composite has none).
        client = FakeClient({"NVDA": full_df(
            [BENIGN] * 6 + [(140.0, 145.0, 130.0)] + [BENIGN] * 2 + [(103.0, 104.0, 100.0)]
        )})
        summary = pco.run(client=client, today=TODAY, market_open=False)
        self.assertEqual(summary["processed"], 1)
        self.assertEqual(summary["newly_completed"], 1)
        row = self._outcome_row(signal_id)
        self.assertEqual(row["close_t10"], 103.0)
        self.assertEqual(row["first_barrier_hit"], "none")  # never 'take_profit'
        self.assertIsNotNone(row["computed_at"])

    def test_stop_hit_reports_stop_loss(self):
        signal_id = self._insert_signal()
        client = FakeClient({"NVDA": full_df([(96.0, 98.0, 95.5)] + [BENIGN] * 9)})
        pco.run(client=client, today=TODAY, market_open=False)
        row = self._outcome_row(signal_id)
        self.assertEqual(row["first_barrier_hit"], "stop_loss")
        self.assertEqual(row["r_multiple"], -1.0)

    def test_idempotent_second_run_skips_complete_rows(self):
        signal_id = self._insert_signal()
        client = FakeClient({"NVDA": full_df([BENIGN] * 10)})
        pco.run(client=client, today=TODAY, market_open=False)
        first_row = dict(self._outcome_row(signal_id))
        fetches_after_first = client.calls

        summary2 = pco.run(client=client, today=TODAY, market_open=False)
        self.assertEqual(summary2["processed"], 0)
        self.assertEqual(client.calls, fetches_after_first)
        with self.db._connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM composite_signal_outcomes").fetchone()[0]
        self.assertEqual(count, 1)
        self.assertEqual(dict(self._outcome_row(signal_id)), first_row)

    def test_fetch_failure_is_reported_and_writes_nothing(self):
        self._insert_signal()
        client = FakeClient({})
        summary = pco.run(client=client, today=TODAY, market_open=False)
        self.assertEqual(summary["processed"], 0)
        self.assertEqual(len(summary["fetch_failures"]), 1)
        symbol, signal_date, _reason = summary["fetch_failures"][0]
        self.assertEqual(symbol, "NVDA")
        self.assertEqual(signal_date, "2026-06-15")
        with self.db._connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM composite_signal_outcomes").fetchone()[0]
        self.assertEqual(count, 0)

    def test_no_pending_signals_is_a_noop(self):
        summary = pco.run(client=FakeClient({}), today=TODAY, market_open=False)
        self.assertEqual(summary["processed"], 0)
        self.assertEqual(summary["fetch_failures"], [])


if __name__ == "__main__":
    unittest.main()
