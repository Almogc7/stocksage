import os
import unittest
from pathlib import Path
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

    def test_is_production_default_false(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(settings.is_production())

    def test_is_production_true_variants(self):
        for value in ("1", "true", "True", "yes", "YES"):
            with mock.patch.dict(os.environ, {"WEBAPP_PRODUCTION": value}):
                self.assertTrue(settings.is_production(), value)

    def test_frontend_dist_path_default_is_outside_the_repo(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            p = settings.frontend_dist_path()
        self.assertEqual(p, Path(r"C:\stocksage-deploy\dist"))

    def test_frontend_dist_path_override(self):
        with mock.patch.dict(os.environ, {"WEBAPP_FRONTEND_DIST_PATH": "D:/custom/dist"}):
            p = settings.frontend_dist_path()
        self.assertEqual(p, Path("D:/custom/dist").resolve())

    def test_repo_db_dir_points_at_repo_root_db(self):
        p = settings.repo_db_dir()
        self.assertEqual(p.name, "db")
        self.assertEqual(p.parent, settings.WEBAPP_DIR.parent)

    def test_webapp_port_default(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(settings.webapp_port(), 8000)

    def test_webapp_port_valid_override(self):
        with mock.patch.dict(os.environ, {"WEBAPP_PORT": "8765"}):
            self.assertEqual(settings.webapp_port(), 8765)

    def test_webapp_port_non_numeric_fails_loudly(self):
        with mock.patch.dict(os.environ, {"WEBAPP_PORT": "notaport"}):
            with self.assertRaises(RuntimeError):
                settings.webapp_port()

    def test_webapp_port_empty_uses_default(self):
        with mock.patch.dict(os.environ, {"WEBAPP_PORT": "  "}):
            self.assertEqual(settings.webapp_port(), 8000)

    def test_webapp_port_zero_fails_loudly(self):
        with mock.patch.dict(os.environ, {"WEBAPP_PORT": "0"}):
            with self.assertRaises(RuntimeError):
                settings.webapp_port()

    def test_webapp_port_too_low_fails_loudly(self):
        with mock.patch.dict(os.environ, {"WEBAPP_PORT": "80"}):
            with self.assertRaises(RuntimeError):
                settings.webapp_port()

    def test_webapp_port_too_high_fails_loudly(self):
        with mock.patch.dict(os.environ, {"WEBAPP_PORT": "70000"}):
            with self.assertRaises(RuntimeError):
                settings.webapp_port()


if __name__ == "__main__":
    unittest.main()
