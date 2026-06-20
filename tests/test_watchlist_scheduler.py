"""
Tests for Phase 6 — watchlist evaluation scheduler and market-calendar
logic (services/watchlist_scheduler.py).

All times are mocked explicitly; every test uses a temporary SQLite
database. None of these tests touch the production database or send
Telegram messages.
"""
import importlib
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from data.market_data_validator import MarketDataResult, ProviderStatus
from tests.fixtures import make_trending_df

ET = ZoneInfo("America/New_York")


def _reload_db(db_path: str):
    import db.database as dbmod
    importlib.reload(dbmod)
    dbmod.DB_PATH = Path(db_path)
    return dbmod


class _FakeClient:
    cache_hits = 0
    cache_misses = 0
    yfinance_request_count = 0
    provider_error_count = 0

    def validate_batch(self, symbols, security_types=None):
        return {
            s: MarketDataResult(
                symbol=s, normalized_symbol=s, security_type="stock",
                provider_status=ProviderStatus.OK, is_valid=True,
                latest_close=150.0, latest_volume=2_000_000,
                average_daily_volume=2_000_000.0, average_daily_dollar_volume=3e8,
                history_days_available=252, data_timestamp_utc="2024-03-15 21:00:00",
                latest_completed_candle_date="2024-03-15",
            )
            for s in symbols
        }

    def get_history(self, symbol):
        return make_trending_df(n=252), None


class SchedulerTestCase(unittest.TestCase):

    def setUp(self):
        f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        f.close()
        self.db = _reload_db(f.name)
        self.db.init_db({"AI & Semiconductors": ["CRM"]})
        self.db.run_initial_classification({"AI & Semiconductors": ["CRM"]})
        import services.watchlist_scheduler as sched_mod
        importlib.reload(sched_mod)
        self.sched = sched_mod

    # A known Thursday market day, 2026-06-18.
    MARKET_DAY = date(2026, 6, 18)

    def _et(self, hour, minute, d=None):
        d = d or self.MARKET_DAY
        return datetime(d.year, d.month, d.day, hour, minute, tzinfo=ET).astimezone(timezone.utc)


class TestMarketCalendar(SchedulerTestCase):

    def test_weekday_market_day_is_a_market_day(self):
        self.assertTrue(self.sched.is_us_market_day(date(2026, 6, 18)))  # Thursday

    def test_weekend_is_not_a_market_day(self):
        self.assertFalse(self.sched.is_us_market_day(date(2026, 6, 20)))  # Saturday
        self.assertFalse(self.sched.is_us_market_day(date(2026, 6, 21)))  # Sunday

    def test_known_holiday_is_not_a_market_day(self):
        self.assertFalse(self.sched.is_us_market_day(date(2026, 12, 25)))  # Christmas (Friday)
        self.assertFalse(self.sched.is_us_market_day(date(2026, 1, 1)))   # New Year's Day (Thursday)
        self.assertFalse(self.sched.is_us_market_day(date(2026, 11, 26)))  # Thanksgiving

    def test_extra_configured_holiday_is_skipped(self):
        import config
        from unittest.mock import patch
        with patch.object(config, "WATCHLIST_EXTRA_HOLIDAY_DATES", frozenset({"2026-06-18"})):
            import services.watchlist_scheduler as sched_mod
            importlib.reload(sched_mod)
            self.assertFalse(sched_mod.is_us_market_day(date(2026, 6, 18)))
        importlib.reload(self.sched)

    def test_early_close_day_after_thanksgiving_detected(self):
        self.assertTrue(self.sched.is_early_close_day(date(2026, 11, 27)))

    def test_early_close_does_not_block_market_day_status(self):
        # Early close is informational only — the day is still a market day.
        self.assertTrue(self.sched.is_us_market_day(date(2026, 11, 27)))


class TestBeforeAfterClose(SchedulerTestCase):

    def test_before_market_close_should_not_run(self):
        now = self._et(16, 0)  # 4pm ET, before 17:30 threshold
        due, reason = self.sched.should_run_watchlist_evaluation(now)
        self.assertFalse(due)
        self.assertIn("17:30", reason)

    def test_after_market_close_should_run(self):
        now = self._et(18, 0)  # 6pm ET
        due, reason = self.sched.should_run_watchlist_evaluation(now)
        self.assertTrue(due)
        self.assertEqual(reason, "due")

    def test_exactly_at_threshold_should_run(self):
        now = self._et(17, 30)
        self.assertTrue(self.sched.is_after_regular_market_close(now))

    def test_weekend_should_not_run(self):
        now = datetime(2026, 6, 20, 22, 0, tzinfo=timezone.utc)  # Saturday evening UTC
        due, reason = self.sched.should_run_watchlist_evaluation(now)
        self.assertFalse(due)
        self.assertIn("weekend", reason)

    def test_holiday_should_not_run(self):
        now = datetime(2026, 12, 25, 22, 0, tzinfo=timezone.utc)
        due, reason = self.sched.should_run_watchlist_evaluation(now)
        self.assertFalse(due)
        self.assertIn("holiday", reason)

    def test_naive_datetime_rejected(self):
        with self.assertRaises(ValueError):
            self.sched.should_run_watchlist_evaluation(datetime(2026, 6, 18, 18, 0))

    def test_naive_datetime_rejected_for_after_close(self):
        with self.assertRaises(ValueError):
            self.sched.is_after_regular_market_close(datetime(2026, 6, 18, 18, 0))


class TestDST(SchedulerTestCase):

    def test_dst_summer_offset_handled(self):
        # mid-June: ET is UTC-4 (EDT). 17:30 ET == 21:30 UTC.
        now_utc = datetime(2026, 6, 18, 21, 30, tzinfo=timezone.utc)
        self.assertTrue(self.sched.is_after_regular_market_close(now_utc))
        now_utc_before = datetime(2026, 6, 18, 21, 29, tzinfo=timezone.utc)
        self.assertFalse(self.sched.is_after_regular_market_close(now_utc_before))

    def test_dst_winter_offset_handled(self):
        # mid-January: ET is UTC-5 (EST). 17:30 ET == 22:30 UTC.
        now_utc = datetime(2026, 1, 15, 22, 30, tzinfo=timezone.utc)
        self.assertTrue(self.sched.is_after_regular_market_close(now_utc))
        now_utc_before = datetime(2026, 1, 15, 22, 29, tzinfo=timezone.utc)
        self.assertFalse(self.sched.is_after_regular_market_close(now_utc_before))

    def test_dst_transition_does_not_break_schedule(self):
        # 2026-03-08 is the US DST "spring forward" Sunday — a market day
        # the following Monday must still compute correctly.
        monday_after_dst = date(2026, 3, 9)
        self.assertTrue(self.sched.is_us_market_day(monday_after_dst))
        now_utc = datetime(2026, 3, 9, 21, 30, tzinfo=timezone.utc)  # 17:30 EDT
        self.assertTrue(self.sched.is_after_regular_market_close(now_utc))


class TestRunOncePerMarketDay(SchedulerTestCase):

    def test_already_ran_successfully_blocks_second_run(self):
        now = self._et(18, 0)
        out1 = self.sched.run_scheduled_evaluation(apply=False, now=now, client=_FakeClient())
        self.assertTrue(out1["ran"])
        out2 = self.sched.run_scheduled_evaluation(apply=False, now=now, client=_FakeClient())
        self.assertFalse(out2["ran"])
        self.assertIn("already ran successfully", out2["skipped_reason"])

    def test_failed_run_today_does_not_block_retry(self):
        now = self._et(18, 0)

        class ExplodingClient:
            def validate_batch(self, *a, **kw):
                raise RuntimeError("boom")

        out1 = self.sched.run_scheduled_evaluation(apply=False, now=now, client=ExplodingClient())
        self.assertTrue(out1["ran"])
        self.assertIsNotNone(out1["result"].fatal_error)

        out2 = self.sched.run_scheduled_evaluation(apply=False, now=now, client=_FakeClient())
        self.assertTrue(out2["ran"], "a failed scheduled run must not block a same-day retry")

    def test_dry_run_today_does_not_block_scheduled_apply(self):
        now = self._et(18, 0)
        from services.watchlist_evaluator import run_watchlist_evaluation
        run_watchlist_evaluation(apply=False, client=_FakeClient(), now=now, triggered_by="manual-cli")

        out = self.sched.run_scheduled_evaluation(apply=False, now=now, client=_FakeClient())
        self.assertTrue(out["ran"], "a manual dry-run (run_type != scheduled) must not block the scheduler")

    def test_manual_apply_today_does_not_block_scheduled_run(self):
        now = self._et(18, 0)
        from services.watchlist_evaluator import run_watchlist_evaluation
        run_watchlist_evaluation(apply=True, client=_FakeClient(), now=now, triggered_by="manual-cli")

        out = self.sched.run_scheduled_evaluation(apply=False, now=now, client=_FakeClient())
        self.assertTrue(out["ran"], "a manual apply run (run_type='manual') must not block the scheduler")


class TestConcurrencyGuard(SchedulerTestCase):

    def test_fresh_in_progress_run_blocks_new_run(self):
        now = self._et(18, 0)
        self.db.create_evaluation_run("scheduled", dry_run=True, started_at=now.strftime("%Y-%m-%d %H:%M:%S"))
        ok, reason = self.sched.can_start_evaluation_run(now_utc=now)
        self.assertFalse(ok)
        self.assertIn("in progress", reason)

    def test_stuck_old_in_progress_run_is_detected_and_cleared(self):
        now = self._et(18, 0)
        old_started = (now - timedelta(minutes=120)).strftime("%Y-%m-%d %H:%M:%S")
        self.db.create_evaluation_run("scheduled", dry_run=True, started_at=old_started)
        ok, reason = self.sched.can_start_evaluation_run(now_utc=now, stuck_timeout_minutes=60)
        self.assertTrue(ok)
        self.assertIn("cleared", reason)
        run = self.db.get_last_evaluation_run()
        self.assertEqual(run["status"], "failed")

    def test_old_stuck_run_does_not_block_forever(self):
        now = self._et(18, 0)
        old_started = (now - timedelta(hours=5)).strftime("%Y-%m-%d %H:%M:%S")
        self.db.create_evaluation_run("scheduled", dry_run=True, started_at=old_started)
        out = self.sched.run_scheduled_evaluation(apply=False, now=now, client=_FakeClient())
        self.assertTrue(out["ran"])

    def test_concurrent_start_prevention_via_run_scheduled_evaluation(self):
        now = self._et(18, 0)
        self.db.create_evaluation_run("scheduled", dry_run=True, started_at=now.strftime("%Y-%m-%d %H:%M:%S"))
        out = self.sched.run_scheduled_evaluation(apply=False, now=now, client=_FakeClient())
        self.assertFalse(out["ran"])
        self.assertIn("in progress", out["skipped_reason"])


class TestScheduledRunRecording(SchedulerTestCase):

    def test_scheduled_dry_run_records_evaluation_run_correctly(self):
        now = self._et(18, 0)
        out = self.sched.run_scheduled_evaluation(apply=False, now=now, client=_FakeClient())
        run = self.db.get_evaluation_run(out["result"].run_id)
        self.assertEqual(run["run_type"], "scheduled")
        self.assertEqual(run["dry_run"], 1)
        self.assertEqual(run["triggered_by"], "scheduler")

    def test_scheduled_apply_not_enabled_by_default(self):
        import config
        self.assertFalse(config.WATCHLIST_SCHEDULE_APPLY)
        now = self._et(18, 0)
        out = self.sched.run_scheduled_evaluation(now=now, client=_FakeClient())  # apply=None -> resolves from config
        run = self.db.get_evaluation_run(out["result"].run_id)
        self.assertEqual(run["dry_run"], 1)

    def test_scheduled_apply_requires_explicit_true(self):
        now = self._et(18, 0)
        out = self.sched.run_scheduled_evaluation(apply=True, now=now, client=_FakeClient())
        run = self.db.get_evaluation_run(out["result"].run_id)
        self.assertEqual(run["dry_run"], 0)
        self.assertTrue(out["result"].applied)

    def test_skipped_attempt_writes_no_evaluation_run(self):
        before_count = len(self.db.list_recent_evaluation_runs(limit=1000))
        now = self._et(16, 0)  # before close
        out = self.sched.run_scheduled_evaluation(apply=False, now=now, client=_FakeClient())
        self.assertFalse(out["ran"])
        after_count = len(self.db.list_recent_evaluation_runs(limit=1000))
        self.assertEqual(before_count, after_count)

    def test_started_at_reflects_injected_now_not_wall_clock(self):
        now = self._et(18, 0)
        out = self.sched.run_scheduled_evaluation(apply=False, now=now, client=_FakeClient())
        run = self.db.get_evaluation_run(out["result"].run_id)
        self.assertEqual(run["started_at"], now.strftime("%Y-%m-%d %H:%M:%S"))


class TestNextEvaluationTime(SchedulerTestCase):

    def test_next_time_before_close_is_today(self):
        now = self._et(16, 0)
        nxt = self.sched.next_watchlist_evaluation_time(now)
        self.assertEqual(nxt.astimezone(ET).date(), self.MARKET_DAY)

    def test_next_time_after_close_steps_to_next_market_day(self):
        now = self._et(18, 0)
        nxt = self.sched.next_watchlist_evaluation_time(now)
        self.assertGreater(nxt.astimezone(ET).date(), self.MARKET_DAY)
        self.assertTrue(self.sched.is_us_market_day(nxt.astimezone(ET).date()))

    def test_next_time_skips_weekend(self):
        friday_after_close = self._et(18, 0, d=date(2026, 6, 19))
        nxt = self.sched.next_watchlist_evaluation_time(friday_after_close)
        self.assertEqual(nxt.astimezone(ET).date(), date(2026, 6, 22))  # Monday


class TestSafety(SchedulerTestCase):

    def test_no_production_db_used(self):
        import db.database as real_db_mod
        self.assertNotEqual(str(self.db.DB_PATH), str(real_db_mod.DB_PATH.parent / "stocksage.db"))

    def test_no_telegram_messages_sent(self):
        import inspect
        import services.watchlist_scheduler as mod
        source = inspect.getsource(mod)
        self.assertNotIn("import telegram", source.lower())
        self.assertNotIn("bot.telegram_bot", source.lower())

    def test_module_does_not_start_background_thread_or_loop(self):
        import inspect
        import services.watchlist_scheduler as mod
        source = inspect.getsource(mod)
        self.assertNotIn("threading.thread", source.lower())
        self.assertNotIn("schedule.every", source.lower())


if __name__ == "__main__":
    unittest.main()
