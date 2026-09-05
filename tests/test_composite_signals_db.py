"""Tests for the composite_signals / composite_signal_outcomes DB helpers
(schema v10): log_composite_signal(), get_composite_signals_pending_outcomes(),
upsert_composite_signal_outcome(). Exercised directly, independent of the
two scripts that call them (see test_log_composite_signals.py and
test_populate_composite_signal_outcomes.py for the end-to-end job tests).

Temp SQLite file only; db/stocksage.db is never touched.
"""
import importlib
import tempfile
import unittest
from pathlib import Path


def _reload_db(db_path: str):
    import db.database as dbmod
    importlib.reload(dbmod)
    dbmod.DB_PATH = Path(db_path)
    return dbmod


class TestCompositeSignalsDb(unittest.TestCase):

    def setUp(self):
        f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        f.close()
        self.db = _reload_db(f.name)
        self.db.init_db({"AI & Semiconductors": ["NVDA"]})

    def _log(self, symbol="NVDA", signal_date="2026-06-15"):
        return self.db.log_composite_signal(
            symbol=symbol, signal_date=signal_date, regime="BULL",
            total_score=79.0, required_score=70,
            trend_pts=25.0, momentum_pts=25.0, volume_pts=15.0, rs_pts=14.0,
            rs_ratio=0.9957, required_rs=1.0,
            price=100.0, atr=2.0, stop_price=97.0, stop_multiplier=1.5,
        )

    def test_log_returns_new_id(self):
        signal_id = self._log()
        self.assertIsNotNone(signal_id)
        with self.db._connect() as conn:
            row = conn.execute(
                "SELECT * FROM composite_signals WHERE id = ?", (signal_id,)
            ).fetchone()
        self.assertEqual(row["symbol"], "NVDA")
        self.assertEqual(row["signal_date"], "2026-06-15")
        self.assertEqual(row["stop_price"], 97.0)

    def test_duplicate_symbol_date_returns_none_and_does_not_insert(self):
        first_id = self._log()
        second_id = self._log()  # same symbol/date
        self.assertIsNotNone(first_id)
        self.assertIsNone(second_id)
        with self.db._connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM composite_signals").fetchone()[0]
        self.assertEqual(count, 1)

    def test_same_symbol_different_date_both_insert(self):
        self._log(signal_date="2026-06-15")
        second_id = self._log(signal_date="2026-06-16")
        self.assertIsNotNone(second_id)
        with self.db._connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM composite_signals").fetchone()[0]
        self.assertEqual(count, 2)

    def test_pending_outcomes_includes_unresolved_signal(self):
        self._log()
        pending = self.db.get_composite_signals_pending_outcomes()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["symbol"], "NVDA")
        self.assertEqual(pending[0]["price"], 100.0)
        self.assertEqual(pending[0]["stop_price"], 97.0)

    def test_pending_outcomes_excludes_complete_row(self):
        signal_id = self._log()
        self.db.upsert_composite_signal_outcome(signal_id, {
            "close_t1": 101.0, "close_t3": 102.0, "close_t5": 103.0,
            "close_t10": 104.0, "max_adverse_excursion": -1.0,
            "first_barrier_hit": "none", "r_multiple": 1.3,
        })
        pending = self.db.get_composite_signals_pending_outcomes()
        self.assertEqual(pending, [])

    def test_pending_outcomes_includes_incomplete_row(self):
        signal_id = self._log()
        self.db.upsert_composite_signal_outcome(signal_id, {
            "close_t1": 101.0, "close_t3": None, "close_t5": None,
            "close_t10": None, "max_adverse_excursion": -0.5,
            "first_barrier_hit": None, "r_multiple": None,
        })
        pending = self.db.get_composite_signals_pending_outcomes()
        self.assertEqual(len(pending), 1)

    def test_upsert_overwrites_in_place_no_duplicate(self):
        signal_id = self._log()
        self.db.upsert_composite_signal_outcome(signal_id, {
            "close_t1": 101.0, "close_t3": None, "close_t5": None,
            "close_t10": None, "max_adverse_excursion": -0.5,
            "first_barrier_hit": None, "r_multiple": None,
        })
        self.db.upsert_composite_signal_outcome(signal_id, {
            "close_t1": 101.0, "close_t3": 102.0, "close_t5": 103.0,
            "close_t10": 104.0, "max_adverse_excursion": -1.0,
            "first_barrier_hit": "none", "r_multiple": 1.3,
        })
        with self.db._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM composite_signal_outcomes WHERE signal_id = ?", (signal_id,)
            ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["close_t10"], 104.0)

    def test_first_barrier_hit_rejects_take_profit(self):
        """composite has no take-profit level — the CHECK constraint must
        reject anything but 'stop_loss'/'none'/NULL."""
        import sqlite3
        signal_id = self._log()
        with self.assertRaises(sqlite3.IntegrityError):
            with self.db._connect() as conn:
                conn.execute(
                    """INSERT INTO composite_signal_outcomes
                       (signal_id, first_barrier_hit) VALUES (?, ?)""",
                    (signal_id, "take_profit"),
                )


if __name__ == "__main__":
    unittest.main()
