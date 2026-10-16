import sqlite3
import unittest

from fastapi.testclient import TestClient

from tests._helpers import TempEnvTestCase, bot_db_module
from app.main import app


class SignalsApiTests(TempEnvTestCase):
    bot_rows = [("AAPL", "Big Tech", "ACTIVE", 0, 1)]

    def test_symbol_with_no_signals_returns_empty_lists(self):
        with TestClient(app) as client:
            resp = client.get("/api/signals/AAPL")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.json(),
            {
                "symbol": "AAPL",
                "from": None,
                "alerts": [],
                "composite_signals": [],
                "backtest_run_id": None,
                "backtest_signals": [],
            },
        )

    def test_legacy_alert_with_full_outcome(self):
        with bot_db_module(self.bot_db) as dbmod:
            alert_id = dbmod.log_alert(
                "AAPL",
                "BUY_SIGNAL",
                "AAPL cleared the bar",
                analysis={
                    "score": 78,
                    "verdict": "BUY",
                    "rsi": 55.0,
                    "atr": 2.1,
                    "stop_loss": 145.0,
                    "take_profit": 160.0,
                    "triggered_signals": ["rsi_healthy_range", "volume_spike"],
                },
                price_at_alert=150.0,
            )
            dbmod.upsert_alert_outcome(
                alert_id,
                {
                    "close_t1": 151.0,
                    "close_t3": 153.0,
                    "close_t5": 155.0,
                    "close_t10": 158.0,
                    "max_adverse_excursion": -1.2,
                    "first_barrier_hit": "none",
                    "r_multiple": 1.6,
                },
            )

        with TestClient(app) as client:
            resp = client.get("/api/signals/AAPL")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(len(body["alerts"]), 1)
        alert = body["alerts"][0]
        self.assertEqual(alert["id"], alert_id)
        self.assertEqual(alert["alert_type"], "BUY_SIGNAL")
        self.assertEqual(alert["score"], 78)
        self.assertEqual(alert["verdict"], "BUY")
        self.assertEqual(alert["price_at_alert"], 150.0)
        self.assertEqual(
            sorted(alert["triggered_signals"]), ["rsi_healthy_range", "volume_spike"]
        )
        self.assertEqual(
            alert["outcome"],
            {
                "pending": False,
                "close_t1": 151.0,
                "close_t3": 153.0,
                "close_t5": 155.0,
                "close_t10": 158.0,
                "max_adverse_excursion": -1.2,
                "first_barrier_hit": "none",
                "r_multiple": 1.6,
            },
        )

    def test_composite_signal_with_pending_outcome(self):
        with bot_db_module(self.bot_db) as dbmod:
            dbmod.log_composite_signal(
                symbol="AAPL",
                signal_date="2026-01-05",
                regime="bull",
                total_score=82.0,
                required_score=70,
                trend_pts=20.0,
                momentum_pts=20.0,
                volume_pts=20.0,
                rs_pts=22.0,
                rs_ratio=1.3,
                required_rs=1.0,
                price=150.0,
                atr=2.1,
                stop_price=145.0,
                stop_multiplier=2.5,
            )

        with TestClient(app) as client:
            resp = client.get("/api/signals/AAPL")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(len(body["composite_signals"]), 1)
        signal = body["composite_signals"][0]
        self.assertEqual(signal["signal_date"], "2026-01-05")
        self.assertEqual(signal["regime"], "bull")
        self.assertEqual(signal["total_score"], 82.0)
        self.assertEqual(
            signal["outcome"],
            {
                "pending": True,
                "close_t1": None,
                "close_t3": None,
                "close_t5": None,
                "close_t10": None,
                "max_adverse_excursion": None,
                "first_barrier_hit": None,
                "r_multiple": None,
            },
        )

    def test_from_date_filters_out_older_rows(self):
        with bot_db_module(self.bot_db) as dbmod:
            dbmod.log_composite_signal(
                symbol="AAPL", signal_date="2026-01-05", regime="bull",
                total_score=80.0, required_score=70, trend_pts=20.0,
                momentum_pts=20.0, volume_pts=20.0, rs_pts=20.0, rs_ratio=1.1,
                required_rs=1.0, price=150.0, atr=2.0, stop_price=145.0,
                stop_multiplier=2.5,
            )
            dbmod.log_composite_signal(
                symbol="AAPL", signal_date="2026-02-10", regime="bull",
                total_score=85.0, required_score=70, trend_pts=22.0,
                momentum_pts=21.0, volume_pts=20.0, rs_pts=22.0, rs_ratio=1.2,
                required_rs=1.0, price=160.0, atr=2.2, stop_price=155.0,
                stop_multiplier=2.5,
            )

        with TestClient(app) as client:
            resp = client.get("/api/signals/AAPL?from=2026-02-01")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(len(body["composite_signals"]), 1)
        self.assertEqual(body["composite_signals"][0]["signal_date"], "2026-02-10")
        self.assertEqual(body["from"], "2026-02-01")

    def test_backtest_signals_only_when_requested_and_latest_finished_run_only(self):
        with bot_db_module(self.bot_db) as dbmod:
            dbmod.start_backtest_run("run-older", period_years=2.0, universe_size=10)
            dbmod.insert_backtest_signals(
                "run-older",
                [
                    {
                        "symbol": "AAPL", "signal_date": "2025-06-01", "regime": "bull",
                        "total_score": 71.0, "required_score": 70, "trend_pts": 18.0,
                        "momentum_pts": 18.0, "volume_pts": 18.0, "rs_pts": 17.0,
                        "rs_ratio": 1.05, "required_rs": 1.0, "price": 140.0,
                    }
                ],
            )
            dbmod.finish_backtest_run("run-older", symbols_ok=1, symbols_failed=0, total_signals=1)

            dbmod.start_backtest_run("run-newer", period_years=2.0, universe_size=10)
            dbmod.insert_backtest_signals(
                "run-newer",
                [
                    {
                        "symbol": "AAPL", "signal_date": "2026-01-05", "regime": "bull",
                        "total_score": 82.0, "required_score": 70, "trend_pts": 20.0,
                        "momentum_pts": 20.0, "volume_pts": 20.0, "rs_pts": 22.0,
                        "rs_ratio": 1.3, "required_rs": 1.0, "price": 150.0,
                    }
                ],
            )
            dbmod.finish_backtest_run("run-newer", symbols_ok=1, symbols_failed=0, total_signals=1)

        # start_backtest_run()'s timestamps only have second resolution, so
        # pin them explicitly to make "latest run" unambiguous rather than
        # relying on two calls landing in different seconds.
        conn = sqlite3.connect(self.bot_db)
        try:
            conn.execute(
                "UPDATE composite_backtest_runs SET started_at = ? WHERE run_id = ?",
                ("2026-01-01 00:00:00", "run-older"),
            )
            conn.execute(
                "UPDATE composite_backtest_runs SET started_at = ? WHERE run_id = ?",
                ("2026-02-01 00:00:00", "run-newer"),
            )
            conn.commit()
        finally:
            conn.close()

        with TestClient(app) as client:
            default_resp = client.get("/api/signals/AAPL")
            self.assertEqual(default_resp.json()["backtest_signals"], [])
            self.assertIsNone(default_resp.json()["backtest_run_id"])

            resp = client.get("/api/signals/AAPL?include_backtest=true")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["backtest_run_id"], "run-newer")
        self.assertEqual(len(body["backtest_signals"]), 1)
        self.assertEqual(body["backtest_signals"][0]["signal_date"], "2026-01-05")

    def test_invalid_symbol_rejected(self):
        with TestClient(app) as client:
            resp = client.get("/api/signals/bad;symbol")
        self.assertEqual(resp.status_code, 422)

    def test_invalid_from_date_rejected(self):
        with TestClient(app) as client:
            resp = client.get("/api/signals/AAPL?from=01-01-2026")
        self.assertEqual(resp.status_code, 422)

    def test_bot_db_untouched(self):
        with bot_db_module(self.bot_db) as dbmod:
            dbmod.log_alert("AAPL", "BUY_SIGNAL", "msg", price_at_alert=100.0)

        before = (self.bot_db.read_bytes(), self.bot_db.stat().st_mtime_ns)
        with TestClient(app) as client:
            client.get("/api/signals/AAPL")
            client.get("/api/signals/AAPL?include_backtest=true")
        after = (self.bot_db.read_bytes(), self.bot_db.stat().st_mtime_ns)
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
