"""Realistic-scale performance test for the baseline computation
(app/patterns/baseline.py) — SYNTHETIC data only, no real market data, no
yfinance. ~340 symbols x ~1250 bars (roughly 5y of daily bars), a few
thousand detections, 3 horizons — sized to match a real
backtest_patterns.py/baseline_diagnostic.py run.

This measures ONLY the baseline computation itself (build_horizon_index +
score_detection_with_baselines), not pattern detection — detection
correctness/behavior is covered elsewhere (test_patterns_*.py); this file
is purely about whether baseline.py's algorithm scales."""
import random
import time
import unittest

from app.patterns import config
from app.patterns.baseline import build_horizon_index, score_detection_with_baselines, spy_date_index
from tests._pattern_fixtures import random_ohlcv

N_SYMBOLS = 340
N_BARS = 1250
N_DETECTIONS_TARGET = 3000
PERFORMANCE_BUDGET_SECONDS = 180  # "a few minutes"


class BaselinePerformanceTests(unittest.TestCase):
    def test_realistic_scale_baseline_computation_runtime(self):
        frames = {f"SYM{i}": random_ohlcv(seed=i, n_bars=N_BARS) for i in range(N_SYMBOLS)}
        spy = random_ohlcv(seed=999_999, n_bars=N_BARS)
        spy_idx = spy_date_index(spy)
        bar_index = {s: {d: i for i, d in enumerate(df.index.to_list())} for s, df in frames.items()}

        rng = random.Random(12345)
        patterns = ["trend_pullback", "flag_breakout", "double_top_bottom"]
        directions = ["long", "short"]
        max_horizon = max(config.FORWARD_HORIZONS)
        detections: list[dict] = []
        per_symbol = max(1, N_DETECTIONS_TARGET // N_SYMBOLS)
        for symbol, df in frames.items():
            dates = df.index.to_list()
            candidate_dates = dates[config.SMA_WARMUP_BARS : len(dates) - max_horizon - 1]
            if not candidate_dates:
                continue
            for _ in range(per_symbol):
                detections.append(
                    {
                        "symbol": symbol,
                        "pattern": rng.choice(patterns),
                        "direction": rng.choice(directions),
                        "confirmed_on": rng.choice(candidate_dates),
                    }
                )
        self.assertGreaterEqual(len(detections), 2000, "fixture should produce a few thousand detections")

        start = time.perf_counter()
        total_scored = 0
        for h in config.FORWARD_HORIZONS:
            index = build_horizon_index(frames, h, spy, spy_idx, warmup_bars=config.SMA_WARMUP_BARS)
            for det in detections:
                result = score_detection_with_baselines(det, index, bar_index)
                if result is not None:
                    total_scored += 1
        elapsed = time.perf_counter() - start

        print(
            f"\n[baseline performance] {N_SYMBOLS} symbols x {N_BARS} bars, {len(detections)} detections x "
            f"{len(config.FORWARD_HORIZONS)} horizons = {len(detections) * len(config.FORWARD_HORIZONS)} "
            f"(detection, horizon) scorings: {elapsed:.1f}s total ({total_scored} scored, rest excluded/no-data)."
        )

        self.assertLess(
            elapsed,
            PERFORMANCE_BUDGET_SECONDS,
            f"baseline computation took {elapsed:.1f}s, over the {PERFORMANCE_BUDGET_SECONDS}s budget",
        )
        self.assertGreater(total_scored, 0)


if __name__ == "__main__":
    unittest.main()
