"""Tests for data/market_data_service.py (Phase 5 — provider fallback
orchestration). No real providers/network calls — uses fake providers that
record their calls and return pre-set results."""
import unittest

import pandas as pd

from data.market_data_service import MarketDataService
from tests.fixtures import make_trending_df


class _FakeProvider:
    def __init__(self, name: str, *, df=None, exc: Exception | None = None):
        self.name = name
        self._df = df
        self._exc = exc
        self.calls: list[tuple[str, str, str]] = []

    def fetch_history(self, symbol: str, period: str, interval: str):
        self.calls.append((symbol, period, interval))
        if self._exc is not None:
            raise self._exc
        return self._df


class TestMarketDataServiceFallback(unittest.TestCase):

    def test_primary_success_short_circuits_fallback(self):
        good_df = make_trending_df(n=260)
        primary = _FakeProvider("stooq", df=good_df)
        fallback = _FakeProvider("yfinance", df=good_df)
        service = MarketDataService([primary, fallback])

        result = service.fetch_history("NVDA")

        self.assertTrue(result.ok)
        self.assertEqual(result.provider_name, "stooq")
        self.assertIs(result.df, good_df)
        self.assertEqual(fallback.calls, [], "fallback must not be called when primary succeeds")

    def test_primary_exception_falls_back_to_secondary(self):
        good_df = make_trending_df(n=260)
        primary = _FakeProvider("stooq", exc=ConnectionError("simulated"))
        fallback = _FakeProvider("yfinance", df=good_df)
        service = MarketDataService([primary, fallback])

        result = service.fetch_history("NVDA")

        self.assertTrue(result.ok)
        self.assertEqual(result.provider_name, "yfinance")
        self.assertIn("stooq", result.failures)
        self.assertIn("exception", result.failures["stooq"])

    def test_primary_none_falls_back_to_secondary(self):
        good_df = make_trending_df(n=260)
        primary = _FakeProvider("stooq", df=None)
        fallback = _FakeProvider("yfinance", df=good_df)
        service = MarketDataService([primary, fallback])

        result = service.fetch_history("NVDA")

        self.assertEqual(result.provider_name, "yfinance")
        self.assertEqual(result.failures["stooq"], "empty_or_none")

    def test_primary_empty_dataframe_falls_back(self):
        good_df = make_trending_df(n=260)
        primary = _FakeProvider("stooq", df=pd.DataFrame())
        fallback = _FakeProvider("yfinance", df=good_df)
        service = MarketDataService([primary, fallback])

        result = service.fetch_history("NVDA")

        self.assertEqual(result.provider_name, "yfinance")
        self.assertEqual(result.failures["stooq"], "empty_or_none")

    def test_primary_missing_ohlcv_columns_falls_back(self):
        good_df = make_trending_df(n=260)
        broken_df = good_df.drop(columns=["volume"])
        primary = _FakeProvider("stooq", df=broken_df)
        fallback = _FakeProvider("yfinance", df=good_df)
        service = MarketDataService([primary, fallback])

        result = service.fetch_history("NVDA")

        self.assertEqual(result.provider_name, "yfinance")
        self.assertEqual(result.failures["stooq"], "missing_ohlcv_columns")

    def test_primary_insufficient_candles_falls_back(self):
        good_df = make_trending_df(n=260)
        tiny_df = good_df.head(2)  # below DEFAULT_MIN_CANDLES=5
        primary = _FakeProvider("stooq", df=tiny_df)
        fallback = _FakeProvider("yfinance", df=good_df)
        service = MarketDataService([primary, fallback])

        result = service.fetch_history("NVDA")

        self.assertEqual(result.provider_name, "yfinance")
        self.assertEqual(result.failures["stooq"], "insufficient_candles")

    def test_min_candles_is_configurable(self):
        good_df = make_trending_df(n=260)
        tiny_df = good_df.head(2)
        primary = _FakeProvider("stooq", df=tiny_df)
        fallback = _FakeProvider("yfinance", df=good_df)
        service = MarketDataService([primary, fallback], min_candles=1)

        result = service.fetch_history("NVDA")

        self.assertEqual(result.provider_name, "stooq", "2 rows should pass with min_candles=1")

    def test_all_providers_failing_returns_none_result_with_reasons(self):
        primary = _FakeProvider("stooq", df=None)
        fallback = _FakeProvider("yfinance", exc=TimeoutError("simulated timeout"))
        service = MarketDataService([primary, fallback])

        result = service.fetch_history("NVDA")

        self.assertFalse(result.ok)
        self.assertIsNone(result.provider_name)
        self.assertEqual(result.attempted, ["stooq", "yfinance"])
        self.assertEqual(result.failures["stooq"], "empty_or_none")
        self.assertIn("exception", result.failures["yfinance"])

    def test_non_daily_interval_is_passed_through_to_providers(self):
        good_df = make_trending_df(n=260)
        primary = _FakeProvider("stooq", df=good_df)
        service = MarketDataService([primary])

        service.fetch_history("NVDA", period="2y", interval="1wk")

        self.assertEqual(primary.calls, [("NVDA", "2y", "1wk")])

    def test_default_providers_are_stooq_then_yfinance(self):
        service = MarketDataService()
        names = [p.name for p in service.providers]
        self.assertEqual(names, ["stooq", "yfinance"])


if __name__ == "__main__":
    unittest.main()
