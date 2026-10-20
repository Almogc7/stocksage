import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import quote

from fastapi.testclient import TestClient

from app import main, settings
from tests._helpers import TempEnvTestCase


def _make_dist(tmp: Path) -> Path:
    dist = tmp / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>spa shell</html>")
    (dist / "assets" / "app.js").write_text("console.log('app')")
    (dist / "favicon.ico").write_text("icon")
    return dist


class _ProdAppTestCase(TempEnvTestCase):
    """Builds a production app.main.create_app(production=True) pointed
    at a temp dist dir with a valid index.html + assets/, with
    WEBAPP_PRODUCTION explicitly cleared first so no leftover env value
    can mask the explicit `production=True` parameter under test."""

    def setUp(self):
        super().setUp()
        self.dist_tmp = Path(tempfile.mkdtemp(prefix="stocksage_dist_test_"))
        self.addCleanup(shutil.rmtree, self.dist_tmp, True)
        self.dist_dir = _make_dist(self.dist_tmp)
        patcher = mock.patch.dict(
            os.environ, {"WEBAPP_FRONTEND_DIST_PATH": str(self.dist_dir)}
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("WEBAPP_PRODUCTION", None)

    def prod_app(self):
        return main.create_app(production=True)


class DevAppTests(TempEnvTestCase):
    def test_docs_endpoints_available(self):
        app = main.create_app(production=False)
        with TestClient(app) as client:
            self.assertEqual(client.get("/docs").status_code, 200)
            self.assertEqual(client.get("/redoc").status_code, 200)
            self.assertEqual(client.get("/openapi.json").status_code, 200)

    def test_patterns_route_is_registered(self):
        # app.routes doesn't eagerly expose included-router paths in
        # this FastAPI version, so registration is proven by an actual
        # request: an invalid symbol is rejected by the route's own
        # validation (422), which only happens if the route exists --
        # no network touched either way.
        app = main.create_app(production=False)
        with TestClient(app) as client:
            resp = client.get("/api/patterns/not a valid symbol!!")
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["detail"], "Invalid symbol")

    def test_unknown_api_path_is_json_404(self):
        app = main.create_app(production=False)
        with TestClient(app) as client:
            resp = client.get("/api/totally-unknown")
        self.assertEqual(resp.status_code, 404)
        self.assertIn("detail", resp.json())

    def test_unknown_non_api_path_has_no_spa_fallback(self):
        app = main.create_app(production=False)
        with TestClient(app) as client:
            resp = client.get("/some/client/route")
        self.assertEqual(resp.status_code, 404)
        self.assertNotIn("spa shell", resp.text)


class ProdAppRoutingTests(_ProdAppTestCase):
    def test_docs_endpoints_disabled(self):
        # docs_url/redoc_url/openapi_url=None means FastAPI never
        # registers the real swagger/redoc/schema routes, so these
        # paths (not under /api/) fall through to the SPA catch-all like
        # any other unknown client route -- 200 with the SPA shell, not
        # the actual docs UI or an OpenAPI schema.
        app = self.prod_app()
        with TestClient(app) as client:
            docs = client.get("/docs")
            redoc = client.get("/redoc")
            openapi = client.get("/openapi.json")
        for resp in (docs, redoc, openapi):
            self.assertEqual(resp.status_code, 200)
            self.assertIn("spa shell", resp.text)

    def test_patterns_route_not_registered(self):
        # Same invalid symbol as the dev-mode test: in dev it's a 422
        # from the route's own validation; in production the route
        # doesn't exist at all, so the SPA fallback's own api/-prefix
        # guard returns a plain 404 instead -- never 422, never
        # index.html.
        app = self.prod_app()
        with TestClient(app) as client:
            resp = client.get("/api/patterns/not a valid symbol!!")
        self.assertEqual(resp.status_code, 404)
        self.assertNotIn("spa shell", resp.text)

    def test_unknown_api_path_is_json_404(self):
        app = self.prod_app()
        with TestClient(app) as client:
            resp = client.get("/api/totally-unknown")
        self.assertEqual(resp.status_code, 404)
        self.assertIn("detail", resp.json())

    def test_root_and_unknown_client_route_serve_index_html(self):
        app = self.prod_app()
        with TestClient(app) as client:
            root = client.get("/")
            other = client.get("/some/client/route")
        self.assertEqual(root.status_code, 200)
        self.assertIn("spa shell", root.text)
        self.assertEqual(other.status_code, 200)
        self.assertIn("spa shell", other.text)

    def test_real_top_level_static_file_is_served(self):
        app = self.prod_app()
        with TestClient(app) as client:
            resp = client.get("/favicon.ico")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.text, "icon")

    def test_asset_file_is_served_via_assets_mount(self):
        app = self.prod_app()
        with TestClient(app) as client:
            resp = client.get("/assets/app.js")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("app", resp.text)

    def test_still_working_api_route_in_production(self):
        app = self.prod_app()
        with TestClient(app) as client:
            resp = client.get("/api/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")


class ProdAppCacheHeaderTests(_ProdAppTestCase):
    def test_index_html_is_no_cache(self):
        app = self.prod_app()
        with TestClient(app) as client:
            resp = client.get("/")
        self.assertEqual(resp.headers.get("cache-control"), "no-cache")

    def test_hashed_asset_is_immutable_long_cache(self):
        app = self.prod_app()
        with TestClient(app) as client:
            resp = client.get("/assets/app.js")
        self.assertEqual(resp.headers.get("cache-control"), "public, max-age=31536000, immutable")


class ProdAppPathTraversalTests(_ProdAppTestCase):
    def _assert_falls_back_to_index(self, client, path: str):
        resp = client.get(path)
        self.assertNotEqual(resp.status_code, 500, f"{path} returned a 500")
        self.assertEqual(resp.status_code, 200, f"{path} did not fall back cleanly")
        self.assertIn("spa shell", resp.text, f"{path} did not serve index.html")

    def test_plain_dotdot_traversal(self):
        app = self.prod_app()
        with TestClient(app) as client:
            self._assert_falls_back_to_index(client, "/../../../../etc/passwd")

    def test_url_encoded_dotdot_traversal(self):
        app = self.prod_app()
        with TestClient(app) as client:
            self._assert_falls_back_to_index(client, "/" + quote("../../../../etc/passwd"))

    def test_double_encoded_dotdot_traversal(self):
        app = self.prod_app()
        with TestClient(app) as client:
            self._assert_falls_back_to_index(client, "/" + quote(quote("../../../../etc/passwd")))

    def test_sibling_of_dist_file_is_not_served(self):
        secret = self.dist_tmp / "secret.txt"
        secret.write_text("do not serve me")
        app = self.prod_app()
        with TestClient(app) as client:
            self._assert_falls_back_to_index(client, "/../secret.txt")

    def test_drive_letter_absolute_path(self):
        app = self.prod_app()
        with TestClient(app) as client:
            self._assert_falls_back_to_index(client, "/C:/Windows/win.ini")

    def test_encoded_backslash_traversal(self):
        app = self.prod_app()
        with TestClient(app) as client:
            self._assert_falls_back_to_index(client, "/..%5c..%5csecret.txt")

    def test_embedded_null_byte(self):
        app = self.prod_app()
        with TestClient(app) as client:
            self._assert_falls_back_to_index(client, "/index.html%00.txt")

    def test_reserved_device_names(self):
        app = self.prod_app()
        with TestClient(app) as client:
            self._assert_falls_back_to_index(client, "/con")
            self._assert_falls_back_to_index(client, "/nul")

    def test_unc_looking_path(self):
        app = self.prod_app()
        with TestClient(app) as client:
            self._assert_falls_back_to_index(client, "//server/share")


class ProdAppStartupFailureTests(TempEnvTestCase):
    def setUp(self):
        super().setUp()
        os.environ.pop("WEBAPP_PRODUCTION", None)

    def test_missing_index_html_fails_loudly(self):
        tmp = Path(tempfile.mkdtemp(prefix="stocksage_missing_index_"))
        self.addCleanup(shutil.rmtree, tmp, True)
        dist = tmp / "dist"
        (dist / "assets").mkdir(parents=True)
        with mock.patch.dict(os.environ, {"WEBAPP_FRONTEND_DIST_PATH": str(dist)}):
            with self.assertRaises(RuntimeError) as ctx:
                main.create_app(production=True)
        self.assertIn(str(dist / "index.html"), str(ctx.exception))

    def test_missing_assets_dir_fails_loudly(self):
        tmp = Path(tempfile.mkdtemp(prefix="stocksage_missing_assets_"))
        self.addCleanup(shutil.rmtree, tmp, True)
        dist = tmp / "dist"
        dist.mkdir(parents=True)
        (dist / "index.html").write_text("<html></html>")
        with mock.patch.dict(os.environ, {"WEBAPP_FRONTEND_DIST_PATH": str(dist)}):
            with self.assertRaises(RuntimeError) as ctx:
                main.create_app(production=True)
        self.assertIn(str(dist / "assets"), str(ctx.exception))

    def test_bot_db_inside_repo_db_dir_is_rejected_in_production(self):
        fake_repo_db_dir = self.tmp / "db"
        fake_repo_db_dir.mkdir(parents=True, exist_ok=True)
        fake_live_db = fake_repo_db_dir / "stocksage.db"
        shutil.copy(self.bot_db, fake_live_db)

        dist_tmp = Path(tempfile.mkdtemp(prefix="stocksage_dist_guard_"))
        self.addCleanup(shutil.rmtree, dist_tmp, True)
        dist = _make_dist(dist_tmp)

        with mock.patch.dict(
            os.environ,
            {"WEBAPP_FRONTEND_DIST_PATH": str(dist), "STOCKSAGE_DB_PATH": str(fake_live_db)},
        ), mock.patch.object(settings, "repo_db_dir", return_value=fake_repo_db_dir.resolve()):
            with self.assertRaises(RuntimeError) as ctx:
                main.create_app(production=True)
        self.assertIn("db/ directory", str(ctx.exception))

    def test_bot_db_outside_repo_db_dir_is_accepted_in_production(self):
        fake_repo_db_dir = self.tmp / "db"
        fake_repo_db_dir.mkdir(parents=True, exist_ok=True)
        snapshot_dir = self.tmp / "snapshot"
        snapshot_dir.mkdir()
        snapshot_db = snapshot_dir / "bot.db"
        shutil.copy(self.bot_db, snapshot_db)

        dist_tmp = Path(tempfile.mkdtemp(prefix="stocksage_dist_guard2_"))
        self.addCleanup(shutil.rmtree, dist_tmp, True)
        dist = _make_dist(dist_tmp)

        with mock.patch.dict(
            os.environ,
            {"WEBAPP_FRONTEND_DIST_PATH": str(dist), "STOCKSAGE_DB_PATH": str(snapshot_db)},
        ), mock.patch.object(settings, "repo_db_dir", return_value=fake_repo_db_dir.resolve()):
            app = main.create_app(production=True)
            with TestClient(app) as client:
                resp = client.get("/api/health")
            self.assertEqual(resp.status_code, 200)

    def test_bot_db_inside_repo_db_dir_is_fine_in_dev_mode(self):
        fake_repo_db_dir = self.tmp / "db"
        fake_repo_db_dir.mkdir(parents=True, exist_ok=True)
        fake_live_db = fake_repo_db_dir / "stocksage.db"
        shutil.copy(self.bot_db, fake_live_db)

        with mock.patch.dict(os.environ, {"STOCKSAGE_DB_PATH": str(fake_live_db)}), mock.patch.object(
            settings, "repo_db_dir", return_value=fake_repo_db_dir.resolve()
        ):
            app = main.create_app(production=False)
            with TestClient(app) as client:
                resp = client.get("/api/health")
            self.assertEqual(resp.status_code, 200)


if __name__ == "__main__":
    unittest.main()
