"""Script-level tests for scripts/baseline_diagnostic.py and the
import-safety of scripts/backtest_patterns.py (both are plain modules
under scripts/, imported directly the same way the real script does)."""
import importlib
import io
import re
import socket
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = str(Path(__file__).resolve().parents[1] / "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)
TESTS_DIR = str(Path(__file__).resolve().parent)
if TESTS_DIR not in sys.path:
    sys.path.insert(0, TESTS_DIR)

from _pattern_fixtures import random_ohlcv  # noqa: E402


class BacktestPatternsImportSafetyTests(unittest.TestCase):
    def test_importing_backtest_patterns_never_touches_the_network(self):
        def blow_up(*args, **kwargs):
            raise AssertionError("import triggered a network call")

        sys.modules.pop("backtest_patterns", None)
        with mock.patch.object(socket, "create_connection", side_effect=blow_up):
            mod = importlib.import_module("backtest_patterns")
        self.assertTrue(hasattr(mod, "main"))
        self.assertTrue(callable(mod.main))


class RequireCleanConfigTests(unittest.TestCase):
    def setUp(self):
        sys.modules.pop("baseline_diagnostic", None)
        self.bd = importlib.import_module("baseline_diagnostic")

    def test_aborts_when_config_has_uncommitted_changes(self):
        fake_result = mock.Mock(stdout=" M app/patterns/config.py\n")
        buf = io.StringIO()
        with mock.patch.object(self.bd.subprocess, "run", return_value=fake_result), redirect_stdout(buf):
            self.assertFalse(self.bd._require_clean_config_or_abort())

    def test_proceeds_when_config_is_clean(self):
        fake_result = mock.Mock(stdout="")
        with mock.patch.object(self.bd.subprocess, "run", return_value=fake_result):
            self.assertTrue(self.bd._require_clean_config_or_abort())

    def test_main_aborts_before_any_fetch_when_config_is_dirty(self):
        buf = io.StringIO()
        with mock.patch.object(self.bd, "_require_clean_config_or_abort", return_value=False), mock.patch.object(
            self.bd, "fetch_with_fallback"
        ) as fwf, redirect_stdout(buf):
            rc = self.bd.main()
        self.assertEqual(rc, 1)
        fwf.assert_not_called()


class PipelineSmokeTests(unittest.TestCase):
    def setUp(self):
        sys.modules.pop("baseline_diagnostic", None)
        self.bd = importlib.import_module("baseline_diagnostic")

    def _run_with_mocks(self, active_frames, etf_frames, spy_frame):
        def fetch_side_effect(symbols, period, **kw):
            if symbols == ["SPY"]:
                return {"SPY": spy_frame}, [], 0
            if symbols and symbols[0] in etf_frames:
                return etf_frames, [], 0
            return active_frames, [], 0

        rows = [{"symbol": s, "category": "x", "wl_state": "ACTIVE", "pinned": False} for s in active_frames]
        rows += [{"symbol": s, "category": "x", "wl_state": "ETF_INDEX_CONTEXT", "pinned": False} for s in etf_frames]

        buf = io.StringIO()
        with mock.patch.object(self.bd, "_require_clean_config_or_abort", return_value=True), mock.patch.object(
            self.bd, "fetch_with_fallback", side_effect=fetch_side_effect
        ), mock.patch.object(self.bd.bt.bot_db, "fetch_watchlist", return_value=rows), redirect_stdout(buf):
            rc = self.bd.main()
        return rc, buf.getvalue()

    def test_pooled_row_excludes_etf_index_context_symbols(self):
        active = {f"S{i}": random_ohlcv(seed=i, n_bars=260, start_date="2020-06-01") for i in range(3)}
        etf = {f"E{i}": random_ohlcv(seed=100 + i, n_bars=260, start_date="2020-06-01") for i in range(2)}
        spy = random_ohlcv(seed=9999, n_bars=260, start_date="2020-06-01")

        rc, output = self._run_with_mocks(active, etf, spy)
        self.assertEqual(rc, 0)

        pooled_header = re.search(r"=== ALL STOCK TIERS \(POOLED.*?\((\d+) symbols", output)
        self.assertIsNotNone(pooled_header)
        self.assertEqual(int(pooled_header.group(1)), len(active))  # not len(active) + len(etf)

        etf_tier_header = re.search(r"=== Tier: ETF_INDEX_CONTEXT \((\d+) symbols", output)
        self.assertIsNotNone(etf_tier_header)
        self.assertEqual(int(etf_tier_header.group(1)), len(etf))

    def test_drift_section_prints_a_row_per_calendar_year_in_addition_to_overall(self):
        # warmup_bars=200 lands in ~Oct 2020; the tail of a 340-bar series
        # starting 2020-01-02 lands in ~Apr 2021, so eligible bars
        # (warmup..n_bars-horizon) straddle the 2020/2021 boundary.
        active = {f"S{i}": random_ohlcv(seed=i, n_bars=340, start_date="2020-01-02") for i in range(3)}
        spy = random_ohlcv(seed=9999, n_bars=340, start_date="2020-01-02")

        rc, output = self._run_with_mocks(active, {}, spy)
        self.assertEqual(rc, 0)

        drift_section = output[output.index("Unconditional drift") :]
        year_lines = re.findall(r"^\s+(\d{4}): .+%\s+\(n=\d+\)$", drift_section, re.MULTILINE)
        overall_lines = re.findall(r"overall mean excess return", drift_section)
        self.assertGreaterEqual(len(overall_lines), 1)
        self.assertGreaterEqual(len(set(year_lines)), 2, msg=f"expected >=2 distinct years in drift output, got: {year_lines}")

    def test_header_text_is_printed(self):
        active = {f"S{i}": random_ohlcv(seed=i, n_bars=260, start_date="2020-06-01") for i in range(2)}
        spy = random_ohlcv(seed=9999, n_bars=260, start_date="2020-06-01")
        rc, output = self._run_with_mocks(active, {}, spy)
        self.assertEqual(rc, 0)
        self.assertIn(
            "post-hoc diagnostic on the data the v1 report used; it can only lower confidence, not raise it",
            output,
        )


if __name__ == "__main__":
    unittest.main()
