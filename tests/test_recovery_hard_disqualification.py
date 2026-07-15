"""
Tests for the recovery-loop fix (2026-07-15): a TEMPORARILY_INELIGIBLE
symbol whose retry is due only recovers to MONITOR when BOTH the re-fetch
succeeds AND the hard disqualifications clear. A symbol whose data is back
but whose price is still below the eligibility floor must stay ineligible
with a refreshed exclusion_reason and a new reeval_date — the old behavior
force-recovered it, causing a permanent nightly INELIGIBLE <-> MONITOR
oscillation (observed live: EU / NAK / AREC, runs 5-6).

Reuses the temp-DB + fake-client harness from tests.test_pinned_watchlist.
No network, no real DB.
"""
import unittest
from datetime import date
from unittest.mock import patch

from data.market_data_validator import ProviderStatus
from tests.test_pinned_watchlist import PinnedTestCase, _FakeBatchClient, _eval_stub


class TestRecoveryHardDisqualification(PinnedTestCase):

    def setUp(self):
        super().setUp()
        self.db.init_db({})

    def _sr(self, result, symbol):
        return next(sr for sr in result.symbol_results if sr.symbol == symbol)

    def test_still_below_floor_stays_ineligible_with_fresh_reason(self):
        # Retry due (reeval_date NULL), fetch succeeds, but the price floor
        # still disqualifies -> stays TEMPORARILY_INELIGIBLE, no recovery.
        self._seed_stock("EU", wl_state="TEMPORARILY_INELIGIBLE")
        outputs = {"EU": (55, "TEMPORARILY_INELIGIBLE", "price $1.25 below minimum $3.0")}
        with patch.object(self.ev, "evaluate_symbol_eligibility", _eval_stub(outputs)):
            result = self.ev.run_watchlist_evaluation(apply=True, client=_FakeBatchClient())

        self.assertIsNone(result.fatal_error)
        sr = self._sr(result, "EU")
        self.assertEqual(sr.proposed_state, "TEMPORARILY_INELIGIBLE")
        self.assertEqual(sr.reason, "still ineligible: price $1.25 below minimum $3.0")
        self.assertFalse(sr.hard_eligibility_passed)
        self.assertNotIn("EU", result.proposed_recoveries)

        row = self._state("EU")
        self.assertEqual(row["wl_state"], "TEMPORARILY_INELIGIBLE")
        self.assertEqual(row["exclusion_reason"], "still ineligible: price $1.25 below minimum $3.0")
        # Fresh retry date, not wiped and not left stale in the past.
        self.assertIsNotNone(row["reeval_date"])
        self.assertGreaterEqual(str(row["reeval_date"]), date.today().isoformat())

    def test_genuine_recovery_still_returns_to_monitor(self):
        # Control case: fetch succeeds and no hard disqualification ->
        # the original recovery-to-MONITOR behavior is unchanged.
        self._seed_stock("BOE", wl_state="TEMPORARILY_INELIGIBLE")
        outputs = {"BOE": (55, "MONITOR", "no change")}
        with patch.object(self.ev, "evaluate_symbol_eligibility", _eval_stub(outputs)):
            result = self.ev.run_watchlist_evaluation(apply=True, client=_FakeBatchClient())

        self.assertIsNone(result.fatal_error)
        sr = self._sr(result, "BOE")
        self.assertEqual(sr.proposed_state, "MONITOR")
        self.assertIn("BOE", result.proposed_recoveries)
        row = self._state("BOE")
        self.assertEqual(row["wl_state"], "MONITOR")
        self.assertEqual(row["exclusion_reason"], "")
        self.assertIsNone(row["reeval_date"])

    def test_no_oscillation_across_two_apply_runs(self):
        # The live bug: run N recovers a sub-floor symbol, run N+1 demotes it
        # again. With the fix, two consecutive runs both keep it ineligible.
        self._seed_stock("NAK", wl_state="TEMPORARILY_INELIGIBLE")
        outputs = {"NAK": (60, "TEMPORARILY_INELIGIBLE", "price $1.74 below minimum $3.0")}
        with patch.object(self.ev, "evaluate_symbol_eligibility", _eval_stub(outputs)):
            self.ev.run_watchlist_evaluation(apply=True, client=_FakeBatchClient())
            first_state = self._state("NAK")["wl_state"]
            # Force the retry due again (the fix stamps a future reeval_date).
            with self.db._connect() as conn:
                conn.execute("UPDATE watchlist SET reeval_date = NULL WHERE symbol = 'NAK'")
            result2 = self.ev.run_watchlist_evaluation(apply=True, client=_FakeBatchClient())

        self.assertEqual(first_state, "TEMPORARILY_INELIGIBLE")
        self.assertEqual(self._state("NAK")["wl_state"], "TEMPORARILY_INELIGIBLE")
        self.assertNotIn("NAK", result2.proposed_recoveries)

    def test_data_quality_refailure_keeps_reason_and_reeval(self):
        # Companion fix in _build_db_fields: an already-ineligible symbol
        # that fails again on data quality no longer has its reason wiped
        # and its reeval_date left stale.
        self._seed_stock("HTBK", wl_state="TEMPORARILY_INELIGIBLE")
        failures = {"HTBK": (ProviderStatus.STALE_DATA, "data_quality", "stale data")}
        result = self.ev.run_watchlist_evaluation(
            apply=True, client=_FakeBatchClient(failures=failures)
        )

        self.assertIsNone(result.fatal_error)
        row = self._state("HTBK")
        self.assertEqual(row["wl_state"], "TEMPORARILY_INELIGIBLE")
        self.assertEqual(row["exclusion_reason"], "still ineligible: stale data")
        self.assertIsNotNone(row["reeval_date"])


if __name__ == "__main__":
    unittest.main()
