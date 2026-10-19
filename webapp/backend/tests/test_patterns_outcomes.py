import unittest

import pandas as pd

from app.patterns.outcomes import compute_forward_outcome, excess_return


def _bars(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows).set_index("date")
    return df


def _row(date, o, h, l, c, volume=1_000_000):
    return {"date": date, "open": o, "high": h, "low": l, "close": c, "volume": volume}


class EntryTimingTests(unittest.TestCase):
    def test_entry_is_the_next_bars_open_not_the_confirming_bars_close_or_own_open(self):
        bars = _bars(
            [
                _row("2024-01-01", 100, 101, 99, 100.5),  # confirming bar
                _row("2024-01-02", 103, 104, 102, 103.5),  # entry bar: open=103
                _row("2024-01-03", 104, 105, 103, 104.5),
            ]
        )
        out = compute_forward_outcome(bars, confirmed_on="2024-01-01", direction="long", stop_price=90)
        self.assertEqual(out["entry_date"], "2024-01-02")
        self.assertEqual(out["entry_price"], 103)  # the next bar's OPEN

    def test_horizons_count_from_the_entry_bar_as_bar_zero(self):
        # entry bar (2024-01-02) is bar 0; T+1 closes bar 0 itself.
        rows = [_row("2024-01-01", 100, 101, 99, 100)]
        price = 100
        for i in range(2, 32):
            price += 1
            rows.append(_row(f"2024-01-{i:02d}", price, price + 1, price - 1, price))
        bars = _bars(rows)
        out = compute_forward_outcome(bars, confirmed_on="2024-01-01", direction="long", stop_price=1, horizons=(1, 5, 10))
        entry_close = bars["close"].loc[out["entry_date"]]
        self.assertEqual(out["closes"][1], entry_close)
        # T+5 = close of the 5th bar counting the entry bar as the 1st.
        dates = bars.index.to_list()
        entry_idx = dates.index(out["entry_date"])
        self.assertEqual(out["closes"][5], bars["close"].iloc[entry_idx + 4])
        self.assertEqual(out["closes"][10], bars["close"].iloc[entry_idx + 9])

    def test_a_stop_breach_on_the_entry_bar_itself_is_caught(self):
        bars = _bars(
            [
                _row("2024-01-01", 100, 101, 99, 100),
                _row("2024-01-02", 100, 101, 94, 95),  # entry bar, low breaches stop=95
                _row("2024-01-03", 95, 96, 94, 95.5),
            ]
        )
        out = compute_forward_outcome(bars, confirmed_on="2024-01-01", direction="long", stop_price=95)
        self.assertEqual(out["exit_date"], "2024-01-02")
        self.assertEqual(out["first_barrier_hit"], "stop_loss")


class GapTests(unittest.TestCase):
    def test_long_entry_already_through_the_stop_is_excluded(self):
        bars = _bars(
            [
                _row("2024-01-01", 100, 101, 99, 100),
                _row("2024-01-02", 90, 91, 89, 90.5),  # gapped down through stop=95 before entry
            ]
        )
        out = compute_forward_outcome(bars, confirmed_on="2024-01-01", direction="long", stop_price=95)
        self.assertTrue(out["excluded"])
        self.assertEqual(out["reason"], "entry_already_through_stop")

    def test_short_entry_already_through_the_stop_is_excluded(self):
        bars = _bars(
            [
                _row("2024-01-01", 100, 101, 99, 100),
                _row("2024-01-02", 110, 111, 109, 110.5),  # gapped up through stop=105
            ]
        )
        out = compute_forward_outcome(bars, confirmed_on="2024-01-01", direction="short", stop_price=105)
        self.assertTrue(out["excluded"])
        self.assertEqual(out["reason"], "entry_already_through_stop")

    def test_long_mid_trade_gap_exits_at_that_bars_open_not_the_stop_level(self):
        bars = _bars(
            [
                _row("2024-01-01", 100, 101, 99, 100),
                _row("2024-01-02", 102, 103, 101, 102.5),  # entry bar, clear of stop=95
                _row("2024-01-03", 90, 91, 88, 89),  # gaps down through stop overnight
            ]
        )
        out = compute_forward_outcome(bars, confirmed_on="2024-01-01", direction="long", stop_price=95)
        self.assertEqual(out["exit_date"], "2024-01-03")
        self.assertEqual(out["exit_price"], 90)  # that bar's OPEN, not 95

    def test_short_mid_trade_gap_exits_at_that_bars_open_not_the_stop_level(self):
        bars = _bars(
            [
                _row("2024-01-01", 100, 101, 99, 100),
                _row("2024-01-02", 98, 99, 97, 97.5),  # entry bar, clear of stop=105
                _row("2024-01-03", 110, 112, 109, 111),  # gaps up through stop overnight
            ]
        )
        out = compute_forward_outcome(bars, confirmed_on="2024-01-01", direction="short", stop_price=105)
        self.assertEqual(out["exit_date"], "2024-01-03")
        self.assertEqual(out["exit_price"], 110)  # that bar's OPEN, not 105

    def test_ordinary_intrabar_touch_still_exits_at_the_stop_level(self):
        bars = _bars(
            [
                _row("2024-01-01", 100, 101, 99, 100),
                _row("2024-01-02", 102, 103, 101, 102.5),  # entry bar
                _row("2024-01-03", 100, 101, 94, 97),  # open (100) clear of stop, low (94) crosses it
            ]
        )
        out = compute_forward_outcome(bars, confirmed_on="2024-01-01", direction="long", stop_price=95)
        self.assertEqual(out["exit_date"], "2024-01-03")
        self.assertEqual(out["exit_price"], 95)  # the stop level itself, unchanged baseline behavior


class TimeoutTests(unittest.TestCase):
    def test_no_stop_touch_within_the_window_times_out_at_the_max_horizon_close(self):
        rows = [_row("2024-01-01", 100, 101, 99, 100)]
        price = 100
        for i in range(2, 22):
            price += 1
            rows.append(_row(f"2024-01-{i:02d}", price, price + 1, price - 1, price + 0.5))
        bars = _bars(rows)
        out = compute_forward_outcome(bars, confirmed_on="2024-01-01", direction="long", stop_price=50, horizons=(5, 10, 20))
        self.assertEqual(out["first_barrier_hit"], "none")
        self.assertIsNotNone(out["r_multiple"])
        self.assertEqual(out["exit_price"], out["closes"][20])


class ExcessReturnTests(unittest.TestCase):
    def test_short_falling_exactly_as_much_as_spy_scores_zero_excess(self):
        # Both fall 10%.
        r = excess_return(position_return=0.10, direction="short", spy_entry=100, spy_exit=90)
        self.assertAlmostEqual(r, 0.0)

    def test_short_falling_more_than_spy_scores_positive_excess(self):
        r = excess_return(position_return=0.20, direction="short", spy_entry=100, spy_exit=90)
        self.assertGreater(r, 0)

    def test_long_falling_while_spy_rises_scores_negative_excess(self):
        r = excess_return(position_return=-0.05, direction="long", spy_entry=100, spy_exit=105)
        self.assertLess(r, 0)


if __name__ == "__main__":
    unittest.main()
