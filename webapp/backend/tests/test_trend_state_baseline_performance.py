"""Realistic-scale performance test for round B's trend-state baseline
(app/patterns/trend_state_baseline.py) — SYNTHETIC data only, no real
market data, no yfinance. ~340 symbols x ~1500 bars (matching the ~5.5y
held-out span)."""
import time
import unittest

from app.patterns import config
from app.patterns.baseline import build_horizon_index, spy_date_index
from app.patterns.trend_state_baseline import build_trend_restricted_date_agg, compute_trend_state
from tests._pattern_fixtures import random_ohlcv

N_SYMBOLS = 340
N_BARS = 1500
PERFORMANCE_BUDGET_SECONDS = 180  # "a few minutes"


class TrendStateBaselinePerformanceTests(unittest.TestCase):
    def test_realistic_scale_runtime(self):
        frames = {f"SYM{i}": random_ohlcv(seed=i, n_bars=N_BARS, start_date="2016-01-04") for i in range(N_SYMBOLS)}
        spy = random_ohlcv(seed=999_999, n_bars=N_BARS, start_date="2016-01-04")
        spy_idx = spy_date_index(spy)

        start = time.perf_counter()
        trend_state = compute_trend_state(frames)
        trend_state_elapsed = time.perf_counter() - start

        agg_elapsed = 0.0
        total_agg_entries = 0
        for h in config.FORWARD_HORIZONS:
            index = build_horizon_index(frames, h, spy, spy_idx, warmup_bars=config.SMA_WARMUP_BARS)
            t0 = time.perf_counter()
            agg = build_trend_restricted_date_agg(index, trend_state, "long")
            agg_elapsed += time.perf_counter() - t0
            total_agg_entries += len(agg)

        total_elapsed = trend_state_elapsed + agg_elapsed
        print(
            f"\n[trend-state baseline performance] {N_SYMBOLS} symbols x {N_BARS} bars: "
            f"compute_trend_state={trend_state_elapsed:.1f}s, "
            f"build_trend_restricted_date_agg (x{len(config.FORWARD_HORIZONS)} horizons)={agg_elapsed:.1f}s, "
            f"total={total_elapsed:.1f}s ({total_agg_entries} aggregate date entries)."
        )

        self.assertLess(
            total_elapsed,
            PERFORMANCE_BUDGET_SECONDS,
            f"trend-state baseline computation took {total_elapsed:.1f}s, over the {PERFORMANCE_BUDGET_SECONDS}s budget",
        )
        self.assertGreater(total_agg_entries, 0)


if __name__ == "__main__":
    unittest.main()
