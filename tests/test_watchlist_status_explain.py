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


def _make_short_history_client(n=126):
    """A client whose history is too short for EMA150 (needs 150+ rows) --
    reproduces the exact TSLA bug: ta.trend.ema_indicator returns NaN, and
    `current_price > NaN` is unconditionally False in Python.

    _FakeClient.get_history() always calls df_factory(n=252) explicitly,
    so the factory below must ignore whatever n it's given and always
    return the short dataframe."""
    return _FakeClient(df_factory=lambda n=None: make_trending_df(n=126))


class TestEmaVetoAccuracy(ExplainSymbolTestCase):
    """
    Regression tests for the EMA150/veto-label bug: insufficient history
    must never be mislabeled as "price below EMA150", and NaN must never
    reach a numeric field that a formatter would render as the string
    "nan" instead of a clean N/A.
    """

    def test_insufficient_history_does_not_claim_price_below_ema150(self):
        self._seed({"AI & Semiconductors": ["TSLA"]})
        out = self.ev.explain_symbol("TSLA", client=_make_short_history_client())
        self.assertNotEqual(out["opportunity"]["vetoed"], "price below EMA150")

    def test_insufficient_history_produces_accurate_veto_message(self):
        self._seed({"AI & Semiconductors": ["TSLA"]})
        out = self.ev.explain_symbol("TSLA", client=_make_short_history_client())
        self.assertIn("insufficient EMA150 data", out["opportunity"]["vetoed"])

    def test_insufficient_history_ema150_is_none_not_nan(self):
        self._seed({"AI & Semiconductors": ["TSLA"]})
        out = self.ev.explain_symbol("TSLA", client=_make_short_history_client())
        ema150 = out["opportunity"]["ema150"]
        self.assertIsNone(ema150)  # never a NaN float that would format as "nan"

    def test_explain_symbol_defaults_to_one_year_history_window(self):
        """The primary fix: explain_symbol's own default client must request
        enough history to compute EMA150/EMA200 for an ordinary symbol like
        TSLA, instead of inheriting the 6-month default used elsewhere."""
        self._seed({"AI & Semiconductors": ["TSLA"]})
        captured = {}
        orig_init = self.ev.MarketDataClient.__init__

        def spy_init(self_client, *a, **kw):
            captured["period"] = kw.get("period")
            orig_init(self_client, *a, **kw)

        with patch.object(self.ev.MarketDataClient, "__init__", spy_init):
            try:
                self.ev.explain_symbol("TSLA")
            except Exception:
                pass  # network call may fail in CI; we only care what period was requested
        self.assertEqual(captured.get("period"), "1y")

    def test_normal_symbol_with_enough_history_shows_real_ema_values(self):
        self._seed({"AI & Semiconductors": ["TSLA"]})
        out = self.ev.explain_symbol("TSLA", client=_FakeClient(df_factory=lambda n=252: make_trending_df(n=n)))
        self.assertIsNotNone(out["opportunity"]["ema150"])
        self.assertIsNotNone(out["opportunity"]["ema200"])
        self.assertNotIn("insufficient", out["opportunity"]["vetoed"] or "")

    def test_does_not_modify_watchlist_state(self):
        self._seed({"AI & Semiconductors": ["TSLA"]})
        before = self.db.get_symbol_status("TSLA")
        self.ev.explain_symbol("TSLA", client=_make_short_history_client())
        after = self.db.get_symbol_status("TSLA")
        self.assertEqual(before, after)

    def test_does_not_create_evaluation_run(self):
        self._seed({"AI & Semiconductors": ["TSLA"]})
        self.ev.explain_symbol("TSLA", client=_make_short_history_client())
        self.assertIsNone(self.db.get_last_evaluation_run())


class TestWatchlistStatusFormatterEmaDisplay(unittest.TestCase):
    """Telegram-formatter-level checks: a None EMA150/200 must render as
    "N/A", never as the literal string "nan"."""

    def setUp(self):
        f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        f.close()
        self.db = _reload_db(f.name)
        self.db.init_db({"AI & Semiconductors": ["TSLA"]})
        self.db.run_initial_classification({"AI & Semiconductors": ["TSLA"]})
        import bot.telegram_bot as bot_mod
        importlib.reload(bot_mod)
        self.bot = bot_mod
        self._auth_patch = patch.object(self.bot, "AUTHORIZED_CHAT_IDS", frozenset([AUTH_ID]))
        self._auth_patch.start()

    def tearDown(self):
        self._auth_patch.stop()

    def test_missing_ema150_renders_as_na_not_nan(self):
        update = _make_update()
        with patch("services.watchlist_evaluator.MarketDataClient", return_value=_make_short_history_client()):
            _run(self.bot.cmd_watchlist_status(update, _make_context(["TSLA"])))
        text = update.message.reply_text.call_args_list[0].args[0]
        self.assertNotIn("nan", text.lower())
        self.assertIn("N/A", text)
        self.assertIn("insufficient EMA150 data", text)

    def test_normal_symbol_shows_formatted_numeric_ema150(self):
        update = _make_update()
        with patch("services.watchlist_evaluator.MarketDataClient",
                    return_value=_FakeClient(df_factory=lambda n=252: make_trending_df(n=n))):
            _run(self.bot.cmd_watchlist_status(update, _make_context(["TSLA"])))
        text = update.message.reply_text.call_args_list[0].args[0]
        self.assertNotIn("nan", text.lower())
        self.assertRegex(text, r"EMA150: [\d,]+\.\d\d")


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
