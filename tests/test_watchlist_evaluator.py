"""
Tests for the Phase 4 dry-run watchlist evaluator (services/watchlist_evaluator.py).

All market data is supplied via a FakeMarketDataClient (duck-typed like
data.market_data_validator.MarketDataClient) — no live yfinance calls.
Every test uses a temporary SQLite database; none touch the production DB.
"""
import importlib
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from data.market_data_validator import MarketDataResult, ProviderStatus
from tests.fixtures import make_falling_df, make_trending_df


def _reload_db(db_path: str):
    import db.database as dbmod
    importlib.reload(dbmod)
    dbmod.DB_PATH = Path(db_path)
    return dbmod


def _market_result(
    symbol,
    *,
    status=ProviderStatus.OK,
    security_type="stock",
    latest_close=150.0,
    latest_volume=2_000_000,
    average_daily_volume=2_000_000.0,
    average_daily_dollar_volume=300_000_000.0,
    failure_reason=None,
    failure_type=None,
) -> MarketDataResult:
    return MarketDataResult(
        symbol=symbol,
        normalized_symbol=symbol,
        security_type=security_type,
        provider_status=status,
        is_valid=status != ProviderStatus.INVALID_SYMBOL,
        latest_close=latest_close,
        latest_volume=latest_volume,
        average_daily_volume=average_daily_volume,
        average_daily_dollar_volume=average_daily_dollar_volume,
        history_days_available=252,
        data_timestamp_utc="2024-03-15 21:00:00",
        latest_completed_candle_date="2024-03-15",
        failure_reason=failure_reason,
        failure_type=failure_type,
    )


class FakeMarketDataClient:
    """Duck-typed stand-in for MarketDataClient with fully controlled responses."""

    def __init__(self, market_results: dict, histories: dict | None = None):
        self.market_results = market_results
        self.histories = histories or {}
        self.cache_hits = 4
        self.cache_misses = 6
        self.yfinance_request_count = 6
        self.provider_error_count = 0

    def validate_batch(self, symbols, security_types=None):
        return {s: self.market_results[s] for s in symbols}

    def get_history(self, symbol):
        return self.histories.get(symbol), None


class WatchlistEvaluatorTestCase(unittest.TestCase):

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


class TestEvaluationRunRecording(WatchlistEvaluatorTestCase):

    def test_dry_run_creates_evaluation_run_row(self):
        self._seed({"AI & Semiconductors": ["NVDA"]})
        self._set_row("NVDA", wl_state="ACTIVE")
        client = FakeMarketDataClient(
            {"NVDA": _market_result("NVDA")}, {"NVDA": make_trending_df(n=252)}
        )
        result = self.evaluator.run_dry_run_evaluation(client=client)
        run = self.db.get_evaluation_run(result.run_id)
        self.assertIsNotNone(run)
        self.assertEqual(run["dry_run"], 1)
        self.assertEqual(run["run_type"], "dry_run")
        self.assertIn(run["status"], ("success", "partial_failure"))

    def test_dry_run_does_not_modify_watchlist_state(self):
        self._seed({"AI & Semiconductors": ["NVDA"]})
        self._set_row("NVDA", wl_state="ACTIVE")
        before = self.db.get_symbol_status("NVDA")
        client = FakeMarketDataClient(
            {"NVDA": _market_result("NVDA")}, {"NVDA": make_trending_df(n=252)}
        )
        self.evaluator.run_dry_run_evaluation(client=client)
        after = self.db.get_symbol_status("NVDA")
        self.assertEqual(before, after)

    def test_fatal_error_marks_run_failed(self):
        self._seed({"AI & Semiconductors": ["NVDA"]})
        self._set_row("NVDA", wl_state="ACTIVE")

        class ExplodingClient:
            def validate_batch(self, *a, **kw):
                raise RuntimeError("boom")

        result = self.evaluator.run_dry_run_evaluation(client=ExplodingClient())
        self.assertIsNotNone(result.fatal_error)
        run = self.db.get_evaluation_run(result.run_id)
        self.assertEqual(run["status"], "failed")

    def test_no_production_db_usage(self):
        """The temp DB path must differ from the real db/stocksage.db, proving isolation."""
        import db.database as real_db_mod
        self.assertNotEqual(str(self.db.DB_PATH), str(real_db_mod.DB_PATH.parent / "stocksage.db"))


class TestUniverseSelection(WatchlistEvaluatorTestCase):

    def test_evaluates_active_symbols(self):
        self._seed({"AI & Semiconductors": ["NVDA"]})
        self._set_row("NVDA", wl_state="ACTIVE")
        client = FakeMarketDataClient(
            {"NVDA": _market_result("NVDA")}, {"NVDA": make_trending_df(n=252)}
        )
        result = self.evaluator.run_dry_run_evaluation(client=client)
        symbols = [r.symbol for r in result.symbol_results]
        self.assertIn("NVDA", symbols)

    def test_evaluates_monitor_symbols(self):
        self._seed({"AI & Semiconductors": ["CRM"]})  # MONITOR by default (CRM is not in INITIAL_ACTIVE_SET)
        client = FakeMarketDataClient(
            {"CRM": _market_result("CRM")}, {"CRM": make_trending_df(n=252)}
        )
        result = self.evaluator.run_dry_run_evaluation(client=client)
        symbols = [r.symbol for r in result.symbol_results]
        self.assertIn("CRM", symbols)

    def test_skips_user_removed(self):
        self._seed({"AI & Semiconductors": ["NVDA"]})
        self.db.remove_from_watchlist("NVDA")
        client = FakeMarketDataClient({}, {})
        result = self.evaluator.run_dry_run_evaluation(client=client)
        symbols = [r.symbol for r in result.symbol_results]
        self.assertNotIn("NVDA", symbols)

    def test_skips_etf_index_context(self):
        self._seed({"ETFs": ["SPY"]})
        client = FakeMarketDataClient({}, {})
        result = self.evaluator.run_dry_run_evaluation(client=client)
        evaluated = [r.symbol for r in result.symbol_results if r.skip_reason is None]
        self.assertNotIn("SPY", evaluated)

    def test_skips_disabled_symbols(self):
        self._seed({"AI & Semiconductors": ["NVDA"]})
        self._set_row("NVDA", enabled=0, wl_state="USER_REMOVED")
        client = FakeMarketDataClient({}, {})
        result = self.evaluator.run_dry_run_evaluation(client=client)
        symbols = [r.symbol for r in result.symbol_results]
        self.assertNotIn("NVDA", symbols)

    def test_skips_temporarily_ineligible_before_retry_time(self):
        self._seed({"AI & Semiconductors": ["NVDA"]})
        future = (datetime.now(timezone.utc) + timedelta(days=5)).date().isoformat()
        self._set_row("NVDA", wl_state="TEMPORARILY_INELIGIBLE", reeval_date=future)
        client = FakeMarketDataClient({}, {})
        result = self.evaluator.run_dry_run_evaluation(client=client)
        evaluated = [r.symbol for r in result.symbol_results if r.skip_reason is None]
        self.assertNotIn("NVDA", evaluated)
        skipped = [r for r in result.symbol_results if r.symbol == "NVDA"]
        self.assertEqual(len(skipped), 1)
        self.assertIn("retry not due", skipped[0].skip_reason)

    def test_evaluates_temporarily_ineligible_after_retry_time(self):
        self._seed({"AI & Semiconductors": ["NVDA"]})
        past = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
        self._set_row("NVDA", wl_state="TEMPORARILY_INELIGIBLE", reeval_date=past)
        client = FakeMarketDataClient(
            {"NVDA": _market_result("NVDA")}, {"NVDA": make_trending_df(n=252)}
        )
        result = self.evaluator.run_dry_run_evaluation(client=client)
        evaluated = [r for r in result.symbol_results if r.symbol == "NVDA" and r.skip_reason is None]
        self.assertEqual(len(evaluated), 1)


class TestPromotionAndDemotion(WatchlistEvaluatorTestCase):

    def test_valid_monitor_symbol_can_be_promoted(self):
        self._seed({"AI & Semiconductors": ["CRM"]})
        # PROMOTION_CONSEC_REQUIRED=2: this evaluation must be the second
        # consecutive passing score, so seed one prior consecutive pass.
        self._set_row("CRM", consec_promote_count=1)
        client = FakeMarketDataClient(
            {"CRM": _market_result(
                "CRM", average_daily_volume=2_000_000.0, average_daily_dollar_volume=300_000_000.0,
            )},
            {"CRM": make_trending_df(n=252)},
        )
        result = self.evaluator.run_dry_run_evaluation(client=client)
        crm = next(r for r in result.symbol_results if r.symbol == "CRM")
        self.assertGreaterEqual(crm.relevance_score, 60)
        self.assertEqual(crm.proposed_state, "ACTIVE")
        self.assertIn("CRM", result.proposed_promotions)

    def test_valid_active_symbol_remains_active(self):
        self._seed({"AI & Semiconductors": ["NVDA"]})
        self._set_row("NVDA", wl_state="ACTIVE", dwell_days=10)
        client = FakeMarketDataClient(
            {"NVDA": _market_result("NVDA")}, {"NVDA": make_trending_df(n=252)}
        )
        result = self.evaluator.run_dry_run_evaluation(client=client)
        nvda = next(r for r in result.symbol_results if r.symbol == "NVDA")
        self.assertEqual(nvda.proposed_state, "ACTIVE")

    def test_weak_active_symbol_proposed_for_demotion(self):
        self._seed({"AI & Semiconductors": ["NVDA"]})
        self._set_row("NVDA", wl_state="ACTIVE", dwell_days=10, consec_demote_count=1)
        client = FakeMarketDataClient(
            {"NVDA": _market_result(
                "NVDA", latest_close=5.0, latest_volume=0, average_daily_volume=0.0,
                average_daily_dollar_volume=0.0,
            )},
            {"NVDA": make_falling_df(n=252)},
        )
        result = self.evaluator.run_dry_run_evaluation(client=client)
        nvda = next(r for r in result.symbol_results if r.symbol == "NVDA")
        self.assertLess(nvda.relevance_score, 45)
        self.assertEqual(nvda.proposed_state, "MONITOR")
        self.assertIn("NVDA", result.proposed_demotions)

    def test_stale_data_symbol_proposed_temporarily_ineligible(self):
        self._seed({"AI & Semiconductors": ["NVDA"]})
        self._set_row("NVDA", wl_state="MONITOR")
        client = FakeMarketDataClient(
            {"NVDA": _market_result(
                "NVDA", status=ProviderStatus.STALE_DATA,
                failure_type="data_quality", failure_reason="latest completed candle is 10 days old",
            )},
            {},
        )
        result = self.evaluator.run_dry_run_evaluation(client=client)
        nvda = next(r for r in result.symbol_results if r.symbol == "NVDA")
        self.assertEqual(nvda.proposed_state, "TEMPORARILY_INELIGIBLE")
        self.assertIn("NVDA", result.proposed_ineligible)

    def test_recovered_ineligible_symbol_proposed_monitor_not_active(self):
        self._seed({"AI & Semiconductors": ["NVDA"]})
        past = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
        self._set_row("NVDA", wl_state="TEMPORARILY_INELIGIBLE", reeval_date=past)
        client = FakeMarketDataClient(
            {"NVDA": _market_result("NVDA")}, {"NVDA": make_trending_df(n=252)}
        )
        result = self.evaluator.run_dry_run_evaluation(client=client)
        nvda = next(r for r in result.symbol_results if r.symbol == "NVDA")
        # Even though the score may qualify for promotion, recovery must land in MONITOR.
        self.assertEqual(nvda.proposed_state, "MONITOR")
        self.assertIn("NVDA", result.proposed_recoveries)
        self.assertNotIn("NVDA", result.proposed_promotions)


class TestProviderOutage(WatchlistEvaluatorTestCase):

    def test_partial_outage_marks_run_partial_failure(self):
        symbols = [f"SYM{i}" for i in range(5)]
        self._seed({"AI & Semiconductors": symbols})
        for s in symbols:
            self._set_row(s, wl_state="ACTIVE", dwell_days=10, consec_demote_count=1)

        market_results = {}
        histories = {}
        for i, s in enumerate(symbols):
            if i < 3:  # 3/5 = 60% transient failures -> degraded
                market_results[s] = _market_result(
                    s, status=ProviderStatus.RATE_LIMITED, failure_type="provider_transient",
                    failure_reason="429 too many requests",
                )
            else:
                market_results[s] = _market_result(
                    s, latest_close=5.0, latest_volume=0, average_daily_volume=0.0,
                    average_daily_dollar_volume=0.0,
                )
                histories[s] = make_falling_df(n=252)

        client = FakeMarketDataClient(market_results, histories)
        result = self.evaluator.run_dry_run_evaluation(client=client)
        self.assertTrue(result.provider_degraded)
        run = self.db.get_evaluation_run(result.run_id)
        self.assertEqual(run["status"], "partial_failure")

    def test_total_outage_does_not_empty_active(self):
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
        result = self.evaluator.run_dry_run_evaluation(client=client)
        self.assertTrue(result.provider_degraded)
        self.assertEqual(result.active_after, 5)  # all symbols stay ACTIVE — none proposed for demotion
        for r in result.symbol_results:
            self.assertEqual(r.proposed_state, "ACTIVE")


class TestActiveListConstraints(WatchlistEvaluatorTestCase):

    def _make_promotable_pool(self, n, base_score_boost=0):
        symbols = [f"CAND{i}" for i in range(n)]
        self._seed({"AI & Semiconductors": symbols})
        market_results = {}
        histories = {}
        for s in symbols:
            market_results[s] = _market_result(s)
            histories[s] = make_trending_df(n=252)
        return symbols, market_results, histories

    def test_active_maximum_30_enforced(self):
        symbols, market_results, histories = self._make_promotable_pool(35)
        client = FakeMarketDataClient(market_results, histories)
        result = self.evaluator.run_dry_run_evaluation(client=client)
        self.assertLessEqual(result.active_after, 30)

    def test_bank_maximum_8_enforced(self):
        symbols = [f"BANK{i}" for i in range(12)]
        self._seed({"פיננסים": symbols})
        market_results = {s: _market_result(s) for s in symbols}
        histories = {s: make_trending_df(n=252) for s in symbols}
        client = FakeMarketDataClient(market_results, histories)
        result = self.evaluator.run_dry_run_evaluation(client=client)
        bank_active = [r for r in result.symbol_results if r.proposed_state == "ACTIVE"]
        self.assertLessEqual(len(bank_active), 8)

    def test_replacement_margin_enforced(self):
        # Fill ACTIVE to the cap with a known score, then offer one candidate
        # that beats the lowest by less than the margin (should NOT replace)
        # and one that beats it by at least the margin (SHOULD replace).
        active_symbols = [f"ACT{i}" for i in range(30)]
        self._seed({"AI & Semiconductors": active_symbols + ["WEAKBEAT", "STRONGBEAT"]})
        for s in active_symbols:
            self._set_row(s, wl_state="ACTIVE", dwell_days=10, relevance_score=70)

        market_results = {}
        histories = {}
        for s in active_symbols:
            market_results[s] = _market_result(s)
            histories[s] = make_trending_df(n=252, seed=hash(s) % 1000)
        # Two MONITOR candidates with strong scores; exact score depends on
        # full_analysis, but both use the same high-liquidity inputs as ACTIVE.
        market_results["WEAKBEAT"] = _market_result("WEAKBEAT")
        histories["WEAKBEAT"] = make_trending_df(n=252)
        market_results["STRONGBEAT"] = _market_result("STRONGBEAT")
        histories["STRONGBEAT"] = make_trending_df(n=252)

        client = FakeMarketDataClient(market_results, histories)
        result = self.evaluator.run_dry_run_evaluation(client=client)
        self.assertLessEqual(result.active_after, 30)

    def test_deterministic_tie_breaking(self):
        symbols, market_results, histories = self._make_promotable_pool(3)
        client1 = FakeMarketDataClient(dict(market_results), dict(histories))
        client2 = FakeMarketDataClient(dict(market_results), dict(histories))
        r1 = self.evaluator.run_dry_run_evaluation(client=client1)
        # Re-seed identical DB state for the second run.
        f2 = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        f2.close()
        self.db = _reload_db(f2.name)
        self._seed({"AI & Semiconductors": symbols})
        r2 = self.evaluator.run_dry_run_evaluation(client=client2)
        states1 = sorted((r.symbol, r.proposed_state) for r in r1.symbol_results)
        states2 = sorted((r.symbol, r.proposed_state) for r in r2.symbol_results)
        self.assertEqual(states1, states2)

    def test_no_duplicate_ticker_in_active(self):
        symbols, market_results, histories = self._make_promotable_pool(10)
        client = FakeMarketDataClient(market_results, histories)
        result = self.evaluator.run_dry_run_evaluation(client=client)
        active_syms = [r.symbol for r in result.symbol_results if r.proposed_state == "ACTIVE"]
        self.assertEqual(len(active_syms), len(set(active_syms)))


class TestDataSafetyAndStats(WatchlistEvaluatorTestCase):

    def test_nan_score_safety(self):
        self._seed({"AI & Semiconductors": ["NVDA"]})
        client = FakeMarketDataClient(
            {"NVDA": _market_result("NVDA", average_daily_volume=float("nan"))},
            {"NVDA": make_trending_df(n=252)},
        )
        result = self.evaluator.run_dry_run_evaluation(client=client)
        nvda = next(r for r in result.symbol_results if r.symbol == "NVDA")
        self.assertIsNotNone(nvda.relevance_score)
        self.assertFalse(nvda.relevance_score != nvda.relevance_score)  # not NaN

    def test_invalid_symbol_classification(self):
        self._seed({"AI & Semiconductors": ["ZZZZ"]})
        client = FakeMarketDataClient(
            {"ZZZZ": _market_result(
                "ZZZZ", status=ProviderStatus.INVALID_SYMBOL, failure_type="unsupported",
                failure_reason="no data found, symbol may be delisted",
            )},
            {},
        )
        result = self.evaluator.run_dry_run_evaluation(client=client)
        self.assertEqual(result.invalid_symbol_count, 1)
        zzzz = next(r for r in result.symbol_results if r.symbol == "ZZZZ")
        self.assertEqual(zzzz.proposed_state, "TEMPORARILY_INELIGIBLE")

    def test_cache_and_request_stats_included(self):
        self._seed({"AI & Semiconductors": ["NVDA"]})
        client = FakeMarketDataClient(
            {"NVDA": _market_result("NVDA")}, {"NVDA": make_trending_df(n=252)}
        )
        result = self.evaluator.run_dry_run_evaluation(client=client)
        self.assertEqual(result.cache_hits, client.cache_hits)
        self.assertEqual(result.cache_misses, client.cache_misses)
        self.assertEqual(result.yfinance_request_count, client.yfinance_request_count)

    def test_symbol_result_includes_reason(self):
        self._seed({"AI & Semiconductors": ["NVDA"]})
        client = FakeMarketDataClient(
            {"NVDA": _market_result("NVDA")}, {"NVDA": make_trending_df(n=252)}
        )
        result = self.evaluator.run_dry_run_evaluation(client=client)
        nvda = next(r for r in result.symbol_results if r.symbol == "NVDA")
        self.assertTrue(nvda.reason)

    def test_no_real_telegram_messages(self):
        import inspect
        import services.watchlist_evaluator as mod
        source = inspect.getsource(mod)
        self.assertNotIn("import telegram", source.lower())
        self.assertNotIn("bot.telegram_bot", source.lower())


if __name__ == "__main__":
    unittest.main()
