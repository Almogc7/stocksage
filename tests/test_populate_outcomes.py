"""Tests for scripts/populate_outcomes.py — the nightly alert-outcome
population job (schema v8).

All price data is synthetic (no network); DB tests use a temp SQLite file
via the same _reload_db pattern as the other schema tests. The real
db/stocksage.db is never touched.
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

import populate_outcomes as po


def _reload_db(db_path: str):
    import db.database as dbmod
    importlib.reload(dbmod)
    dbmod.DB_PATH = Path(db_path)
    return dbmod


# ── Synthetic bars ────────────────────────────────────────────────────────────

ENTRY, STOP, TP = 100.0, 97.0, 106.0   # risk = 3, reward = 6 → +2R / -1R

# (close, high, low) that touches neither barrier
BENIGN = (101.0, 102.0, 99.0)


def make_bars(specs: list[tuple], start: str = "2026-06-16") -> pd.DataFrame:
    """Build a daily-bar DataFrame from (close, high, low) tuples, indexed on
    consecutive business days starting at `start` (a Tuesday: the day after
    the 2026-06-15 Monday used as the alert date throughout these tests)."""
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


ALERT_DATE = date(2026, 6, 15)          # Monday
T0_BAR = [(100.5, 103.0, 95.0)]         # alert-day bar: touches BOTH barriers
                                        # — must be ignored (T+0 exclusion)


def full_df(after_specs: list[tuple]) -> pd.DataFrame:
    """A realistic fetch result: the alert-day (T+0) bar plus the bars after."""
    t0 = make_bars(T0_BAR, start="2026-06-15")
    rest = make_bars(after_specs, start="2026-06-16")
    return pd.concat([t0, rest])


# ── Pure computation ──────────────────────────────────────────────────────────

class TestComputeOutcome(unittest.TestCase):

    def test_stop_hit_on_day_1(self):
        bars = make_bars([(97.5, 99.0, 96.5)] + [BENIGN] * 9)
        out = po.compute_outcome(ENTRY, STOP, TP, bars)
        self.assertEqual(out["first_barrier_hit"], "stop_loss")
        self.assertEqual(out["r_multiple"], -1.0)
        # MAE through the exit bar: worst low is day 1's 96.5
        self.assertEqual(out["max_adverse_excursion"], -3.5)
        self.assertEqual(out["close_t1"], 97.5)

    def test_take_profit_hit_on_day_7(self):
        bars = make_bars([BENIGN] * 6 + [(105.0, 106.5, 101.0)] + [BENIGN] * 3)
        out = po.compute_outcome(ENTRY, STOP, TP, bars)
        self.assertEqual(out["first_barrier_hit"], "take_profit")
        self.assertEqual(out["r_multiple"], 2.0)
        # worst low across days 1..7 is the benign 99.0
        self.assertEqual(out["max_adverse_excursion"], -1.0)

    def test_timeout_no_barrier_hit(self):
        bars = make_bars([BENIGN] * 9 + [(103.0, 104.0, 100.0)])
        out = po.compute_outcome(ENTRY, STOP, TP, bars)
        self.assertEqual(out["first_barrier_hit"], "none")
        self.assertEqual(out["close_t10"], 103.0)
        # timeout exit at close_t10: (103 - 100) / 3 = 1.0
        self.assertEqual(out["r_multiple"], 1.0)
        self.assertEqual(out["close_t1"], 101.0)
        self.assertEqual(out["close_t3"], 101.0)
        self.assertEqual(out["close_t5"], 101.0)

    def test_both_barriers_same_day_assumes_stop_first(self):
        bars = make_bars([(100.0, 107.0, 96.0)] + [BENIGN] * 9)
        out = po.compute_outcome(ENTRY, STOP, TP, bars)
        self.assertEqual(out["first_barrier_hit"], "stop_loss")
        self.assertEqual(out["r_multiple"], -1.0)

    def test_mae_ignores_drawdown_after_exit(self):
        # TP hit day 2; the day-9 crash to 90 is after the exit — irrelevant.
        bars = make_bars(
            [BENIGN, (105.5, 106.2, 100.0)] + [BENIGN] * 6 + [(91.0, 92.0, 90.0), BENIGN]
        )
        out = po.compute_outcome(ENTRY, STOP, TP, bars)
        self.assertEqual(out["first_barrier_hit"], "take_profit")
        self.assertEqual(out["max_adverse_excursion"], -1.0)  # benign low 99

    def test_partial_fill_three_days(self):
        bars = make_bars([BENIGN, BENIGN, (102.0, 103.0, 98.5)])
        out = po.compute_outcome(ENTRY, STOP, TP, bars)
        self.assertEqual(out["close_t1"], 101.0)
        self.assertEqual(out["close_t3"], 102.0)
        self.assertIsNone(out["close_t5"])
        self.assertIsNone(out["close_t10"])
        # verdict still open: no 'none' before 10 bars, no timeout r_multiple
        self.assertIsNone(out["first_barrier_hit"])
        self.assertIsNone(out["r_multiple"])
        # running MAE over available bars: worst low 98.5
        self.assertEqual(out["max_adverse_excursion"], -1.5)

    def test_barrier_hit_recordable_before_ten_bars(self):
        bars = make_bars([BENIGN, (97.0, 99.0, 96.8)])  # stop on day 2 of 2
        out = po.compute_outcome(ENTRY, STOP, TP, bars)
        self.assertEqual(out["first_barrier_hit"], "stop_loss")
        self.assertEqual(out["r_multiple"], -1.0)
        self.assertIsNone(out["close_t10"])  # row stays incomplete

    def test_no_bars_returns_all_none(self):
        out = po.compute_outcome(ENTRY, STOP, TP, make_bars([]))
        self.assertTrue(all(v is None for v in out.values()))

    def test_zero_risk_guard(self):
        # entry <= stop_loss (bad data) must not divide by zero
        bars = make_bars([BENIGN] * 10)
        out = po.compute_outcome(100.0, 100.0, 106.0, bars)
        self.assertIsNone(out["r_multiple"])

    def test_bars_after_excludes_alert_day(self):
        df = full_df([BENIGN] * 10)
        window = po.bars_after(df, ALERT_DATE)
        self.assertEqual(len(window), 10)
        # The T+0 bar (which touches both barriers) must be gone
        out = po.compute_outcome(ENTRY, STOP, TP, window)
        self.assertEqual(out["first_barrier_hit"], "none")


# ── End-to-end job over a temp DB with a fake client ─────────────────────────

class FakeClient:
    def __init__(self, frames: dict):
        self.frames = frames
        self.calls = 0

    def get_history(self, symbol: str):
        self.calls += 1
        return self.frames.get(symbol.upper()), None


_ANALYSIS = {
    "score": 78, "verdict": "STRONG BUY", "rsi": 55.0, "atr": 2.0,
    "stop_loss": STOP, "take_profit": TP,
    "triggered_signals": ["rsi_healthy_range", "volume_spike"],
}

TODAY = date(2026, 7, 10)


class TestPopulateOutcomesJob(unittest.TestCase):

    def setUp(self):
        f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        f.close()
        self.db = _reload_db(f.name)
        self.db.init_db({"AI & Semiconductors": ["NVDA"]})

    def _insert_alert(self, symbol="NVDA", alert_date="2026-06-15") -> int:
        alert_id = self.db.log_alert(symbol, "BUY_SIGNAL", "msg",
                                     analysis=_ANALYSIS, price_at_alert=ENTRY)
        with self.db._connect() as conn:
            conn.execute("UPDATE alerts SET triggered_at = ? WHERE id = ?",
                         (f"{alert_date} 15:00:00", alert_id))
        return alert_id

    def _outcome_row(self, alert_id):
        with self.db._connect() as conn:
            return conn.execute(
                "SELECT * FROM alert_outcomes WHERE alert_id = ?", (alert_id,)
            ).fetchone()

    def test_full_run_completes_old_alert(self):
        alert_id = self._insert_alert()
        client = FakeClient({"NVDA": full_df([BENIGN] * 9 + [(103.0, 104.0, 100.0)])})
        summary = po.run(client=client, today=TODAY, market_open=False)
        self.assertEqual(summary["processed"], 1)
        self.assertEqual(summary["newly_completed"], 1)
        self.assertEqual(summary["still_pending"], 0)
        row = self._outcome_row(alert_id)
        self.assertEqual(row["close_t10"], 103.0)
        self.assertEqual(row["first_barrier_hit"], "none")
        self.assertEqual(row["r_multiple"], 1.0)
        self.assertIsNotNone(row["computed_at"])

    def test_idempotent_second_run_skips_complete_rows(self):
        alert_id = self._insert_alert()
        client = FakeClient({"NVDA": full_df([BENIGN] * 10)})
        po.run(client=client, today=TODAY, market_open=False)
        first_row = dict(self._outcome_row(alert_id))
        fetches_after_first = client.calls

        summary2 = po.run(client=client, today=TODAY, market_open=False)
        self.assertEqual(summary2["processed"], 0)
        self.assertEqual(client.calls, fetches_after_first)  # no refetch at all
        with self.db._connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM alert_outcomes").fetchone()[0]
        self.assertEqual(count, 1)  # no duplicate row
        self.assertEqual(dict(self._outcome_row(alert_id)), first_row)  # unchanged

    def test_partial_fill_then_completion(self):
        alert_id = self._insert_alert()
        # First run: only 3 trading days have elapsed
        client3 = FakeClient({"NVDA": full_df([BENIGN, BENIGN, (102.0, 103.0, 98.5)])})
        summary = po.run(client=client3, today=date(2026, 6, 18), market_open=False)
        self.assertEqual(summary["processed"], 1)
        self.assertEqual(summary["newly_completed"], 0)
        self.assertEqual(summary["still_pending"], 1)
        row = dict(self._outcome_row(alert_id))
        self.assertEqual(row["close_t1"], 101.0)
        self.assertEqual(row["close_t3"], 102.0)
        self.assertIsNone(row["close_t5"])
        self.assertIsNone(row["close_t10"])
        self.assertIsNone(row["first_barrier_hit"])

        # Later run: full history available — row completes in place
        client10 = FakeClient({"NVDA": full_df(
            [BENIGN, BENIGN, (102.0, 103.0, 98.5)] + [BENIGN] * 6 + [(103.0, 104.0, 100.0)]
        )})
        summary = po.run(client=client10, today=TODAY, market_open=False)
        self.assertEqual(summary["newly_completed"], 1)
        row = dict(self._outcome_row(alert_id))
        self.assertEqual(row["close_t10"], 103.0)
        self.assertEqual(row["first_barrier_hit"], "none")
        with self.db._connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM alert_outcomes").fetchone()[0]
        self.assertEqual(count, 1)

    def test_barrier_hit_end_to_end(self):
        alert_id = self._insert_alert()
        client = FakeClient({"NVDA": full_df(
            [BENIGN] * 6 + [(105.0, 106.5, 101.0)] + [BENIGN] * 3
        )})
        po.run(client=client, today=TODAY, market_open=False)
        row = self._outcome_row(alert_id)
        self.assertEqual(row["first_barrier_hit"], "take_profit")
        self.assertEqual(row["r_multiple"], 2.0)

    def test_fetch_failure_is_reported_and_writes_nothing(self):
        self._insert_alert()
        client = FakeClient({})  # returns (None, None) for every symbol
        summary = po.run(client=client, today=TODAY, market_open=False)
        self.assertEqual(summary["processed"], 0)
        self.assertEqual(len(summary["fetch_failures"]), 1)
        symbol, alert_date, _reason = summary["fetch_failures"][0]
        self.assertEqual(symbol, "NVDA")
        self.assertEqual(alert_date, "2026-06-15")
        with self.db._connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM alert_outcomes").fetchone()[0]
        self.assertEqual(count, 0)

    def test_alert_with_no_completed_bars_yet_stays_unwritten(self):
        # Alert fired "today" — zero completed bars after it
        alert_id = self._insert_alert(alert_date="2026-07-10")
        client = FakeClient({"NVDA": full_df([BENIGN] * 10)})  # all bars ≤ 2026-06-29
        summary = po.run(client=client, today=TODAY, market_open=False)
        self.assertEqual(summary["processed"], 0)
        self.assertEqual(summary["still_pending"], 1)
        self.assertIsNone(self._outcome_row(alert_id))

    def test_in_progress_bar_is_dropped_when_market_open(self):
        alert_id = self._insert_alert(alert_date="2026-07-08")  # Wednesday
        # One completed bar (Thu 07-09) + today's in-progress bar (Fri 07-10)
        bars = make_bars([BENIGN, (95.0, 101.0, 94.0)], start="2026-07-09")
        client = FakeClient({"NVDA": bars})
        po.run(client=client, today=TODAY, market_open=True)
        row = self._outcome_row(alert_id)
        # In-progress Friday bar (which would hit the stop) must be ignored
        self.assertEqual(row["close_t1"], 101.0)
        self.assertIsNone(row["first_barrier_hit"])


if __name__ == "__main__":
    unittest.main()
