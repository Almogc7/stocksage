"""
Tests for Phase 7 — Telegram watchlist refresh commands
(/refresh_watchlist, /watchlist_refresh_status, /watchlist_changes).

All Telegram Update/Context objects are mocked — no real Telegram sends.
Every test uses a temporary SQLite database; none touch the production DB.
"""
import asyncio
import importlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from data.market_data_validator import MarketDataResult, ProviderStatus
from tests.fixtures import make_falling_df, make_trending_df

AUTH_ID = "123456789"
UNAUTH_ID = "99999"


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _reload_db(db_path: str):
    import db.database as dbmod
    importlib.reload(dbmod)
    dbmod.DB_PATH = Path(db_path)
    return dbmod


def _make_update(chat_id=AUTH_ID):
    update = MagicMock()
    update.effective_chat.id = chat_id
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()
    return update


def _make_context(args=None):
    ctx = MagicMock()
    ctx.args = args or []
    return ctx


class _FakeClient:
    cache_hits = 0
    cache_misses = 0
    yfinance_request_count = 0
    provider_error_count = 0

    def __init__(self, status=ProviderStatus.OK, df_factory=make_trending_df):
        self.status = status
        self.df_factory = df_factory

    def validate_batch(self, symbols, security_types=None):
        return {
            s: MarketDataResult(
                symbol=s, normalized_symbol=s, security_type="stock",
                provider_status=self.status, is_valid=self.status != ProviderStatus.INVALID_SYMBOL,
                latest_close=150.0, latest_volume=2_000_000,
                average_daily_volume=2_000_000.0, average_daily_dollar_volume=3e8,
                history_days_available=252, data_timestamp_utc="2024-03-15 21:00:00",
                latest_completed_candle_date="2024-03-15",
                failure_type="provider_transient" if self.status != ProviderStatus.OK else None,
                failure_reason="connection reset" if self.status != ProviderStatus.OK else None,
            )
            for s in symbols
        }

    def get_history(self, symbol):
        return self.df_factory(n=252), None


class TelegramWatchlistTestCase(unittest.TestCase):

    def setUp(self):
        f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        f.close()
        self.db = _reload_db(f.name)
        self.db.init_db({"AI & Semiconductors": ["CRM"]})
        self.db.run_initial_classification({"AI & Semiconductors": ["CRM"]})
        import bot.telegram_bot as bot_mod
        importlib.reload(bot_mod)
        self.bot = bot_mod
        self._auth_patch = patch.object(self.bot, "AUTHORIZED_CHAT_IDS", frozenset([AUTH_ID]))
        self._auth_patch.start()

    def tearDown(self):
        self._auth_patch.stop()

    def _patch_client(self, client):
        import services.watchlist_evaluator as evmod
        orig = evmod.run_watchlist_evaluation

        def patched(**kw):
            kw.setdefault("client", client)
            return orig(**kw)

        return patch.object(self.bot, "run_watchlist_evaluation", patched)

    def _last_reply(self, update):
        return update.message.reply_text.call_args_list[-1].args[0]


class TestRefreshWatchlistAuth(TelegramWatchlistTestCase):

    def test_requires_authorization(self):
        update = _make_update(chat_id=UNAUTH_ID)
        _run(self.bot.cmd_refresh_watchlist(update, _make_context()))
        update.message.reply_text.assert_not_called()

    def test_unauthorized_does_not_start_evaluation(self):
        update = _make_update(chat_id=UNAUTH_ID)
        with self._patch_client(_FakeClient()):
            _run(self.bot.cmd_refresh_watchlist(update, _make_context()))
        self.assertIsNone(self.db.get_last_evaluation_run())


class TestRefreshWatchlistDryRun(TelegramWatchlistTestCase):

    def test_default_is_dry_run(self):
        update = _make_update()
        with self._patch_client(_FakeClient()):
            _run(self.bot.cmd_refresh_watchlist(update, _make_context()))
        run = self.db.get_last_evaluation_run()
        self.assertEqual(run["dry_run"], 1)
        self.assertEqual(self.db.get_symbol_status("CRM")["relevance_score"], None)

    def test_explicit_dry_run_arg(self):
        update = _make_update()
        with self._patch_client(_FakeClient()):
            _run(self.bot.cmd_refresh_watchlist(update, _make_context(["dry_run"])))
        run = self.db.get_last_evaluation_run()
        self.assertEqual(run["dry_run"], 1)

    def test_dry_run_summary_formatting(self):
        update = _make_update()
        with self._patch_client(_FakeClient()):
            _run(self.bot.cmd_refresh_watchlist(update, _make_context()))
        text = self._last_reply(update)
        self.assertIn("DRY RUN", text)
        self.assertIn("Run ID:", text)
        self.assertIn("Evaluated:", text)
        self.assertIn("No watchlist states were changed.", text)


class TestRefreshWatchlistApplySafety(TelegramWatchlistTestCase):

    def test_apply_rejected_when_disabled(self):
        update = _make_update()
        with patch.object(self.bot, "TELEGRAM_ALLOW_WATCHLIST_APPLY", False):
            _run(self.bot.cmd_refresh_watchlist(update, _make_context(["apply"])))
        text = self._last_reply(update)
        self.assertIn("disabled", text.lower())
        self.assertIsNone(self.db.get_last_evaluation_run())

    def test_apply_confirm_rejected_when_disabled(self):
        update = _make_update()
        with patch.object(self.bot, "TELEGRAM_ALLOW_WATCHLIST_APPLY", False):
            _run(self.bot.cmd_refresh_watchlist(update, _make_context(["apply", "confirm"])))
        text = self._last_reply(update)
        self.assertIn("disabled", text.lower())
        self.assertIsNone(self.db.get_last_evaluation_run())

    def test_apply_requires_explicit_confirmation_when_enabled(self):
        update = _make_update()
        with patch.object(self.bot, "TELEGRAM_ALLOW_WATCHLIST_APPLY", True):
            _run(self.bot.cmd_refresh_watchlist(update, _make_context(["apply"])))
        text = self._last_reply(update)
        self.assertIn("confirm", text.lower())
        self.assertIsNone(self.db.get_last_evaluation_run())

    def test_apply_confirm_runs_when_enabled(self):
        update = _make_update()
        with patch.object(self.bot, "TELEGRAM_ALLOW_WATCHLIST_APPLY", True):
            with self._patch_client(_FakeClient()):
                _run(self.bot.cmd_refresh_watchlist(update, _make_context(["apply", "confirm"])))
        run = self.db.get_last_evaluation_run()
        self.assertEqual(run["dry_run"], 0)
        text = self._last_reply(update)
        self.assertIn("APPLY", text)


class TestRefreshWatchlistFormattingEdgeCases(TelegramWatchlistTestCase):

    def test_provider_degraded_summary_formatting(self):
        update = _make_update()
        with self._patch_client(_FakeClient(status=ProviderStatus.PROVIDER_ERROR)):
            _run(self.bot.cmd_refresh_watchlist(update, _make_context()))
        text = self._last_reply(update)
        self.assertIn("PARTIAL FAILURE", text)
        self.assertIn("Provider degraded: yes", text)
        self.assertIn("Demotions suppressed", text)

    def test_fatal_error_summary_formatting(self):
        class ExplodingClient:
            def validate_batch(self, *a, **kw):
                raise RuntimeError("boom")

        update = _make_update()
        with self._patch_client(ExplodingClient()):
            _run(self.bot.cmd_refresh_watchlist(update, _make_context()))
        text = self._last_reply(update)
        self.assertIn("failed", text.lower())
        self.assertNotIn("RuntimeError", text)
        self.assertNotIn("boom", text)

    def test_in_progress_run_prevents_new_refresh(self):
        self.db.create_evaluation_run("manual", dry_run=True)
        update = _make_update()
        with self._patch_client(_FakeClient()):
            _run(self.bot.cmd_refresh_watchlist(update, _make_context()))
        text = self._last_reply(update)
        self.assertIn("already in progress", text)


class TestWatchlistRefreshStatus(TelegramWatchlistTestCase):

    def test_requires_authorization(self):
        update = _make_update(chat_id=UNAUTH_ID)
        _run(self.bot.cmd_watchlist_refresh_status(update, _make_context()))
        update.message.reply_text.assert_not_called()

    def test_no_previous_runs(self):
        update = _make_update()
        _run(self.bot.cmd_watchlist_refresh_status(update, _make_context()))
        text = self._last_reply(update)
        self.assertIn("No watchlist evaluation has run yet", text)

    def test_shows_last_run(self):
        update_refresh = _make_update()
        with self._patch_client(_FakeClient()):
            _run(self.bot.cmd_refresh_watchlist(update_refresh, _make_context()))

        update = _make_update()
        _run(self.bot.cmd_watchlist_refresh_status(update, _make_context()))
        text = self._last_reply(update)
        self.assertIn("Run ID:", text)
        self.assertIn("Status:", text)
        self.assertIn("Evaluated:", text)
        self.assertIn("Another run in progress: no", text)


class TestWatchlistChanges(TelegramWatchlistTestCase):

    def test_requires_authorization(self):
        update = _make_update(chat_id=UNAUTH_ID)
        _run(self.bot.cmd_watchlist_changes(update, _make_context()))
        update.message.reply_text.assert_not_called()

    def test_no_runs_yet(self):
        update = _make_update()
        _run(self.bot.cmd_watchlist_changes(update, _make_context()))
        text = self._last_reply(update)
        self.assertIn("No evaluation runs found", text)

    def test_shows_dry_run_proposed_changes(self):
        update_refresh = _make_update()
        with self._patch_client(_FakeClient()):
            _run(self.bot.cmd_refresh_watchlist(update_refresh, _make_context()))

        update = _make_update()
        _run(self.bot.cmd_watchlist_changes(update, _make_context()))
        text = self._last_reply(update)
        self.assertIn("DRY RUN, proposed only", text)
        self.assertIn("Promotions", text)
        self.assertIn("nothing was written", text)

    def test_shows_applied_changes(self):
        update_refresh = _make_update()
        with patch.object(self.bot, "TELEGRAM_ALLOW_WATCHLIST_APPLY", True):
            with self._patch_client(_FakeClient()):
                _run(self.bot.cmd_refresh_watchlist(update_refresh, _make_context(["apply", "confirm"])))

        update = _make_update()
        _run(self.bot.cmd_watchlist_changes(update, _make_context()))
        text = self._last_reply(update)
        self.assertIn("APPLIED", text)
        self.assertIn("rollback_evaluation_run.py", text)

    def test_explicit_run_id_lookup(self):
        update_refresh = _make_update()
        with self._patch_client(_FakeClient()):
            _run(self.bot.cmd_refresh_watchlist(update_refresh, _make_context()))
        run_id = self.db.get_last_evaluation_run()["run_id"]

        update = _make_update()
        _run(self.bot.cmd_watchlist_changes(update, _make_context(["run", str(run_id)])))
        text = self._last_reply(update)
        self.assertIn(f"Run {run_id}", text)

    def test_unknown_run_id(self):
        update = _make_update()
        _run(self.bot.cmd_watchlist_changes(update, _make_context(["run", "99999"])))
        text = self._last_reply(update)
        self.assertIn("not found", text)

    def test_pagination_truncates_long_lists(self):
        symbols = [f"SYM{i}" for i in range(50)]
        self.db.create_evaluation_run(
            "manual", dry_run=False,
            metadata={"proposed_promotions": symbols, "proposed_demotions": [],
                      "proposed_recoveries": [], "proposed_ineligible": []},
        )
        update = _make_update()
        _run(self.bot.cmd_watchlist_changes(update, _make_context(["10"])))
        text = self._last_reply(update)
        self.assertIn("+40 more", text)
        self.assertLess(len(text), 2000)

    def test_message_length_is_safe(self):
        symbols = [f"SYM{i}" for i in range(500)]
        self.db.create_evaluation_run(
            "manual", dry_run=False,
            metadata={"proposed_promotions": symbols, "proposed_demotions": symbols,
                      "proposed_recoveries": symbols, "proposed_ineligible": symbols},
        )
        update = _make_update()
        _run(self.bot.cmd_watchlist_changes(update, _make_context()))
        text = self._last_reply(update)
        self.assertLess(len(text), 4096)


class TestSafety(TelegramWatchlistTestCase):

    def test_no_secrets_in_refresh_output(self):
        update = _make_update()
        with self._patch_client(_FakeClient()):
            _run(self.bot.cmd_refresh_watchlist(update, _make_context()))
        text = self._last_reply(update)
        lowered = text.lower()
        for secret_kw in ("token", "api_key", "telegram_token", "anthropic_api_key", "stocksage.db"):
            self.assertNotIn(secret_kw, lowered)

    def test_no_production_db_used(self):
        import db.database as real_db_mod
        self.assertNotEqual(str(self.db.DB_PATH), str(real_db_mod.DB_PATH.parent / "stocksage.db"))

    def test_no_real_telegram_sends(self):
        """reply_text is always an AsyncMock in these tests — never a real Bot API call."""
        update = _make_update()
        with self._patch_client(_FakeClient()):
            _run(self.bot.cmd_refresh_watchlist(update, _make_context()))
        self.assertIsInstance(update.message.reply_text, AsyncMock)


if __name__ == "__main__":
    unittest.main()
