"""Script-level tests for scripts/eval_trend_pullback_holdout.py."""
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


class ImportSafetyTests(unittest.TestCase):
    def test_importing_the_script_never_touches_the_network(self):
        def blow_up(*args, **kwargs):
            raise AssertionError("import triggered a network call")

        sys.modules.pop("eval_trend_pullback_holdout", None)
        with mock.patch.object(socket, "create_connection", side_effect=blow_up):
            mod = importlib.import_module("eval_trend_pullback_holdout")
        self.assertTrue(hasattr(mod, "main"))
        self.assertTrue(callable(mod.main))


class RequireCleanOrAbortTests(unittest.TestCase):
    def setUp(self):
        sys.modules.pop("eval_trend_pullback_holdout", None)
        self.ev = importlib.import_module("eval_trend_pullback_holdout")

    def test_aborts_when_config_is_dirty(self):
        buf = io.StringIO()
        with mock.patch.object(self.ev, "_require_clean_or_abort") as rc, redirect_stdout(buf):
            rc.side_effect = [False]
            exit_code = self.ev.main()
        self.assertEqual(exit_code, 1)

    def test_aborts_when_prereg_is_dirty_even_if_config_is_clean(self):
        buf = io.StringIO()
        with mock.patch.object(self.ev, "_require_clean_or_abort") as rc, redirect_stdout(buf):
            rc.side_effect = [True, False]
            exit_code = self.ev.main()
        self.assertEqual(exit_code, 1)
        self.assertEqual(rc.call_count, 2)

    def test_porcelain_output_triggers_abort(self):
        fake_dirty = mock.Mock(stdout=" M app/patterns/prereg_v2.py\n")
        buf = io.StringIO()
        with mock.patch.object(self.ev.subprocess, "run", return_value=fake_dirty), redirect_stdout(buf):
            self.assertFalse(self.ev._require_clean_or_abort(self.ev._PREREG_PATH, "app/patterns/prereg_v2.py"))

    def test_clean_porcelain_output_proceeds(self):
        fake_clean = mock.Mock(stdout="")
        with mock.patch.object(self.ev.subprocess, "run", return_value=fake_clean):
            self.assertTrue(self.ev._require_clean_or_abort(self.ev._CONFIG_PATH, "app/patterns/config.py"))


class PipelineSmokeTests(unittest.TestCase):
    def setUp(self):
        sys.modules.pop("eval_trend_pullback_holdout", None)
        self.ev = importlib.import_module("eval_trend_pullback_holdout")

    def _run_with_mocks(self, frames, spy_frame):
        def fetch_side_effect(symbols, start, end, **kw):
            if symbols == ["SPY"]:
                return {"SPY": spy_frame}, [], 0
            return frames, [], 0

        rows = [{"symbol": s, "category": "x", "wl_state": "ACTIVE", "pinned": False} for s in frames]

        buf = io.StringIO()
        with mock.patch.object(self.ev, "_require_clean_or_abort", return_value=True), mock.patch.object(
            self.ev, "fetch_with_fallback_ranged", side_effect=fetch_side_effect
        ) as fwf, mock.patch.object(self.ev.bt.bot_db, "fetch_watchlist", return_value=rows), redirect_stdout(buf):
            rc = self.ev.main()
        return rc, buf.getvalue(), fwf

    def test_report_prints_pass1_pass2_verdict_and_two_tests_examined(self):
        frames = {f"S{i}": random_ohlcv(seed=i, n_bars=500, start_date="2016-01-04") for i in range(20)}
        spy = random_ohlcv(seed=99_999, n_bars=500, start_date="2016-01-04")
        rc, output, _fwf = self._run_with_mocks(frames, spy)
        self.assertEqual(rc, 0)
        self.assertIn("PASS-1 (B):", output)
        self.assertIn("PASS-2 (B-trend):", output)
        self.assertIn("VERDICT:", output)
        self.assertIn("2 tests examined (1 primary hypothesis x 2 baselines: B, B-trend)", output)
        self.assertIn("informational", output)

    def test_spy_is_fetched_exactly_once_with_the_held_out_range(self):
        frames = {f"S{i}": random_ohlcv(seed=i, n_bars=500, start_date="2016-01-04") for i in range(20)}
        spy = random_ohlcv(seed=99_999, n_bars=500, start_date="2016-01-04")
        rc, _output, fwf = self._run_with_mocks(frames, spy)
        self.assertEqual(rc, 0)
        spy_calls = [c for c in fwf.call_args_list if c.args[0] == ["SPY"]]
        self.assertEqual(len(spy_calls), 1)
        args, kwargs = spy_calls[0]
        self.assertEqual(args[1], self.ev.prereg_v2.HELD_OUT_PERIOD_START)
        self.assertEqual(args[2], self.ev.prereg_v2.HELD_OUT_PERIOD_END)

    def test_etf_index_context_symbols_are_excluded_from_the_pooled_universe(self):
        active = {f"S{i}": random_ohlcv(seed=i, n_bars=500, start_date="2016-01-04") for i in range(10)}
        etf = {f"E{i}": random_ohlcv(seed=200 + i, n_bars=500, start_date="2016-01-04") for i in range(5)}
        spy = random_ohlcv(seed=99_999, n_bars=500, start_date="2016-01-04")

        def fetch_side_effect(symbols, start, end, **kw):
            if symbols == ["SPY"]:
                return {"SPY": spy}, [], 0
            # The script should only ever ask for ACTIVE (non-ETF) symbols.
            self.assertTrue(all(not s.startswith("E") for s in symbols))
            return active, [], 0

        rows = [{"symbol": s, "category": "x", "wl_state": "ACTIVE", "pinned": False} for s in active]
        rows += [{"symbol": s, "category": "x", "wl_state": "ETF_INDEX_CONTEXT", "pinned": False} for s in etf]

        buf = io.StringIO()
        with mock.patch.object(self.ev, "_require_clean_or_abort", return_value=True), mock.patch.object(
            self.ev, "fetch_with_fallback_ranged", side_effect=fetch_side_effect
        ), mock.patch.object(self.ev.bt.bot_db, "fetch_watchlist", return_value=rows), redirect_stdout(buf):
            rc = self.ev.main()
        self.assertEqual(rc, 0)

    def test_peer_exclusion_counts_are_printed_per_baseline_per_year(self):
        # Few enough symbols that PREREG_MIN_PEERS=20 definitely excludes
        # some detections from both baselines.
        frames = {f"S{i}": random_ohlcv(seed=i, n_bars=500, start_date="2016-01-04") for i in range(10)}
        spy = random_ohlcv(seed=99_999, n_bars=500, start_date="2016-01-04")
        rc, output, _fwf = self._run_with_mocks(frames, spy)
        self.assertEqual(rc, 0)
        self.assertIn("Peer-exclusion counts", output)
        self.assertRegex(output, r"\d{4}: B=\d+ B-trend=\d+")


if __name__ == "__main__":
    unittest.main()
