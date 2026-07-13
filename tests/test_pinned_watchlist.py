"""
Tests for the /pin feature (v9): the pinned watchlist column, its DB helpers,
and the evaluator's pin enforcement — a pinned symbol stays in the ACTIVE
tier no matter what the eligibility engine proposes (no demotion, no
replacement eviction, no data-quality ineligibility), with hysteresis
counters held at zero while pinned.

Every test uses a temp SQLite file; none touch the real production database.
Market data is stubbed (no network).
"""
import importlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from data.market_data_validator import MarketDataResult, ProviderStatus
from tests.fixtures import make_trending_df


def _reload_db(db_path: str):
    import db.database as dbmod
    importlib.reload(dbmod)
    dbmod.DB_PATH = Path(db_path)
    return dbmod


class PinnedTestCase(unittest.TestCase):

    def setUp(self):
        f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        f.close()
        self.db = _reload_db(f.name)
        import services.watchlist_evaluator as evmod
        importlib.reload(evmod)
        self.ev = evmod

    def _seed_stock(self, symbol, wl_state="ACTIVE", pinned=0, relevance_score=50,
                    consec_promote=0, consec_demote=0, dwell_days=30):
        with self.db._connect() as conn:
            conn.execute(
                "INSERT INTO watchlist (symbol, category, enabled, wl_state, wl_classified,"
                " security_type, pinned, relevance_score, consec_promote_count,"
                " consec_demote_count, dwell_days)"
                " VALUES (?, 'Test', 1, ?, 1, 'stock', ?, ?, ?, ?, ?)",
                (symbol, wl_state, pinned, relevance_score, consec_promote, consec_demote, dwell_days),
            )
            conn.execute(
                "INSERT OR IGNORE INTO symbol_categories (symbol, category) VALUES (?, 'Test')",
                (symbol,),
            )

    def _state(self, symbol):
        return self.db.get_symbol_status(symbol)


# ── DB layer ──────────────────────────────────────────────────────────────────

class TestPinnedSchema(PinnedTestCase):

    def setUp(self):
        super().setUp()
        self.db.init_db({})

    def test_fresh_db_has_pinned_column_default_zero(self):
        self._seed_stock("NVDA")
        self.assertEqual(self._state("NVDA")["pinned"], 0)

    def test_migration_is_idempotent(self):
        self.db.migrate_db()
        self.db.migrate_db()
        self._seed_stock("NVDA")
        self.assertEqual(self._state("NVDA")["pinned"], 0)

    def test_set_pinned_and_get_pinned_symbols(self):
        self._seed_stock("NVDA")
        self._seed_stock("AMD")
        self.assertTrue(self.db.set_pinned("NVDA", True))
        self.assertEqual(self.db.get_pinned_symbols(), ["NVDA"])
        self.assertEqual(self._state("NVDA")["pinned"], 1)
        self.assertTrue(self.db.set_pinned("NVDA", False))
        self.assertEqual(self.db.get_pinned_symbols(), [])

    def test_set_pinned_unknown_symbol_returns_false(self):
        self.assertFalse(self.db.set_pinned("NOPE", True))

    def test_remove_clears_pin(self):
        self._seed_stock("NVDA", pinned=1)
        self.db.remove_from_watchlist("NVDA")
        row = self._state("NVDA")
        self.assertEqual(row["pinned"], 0)
        self.assertEqual(row["wl_state"], "USER_REMOVED")

    def test_add_preserves_state_for_pinned_symbol(self):
        self._seed_stock("NVDA", wl_state="ACTIVE", pinned=1)
        self.db.add_to_watchlist("NVDA", "Test")
        self.assertEqual(self._state("NVDA")["wl_state"], "ACTIVE")

    def test_add_still_resets_unpinned_symbol_to_monitor(self):
        self._seed_stock("NVDA", wl_state="ACTIVE", pinned=0)
        self.db.add_to_watchlist("NVDA", "Test")
        self.assertEqual(self._state("NVDA")["wl_state"], "MONITOR")


# ── Evaluator enforcement ─────────────────────────────────────────────────────

class _FakeBatchClient:
    """Duck-typed MarketDataClient stand-in for run_watchlist_evaluation."""
    cache_hits = 0
    cache_misses = 0
    yfinance_request_count = 0
    provider_error_count = 0

    def __init__(self, failures=None):
        # failures: {symbol: (ProviderStatus, failure_type, failure_reason)}
        self.failures = failures or {}

    def _result(self, symbol):
        if symbol in self.failures:
            status, ftype, freason = self.failures[symbol]
            return MarketDataResult(
                symbol=symbol, normalized_symbol=symbol, provider_status=status,
                is_valid=False, failure_type=ftype, failure_reason=freason,
            )
        return MarketDataResult(
            symbol=symbol, normalized_symbol=symbol, provider_status=ProviderStatus.OK,
            is_valid=True, latest_close=150.0, latest_volume=2_000_000,
            average_daily_volume=2_000_000.0, average_daily_dollar_volume=3e8,
            history_days_available=252, data_timestamp_utc="2026-07-10 21:00:00",
            latest_completed_candle_date="2026-07-10",
        )

    def validate_batch(self, symbols, security_types=None):
        return {s: self._result(s) for s in symbols}

    def get_history(self, symbol):
        return make_trending_df(n=252), None


def _eval_stub(outputs):
    """Return an evaluate_symbol_eligibility stand-in with canned per-symbol
    (score, new_state, reason) outputs."""
    def stub(symbol, price_data, df, avg_volume, **kwargs):
        score, new_state, reason = outputs[symbol]
        return {"symbol": symbol, "score": score, "new_state": new_state,
                "reason": reason, "components": {}}
    return stub


class TestEvaluatorPinEnforcement(PinnedTestCase):

    def setUp(self):
        super().setUp()
        self.db.init_db({})

    def _sr(self, result, symbol):
        return next(sr for sr in result.symbol_results if sr.symbol == symbol)

    def test_pinned_active_survives_low_score_unpinned_is_demoted(self):
        self._seed_stock("PINNED", wl_state="ACTIVE", pinned=1)
        self._seed_stock("LOOSE", wl_state="ACTIVE", pinned=0, consec_demote=5)
        outputs = {
            "PINNED": (10, "MONITOR", "score 10 below threshold"),
            "LOOSE": (10, "MONITOR", "score 10 below threshold"),
        }
        with patch.object(self.ev, "evaluate_symbol_eligibility", _eval_stub(outputs)):
            result = self.ev.run_watchlist_evaluation(apply=True, client=_FakeBatchClient())

        self.assertIsNone(result.fatal_error)
        pinned = self._sr(result, "PINNED")
        self.assertEqual(pinned.proposed_state, "ACTIVE")
        self.assertIn("pinned", pinned.reason)
        self.assertEqual(pinned.relevance_score, 10)  # score still recorded
        self.assertNotIn("PINNED", result.proposed_demotions)
        self.assertIn("LOOSE", result.proposed_demotions)
        self.assertEqual(self._state("PINNED")["wl_state"], "ACTIVE")
        self.assertEqual(self._state("LOOSE")["wl_state"], "MONITOR")

    def test_pinned_counters_held_at_zero_while_unpinned_counts_up(self):
        # Both stay ACTIVE this run (streak not yet complete for the loose
        # one), with a score below the demotion threshold.
        self._seed_stock("PINNED", wl_state="ACTIVE", pinned=1, consec_demote=0)
        self._seed_stock("LOOSE", wl_state="ACTIVE", pinned=0, consec_demote=0)
        outputs = {
            "PINNED": (10, "ACTIVE", "no change"),
            "LOOSE": (10, "ACTIVE", "no change"),
        }
        with patch.object(self.ev, "evaluate_symbol_eligibility", _eval_stub(outputs)):
            result = self.ev.run_watchlist_evaluation(apply=True, client=_FakeBatchClient())

        self.assertIsNone(result.fatal_error)
        self.assertEqual(self._state("PINNED")["consec_demote_count"], 0)
        self.assertEqual(self._state("LOOSE")["consec_demote_count"], 1)

    def test_pinned_never_chosen_as_replacement_eviction_victim(self):
        # ACTIVE is at (patched) cap 2: PINNED has the LOWEST real score and
        # would be the natural eviction victim; the unpinned one must be
        # evicted instead when a stronger MONITOR candidate is promoted.
        from config import PROMOTION_CONSEC_REQUIRED, PROMOTION_THRESHOLD
        self._seed_stock("PINNED", wl_state="ACTIVE", pinned=1)
        self._seed_stock("LOOSE", wl_state="ACTIVE", pinned=0)
        self._seed_stock("RISER", wl_state="MONITOR", pinned=0,
                         consec_promote=PROMOTION_CONSEC_REQUIRED - 1)
        high = max(PROMOTION_THRESHOLD + 20, 95)
        outputs = {
            "PINNED": (5, "ACTIVE", "no change"),
            "LOOSE": (40, "ACTIVE", "no change"),
            "RISER": (high, "ACTIVE", "promote"),
        }
        with patch.object(self.ev, "evaluate_symbol_eligibility", _eval_stub(outputs)), \
             patch.object(self.ev, "ACTIVE_MAX_SIZE", 2):
            result = self.ev.run_watchlist_evaluation(apply=False, client=_FakeBatchClient())

        self.assertIsNone(result.fatal_error)
        self.assertIn("RISER", result.proposed_promotions)
        self.assertIn("LOOSE", result.proposed_demotions)
        self.assertNotIn("PINNED", result.proposed_demotions)
        self.assertEqual(self._sr(result, "PINNED").proposed_state, "ACTIVE")

    def test_pinned_survives_data_quality_failure(self):
        self._seed_stock("PINNED", wl_state="ACTIVE", pinned=1, relevance_score=55)
        self._seed_stock("LOOSE", wl_state="ACTIVE", pinned=0, relevance_score=55)
        failures = {
            "PINNED": (ProviderStatus.STALE_DATA, "data_quality", "stale data"),
            "LOOSE": (ProviderStatus.STALE_DATA, "data_quality", "stale data"),
        }
        result = self.ev.run_watchlist_evaluation(
            apply=True, client=_FakeBatchClient(failures=failures)
        )

        self.assertIsNone(result.fatal_error)
        pinned = self._sr(result, "PINNED")
        self.assertEqual(pinned.proposed_state, "ACTIVE")
        self.assertIn("pinned", pinned.reason)
        self.assertEqual(self._state("PINNED")["wl_state"], "ACTIVE")
        # Nothing was written for the pinned symbol — last-known-good survives.
        self.assertEqual(self._state("PINNED")["relevance_score"], 55)
        self.assertEqual(self._state("LOOSE")["wl_state"], "TEMPORARILY_INELIGIBLE")

    def test_pinned_monitor_symbol_self_heals_to_active(self):
        self._seed_stock("PINNED", wl_state="MONITOR", pinned=1)
        outputs = {"PINNED": (10, "MONITOR", "weak score")}
        with patch.object(self.ev, "evaluate_symbol_eligibility", _eval_stub(outputs)):
            result = self.ev.run_watchlist_evaluation(apply=True, client=_FakeBatchClient())

        self.assertIsNone(result.fatal_error)
        sr = self._sr(result, "PINNED")
        self.assertEqual(sr.proposed_state, "ACTIVE")
        self.assertIn("pin invariant", sr.reason)
        self.assertEqual(self._state("PINNED")["wl_state"], "ACTIVE")

    def test_pinned_ineligible_symbol_recovers_straight_to_active(self):
        self._seed_stock("PINNED", wl_state="TEMPORARILY_INELIGIBLE", pinned=1)
        outputs = {"PINNED": (50, "MONITOR", "ok again")}
        with patch.object(self.ev, "evaluate_symbol_eligibility", _eval_stub(outputs)):
            result = self.ev.run_watchlist_evaluation(apply=True, client=_FakeBatchClient())

        self.assertIsNone(result.fatal_error)
        sr = self._sr(result, "PINNED")
        self.assertEqual(sr.proposed_state, "ACTIVE")
        self.assertIn("PINNED", result.proposed_recoveries)
        self.assertEqual(self._state("PINNED")["wl_state"], "ACTIVE")


if __name__ == "__main__":
    unittest.main()
