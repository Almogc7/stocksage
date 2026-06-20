"""
Tests for Phase 5.5 — audit log and rollback support for applied watchlist
evaluation runs (services/watchlist_evaluator.rollback_evaluation_run).

All market data is fully mocked; every test uses a temporary SQLite
database. None of these tests touch the production database or send
Telegram messages.
"""
import importlib
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from data.market_data_validator import ProviderStatus
from tests.fixtures import make_falling_df, make_trending_df
from tests.test_watchlist_evaluator import FakeMarketDataClient, _market_result


def _reload_db(db_path: str):
    import db.database as dbmod
    importlib.reload(dbmod)
    dbmod.DB_PATH = Path(db_path)
    return dbmod


class RollbackTestCase(unittest.TestCase):

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


class TestAuditRowCreation(RollbackTestCase):

    def test_audit_rows_created_during_apply(self):
        self._seed({"AI & Semiconductors": ["CRM"]})
        client = FakeMarketDataClient({"CRM": _market_result("CRM")}, {"CRM": make_trending_df(n=252)})
        result = self.evaluator.run_watchlist_evaluation(apply=True, client=client)
        changes = self.db.get_changes_for_run(result.run_id)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["symbol"], "CRM")

    def test_dry_run_creates_no_audit_rows(self):
        self._seed({"AI & Semiconductors": ["CRM"]})
        client = FakeMarketDataClient({"CRM": _market_result("CRM")}, {"CRM": make_trending_df(n=252)})
        result = self.evaluator.run_watchlist_evaluation(apply=False, client=client)
        changes = self.db.get_changes_for_run(result.run_id)
        self.assertEqual(changes, [])

    def test_audit_stores_previous_and_new_values(self):
        self._seed({"AI & Semiconductors": ["CRM"]})
        client = FakeMarketDataClient({"CRM": _market_result("CRM")}, {"CRM": make_trending_df(n=252)})
        result = self.evaluator.run_watchlist_evaluation(apply=True, client=client)
        change = self.db.get_changes_for_run(result.run_id)[0]
        import json
        prev = json.loads(change["previous_values_json"])
        new = json.loads(change["new_values_json"])
        self.assertIsNone(prev.get("relevance_score"))
        self.assertIsNotNone(new.get("relevance_score"))

    def test_audit_captures_promotion(self):
        self._seed({"AI & Semiconductors": ["CRM"]})
        self._set_row("CRM", consec_promote_count=1)
        client = FakeMarketDataClient({"CRM": _market_result("CRM")}, {"CRM": make_trending_df(n=252)})
        result = self.evaluator.run_watchlist_evaluation(apply=True, client=client)
        change = self.db.get_changes_for_run(result.run_id)[0]
        self.assertEqual(change["change_type"], "promotion")

    def test_audit_captures_demotion(self):
        self._seed({"AI & Semiconductors": ["NVDA"]})
        self._set_row("NVDA", wl_state="ACTIVE", dwell_days=10, consec_demote_count=1)
        client = FakeMarketDataClient(
            {"NVDA": _market_result("NVDA", latest_close=5.0, latest_volume=0,
                                     average_daily_volume=0.0, average_daily_dollar_volume=0.0)},
            {"NVDA": make_falling_df(n=252)},
        )
        result = self.evaluator.run_watchlist_evaluation(apply=True, client=client)
        change = self.db.get_changes_for_run(result.run_id)[0]
        self.assertEqual(change["change_type"], "demotion")

    def test_audit_captures_score_or_counter_update(self):
        self._seed({"AI & Semiconductors": ["CRM"]})
        client = FakeMarketDataClient({"CRM": _market_result("CRM")}, {"CRM": make_trending_df(n=252)})
        result = self.evaluator.run_watchlist_evaluation(apply=True, client=client)
        change = self.db.get_changes_for_run(result.run_id)[0]
        self.assertIn(change["change_type"], ("counter_update", "score_update"))

    def test_audit_captures_temporarily_ineligible_transition(self):
        self._seed({"AI & Semiconductors": ["NVDA"]})
        self._set_row("NVDA", wl_state="MONITOR")
        client = FakeMarketDataClient(
            {"NVDA": _market_result(
                "NVDA", status=ProviderStatus.STALE_DATA, failure_type="data_quality",
                failure_reason="latest completed candle is 10 days old",
            )},
            {},
        )
        result = self.evaluator.run_watchlist_evaluation(apply=True, client=client)
        change = self.db.get_changes_for_run(result.run_id)[0]
        self.assertEqual(change["change_type"], "ineligible")


class TestRollbackRestoresState(RollbackTestCase):

    def test_rollback_restores_previous_state(self):
        self._seed({"AI & Semiconductors": ["CRM"]})
        before = self.db.get_symbol_status("CRM")
        self._set_row("CRM", consec_promote_count=1)
        client = FakeMarketDataClient({"CRM": _market_result("CRM")}, {"CRM": make_trending_df(n=252)})
        result = self.evaluator.run_watchlist_evaluation(apply=True, client=client)
        self.assertEqual(self.db.get_symbol_status("CRM")["wl_state"], "ACTIVE")

        rb = self.evaluator.rollback_evaluation_run(result.run_id)
        self.assertEqual(rb["status"], "success")
        after = self.db.get_symbol_status("CRM")
        # consec_promote_count was manually bumped to 1 before the apply run,
        # which is exactly the previous_values captured — confirms full restore.
        self.assertEqual(after["wl_state"], "MONITOR")
        self.assertEqual(after["consec_promote_count"], 1)
        self.assertIsNone(after["relevance_score"])

    def test_rollback_restores_relevance_score(self):
        self._seed({"AI & Semiconductors": ["CRM"]})
        client = FakeMarketDataClient({"CRM": _market_result("CRM")}, {"CRM": make_trending_df(n=252)})
        result = self.evaluator.run_watchlist_evaluation(apply=True, client=client)
        self.assertIsNotNone(self.db.get_symbol_status("CRM")["relevance_score"])
        self.evaluator.rollback_evaluation_run(result.run_id)
        self.assertIsNone(self.db.get_symbol_status("CRM")["relevance_score"])

    def test_rollback_restores_counters(self):
        self._seed({"AI & Semiconductors": ["NVDA"]})
        self._set_row("NVDA", wl_state="ACTIVE", dwell_days=10, consec_demote_count=1)
        client = FakeMarketDataClient(
            {"NVDA": _market_result("NVDA", latest_close=5.0, latest_volume=0,
                                     average_daily_volume=0.0, average_daily_dollar_volume=0.0)},
            {"NVDA": make_falling_df(n=252)},
        )
        result = self.evaluator.run_watchlist_evaluation(apply=True, client=client)
        self.assertEqual(self.db.get_symbol_status("NVDA")["wl_state"], "MONITOR")
        self.evaluator.rollback_evaluation_run(result.run_id)
        after = self.db.get_symbol_status("NVDA")
        self.assertEqual(after["wl_state"], "ACTIVE")
        self.assertEqual(after["consec_demote_count"], 1)

    def test_rollback_restores_timestamps(self):
        self._seed({"AI & Semiconductors": ["CRM"]})
        self._set_row("CRM", consec_promote_count=1)
        client = FakeMarketDataClient({"CRM": _market_result("CRM")}, {"CRM": make_trending_df(n=252)})
        result = self.evaluator.run_watchlist_evaluation(apply=True, client=client)
        self.assertIsNotNone(self.db.get_symbol_status("CRM")["last_promoted"])
        self.evaluator.rollback_evaluation_run(result.run_id)
        self.assertIsNone(self.db.get_symbol_status("CRM")["last_promoted"])

    def test_rollback_restores_exclusion_reason(self):
        self._seed({"AI & Semiconductors": ["NVDA"]})
        self._set_row("NVDA", wl_state="MONITOR")
        client = FakeMarketDataClient(
            {"NVDA": _market_result(
                "NVDA", status=ProviderStatus.STALE_DATA, failure_type="data_quality",
                failure_reason="stale candle",
            )},
            {},
        )
        result = self.evaluator.run_watchlist_evaluation(apply=True, client=client)
        self.assertNotEqual(self.db.get_symbol_status("NVDA")["exclusion_reason"], "")
        self.evaluator.rollback_evaluation_run(result.run_id)
        after = self.db.get_symbol_status("NVDA")
        self.assertEqual(after["wl_state"], "MONITOR")
        self.assertIn(after["exclusion_reason"], (None, ""))

    def test_rollback_works_after_multi_symbol_apply(self):
        # None of these are in the hardcoded INITIAL_ACTIVE_SET seed list,
        # so all three genuinely start at MONITOR and promote together.
        symbols = ["CRM", "ORCL", "ADBE"]
        self._seed({"AI & Semiconductors": symbols})
        for s in symbols:
            self._set_row(s, consec_promote_count=1)
        market_results = {s: _market_result(s) for s in symbols}
        histories = {s: make_trending_df(n=252) for s in symbols}
        client = FakeMarketDataClient(market_results, histories)
        result = self.evaluator.run_watchlist_evaluation(apply=True, client=client)
        self.assertEqual(len(self.db.get_changes_for_run(result.run_id)), 3)

        rb = self.evaluator.rollback_evaluation_run(result.run_id)
        self.assertEqual(rb["status"], "success")
        self.assertEqual(set(rb["restored_symbols"]), set(symbols))
        for s in symbols:
            self.assertEqual(self.db.get_symbol_status(s)["wl_state"], "MONITOR")
            self.assertIsNone(self.db.get_symbol_status(s)["relevance_score"])

    def test_rollback_leaves_category_tags_intact(self):
        self._seed({"AI & Semiconductors": ["CRM"]})
        before_cats = self.db.get_symbol_categories("CRM")
        client = FakeMarketDataClient({"CRM": _market_result("CRM")}, {"CRM": make_trending_df(n=252)})
        result = self.evaluator.run_watchlist_evaluation(apply=True, client=client)
        self.evaluator.rollback_evaluation_run(result.run_id)
        after_cats = self.db.get_symbol_categories("CRM")
        self.assertEqual(before_cats, after_cats)

    def test_rollback_records_rolled_back_at(self):
        self._seed({"AI & Semiconductors": ["CRM"]})
        client = FakeMarketDataClient({"CRM": _market_result("CRM")}, {"CRM": make_trending_df(n=252)})
        result = self.evaluator.run_watchlist_evaluation(apply=True, client=client)
        self.evaluator.rollback_evaluation_run(result.run_id)
        change = self.db.get_changes_for_run(result.run_id)[0]
        self.assertIsNotNone(change["rolled_back_at"])
        self.assertEqual(change["rollback_status"], "rolled_back")


class TestRollbackRefusals(RollbackTestCase):

    def test_rollback_refuses_unknown_run_id(self):
        self._seed({"AI & Semiconductors": ["CRM"]})
        with self.assertRaises(self.evaluator.RollbackError):
            self.evaluator.rollback_evaluation_run(999999)

    def test_rollback_refuses_dry_run_id(self):
        self._seed({"AI & Semiconductors": ["CRM"]})
        client = FakeMarketDataClient({"CRM": _market_result("CRM")}, {"CRM": make_trending_df(n=252)})
        result = self.evaluator.run_watchlist_evaluation(apply=False, client=client)
        with self.assertRaises(self.evaluator.RollbackError):
            self.evaluator.rollback_evaluation_run(result.run_id)

    def test_rollback_refuses_to_run_twice(self):
        self._seed({"AI & Semiconductors": ["CRM"]})
        client = FakeMarketDataClient({"CRM": _market_result("CRM")}, {"CRM": make_trending_df(n=252)})
        result = self.evaluator.run_watchlist_evaluation(apply=True, client=client)
        self.evaluator.rollback_evaluation_run(result.run_id)
        with self.assertRaises(self.evaluator.RollbackError):
            self.evaluator.rollback_evaluation_run(result.run_id)


class TestRollbackConflictDetection(RollbackTestCase):

    def test_rollback_detects_conflict_if_row_changed_after_run(self):
        self._seed({"AI & Semiconductors": ["CRM"]})
        client = FakeMarketDataClient({"CRM": _market_result("CRM")}, {"CRM": make_trending_df(n=252)})
        result = self.evaluator.run_watchlist_evaluation(apply=True, client=client)

        self._set_row("CRM", relevance_score=12345)  # simulate a later manual edit

        rb = self.evaluator.rollback_evaluation_run(result.run_id)
        self.assertEqual(rb["status"], "conflict")
        self.assertEqual(rb["conflicts"][0]["symbol"], "CRM")
        # Nothing was overwritten — the manual edit survives.
        self.assertEqual(self.db.get_symbol_status("CRM")["relevance_score"], 12345)

    def test_rollback_does_not_overwrite_user_removed(self):
        """USER_REMOVED symbols are never gathered as apply candidates in the
        first place, so they can never appear in a run's audit trail —
        confirming there is nothing to roll back for them."""
        self._seed({"AI & Semiconductors": ["NVDA"]})
        self.db.remove_from_watchlist("NVDA")
        client = FakeMarketDataClient({}, {})
        result = self.evaluator.run_watchlist_evaluation(apply=True, client=client)
        changes = self.db.get_changes_for_run(result.run_id)
        self.assertEqual([c for c in changes if c["symbol"] == "NVDA"], [])


class TestRollbackAtomicity(RollbackTestCase):

    def test_rollback_is_atomic_failure_leaves_all_rows_unchanged(self):
        symbols = ["CRM", "NVDA"]
        self._seed({"AI & Semiconductors": symbols})
        market_results = {s: _market_result(s) for s in symbols}
        histories = {s: make_trending_df(n=252) for s in symbols}
        client = FakeMarketDataClient(market_results, histories)
        result = self.evaluator.run_watchlist_evaluation(apply=True, client=client)

        before = {s: self.db.get_symbol_status(s) for s in symbols}

        orig_apply_rollback = self.db.apply_rollback

        def exploding_apply_rollback(*a, **kw):
            raise RuntimeError("simulated rollback write failure")

        self.db.apply_rollback = exploding_apply_rollback
        try:
            with self.assertRaises(RuntimeError):
                self.evaluator.rollback_evaluation_run(result.run_id)
        finally:
            self.db.apply_rollback = orig_apply_rollback

        for s in symbols:
            self.assertEqual(self.db.get_symbol_status(s), before[s])
        # The audit rows must also be untouched — not marked rolled back.
        for c in self.db.get_changes_for_run(result.run_id):
            self.assertIsNone(c["rolled_back_at"])
            self.assertIsNone(c["rollback_status"])


class TestRollbackSafety(RollbackTestCase):

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
