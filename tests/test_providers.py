"""Tests for data/providers/ (Phase 5 — provider abstraction).

No real network calls: StooqProvider is tested with a fake HTTP session
injected via its `session` constructor arg; YFinanceProvider is tested with
data.fetcher.get_historical mocked.
"""
import unittest
from unittest.mock import patch

import pandas as pd

from data.providers.stooq_provider import StooqProvider, _map_symbol
from data.providers.yfinance_provider import YFinanceProvider
from tests.fixtures import make_trending_df


class _FakeResponse:
    def __init__(self, text: str, ok: bool = True):
        self.text = text
        self._ok = ok

    def raise_for_status(self):
        if not self._ok:
            raise RuntimeError("simulated HTTP error")


class _FakeSession:
    def __init__(self, text: str | None = None, exc: Exception | None = None, ok: bool = True):
        self._text = text
        self._exc = exc
        self._ok = ok
        self.calls: list[tuple[str, dict, float]] = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params, timeout))
        if self._exc is not None:
            raise self._exc
        return _FakeResponse(self._text or "", ok=self._ok)


_VALID_CSV = (
    "Date,Open,High,Low,Close,Volume\n"
    "2026-06-01,100.0,101.0,99.0,100.5,1000000\n"
    "2026-06-02,100.5,102.0,100.0,101.5,1100000\n"
    "2026-06-03,101.5,103.0,101.0,102.5,1200000\n"
    "2026-06-04,102.5,104.0,102.0,103.5,1300000\n"
    "2026-06-05,103.5,105.0,103.0,104.5,1400000\n"
)


class TestStooqSymbolMapping(unittest.TestCase):

    def test_plain_equity_gets_us_suffix(self):
        self.assertEqual(_map_symbol("AAPL"), "aapl.us")

    def test_index_symbol_is_lowercased_with_caret_kept(self):
        self.assertEqual(_map_symbol("^GSPC"), "^gspc")

    def test_crypto_dash_usd_mapped_to_stooq_style(self):
        self.assertEqual(_map_symbol("BTC-USD"), "btcusd")

    def test_symbol_with_dot_is_passed_through_lowercased(self):
        self.assertEqual(_map_symbol("BRK.B"), "brk.b")


class TestStooqProvider(unittest.TestCase):

    def test_valid_csv_returns_dataframe_with_lowercase_columns(self):
        session = _FakeSession(text=_VALID_CSV)
        provider = StooqProvider(session=session)
        df = provider.fetch_history("AAPL", period="1y", interval="1d")
        self.assertIsNotNone(df)
        self.assertEqual(set(df.columns), {"open", "high", "low", "close", "volume"})
        self.assertEqual(len(df), 5)

    def test_request_uses_mapped_symbol_and_daily_interval_param(self):
        session = _FakeSession(text=_VALID_CSV)
        provider = StooqProvider(session=session)
        provider.fetch_history("AAPL", period="1y", interval="1d")
        self.assertEqual(len(session.calls), 1)
        _, params, _ = session.calls[0]
        self.assertEqual(params["s"], "aapl.us")
        self.assertEqual(params["i"], "d")

    def test_weekly_interval_maps_to_w_param(self):
        session = _FakeSession(text=_VALID_CSV)
        provider = StooqProvider(session=session)
        provider.fetch_history("AAPL", period="1y", interval="1wk")
        _, params, _ = session.calls[0]
        self.assertEqual(params["i"], "w")

    def test_unsupported_interval_fails_cleanly_without_network_call(self):
        session = _FakeSession(text=_VALID_CSV)
        provider = StooqProvider(session=session)
        df = provider.fetch_history("AAPL", period="1y", interval="1h")
        self.assertIsNone(df)
        self.assertEqual(session.calls, [], "unsupported interval must not trigger a network call")

    def test_network_exception_returns_none(self):
        session = _FakeSession(exc=ConnectionError("simulated network failure"))
        provider = StooqProvider(session=session)
        df = provider.fetch_history("AAPL", period="1y", interval="1d")
        self.assertIsNone(df)

    def test_http_error_returns_none(self):
        session = _FakeSession(text=_VALID_CSV, ok=False)
        provider = StooqProvider(session=session)
        df = provider.fetch_history("AAPL", period="1y", interval="1d")
        self.assertIsNone(df)

    def test_empty_response_returns_none(self):
        session = _FakeSession(text="")
        provider = StooqProvider(session=session)
        df = provider.fetch_history("UNRESOLVABLE", period="1y", interval="1d")
        self.assertIsNone(df)

    def test_header_only_response_returns_none(self):
        """Stooq's real response for an unresolvable symbol is just the CSV
        header with no data rows — must be treated as a clean failure."""
        session = _FakeSession(text="Date,Open,High,Low,Close,Volume\n")
        provider = StooqProvider(session=session)
        df = provider.fetch_history("UNRESOLVABLE", period="1y", interval="1d")
        self.assertIsNone(df)

    def test_missing_required_column_returns_none(self):
        csv_no_volume = "Date,Open,High,Low,Close\n2026-06-01,100.0,101.0,99.0,100.5\n"
        session = _FakeSession(text=csv_no_volume)
        provider = StooqProvider(session=session)
        df = provider.fetch_history("AAPL", period="1y", interval="1d")
        self.assertIsNone(df)

    def test_unparseable_response_returns_none(self):
        session = _FakeSession(text="this is not valid csv data at all {{{")
        provider = StooqProvider(session=session)
        df = provider.fetch_history("AAPL", period="1y", interval="1d")
        # Either parses into a single garbage column (missing required OHLCV
        # columns -> None) or fails to parse outright (-> None) — either way
        # this must never raise or return unusable data.
        self.assertIsNone(df)


class TestYFinanceProvider(unittest.TestCase):

    def test_delegates_to_get_historical(self):
        df = make_trending_df(n=260)
        with patch("data.providers.yfinance_provider.get_historical", return_value=df) as mock_get:
            provider = YFinanceProvider()
            result = provider.fetch_history("NVDA", period="1y", interval="1d")
        mock_get.assert_called_once_with("NVDA", period="1y", interval="1d")
        self.assertIs(result, df)

    def test_returns_none_when_get_historical_returns_none(self):
        with patch("data.providers.yfinance_provider.get_historical", return_value=None):
            provider = YFinanceProvider()
            result = provider.fetch_history("BADSYM", period="1y", interval="1d")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
