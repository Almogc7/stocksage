"""Tests for setups/sizing.py (docs/SETUP_SPEC.md §10)."""
import math
import unittest

from setups.sizing import position_size


class TestPositionSize(unittest.TestCase):

    def test_round_numbers(self):
        self.assertEqual(position_size(1000.0, 100.0, 92.0), math.floor(1000.0 / 8.0))

    def test_floors_not_rounds(self):
        # risk/distance = 1000/7 = 142.857... -> floor 142
        self.assertEqual(position_size(1000.0, 100.0, 93.0), 142)

    def test_risk_usd_smaller_than_one_shares_risk_returns_zero_not_none(self):
        """A valid, positive risk budget that just can't afford even one
        share is 0 shares -- a real, distinct answer from "invalid
        input" (None). docs/SETUP_SPEC.md §10: the caller's message must
        handle this 0-shares case explicitly."""
        self.assertEqual(position_size(5.0, 100.0, 92.0), 0)
        self.assertIsNotNone(position_size(5.0, 100.0, 92.0))

    def test_none_for_non_positive_risk(self):
        self.assertIsNone(position_size(0.0, 100.0, 92.0))
        self.assertIsNone(position_size(-10.0, 100.0, 92.0))

    def test_none_for_entry_at_or_below_stop(self):
        self.assertIsNone(position_size(1000.0, 100.0, 100.0))
        self.assertIsNone(position_size(1000.0, 100.0, 105.0))

    def test_none_for_non_numeric_input(self):
        self.assertIsNone(position_size("not a number", 100.0, 92.0))
        self.assertIsNone(position_size(1000.0, None, 92.0))

    def test_none_for_nan_and_infinite_input(self):
        self.assertIsNone(position_size(float("nan"), 100.0, 92.0))
        self.assertIsNone(position_size(1000.0, float("inf"), 92.0))
        self.assertIsNone(position_size(1000.0, 100.0, float("-inf")))


if __name__ == "__main__":
    unittest.main()
