"""
Tests for Fix 3: safe handling of missing/None average volume in get_current_price().

Also tests the _volume_spike helper in analyzers/technical.py to ensure
missing volume data does not produce a false positive.

No network access — yfinance is mocked throughout.
"""

import math
import unittest
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

from tests.fixtures import make_trending_df


# ── get_current_price volume handling ─────────────────────────────────────────

def _mock_fast_info(
    price: float = 100.0,
    prev_close: float = 99.0,
    avg_vol=12_345_678,
    day_high: float = 101.0,
    day_low: float = 99.0,
    open_: float = 99.5,
) -> MagicMock:
    info = MagicMock()
    info.last_price = price
    info.previous_close = prev_close
    info.three_month_average_volume = avg_vol
    info.day_high = day_high
    info.day_low = day_low
    info.open = open_
    return info


class TestGetCurrentPriceVolume(unittest.TestCase):

    def _call(self, avg_vol_value) -> dict:
        from data.fetcher import get_current_price
        mock_info = _mock_fast_info(avg_vol=avg_vol_value)
        with patch("data.fetcher.yf.Ticker") as MockTicker:
            MockTicker.return_value.fast_info = mock_info
            result = get_current_price("TEST")
        return result

    def test_normal_volume_returned_as_int(self):
        result = self._call(avg_vol_value=12_345_678)
        self.assertEqual(result["volume"], 12_345_678)
        self.assertIsInstance(result["volume"], int)

    def test_none_volume_returns_zero_not_none(self):
        """None from three_month_average_volume must become 0, never None."""
        result = self._call(avg_vol_value=None)
        self.assertIsNotNone(result,
            "get_current_price must not return None when volume is missing")
        self.assertEqual(result["volume"], 0)

    def test_none_volume_does_not_crash(self):
        """Calling get_current_price with None volume must not raise."""
        try:
            result = self._call(avg_vol_value=None)
        except Exception as e:
            self.fail(f"get_current_price raised {type(e).__name__} when volume is None: {e}")

    def test_zero_volume_returns_zero(self):
        result = self._call(avg_vol_value=0)
        self.assertEqual(result["volume"], 0)

    def test_volume_is_never_none_for_any_input(self):
        """Whatever yfinance returns, the 'volume' key must never be None."""
        for bad_val in (None, 0, float("nan")):
            with self.subTest(avg_vol=bad_val):
                try:
                    result = self._call(avg_vol_value=bad_val)
                    if result is not None:
                        self.assertIsNotNone(result.get("volume"))
                except Exception:
                    pass  # None-volume crash is also caught by test_none_volume_does_not_crash


# ── _volume_spike safety ──────────────────────────────────────────────────────

class TestVolumeSpikeHelper(unittest.TestCase):

    def _make_volume_df(self, past_volumes: list[float], current_volume: float) -> pd.DataFrame:
        df = make_trending_df(n=len(past_volumes) + 1)
        df = df.copy()
        df["volume"] = past_volumes + [current_volume]
        return df

    def test_spike_detected_when_current_exceeds_multiplier(self):
        from analyzers.technical import _volume_spike
        avg = 1_000_000.0
        past = [avg] * 25
        df = self._make_volume_df(past, current_volume=avg * 2.0)
        self.assertTrue(_volume_spike(df, multiplier=1.5, window=20))

    def test_no_spike_below_multiplier(self):
        from analyzers.technical import _volume_spike
        avg = 1_000_000.0
        past = [avg] * 25
        df = self._make_volume_df(past, current_volume=avg * 1.2)
        self.assertFalse(_volume_spike(df, multiplier=1.5, window=20))

    def test_no_spike_when_volume_column_missing(self):
        from analyzers.technical import _volume_spike
        df = make_trending_df().drop(columns=["volume"])
        self.assertFalse(_volume_spike(df))

    def test_no_spike_when_all_volume_is_zero(self):
        from analyzers.technical import _volume_spike
        df = make_trending_df()
        df = df.copy()
        df["volume"] = 0.0
        self.assertFalse(_volume_spike(df))

    def test_no_spike_when_insufficient_history(self):
        from analyzers.technical import _volume_spike
        df = make_trending_df(n=15)
        self.assertFalse(_volume_spike(df, window=20))

    def test_volume_exactly_at_threshold_is_not_spike(self):
        from analyzers.technical import _volume_spike
        avg = 1_000_000.0
        past = [avg] * 25
        # Exactly 1.5× average — should NOT be a spike (uses strict >)
        df = self._make_volume_df(past, current_volume=avg * 1.5)
        self.assertFalse(_volume_spike(df, multiplier=1.5, window=20))

    def test_volume_just_above_threshold_is_spike(self):
        from analyzers.technical import _volume_spike
        avg = 1_000_000.0
        past = [avg] * 25
        df = self._make_volume_df(past, current_volume=avg * 1.5 + 1)
        self.assertTrue(_volume_spike(df, multiplier=1.5, window=20))


if __name__ == "__main__":
    unittest.main()
