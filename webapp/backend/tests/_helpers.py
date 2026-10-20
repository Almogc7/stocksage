"""Temp-DB helpers. Bot DB is built from the real schema (db/database.py
init_db) in a temp dir; the real db/stocksage.db is never opened."""
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[3]
REAL_DB = (REPO_ROOT / "db" / "stocksage.db").resolve()

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def make_temp_bot_db(directory: Path, rows: list[tuple] | None = None) -> Path:
    """rows: (symbol, category, wl_state, pinned, enabled)."""
    import db.database as dbmod  # read-only use of the bot schema builder

    path = (directory / "bot.db").resolve()
    assert path != REAL_DB
    original = dbmod.DB_PATH
    dbmod.DB_PATH = path
    try:
        dbmod.init_db()
    finally:
        dbmod.DB_PATH = original
    conn = sqlite3.connect(path)
    try:
        for sym, cat, state, pinned, enabled in rows or []:
            conn.execute(
                "INSERT INTO watchlist (symbol, category, wl_state, pinned, enabled)"
                " VALUES (?, ?, ?, ?, ?)",
                (sym, cat, state, pinned, enabled),
            )
        conn.commit()
    finally:
        conn.close()
    return path


@contextmanager
def bot_db_module(path: Path):
    """Points db.database at `path` for the duration of the block, so tests
    can seed alerts/composite_signals/backtest rows through the bot's own
    real writer functions instead of hand-rolled SQL. Refuses anything that
    isn't a throwaway temp file, so a bug here can never point the bot
    module at the real db/stocksage.db."""
    import db.database as dbmod

    resolved = path.resolve()
    tmp_root = Path(tempfile.gettempdir()).resolve()
    assert resolved.is_relative_to(tmp_root), f"{resolved} is not under {tmp_root}"
    assert resolved != REAL_DB, "refusing to point db.database at the real bot DB"
    original = dbmod.DB_PATH
    dbmod.DB_PATH = resolved
    try:
        yield dbmod
    finally:
        dbmod.DB_PATH = original


class TempEnvTestCase(unittest.TestCase):
    """Points both DB env vars at a fresh temp dir for each test."""

    bot_rows: list[tuple] = []

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="stocksage_webapp_test_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bot_db = make_temp_bot_db(self.tmp, self.bot_rows)
        self.state_db = self.tmp / "state.db"
        patcher = mock.patch.dict(
            os.environ,
            {
                "STOCKSAGE_DB_PATH": str(self.bot_db),
                "WEBAPP_STATE_DB_PATH": str(self.state_db),
            },
        )
        patcher.start()
        self.addCleanup(patcher.stop)
