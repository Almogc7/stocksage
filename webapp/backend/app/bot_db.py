"""The ONLY door to the bot database: read-only, one connection per call."""
import sqlite3
from contextlib import contextmanager
from typing import Iterator

from . import settings


@contextmanager
def read_conn() -> Iterator[sqlite3.Connection]:
    uri = settings.bot_db_path().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=2.0)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")  # second guard beyond mode=ro
        yield conn
    finally:
        conn.close()


def fetch_watchlist() -> list[dict]:
    with read_conn() as conn:
        rows = conn.execute(
            "SELECT symbol, category, wl_state, pinned FROM watchlist"
            " WHERE enabled = 1 ORDER BY category, symbol"
        ).fetchall()
    return [
        {
            "symbol": r["symbol"],
            "category": r["category"],
            "wl_state": r["wl_state"],
            "pinned": bool(r["pinned"]),
        }
        for r in rows
    ]


def ping() -> None:
    with read_conn() as conn:
        conn.execute("SELECT 1").fetchone()
