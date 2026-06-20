"""
Tests for the Phase 3 market-data validation layer
(data/market_data_validator.py).

All yfinance access is mocked — no live network calls, no production
database usage (this module never touches db/database.py at all).
"""
import inspect
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import numpy as np
import pandas as pd

from data.market_data_validator import (
    MarketDataClient,
    ProviderStatus,
    summarize_history,
)


def _df(n: int, *, end: str, volume=None, drop_cols=None, last_close_nan=False,
        last_volume_nan=False, zero_volume=False) -> pd.DataFrame:
    """Build a small deterministic daily OHLCV DataFrame ending on `end`."""
    dates = pd.bdate_range(end=end, periods=n)
    close = np.linspace(100.0, 100.0 + n, n)
    high = close + 1.0
    low = close - 1.0
    open_ = close - 0.5
    if volume is None:
        volume = np.full(n, 1_000_000.0)
    df = pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=pd.DatetimeIndex(dates, name="Date"),
    )
    if zero_volume:
        df["volume"] = 0.0
    if last_close_nan:
        df.iloc[-1, df.columns.get_loc("close")] = np.nan
    if last_volume_nan:
        df.iloc[-1, df.columns.get_loc("volume")] = np.nan
    if drop_cols:
        df = df.drop(columns=drop_cols)
    return df


# A Friday after close — convenient "now" for most non-stale tests.
_FRIDAY = "2024-03-15"
_NOW_FRIDAY_CLOSED = datetime(2024, 3, 15, 23, 0, tzinfo=timezone.utc)  # ~7pm ET, market closed


class TestSummarizeHistoryBasics(unittest.TestCase):

    def test_valid_stock_sufficient_history(self):
        df = _df(40, end=_FRIDAY)
        result = summarize_history("AAPL", df, security_type="stock",
                                    market_open=False, now=_NOW_FRIDAY_CLOSED)
        self.assertEqual(result.provider_status, ProviderStatus.OK)
        self.assertTrue(result.is_valid)
        self.assertTrue(result.has_sufficient_history)
        self.assertFalse(result.is_stale)
        self.assertIsNone(result.failure_type)

    def test_valid_etf(self):
        df = _df(40, end=_FRIDAY)
        result = summarize_history("SPY", df, security_type="etf",
                                    market_open=False, now=_NOW_FRIDAY_CLOSED)
        self.assertEqual(result.provider_status, ProviderStatus.OK)
        self.assertEqual(result.security_type, "etf")

    def test_valid_index(self):
        df = _df(40, end=_FRIDAY)
        result = summarize_history("^GSPC", df, security_type="index",
                                    market_open=False, now=_NOW_FRIDAY_CLOSED)
        self.assertEqual(result.provider_status, ProviderStatus.OK)
        self.assertEqual(result.security_type, "index")

    def test_empty_history(self):
        result = summarize_history("ZZZZ", pd.DataFrame(), market_open=False, now=_NOW_FRIDAY_CLOSED)
        self.assertEqual(result.provider_status, ProviderStatus.EMPTY_HISTORY)
        self.assertFalse(result.is_valid)

    def test_none_history(self):
        result = summarize_history("ZZZZ", None, market_open=False, now=_NOW_FRIDAY_CLOSED)
        self.assertEqual(result.provider_status, ProviderStatus.EMPTY_HISTORY)

    def test_missing_close_column(self):
        df = _df(40, end=_FRIDAY, drop_cols=["close"])
        result = summarize_history("AAPL", df, market_open=False, now=_NOW_FRIDAY_CLOSED)
        self.assertEqual(result.provider_status, ProviderStatus.MISSING_OHLCV)
        self.assertFalse(result.has_required_ohlcv)

    def test_missing_volume_column(self):
        df = _df(40, end=_FRIDAY, drop_cols=["volume"])
        result = summarize_history("AAPL", df, market_open=False, now=_NOW_FRIDAY_CLOSED)
        self.assertEqual(result.provider_status, ProviderStatus.MISSING_VOLUME)
        self.assertFalse(result.has_required_ohlcv)

    def test_nan_close_falls_back_with_warning(self):
        df = _df(40, end=_FRIDAY, last_close_nan=True)
        result = summarize_history("AAPL", df, market_open=False, now=_NOW_FRIDAY_CLOSED)
        self.assertIsNotNone(result.latest_close)
        self.assertTrue(any("close" in w.lower() for w in result.warnings))

    def test_nan_volume_recorded_as_warning(self):
        df = _df(40, end=_FRIDAY, last_volume_nan=True)
        result = summarize_history("AAPL", df, market_open=False, now=_NOW_FRIDAY_CLOSED)
        self.assertIsNone(result.latest_volume)
        self.assertTrue(any("volume" in w.lower() for w in result.warnings))
        # Average is still computable from the other valid rows.
        self.assertIsNotNone(result.average_daily_volume)

    def test_zero_volume(self):
        df = _df(40, end=_FRIDAY, zero_volume=True)
        result = summarize_history("AAPL", df, market_open=False, now=_NOW_FRIDAY_CLOSED)
        self.assertEqual(result.provider_status, ProviderStatus.ZERO_VOLUME)
        self.assertEqual(result.average_daily_volume, 0.0)

    def test_insufficient_history(self):
        df = _df(10, end=_FRIDAY)
        result = summarize_history("AAPL", df, market_open=False, now=_NOW_FRIDAY_CLOSED,
                                    min_history_days=30)
        self.assertEqual(result.provider_status, ProviderStatus.INSUFFICIENT_HISTORY)
        self.assertFalse(result.has_sufficient_history)

    def test_non_numeric_volume_values_are_dropped_not_crashing(self):
        df = _df(40, end=_FRIDAY)
        df["volume"] = df["volume"].astype(object)
        df.iloc[-1, df.columns.get_loc("volume")] = "N/A"
        result = summarize_history("AAPL", df, market_open=False, now=_NOW_FRIDAY_CLOSED)
        self.assertIn(result.provider_status, (ProviderStatus.OK, ProviderStatus.ZERO_VOLUME))
        self.assertIsNotNone(result.average_daily_volume)


class TestCompletedCandleHandling(unittest.TestCase):

    def test_incomplete_daily_candle_single_row_market_open(self):
        df = _df(1, end="2024-03-18")  # a Monday
        result = summarize_history(
            "AAPL", df, market_open=True,
            now=datetime(2024, 3, 18, 15, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(result.provider_status, ProviderStatus.INCOMPLETE_DAILY_CANDLE)
        self.assertFalse(result.is_complete_daily_candle)

    def test_completed_daily_candle_drops_forming_row_when_market_open(self):
        df = _df(40, end="2024-03-18")  # Monday, last row would be "today"
        result = summarize_history(
            "AAPL", df, market_open=True,
            now=datetime(2024, 3, 18, 15, 0, tzinfo=timezone.utc),
        )
        self.assertTrue(result.is_complete_daily_candle)
        self.assertEqual(result.history_days_available, 39)  # last row dropped
        # The latest *completed* candle is the previous business day (Friday).
        self.assertEqual(result.latest_completed_candle_date, "2024-03-15")

    def test_completed_daily_candle_keeps_last_row_when_market_closed(self):
        df = _df(40, end=_FRIDAY)
        result = summarize_history("AAPL", df, market_open=False, now=_NOW_FRIDAY_CLOSED)
        self.assertTrue(result.is_complete_daily_candle)
        self.assertEqual(result.history_days_available, 40)
        self.assertEqual(result.latest_completed_candle_date, _FRIDAY)


class TestFreshness(unittest.TestCase):

    def test_weekend_freshness_friday_candle_is_fresh(self):
        df = _df(40, end=_FRIDAY)
        # "Now" is Saturday — market closed, weekend.
        now_saturday = datetime(2024, 3, 16, 12, 0, tzinfo=timezone.utc)
        result = summarize_history("AAPL", df, market_open=False, now=now_saturday)
        self.assertFalse(result.is_stale)
        self.assertEqual(result.provider_status, ProviderStatus.OK)

    def test_stale_data_flagged_when_gap_exceeds_threshold(self):
        df = _df(40, end="2024-02-01")  # over a month before "now"
        result = summarize_history("AAPL", df, market_open=False, now=_NOW_FRIDAY_CLOSED)
        self.assertTrue(result.is_stale)
        self.assertEqual(result.provider_status, ProviderStatus.STALE_DATA)

    def test_holiday_like_one_day_gap_is_tolerated(self):
        """
        A single missing trading day (e.g. a market holiday we don't have a
        calendar for) must not be flagged stale given the default
        stale_days threshold — documented limitation, not a bug.
        """
        df = _df(40, end="2024-03-14")  # Thursday
        # "Now" is Friday after close; expected trading day is Friday,
        # latest completed candle is Thursday -> 1-day gap.
        result = summarize_history("AAPL", df, market_open=False, now=_NOW_FRIDAY_CLOSED)
        self.assertFalse(result.is_stale)
        self.assertEqual(result.provider_status, ProviderStatus.OK)


class TestLiquidityCalculations(unittest.TestCase):

    def test_average_daily_volume_matches_hand_computed_mean(self):
        volumes = np.array([1_000_000.0, 2_000_000.0, 3_000_000.0, 4_000_000.0])
        df = _df(4, end=_FRIDAY, volume=volumes)
        result = summarize_history("AAPL", df, market_open=False, now=_NOW_FRIDAY_CLOSED,
                                    lookback_days=4, min_history_days=4)
        self.assertEqual(result.average_daily_volume, float(volumes.mean()))

    def test_average_dollar_volume_matches_hand_computed_mean(self):
        volumes = np.array([1_000_000.0, 2_000_000.0])
        df = _df(2, end=_FRIDAY, volume=volumes)
        result = summarize_history("AAPL", df, market_open=False, now=_NOW_FRIDAY_CLOSED,
                                    lookback_days=2, min_history_days=2)
        expected = float((df["close"].to_numpy() * volumes).mean())
        self.assertAlmostEqual(result.average_daily_dollar_volume, round(expected, 2), places=2)

    def test_identical_input_produces_identical_score_inputs(self):
        df1 = _df(40, end=_FRIDAY)
        df2 = _df(40, end=_FRIDAY)
        r1 = summarize_history("AAPL", df1, market_open=False, now=_NOW_FRIDAY_CLOSED)
        r2 = summarize_history("AAPL", df2, market_open=False, now=_NOW_FRIDAY_CLOSED)
        self.assertEqual(r1.average_daily_volume, r2.average_daily_volume)
        self.assertEqual(r1.average_daily_dollar_volume, r2.average_daily_dollar_volume)
        self.assertEqual(r1.provider_status, r2.provider_status)


class TestProviderErrorClassificationAndRetry(unittest.TestCase):

    def test_invalid_symbol_is_not_retried(self):
        client = MarketDataClient(max_retries=2, sleep_fn=lambda s: None)
        with patch("data.market_data_validator.yf.download",
                   side_effect=Exception("No data found, symbol may be delisted")):
            df, status = client.get_history("ZZZZINVALID")
        self.assertIsNone(df)
        self.assertEqual(status, ProviderStatus.INVALID_SYMBOL)
        self.assertEqual(client.yfinance_request_count, 1)  # no retries

    def test_temporary_failure_is_retried_then_fails(self):
        client = MarketDataClient(max_retries=2, sleep_fn=lambda s: None)
        with patch("data.market_data_validator.yf.download",
                   side_effect=Exception("Connection timed out")):
            df, status = client.get_history("AAPL")
        self.assertIsNone(df)
        self.assertEqual(status, ProviderStatus.TEMPORARY_FAILURE)
        self.assertEqual(client.yfinance_request_count, 3)  # 1 + 2 retries
        self.assertEqual(client.provider_error_count, 3)

    def test_rate_limited_is_classified_distinctly(self):
        client = MarketDataClient(max_retries=1, sleep_fn=lambda s: None)
        with patch("data.market_data_validator.yf.download",
                   side_effect=Exception("429 Too Many Requests")):
            df, status = client.get_history("AAPL")
        self.assertEqual(status, ProviderStatus.RATE_LIMITED)

    def test_retry_eventually_succeeds(self):
        client = MarketDataClient(max_retries=2, sleep_fn=lambda s: None)
        good_df = _df(40, end=_FRIDAY)
        calls = {"n": 0}

        def flaky(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] < 2:
                raise Exception("Connection timed out")
            return good_df.copy()

        with patch("data.market_data_validator.yf.download", side_effect=flaky):
            df, status = client.get_history("AAPL")
        self.assertIsNone(status)
        self.assertIsNotNone(df)
        self.assertEqual(calls["n"], 2)


class TestCaching(unittest.TestCase):

    def test_cache_miss_then_hit(self):
        client = MarketDataClient(sleep_fn=lambda s: None)
        good_df = _df(40, end=_FRIDAY)
        with patch("data.market_data_validator.yf.download", return_value=good_df.copy()) as mock_dl:
            client.get_history("AAPL")
            client.get_history("AAPL")
        self.assertEqual(mock_dl.call_count, 1)
        self.assertEqual(client.cache_misses, 1)
        self.assertEqual(client.cache_hits, 1)

    def test_different_symbols_both_miss(self):
        client = MarketDataClient(sleep_fn=lambda s: None)
        good_df = _df(40, end=_FRIDAY)
        with patch("data.market_data_validator.yf.download", return_value=good_df.copy()):
            client.get_history("AAPL")
            client.get_history("MSFT")
        self.assertEqual(client.cache_misses, 2)
        self.assertEqual(client.cache_hits, 0)


class TestBatchRetrieval(unittest.TestCase):

    def _make_multiindex_batch(self, symbols, end=_FRIDAY, n=40):
        frames = {}
        for sym in symbols:
            df = _df(n, end=end)
            df.columns = [c.capitalize() for c in df.columns]
            frames[sym] = df
        return pd.concat(frames, axis=1)

    def test_batch_with_one_invalid_symbol_does_not_fail_others(self):
        client = MarketDataClient(sleep_fn=lambda s: None, batch_size=10)
        good_symbols = ["AAPL", "MSFT"]
        batch_df = self._make_multiindex_batch(good_symbols)

        def fake_download(target, **kwargs):
            if isinstance(target, list):
                return batch_df  # 'BAD' silently absent, simulating a parse gap
            raise Exception("No data found, symbol may be delisted")

        with patch("data.market_data_validator.yf.download", side_effect=fake_download):
            results = client.get_history_batch(["AAPL", "BAD", "MSFT"])

        self.assertEqual(list(results.keys()), ["AAPL", "BAD", "MSFT"])  # order preserved
        self.assertIsNotNone(results["AAPL"][0])
        self.assertIsNotNone(results["MSFT"][0])
        self.assertIsNone(results["BAD"][0])
        self.assertEqual(results["BAD"][1], ProviderStatus.INVALID_SYMBOL)

    def test_batch_validate_returns_one_result_per_symbol(self):
        client = MarketDataClient(sleep_fn=lambda s: None, batch_size=10)
        batch_df = self._make_multiindex_batch(["AAPL", "MSFT"])
        with patch("data.market_data_validator.yf.download", return_value=batch_df):
            results = client.validate_batch(["AAPL", "MSFT"])
        self.assertEqual(set(results.keys()), {"AAPL", "MSFT"})
        for r in results.values():
            self.assertIn(r.provider_status, (ProviderStatus.OK, ProviderStatus.STALE_DATA))

    def test_chunk_level_failure_degrades_to_per_symbol(self):
        client = MarketDataClient(sleep_fn=lambda s: None, batch_size=10)
        good_df = _df(40, end=_FRIDAY)
        call_log = []

        def fake_download(target, **kwargs):
            call_log.append(target)
            if isinstance(target, list):
                raise Exception("Connection timed out")
            return good_df.copy()

        with patch("data.market_data_validator.yf.download", side_effect=fake_download):
            results = client.get_history_batch(["AAPL", "MSFT"])

        self.assertIsNotNone(results["AAPL"][0])
        self.assertIsNotNone(results["MSFT"][0])


class TestResultSafetyAndIsolation(unittest.TestCase):

    def test_result_object_has_no_secret_like_fields(self):
        df = _df(40, end=_FRIDAY)
        result = summarize_history("AAPL", df, market_open=False, now=_NOW_FRIDAY_CLOSED)
        d = result.to_dict()
        blob = repr(d).lower()
        for secret_kw in ("api_key", "token", "password", "telegram_token", "anthropic_api_key"):
            self.assertNotIn(secret_kw, blob)
        self.assertEqual(d["raw_metadata"], {})

    def test_module_does_not_import_database(self):
        import data.market_data_validator as mod
        source = inspect.getsource(mod)
        self.assertNotIn("db.database", source)
        self.assertNotIn("import db", source)

    def test_module_does_not_import_telegram(self):
        import data.market_data_validator as mod
        source = inspect.getsource(mod)
        self.assertNotIn("import telegram", source.lower())
        self.assertNotIn("from telegram", source.lower())
        self.assertNotIn("bot.telegram_bot", source.lower())


if __name__ == "__main__":
    unittest.main()
