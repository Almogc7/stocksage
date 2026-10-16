"""Webapp's own SQLite state (price cache; layouts later). Not the bot DB."""
import sqlite3
from contextlib import contextmanager
from typing import Iterator

from . import settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS price_cache (
    symbol     TEXT NOT NULL,
    range      TEXT NOT NULL,
    fetched_at REAL NOT NULL,
    payload    TEXT NOT NULL,
    PRIMARY KEY (symbol, range)
);
CREATE TABLE IF NOT EXISTS sparkline_cache (
    symbol     TEXT NOT NULL,
    range      TEXT NOT NULL,
    fetched_at REAL NOT NULL,
    negative   INTEGER NOT NULL DEFAULT 0,
    payload    TEXT,
    PRIMARY KEY (symbol, range)
)
"""


def init_schema() -> None:
    path = settings.state_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(_SCHEMA)


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    path = settings.state_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5.0)
    try:
        conn.row_factory = sqlite3.Row
        with conn:  # commit on success, rollback on error
            yield conn
    finally:
        conn.close()
