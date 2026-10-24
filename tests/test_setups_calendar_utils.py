"""Tests for setups/calendar_utils.py (docs/SETUP_SPEC.md §3/§6).

Not previously covered by any test -- neither most_recent_us_trading_day()
nor advance_us_trading_days() had tests before this file.
"""
import unittest
from datetime import date

from setups.calendar_utils import advance_us_trading_days, most_recent_us_trading_day


class TestMostRecentUsTradingDay(unittest.TestCase):

    def test_already_a_trading_day_returns_itself(self):
        # 2026-06-15 is a Monday, not a holiday.
        self.assertEqual(most_recent_us_trading_day(date(2026, 6, 15)), date(2026, 6, 15))

    def test_weekend_steps_back_to_friday(self):
        # 2026-06-14 is a Sunday.
        self.assertEqual(most_recent_us_trading_day(date(2026, 6, 14)), date(2026, 6, 12))

    def test_holiday_steps_back_past_thanksgiving(self):
        # 2026-11-26 is Thanksgiving (Thursday); the prior trading day is
        # 2026-11-25 (Wednesday).
        self.assertEqual(most_recent_us_trading_day(date(2026, 11, 26)), date(2026, 11, 25))


class TestAdvanceUsTradingDays(unittest.TestCase):

    def test_advance_within_the_same_week(self):
        # 2026-06-15 (Monday) + 2 trading days -> Wednesday 2026-06-17.
        self.assertEqual(advance_us_trading_days(date(2026, 6, 15), 2), date(2026, 6, 17))

    def test_advance_across_a_weekend(self):
        # 2026-06-12 (Friday) + 1 trading day must skip Sat/Sun and land
        # on Monday 2026-06-15, not Saturday 2026-06-13.
        self.assertEqual(advance_us_trading_days(date(2026, 6, 12), 1), date(2026, 6, 15))

    def test_advance_across_a_market_holiday(self):
        # 2026-11-25 (Wednesday, the day before Thanksgiving) + 1 trading
        # day must skip Thanksgiving (2026-11-26, a Thursday) and land on
        # Friday 2026-11-27, not on the holiday itself.
        self.assertEqual(advance_us_trading_days(date(2026, 11, 25), 1), date(2026, 11, 27))

    def test_advance_across_both_a_holiday_and_a_weekend(self):
        # 2026-11-25 (Wednesday) + 2 trading days: day 1 skips Thanksgiving
        # (Thu 11-26) to land on Fri 11-27; day 2 then skips the weekend
        # (Sat 11-28 / Sun 11-29) to land on Mon 11-30.
        self.assertEqual(advance_us_trading_days(date(2026, 11, 25), 2), date(2026, 11, 30))

    def test_start_date_itself_is_not_counted(self):
        self.assertEqual(advance_us_trading_days(date(2026, 6, 15), 0), date(2026, 6, 15))


if __name__ == "__main__":
    unittest.main()
