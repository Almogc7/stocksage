import sqlite3
import unittest

from tests._helpers import TempEnvTestCase
from app import bot_db


class BotDbReadOnlyTests(TempEnvTestCase):
    def test_select_works(self):
        with bot_db.read_conn() as conn:
            self.assertEqual(conn.execute("SELECT 1").fetchone()[0], 1)

    def test_writes_and_ddl_rejected(self):
        with bot_db.read_conn() as conn:
            for sql in (
                "INSERT INTO watchlist (symbol, category) VALUES ('X', 'Y')",
                "UPDATE watchlist SET category = 'Z'",
                "CREATE TABLE evil (a INTEGER)",
                "DROP TABLE watchlist",
            ):
                with self.assertRaises(sqlite3.OperationalError):
                    conn.execute(sql)

    def test_connection_closed_after_context(self):
        with bot_db.read_conn() as conn:
            pass
        with self.assertRaises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")


if __name__ == "__main__":
    unittest.main()
