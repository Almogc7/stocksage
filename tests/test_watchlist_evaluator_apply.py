"""
Tests for Phase 5 — applying watchlist evaluation results transactionally.

Reuses the FakeMarketDataClient/test helpers from test_watchlist_evaluator.py.
All market data is fully mocked; every test uses a temporary SQLite database.
None of these tests touch the production database or send Telegram messages.
"""
import importlib
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from data.market_data_validator import MarketDataResult, ProviderStatus
from tests.fixtures import make_falling_df, make_trending_df
from tests.test_watchlist_evaluator import FakeMarketDataClient, _market_result


def _reload_db(db_path: str):
    import db.database as dbmod
    importlib.reload(dbmod)
    dbmod.DB_PATH = Path(db_path)
    return dbmod


class ApplyTestCase(unittest.TestCase):

    def setUp(self):
        f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        f.close()
        self.db = _reload_db(f.name)
        import services.watchlist_evaluator as evmod
        importlib.reload(evmod)
        self.evaluator = evmod

    def _seed(self, wl: dict):
        self.db.init_db(wl)
        self.db.run_initial_classification(wl)

    def _set_row(self, symbol, **fields):
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self.db._connect() as conn:
            conn.execute(f"UPDATE watchlist SET {cols} WHERE symbol = ?", (*fields.values(), symbol.upper()))


class TestApplyBasics(ApplyTestCase):

    def test_apply_mode_modifies_watchlist_state(self):
        self._seed({"AI & Semiconductors": ["CRM"]})
        before = self.db.get_symbol_status("CRM")
        client = FakeMarketDataClient({"CRM": _market_result("CRM")}, {"CRM": make_trending_df(n=252)})
        self.evaluator.run_watchlist_evaluation(apply=True, client=client)
        after = self.db.get_symbol_status("CRM")
        self.assertNotEqual(before["relevance_score"], after["relevance_score"])
        self.assertIsNotNone(after["last_evaluated"])

    def test_dry_run_mode_still_does_not_modify_state(self):
        self._seed({"AI & Semiconductors": ["CRM"]})
        before = self.db.get_symbol_status("CRM")
        client = FakeMarketDataClient({"CRM": _market_result("CRM")}, {"CRM": make_trending_df(n=252)})
        self.evaluator.run_watchlist_evaluation(apply=False, client=client)
        after = self.db.get_symbol_status("CRM")
        self.assertEqual(before, after)

    def test_evaluation_runs_records_dry_run_false_for_apply(self):
        self._seed({"AI & Semiconductors": ["CRM"]})
        client = FakeMarketDataClient({"CRM": _market_result("CRM")}, {"CRM": make_trending_df(n=252)})
        result = self.evaluator.run_watchlist_evaluation(apply=True, client=client)
        run = self.db.get_evaluation_run(result.run_id)
        self.assertEqual(run["dry_run"], 0)
        self.assertEqual(run["run_type"], "manual")

    def test_before_after_counts_correct(self):
        self._seed({"AI & Semiconductors": ["CRM"]})
        self._set_row("CRM", consec_promote_count=1)
        client = FakeMarketDataClient({"CRM": _market_result("CRM")}, {"CRM": make_trending_df(n=252)})
        result = self.evaluator.run_watchlist_evaluation(apply=True, client=client)
        self.assertEqual(result.monitor_before, 1)
        self.assertEqual(result.active_after, 1)  # CRM promotes (2nd consecutive pass)
        summary = self.db.get_watchlist_summary()
        self.assertEqual(summary.get("ACTIVE", 0), 1)


class TestPromotionHysteresisApply(ApplyTestCase):

    def test_first_promotion_pass_increments_counter_but_does_not_promote(self):
        self._seed({"AI & Semiconductors": ["CRM"]})
        client = FakeMarketDataClient({"CRM": _market_result("CRM")}, {"CRM": make_trending_df(n=252)})
        self.evaluator.run_watchlist_evaluation(apply=True, client=client)
        row = self.db.get_symbol_status("CRM")
        self.assertEqual(row["wl_state"], "MONITOR")
        self.assertEqual(row["consec_promote_count"], 1)

    def test_second_promotion_pass_promotes_to_active(self):
        self._seed({"AI & Semiconductors": ["CRM"]})
        client1 = FakeMarketDataClient({"CRM": _market_result("CRM")}, {"CRM": make_trending_df(n=252)})
        self.evaluator.run_watchlist_evaluation(apply=True, client=client1)
        client2 = FakeMarketDataClient({"CRM": _market_result("CRM")}, {"CRM": make_trending_df(n=252)})
        result2 = self.evaluator.run_watchlist_evaluation(apply=True, client=client2)
        row = self.db.get_symbol_status("CRM")
        self.assertEqual(row["wl_state"], "ACTIVE")
        self.assertIsNotNone(row["last_promoted"])
        self.assertEqual(row["consec_promote_count"], 0)  # reset on real transition
        self.assertIn("CRM", result2.proposed_promotions)


class TestDemotionHysteresisApply(ApplyTestCase):

    def _weak_market_result(self, sym):
        return _market_result(
            sym, latest_close=5.0, latest_volume=0, average_daily_volume=0.0,
            average_daily_dollar_volume=0.0,
        )

    def test_first_demotion_failure_increments_counter_but_does_not_demote(self):
        self._seed({"AI & Semiconductors": ["NVDA"]})
        self._set_row("NVDA", wl_state="ACTIVE", dwell_days=10)
        client = FakeMarketDataClient(
            {"NVDA": self._weak_market_result("NVDA")}, {"NVDA": make_falling_df(n=252)}
        )
        self.evaluator.run_watchlist_evaluation(apply=True, client=client)
        row = self.db.get_symbol_status("NVDA")
        self.assertEqual(row["wl_state"], "ACTIVE")
        self.assertEqual(row["consec_demote_count"], 1)

    def test_second_demotion_failure_demotes_to_monitor(self):
        self._seed({"AI & Semiconductors": ["NVDA"]})
        self._set_row("NVDA", wl_state="ACTIVE", dwell_days=10, consec_demote_count=1)
        client = FakeMarketDataClient(
            {"NVDA": self._weak_market_result("NVDA")}, {"NVDA": make_falling_df(n=252)}
        )
        result = self.evaluator.run_watchlist_evaluation(apply=True, client=client)
        row = self.db.get_symbol_status("NVDA")
        self.assertEqual(row["wl_state"], "MONITOR")
        self.assertIsNotNone(row["last_demoted"])
        self.assertEqual(row["consec_demote_count"], 0)
        self.assertIn("NVDA", result.proposed_demotions)


class TestIneligibleAndRecoveryApply(ApplyTestCase):

    def test_temporarily_ineligible_transition_writes_reason_and_retry_date(self):
        self._seed({"AI & Semiconductors": ["NVDA"]})
        self._set_row("NVDA", wl_state="MONITOR")
        client = FakeMarketDataClient(
            {"NVDA": _market_result(
                "NVDA", status=ProviderStatus.STALE_DATA, failure_type="data_quality",
                failure_reason="latest completed candle is 10 days old",
            )},
            {},
        )
        self.evaluator.run_watchlist_evaluation(apply=True, client=client)
        row = self.db.get_symbol_status("NVDA")
        self.assertEqual(row["wl_state"], "TEMPORARILY_INELIGIBLE")
        self.assertIn("10 days old", row["exclusion_reason"])
        self.assertIsNotNone(row["reeval_date"])

    def test_recovered_ineligible_returns_to_monitor(self):
        self._seed({"AI & Semiconductors": ["NVDA"]})
        past = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
        self._set_row("NVDA", wl_state="TEMPORARILY_INELIGIBLE", reeval_date=past,
                       exclusion_reason="old reason")
        client = FakeMarketDataClient(
            {"NVDA": _market_result("NVDA")}, {"NVDA": make_trending_df(n=252)}
        )
        result = self.evaluator.run_watchlist_evaluation(apply=True, client=client)
        row = self.db.get_symbol_status("NVDA")
        self.assertEqual(row["wl_state"], "MONITOR")
        self.assertEqual(row["exclusion_reason"], "")
        self.assertIsNone(row["reeval_date"])
        self.assertIn("NVDA", result.proposed_recoveries)
        self.assertNotIn("NVDA", result.proposed_promotions)


class TestProtectedRowsApply(ApplyTestCase):

    def test_user_removed_is_never_modified(self):
        self._seed({"AI & Semiconductors": ["NVDA"]})
        self.db.remove_from_watchlist("NVDA")
        before = self.db.get_symbol_status("NVDA")
        client = FakeMarketDataClient({}, {})
        self.evaluator.run_watchlist_evaluation(apply=True, client=client)
        after = self.db.get_symbol_status("NVDA")
        self.assertEqual(before, after)

    def test_etf_index_context_is_never_promoted(self):
        self._seed({"ETFs": ["SPY"]})
        before = self.db.get_symbol_status("SPY")
        client = FakeMarketDataClient({}, {})
        self.evaluator.run_watchlist_evaluation(apply=True, client=client)
        after = self.db.get_symbol_status("SPY")
        self.assertEqual(before["wl_state"], "ETF_INDEX_CONTEXT")
        self.assertEqual(after["wl_state"], "ETF_INDEX_CONTEXT")

    def test_categories_preserved(self):
        self._seed({"AI & Semiconductors": ["CRM"]})
        before_cats = self.db.get_symbol_categories("CRM")
        client = FakeMarketDataClient({"CRM": _market_result("CRM")}, {"CRM": make_trending_df(n=252)})
        self.evaluator.run_watchlist_evaluation(apply=True, client=client)
        after_cats = self.db.get_symbol_categories("CRM")
        self.assertEqual(before_cats, after_cats)

    def test_enabled_status_preserved(self):
        self._seed({"AI & Semiconductors": ["CRM"]})
        client = FakeMarketDataClient({"CRM": _market_result("CRM")}, {"CRM": make_trending_df(n=252)})
        self.evaluator.run_watchlist_evaluation(apply=True, client=client)
        after = self.db.get_symbol_status("CRM")
        self.assertEqual(after["enabled"], 1)


class TestActiveConstraintsApply(ApplyTestCase):

    def _pool(self, n):
        symbols = [f"CAND{i}" for i in range(n)]
        self._seed({"AI & Semiconductors": symbols})
        for s in symbols:
            self._set_row(s, consec_promote_count=1)  # one pass away from promotion
        market_results = {s: _market_result(s) for s in symbols}
        histories = {s: make_trending_df(n=252) for s in symbols}
        return symbols, market_results, histories

    def test_active_maximum_30_enforced(self):
        symbols, market_results, histories = self._pool(35)
        client = FakeMarketDataClient(market_results, histories)
        self.evaluator.run_watchlist_evaluation(apply=True, client=client)
        summary = self.db.get_watchlist_summary()
        self.assertLessEqual(summary.get("ACTIVE", 0), 30)

    def test_bank_maximum_8_enforced(self):
        symbols = [f"BANK{i}" for i in range(12)]
        self._seed({"פיננסים": symbols})
        for s in symbols:
            self._set_row(s, consec_promote_count=1)
        market_results = {s: _market_result(s) for s in symbols}
        histories = {s: make_trending_df(n=252) for s in symbols}
        client = FakeMarketDataClient(market_results, histories)
        self.evaluator.run_watchlist_evaluation(apply=True, client=client)
        with self.db._connect() as conn:
            n_active_bank = conn.execute(
                "SELECT COUNT(*) FROM watchlist WHERE wl_state = 'ACTIVE' AND symbol IN"
                f" ({','.join('?' for _ in symbols)})", symbols,
            ).fetchone()[0]
        self.assertLessEqual(n_active_bank, 8)

    def test_no_duplicate_active_ticker(self):
        symbols, market_results, histories = self._pool(10)
        client = FakeMarketDataClient(market_results, histories)
        self.evaluator.run_watchlist_evaluation(apply=True, client=client)
        with self.db._connect() as conn:
            rows = conn.execute("SELECT symbol FROM watchlist WHERE wl_state = 'ACTIVE'").fetchall()
        syms = [r["symbol"] for r in rows]
        self.assertEqual(len(syms), len(set(syms)))


class TestTransactionSafety(ApplyTestCase):

    def test_fatal_error_rolls_back_leaves_states_unchanged(self):
        self._seed({"AI & Semiconductors": ["CRM"]})
        before = self.db.get_symbol_status("CRM")

        class ExplodingClient:
            def validate_batch(self, *a, **kw):
                raise RuntimeError("boom")

        result = self.evaluator.run_watchlist_evaluation(apply=True, client=ExplodingClient())
        self.assertIsNotNone(result.fatal_error)
        after = self.db.get_symbol_status("CRM")
        self.assertEqual(before, after)
        run = self.db.get_evaluation_run(result.run_id)
        self.assertEqual(run["status"], "failed")

    def test_apply_write_failure_does_not_partially_apply(self):
        """If db.apply_evaluation_changes itself raises, no row already
        written in that same batch should remain (atomic transaction)."""
        self._seed({"AI & Semiconductors": ["CRM", "NVDA"]})
        before_crm = self.db.get_symbol_status("CRM")
        before_nvda = self.db.get_symbol_status("NVDA")

        orig_apply = self.db.apply_evaluation_changes

        def exploding_apply(updates):
            # Simulate a failure partway through a multi-row batch.
            raise RuntimeError("simulated write failure")

        self.db.apply_evaluation_changes = exploding_apply
        try:
            client = FakeMarketDataClient(
                {"CRM": _market_result("CRM"), "NVDA": _market_result("NVDA")},
                {"CRM": make_trending_df(n=252), "NVDA": make_trending_df(n=252)},
            )
            result = self.evaluator.run_watchlist_evaluation(apply=True, client=client)
        finally:
            self.db.apply_evaluation_changes = orig_apply

        self.assertIsNotNone(result.fatal_error)
        self.assertEqual(self.db.get_symbol_status("CRM"), before_crm)
        self.assertEqual(self.db.get_symbol_status("NVDA"), before_nvda)


class TestProviderOutageApply(ApplyTestCase):

    def test_provider_outage_does_not_mass_demote_on_apply(self):
        symbols = [f"SYM{i}" for i in range(5)]
        self._seed({"AI & Semiconductors": symbols})
        for s in symbols:
            self._set_row(s, wl_state="ACTIVE", dwell_days=10, consec_demote_count=1)
        market_results = {
            s: _market_result(
                s, status=ProviderStatus.PROVIDER_ERROR, failure_type="provider_transient",
                failure_reason="connection reset",
            )
            for s in symbols
        }
        client = FakeMarketDataClient(market_results, {})
        result = self.evaluator.run_watchlist_evaluation(apply=True, client=client)
        self.assertTrue(result.provider_degraded)
        summary = self.db.get_watchlist_summary()
        self.assertEqual(summary.get("ACTIVE", 0), 5)
        run = self.db.get_evaluation_run(result.run_id)
        self.assertEqual(run["status"], "partial_failure")

    def test_transient_failure_does_not_touch_score_or_timestamp(self):
        self._seed({"AI & Semiconductors": ["NVDA"]})
        self._set_row("NVDA", wl_state="ACTIVE", dwell_days=10, relevance_score=55, last_evaluated=None)
        before = self.db.get_symbol_status("NVDA")
        client = FakeMarketDataClient(
            {"NVDA": _market_result(
                "NVDA", status=ProviderStatus.PROVIDER_ERROR, failure_type="provider_transient",
                failure_reason="connection reset",
            )},
            {},
        )
        self.evaluator.run_watchlist_evaluation(apply=True, client=client)
        after = self.db.get_symbol_status("NVDA")
        self.assertEqual(before["relevance_score"], after["relevance_score"])
        self.assertEqual(before["last_evaluated"], after["last_evaluated"])


class TestApplyVsDryRunIsolation(ApplyTestCase):

    def test_relevance_score_updated_only_in_apply_mode(self):
        self._seed({"AI & Semiconductors": ["CRM"]})
        client_dry = FakeMarketDataClient({"CRM": _market_result("CRM")}, {"CRM": make_trending_df(n=252)})
        self.evaluator.run_watchlist_evaluation(apply=False, client=client_dry)
        self.assertIsNone(self.db.get_symbol_status("CRM")["relevance_score"])

        client_apply = FakeMarketDataClient({"CRM": _market_result("CRM")}, {"CRM": make_trending_df(n=252)})
        self.evaluator.run_watchlist_evaluation(apply=True, client=client_apply)
        self.assertIsNotNone(self.db.get_symbol_status("CRM")["relevance_score"])

    def test_last_evaluated_updated_only_in_apply_mode(self):
        self._seed({"AI & Semiconductors": ["CRM"]})
        client_dry = FakeMarketDataClient({"CRM": _market_result("CRM")}, {"CRM": make_trending_df(n=252)})
        self.evaluator.run_watchlist_evaluation(apply=False, client=client_dry)
        self.assertIsNone(self.db.get_symbol_status("CRM")["last_evaluated"])

        client_apply = FakeMarketDataClient({"CRM": _market_result("CRM")}, {"CRM": make_trending_df(n=252)})
        self.evaluator.run_watchlist_evaluation(apply=True, client=client_apply)
        self.assertIsNotNone(self.db.get_symbol_status("CRM")["last_evaluated"])


class TestSafetyApply(ApplyTestCase):

    def test_no_production_db_used(self):
        import db.database as real_db_mod
        self.assertNotEqual(str(self.db.DB_PATH), str(real_db_mod.DB_PATH.parent / "stocksage.db"))

    def test_no_telegram_messages_sent(self):
        import inspect
        import services.watchlist_evaluator as mod
        source = inspect.getsource(mod)
        self.assertNotIn("import telegram", source.lower())
        self.assertNotIn("bot.telegram_bot", source.lower())


if __name__ == "__main__":
    unittest.main()
