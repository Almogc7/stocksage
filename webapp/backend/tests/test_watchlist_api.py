import unittest

from fastapi.testclient import TestClient

from tests._helpers import TempEnvTestCase
from app.main import app


class WatchlistApiTests(TempEnvTestCase):
    bot_rows = [
        ("NVDA", "Semis", "ACTIVE", 1, 1),
        ("AAPL", "Big Tech", "MONITOR", 0, 1),
        ("GONE", "Big Tech", "USER_REMOVED", 0, 0),
    ]

    def test_watchlist_shape_and_filtering(self):
        with TestClient(app) as client:
            resp = client.get("/api/watchlist")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.json(),
            [
                {"symbol": "AAPL", "category": "Big Tech", "wl_state": "MONITOR", "pinned": False},
                {"symbol": "NVDA", "category": "Semis", "wl_state": "ACTIVE", "pinned": True},
            ],
        )

    def test_health(self):
        with TestClient(app) as client:
            resp = client.get("/api/health")
        self.assertEqual(resp.json(), {"status": "ok", "bot_db": "ok"})

    def test_bot_db_untouched(self):
        before = (self.bot_db.read_bytes(), self.bot_db.stat().st_mtime_ns)
        with TestClient(app) as client:
            client.get("/api/watchlist")
            client.get("/api/health")
        after = (self.bot_db.read_bytes(), self.bot_db.stat().st_mtime_ns)
        self.assertEqual(before, after)

    def test_startup_fails_loudly_without_env(self):
        import os
        from unittest import mock

        with mock.patch.dict(os.environ, {"STOCKSAGE_DB_PATH": ""}):
            with self.assertRaises(RuntimeError):
                with TestClient(app):
                    pass


if __name__ == "__main__":
    unittest.main()
