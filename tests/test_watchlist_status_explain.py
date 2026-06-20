"""
Tests for the explainability improvement to services.watchlist_evaluator.
explain_symbol() and the enriched /watchlist_status Telegram command.

Read-only by design: explain_symbol() never writes to the watchlist table
and is not a dry-run/apply evaluation. All Update/Context objects are
mocked -- no real Telegram sends, no DB apply mode, every test uses a
temporary SQLite database.
"""
import asyncio
import importlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from data.market_data_validator import MarketDataResult, ProviderStatus
from tests.fixtures import make_trending_df

AUTH_ID = "123456789"


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _reload_db(db_path: str):
    import db.database as dbmod
    importlib.reload(dbmod)
    dbmod.DB_PATH = Path(db_path)
    return dbmod


def _make_update():
    update = MagicMock()
    update.effective_chat.id = AUTH_ID
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()
    return update


def _make_context(args=None):
    ctx = MagicMock()
    ctx.args = args or []
    return ctx


class _FakeClient:
    """Duck-typed MarketDataClient stand-in with a controlled response."""

    def __init__(self, status=ProviderStatus.OK, failure_reason=None, df_factory=make_trending_df):
        self.status = status
        self.failure_reason = failure_reason
        self.df_factory = df_factory

    def validate(self, symbol, security_type=None):
        return MarketDataResult(
            symbol=symbol, normalized_symbol=symbol, security_type=security_type or "stock",
            provider_status=self.status, is_valid=self.status == ProviderStatus.OK,
            latest_close=150.0, latest_volume=2_000_000,
            average_daily_volume=2_000_000.0, average_daily_dollar_volume=3e8,
            history_days_available=252, data_timestamp_utc="2024-03-15 21:00:00",
            latest_completed_candle_date="2024-03-15",
            failure_reason=self.failure_reason,
        )

    def get_history(self, symbol):
        return self.df_factory(n=252), None


class ExplainSymbolTestCase(unittest.TestCase):

    def setUp(self):
        f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        f.close()
        self.db = _reload_db(f.name)
        import services.watchlist_evaluator as evmod
        importlib.reload(evmod)
        self.ev = evmod

    def _seed(self, wl):
        self.db.init_db(wl)
        self.db.run_initial_classification(wl)


class TestExplainSymbolFunction(ExplainSymbolTestCase):

    def test_unknown_symbol_returns_not_found(self):
        self._seed({"AI & Semiconductors": ["CRM"]})
        out = self.ev.explain_symbol("NOPE", client=_FakeClient())
        self.assertFalse(out["found"])

    def test_includes_lifecycle_state(self):
        self._seed({"AI & Semiconductors": ["CRM"]})
        out = self.ev.explain_symbol("CRM", client=_FakeClient())
        self.assertEqual(out["lifecycle"]["state"], "MONITOR")

    def test_includes_relevance_components(self):
        self._seed({"AI & Semiconductors": ["CRM"]})
        out = self.ev.explain_symbol("CRM", client=_FakeClient())
        comp = out["relevance"]["components"]
        for key in ("data_quality", "liquidity", "trend", "momentum", "proximity", "volatility"):
            self.assertIn(key, comp)
            self.assertGreaterEqual(comp[key], 0.0)
            self.assertLessEqual(comp[key], 1.0)

    def test_includes_opportunity_breakdown(self):
        self._seed({"AI & Semiconductors": ["CRM"]})
        out = self.ev.explain_symbol("CRM", client=_FakeClient())
        self.assertIsNotNone(out["opportunity"])
        self.assertIn("score", out["opportunity"])
        self.assertIn("signals", out["opportunity"])

    def test_does_not_persist_anything(self):
        self._seed({"AI & Semiconductors": ["CRM"]})
        before = self.db.get_symbol_status("CRM")
        self.ev.explain_symbol("CRM", client=_FakeClient())
        after = self.db.get_symbol_status("CRM")
        self.assertEqual(before, after)

    def test_does_not_create_evaluation_run(self):
        self._seed({"AI & Semiconductors": ["CRM"]})
        self.ev.explain_symbol("CRM", client=_FakeClient())
        self.assertIsNone(self.db.get_last_evaluation_run())

    def test_failed_data_fetch_reports_failure_reason(self):
        self._seed({"AI & Semiconductors": ["CRM"]})
        out = self.ev.explain_symbol(
            "CRM", client=_FakeClient(status=ProviderStatus.PROVIDER_ERROR, failure_reason="connection reset")
        )
        self.assertFalse(out["data_ok"])
        self.assertEqual(out["failure_reason"], "connection reset")
        self.assertIsNone(out["relevance"])
        self.assertIsNone(out["opportunity"])

    def test_etf_shows_permanent_context_state(self):
        self._seed({"ETFs": ["SPY"]})
        out = self.ev.explain_symbol("SPY", client=_FakeClient(df_factory=make_trending_df))
        # Even though a live score is computed, ETFs never promote to ACTIVE.
        self.assertEqual(out["would_be_state"], "ETF_INDEX_CONTEXT")

    def test_user_removed_stays_removed_in_hypothetical(self):
        self._seed({"AI & Semiconductors": ["CRM"]})
        self.db.remove_from_watchlist("CRM")
        out = self.ev.explain_symbol("CRM", client=_FakeClient())
        self.assertEqual(out["would_be_state"], "USER_REMOVED")

    def test_temporarily_ineligible_recovery_never_shows_direct_active(self):
        self._seed({"AI & Semiconductors": ["CRM"]})
        self.db.update_symbol_state("CRM", "TEMPORARILY_INELIGIBLE", "test reason")
        with self.db._connect() as conn:
            conn.execute("UPDATE watchlist SET consec_promote_count = 5 WHERE symbol = 'CRM'")
        out = self.ev.explain_symbol("CRM", client=_FakeClient())
        self.assertEqual(out["would_be_state"], "MONITOR")
        self.assertIn("never directly to ACTIVE", out["would_be_reason"])


class TestWatchlistStatusCommand(unittest.TestCase):

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

    def _send_status(self, symbol, client=None):
        update = _make_update()
        with patch("services.watchlist_evaluator.MarketDataClient", return_value=client or _FakeClient()):
            _run(self.bot.cmd_watchlist_status(update, _make_context([symbol])))
        return update

    def test_requires_authorization(self):
        update = MagicMock()
        update.effective_chat.id = "99999"
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()
        _run(self.bot.cmd_watchlist_status(update, _make_context(["CRM"])))
        update.message.reply_text.assert_not_called()

    def test_output_includes_lifecycle_state(self):
        update = self._send_status("CRM")
        text = update.message.reply_text.call_args_list[0].args[0]
        self.assertIn("MONITOR", text)

    def test_output_includes_relevance_breakdown(self):
        update = self._send_status("CRM")
        text = update.message.reply_text.call_args_list[0].args[0]
        self.assertIn("Relevance score", text)
        self.assertIn("Data quality", text)

    def test_output_includes_opportunity_breakdown(self):
        update = self._send_status("CRM")
        text = update.message.reply_text.call_args_list[0].args[0]
        self.assertIn("Live opportunity score", text)
        self.assertIn("Veto:", text)

    def test_output_includes_not_a_buy_recommendation_warning(self):
        update = self._send_status("CRM")
        text = update.message.reply_text.call_args_list[0].args[0]
        self.assertIn("not a verified BUY recommendation", text)
        self.assertIn("No fundamentals, news, earnings calendar, or backtest", text)

    def test_failed_data_fetch_shows_unavailable_message(self):
        update = self._send_status("CRM", client=_FakeClient(status=ProviderStatus.PROVIDER_ERROR, failure_reason="boom"))
        text = update.message.reply_text.call_args_list[0].args[0]
        self.assertIn("Live data fetch failed", text)
        self.assertIn("breakdown unavailable", text)
        # The mandatory disclaimer must still be present even on failure.
        self.assertIn("not a verified BUY recommendation", text)

    def test_unknown_symbol_message_unchanged(self):
        update = _make_update()
        _run(self.bot.cmd_watchlist_status(update, _make_context(["NOPE"])))
        text = update.message.reply_text.call_args_list[0].args[0]
        self.assertNotIn("Relevance score", text)

    def test_output_stays_under_safe_telegram_limit(self):
        update = self._send_status("CRM")
        text = update.message.reply_text.call_args_list[0].args[0]
        self.assertLessEqual(len(text), self.bot.TELEGRAM_SAFE_CHUNK_LIMIT)

    def test_does_not_modify_watchlist_state(self):
        before = self.db.get_symbol_status("CRM")
        self._send_status("CRM")
        after = self.db.get_symbol_status("CRM")
        self.assertEqual(before, after)

    def test_does_not_create_evaluation_run(self):
        self._send_status("CRM")
        self.assertIsNone(self.db.get_last_evaluation_run())

    def test_no_real_telegram_sends(self):
        update = self._send_status("CRM")
        self.assertIsInstance(update.message.reply_text, AsyncMock)

    def test_explain_symbol_internal_error_does_not_crash_handler(self):
        update = _make_update()
        with patch.object(self.bot, "explain_symbol", side_effect=RuntimeError("boom")):
            _run(self.bot.cmd_watchlist_status(update, _make_context(["CRM"])))
        text = update.message.reply_text.call_args_list[0].args[0]
        self.assertIn("MONITOR", text)  # lifecycle section still shown
        self.assertIn("Live breakdown unavailable", text)


if __name__ == "__main__":
    unittest.main()
