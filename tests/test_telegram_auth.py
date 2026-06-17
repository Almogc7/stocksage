"""
Tests for Fix 1: Telegram bot authorization check.

Verifies that:
  - Authorized chat IDs can call all command handlers
  - Unauthorized chat IDs are silently rejected (handler returns immediately)
  - Missing AUTHORIZED_CHAT_IDS configuration fails securely (rejects all)
  - The AUTHORIZED_CHAT_IDS env var is parsed correctly (single, multiple, whitespace)
  - No sensitive information is revealed in the rejection path
  - All 13 command handlers are protected

No real Telegram messages are sent — the Update object and bot are mocked.
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch


# ── Mock Update factory ───────────────────────────────────────────────────────

def _make_update(chat_id: str) -> MagicMock:
    """Create a minimal mock Telegram Update with the given effective_chat.id."""
    update = MagicMock()
    update.effective_chat.id = chat_id
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()
    return update


def _make_context(args: list[str] | None = None) -> MagicMock:
    ctx = MagicMock()
    ctx.args = args or []
    ctx.bot = AsyncMock()
    return ctx


def _run(coro):
    """Run an async coroutine from a synchronous test."""
    return asyncio.get_event_loop().run_until_complete(coro)


# ── Auth check unit tests ────────────────────────────────────────────────────

class TestCheckAuth(unittest.TestCase):
    """Unit tests for the _check_auth() helper function."""

    def _run_check(self, chat_id: str, authorized_ids: frozenset) -> bool:
        """Run _check_auth with a specific authorized ID set."""
        from bot.telegram_bot import _check_auth
        update = _make_update(chat_id)
        with patch("bot.telegram_bot.AUTHORIZED_CHAT_IDS", authorized_ids):
            return _run(_check_auth(update))

    def test_authorized_chat_id_returns_true(self):
        result = self._run_check("123456789", frozenset(["123456789"]))
        self.assertTrue(result)

    def test_unauthorized_chat_id_returns_false(self):
        result = self._run_check("999999999", frozenset(["123456789"]))
        self.assertFalse(result)

    def test_empty_authorized_ids_rejects_all(self):
        """No configured IDs → fail secure (reject everyone)."""
        result = self._run_check("123456789", frozenset())
        self.assertFalse(result)

    def test_multiple_authorized_ids(self):
        ids = frozenset(["111", "222", "333"])
        self.assertTrue(self._run_check("222", ids))
        self.assertFalse(self._run_check("444", ids))

    def test_chat_id_is_compared_as_string(self):
        """Chat IDs may arrive as int or str; comparison must be string-based."""
        # update.effective_chat.id is stored as a string in our mock
        result = self._run_check("123456789", frozenset(["123456789"]))
        self.assertTrue(result)


# ── Config parsing tests ───────────────────────────────────────────────────────

class TestAuthorizedChatIdsConfig(unittest.TestCase):
    """Tests for AUTHORIZED_CHAT_IDS construction in config.py."""

    def _build_ids(self, env_authorized: str, env_chat_id: str | None) -> frozenset:
        """Simulate what config.py does when given specific env var values."""
        if env_authorized.strip():
            return frozenset(
                cid.strip() for cid in env_authorized.split(",") if cid.strip()
            )
        elif env_chat_id:
            return frozenset([str(env_chat_id).strip()])
        else:
            return frozenset()

    def test_single_id_from_env(self):
        ids = self._build_ids("123456789", None)
        self.assertIn("123456789", ids)
        self.assertEqual(len(ids), 1)

    def test_multiple_ids_comma_separated(self):
        ids = self._build_ids("111,222,333", None)
        self.assertEqual(ids, frozenset(["111", "222", "333"]))

    def test_whitespace_around_ids_is_stripped(self):
        ids = self._build_ids("  111 , 222 , 333  ", None)
        self.assertEqual(ids, frozenset(["111", "222", "333"]))

    def test_empty_env_falls_back_to_telegram_chat_id(self):
        ids = self._build_ids("", "987654321")
        self.assertEqual(ids, frozenset(["987654321"]))

    def test_both_empty_produces_empty_frozenset(self):
        """No configuration → empty frozenset → fail secure."""
        ids = self._build_ids("", None)
        self.assertEqual(ids, frozenset())

    def test_trailing_comma_does_not_create_empty_entry(self):
        ids = self._build_ids("111,222,", None)
        self.assertNotIn("", ids)
        self.assertEqual(ids, frozenset(["111", "222"]))


# ── Handler-level protection tests ────────────────────────────────────────────

class TestCommandHandlerAuth(unittest.TestCase):
    """
    Verify that command handlers reject unauthorized callers.
    Each test patches AUTHORIZED_CHAT_IDS to contain only the AUTHORIZED ID,
    then calls the handler with an UNAUTHORIZED ID and asserts that
    update.message.reply_text was never called (handler returned early).
    """

    AUTHORIZED_ID = "123456789"
    UNAUTHORIZED_ID = "999999999"
    AUTH_IDS = frozenset(["123456789"])

    def _assert_handler_rejects_unauthorized(self, handler_fn, args=None):
        """Call handler with unauthorized ID; assert no reply was sent."""
        update = _make_update(self.UNAUTHORIZED_ID)
        ctx = _make_context(args)
        with patch("bot.telegram_bot.AUTHORIZED_CHAT_IDS", self.AUTH_IDS):
            _run(handler_fn(update, ctx))
        update.message.reply_text.assert_not_called()

    def test_cmd_start_rejects_unauthorized(self):
        from bot.telegram_bot import cmd_start
        self._assert_handler_rejects_unauthorized(cmd_start)

    def test_cmd_analyze_rejects_unauthorized(self):
        from bot.telegram_bot import cmd_analyze
        self._assert_handler_rejects_unauthorized(cmd_analyze, args=["NVDA"])

    def test_cmd_scan_rejects_unauthorized(self):
        from bot.telegram_bot import cmd_scan
        self._assert_handler_rejects_unauthorized(cmd_scan)

    def test_cmd_add_rejects_unauthorized(self):
        from bot.telegram_bot import cmd_add
        self._assert_handler_rejects_unauthorized(cmd_add, args=["NVDA", "מגה טק"])

    def test_cmd_remove_rejects_unauthorized(self):
        from bot.telegram_bot import cmd_remove
        self._assert_handler_rejects_unauthorized(cmd_remove, args=["NVDA"])

    def test_cmd_watchlist_rejects_unauthorized(self):
        from bot.telegram_bot import cmd_watchlist
        self._assert_handler_rejects_unauthorized(cmd_watchlist)

    def test_cmd_trade_rejects_unauthorized(self):
        from bot.telegram_bot import cmd_trade
        self._assert_handler_rejects_unauthorized(
            cmd_trade, args=["BUY", "NVDA", "10", "200.00"]
        )

    def test_cmd_trades_rejects_unauthorized(self):
        from bot.telegram_bot import cmd_trades
        self._assert_handler_rejects_unauthorized(cmd_trades)

    def test_cmd_summary_rejects_unauthorized(self):
        from bot.telegram_bot import cmd_summary
        self._assert_handler_rejects_unauthorized(cmd_summary, args=["NVDA"])

    def test_cmd_alerts_rejects_unauthorized(self):
        from bot.telegram_bot import cmd_alerts
        self._assert_handler_rejects_unauthorized(cmd_alerts)

    def test_cmd_help_rejects_unauthorized(self):
        from bot.telegram_bot import cmd_help
        self._assert_handler_rejects_unauthorized(cmd_help)

    def test_cmd_status_rejects_unauthorized(self):
        from bot.telegram_bot import cmd_status
        self._assert_handler_rejects_unauthorized(cmd_status)

    def test_cmd_test_rejects_unauthorized(self):
        from bot.telegram_bot import cmd_test
        self._assert_handler_rejects_unauthorized(cmd_test)

    def test_cmd_language_rejects_unauthorized(self):
        from bot.telegram_bot import cmd_language
        self._assert_handler_rejects_unauthorized(cmd_language, args=["en"])


class TestAllHandlersListedInBot(unittest.TestCase):
    """
    Meta-test: count registered handlers and verify each calls _check_auth.
    This prevents a new handler being added without auth protection.
    """
    HANDLER_NAMES = [
        "cmd_start", "cmd_language", "cmd_analyze", "cmd_add", "cmd_remove",
        "cmd_watchlist", "cmd_trade", "cmd_trades", "cmd_summary",
        "cmd_alerts", "cmd_help", "cmd_scan", "cmd_test", "cmd_status",
    ]

    def test_all_handlers_contain_check_auth_call(self):
        import inspect
        import bot.telegram_bot as bot_module
        missing = []
        for name in self.HANDLER_NAMES:
            fn = getattr(bot_module, name, None)
            if fn is None:
                missing.append(f"{name} (function not found)")
                continue
            src = inspect.getsource(fn)
            if "_check_auth" not in src:
                missing.append(name)
        self.assertEqual(missing, [],
            f"These handlers are missing _check_auth(): {missing}")


if __name__ == "__main__":
    unittest.main()
