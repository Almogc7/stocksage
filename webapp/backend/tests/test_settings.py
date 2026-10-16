import os
import unittest
from unittest import mock

from tests import _helpers  # noqa: F401  (sys.path setup)
from app import settings


class SettingsTests(unittest.TestCase):
    def test_missing_var_fails_loudly(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                settings.bot_db_path()

    def test_empty_var_fails_loudly(self):
        with mock.patch.dict(os.environ, {"STOCKSAGE_DB_PATH": "  "}):
            with self.assertRaises(RuntimeError):
                settings.bot_db_path()

    def test_nonexistent_file_fails_loudly(self):
        with mock.patch.dict(os.environ, {"STOCKSAGE_DB_PATH": "Z:/nope/missing.db"}):
            with self.assertRaises(RuntimeError):
                settings.bot_db_path()

    def test_state_db_default_is_under_webapp_data(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            p = settings.state_db_path()
        self.assertEqual(p.parent.name, "data")
        self.assertEqual(p.parent.parent.name, "webapp")


if __name__ == "__main__":
    unittest.main()
