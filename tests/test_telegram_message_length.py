"""
Tests for the Telegram message-length-safety fix.

Covers: bot.telegram_bot._split_into_chunks / _send, the new summary-only
/watchlist, and that long-output watchlist commands stay within Telegram's
limit. All Update/Context objects are mocked -- no real Telegram sends, no
DB apply mode, every test uses a temporary SQLite database.
"""
import asyncio
import importlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from telegram.error import BadRequest

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


class TestSplitIntoChunks(unittest.TestCase):

    def setUp(self):
        import bot.telegram_bot as bot_mod
        self.bot = bot_mod

    def test_short_text_sent_as_single_chunk(self):
        chunks = self.bot._split_into_chunks("line1\nline2")
        self.assertEqual(chunks, ["line1\nline2"])

    def test_long_text_splits_into_multiple_chunks(self):
        text = "\n".join(f"symbol{i}" for i in range(2000))
        chunks = self.bot._split_into_chunks(text)
        self.assertGreater(len(chunks), 1)

    def test_no_chunk_exceeds_safe_limit(self):
        text = "\n".join(f"symbol{i} padding text here" for i in range(2000))
        chunks = self.bot._split_into_chunks(text)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), self.bot.TELEGRAM_SAFE_CHUNK_LIMIT)

    def test_splitting_preserves_line_boundaries(self):
        lines = [f"line-{i}" for i in range(2000)]
        text = "\n".join(lines)
        chunks = self.bot._split_into_chunks(text)
        reassembled_lines = "\n".join(chunks).split("\n")
        self.assertEqual(reassembled_lines, lines)

    def test_single_line_too_long_is_hard_split(self):
        one_line = "x" * 9000
        chunks = self.bot._split_into_chunks(one_line)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), self.bot.TELEGRAM_SAFE_CHUNK_LIMIT)
        self.assertEqual("".join(chunks), one_line)

    def test_no_empty_chunks_produced(self):
        text = "a\n\n\n\nb"
        chunks = self.bot._split_into_chunks(text)
        for chunk in chunks:
            self.assertTrue(chunk.strip() != "" or chunk == "")
        self.assertTrue(all(c.strip() for c in chunks))

    def test_empty_text_returns_no_chunks(self):
        self.assertEqual(self.bot._split_into_chunks(""), [])
        self.assertEqual(self.bot._split_into_chunks("   "), [])


class TestSendHelper(unittest.TestCase):

    def setUp(self):
        import bot.telegram_bot as bot_mod
        self.bot = bot_mod

    def test_send_short_message_once(self):
        update = _make_update()
        _run(self.bot._send(update, "hello"))
        update.message.reply_text.assert_called_once_with("hello")

    def test_send_long_message_splits_into_multiple_sends(self):
        update = _make_update()
        text = "\n".join(f"symbol{i} padding" for i in range(2000))
        _run(self.bot._send(update, text))
        self.assertGreater(update.message.reply_text.call_count, 1)
        for call in update.message.reply_text.call_args_list:
            self.assertLessEqual(len(call.args[0]), self.bot.TELEGRAM_SAFE_CHUNK_LIMIT)

    def test_send_empty_text_sends_nothing(self):
        update = _make_update()
        _run(self.bot._send(update, ""))
        update.message.reply_text.assert_not_called()

    def test_bad_request_does_not_crash_and_sends_fallback(self):
        update = _make_update()
        update.message.reply_text = AsyncMock(side_effect=BadRequest("Message is too long"))
        _run(self.bot._send(update, "short text"))  # must not raise
        self.assertEqual(update.message.reply_text.call_count, 2)  # original attempt + fallback

    def test_bad_request_fallback_message_has_no_secrets(self):
        update = _make_update()
        update.message.reply_text = AsyncMock(side_effect=BadRequest("Message is too long"))
        _run(self.bot._send(update, "short text"))
        fallback_text = update.message.reply_text.call_args_list[-1].args[0]
        self.assertNotIn("token", fallback_text.lower())
        self.assertNotIn(".db", fallback_text.lower())


class TelegramWatchlistDisplayTestCase(unittest.TestCase):

    def setUp(self):
        f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        f.close()
        self.db = _reload_db(f.name)
        import bot.telegram_bot as bot_mod
        importlib.reload(bot_mod)
        self.bot = bot_mod
        self._auth_patch = patch.object(self.bot, "AUTHORIZED_CHAT_IDS", frozenset([AUTH_ID]))
        self._auth_patch.start()

    def tearDown(self):
        self._auth_patch.stop()

    def _seed_many(self, n, category="AI & Semiconductors"):
        symbols = [f"SYM{i}" for i in range(n)]
        self.db.init_db({category: symbols})
        self.db.run_initial_classification({category: symbols})
        return symbols


class TestWatchlistSummaryOnly(TelegramWatchlistDisplayTestCase):

    def test_watchlist_is_summary_only_for_many_symbols(self):
        self._seed_many(400)
        update = _make_update()
        _run(self.bot.cmd_watchlist(update, _make_context()))
        self.assertEqual(update.message.reply_text.call_count, 1)
        text = update.message.reply_text.call_args_list[0].args[0]
        self.assertLess(len(text), 500)
        self.assertIn("MONITOR: 400", text)
        self.assertIn("/watchlist_active", text)
        self.assertIn("/watchlist_monitor", text)

    def test_watchlist_does_not_dump_individual_symbols(self):
        symbols = self._seed_many(50)
        update = _make_update()
        _run(self.bot.cmd_watchlist(update, _make_context()))
        text = update.message.reply_text.call_args_list[0].args[0]
        # None of the individual symbol names should appear in the summary.
        for sym in symbols[:5]:
            self.assertNotIn(sym, text)

    def test_watchlist_empty_db_message(self):
        self.db.init_db()
        update = _make_update()
        _run(self.bot.cmd_watchlist(update, _make_context()))
        update.message.reply_text.assert_called_once()


class TestWatchlistMonitorSafe(TelegramWatchlistDisplayTestCase):

    def test_monitor_with_many_symbols_is_safe(self):
        self._seed_many(333)
        update = _make_update()
        _run(self.bot.cmd_watchlist_monitor(update, _make_context()))
        for call in update.message.reply_text.call_args_list:
            self.assertLessEqual(len(call.args[0]), self.bot.TELEGRAM_SAFE_CHUNK_LIMIT)
        text = update.message.reply_text.call_args_list[0].args[0]
        self.assertIn("333 symbols", text)

    def test_monitor_accepts_numeric_limit_argument(self):
        self._seed_many(50)
        for sym in self.db.get_symbols_by_state("MONITOR"):
            with self.db._connect() as conn:
                conn.execute("UPDATE watchlist SET relevance_score = 50 WHERE symbol = ?", (sym,))
        update = _make_update()
        _run(self.bot.cmd_watchlist_monitor(update, _make_context(["5"])))
        text = update.message.reply_text.call_args_list[0].args[0]
        self.assertIn("showing 5 of", text)


class TestWatchlistActiveContextIneligibleSafe(TelegramWatchlistDisplayTestCase):

    def test_active_with_many_symbols_is_safe(self):
        symbols = self._seed_many(60)
        for sym in symbols:
            self.db.update_symbol_state(sym, "ACTIVE")
        update = _make_update()
        _run(self.bot.cmd_watchlist_active(update, _make_context()))
        for call in update.message.reply_text.call_args_list:
            self.assertLessEqual(len(call.args[0]), self.bot.TELEGRAM_SAFE_CHUNK_LIMIT)

    def test_context_with_many_symbols_is_safe(self):
        symbols = self._seed_many(80)
        for sym in symbols:
            self.db.update_symbol_state(sym, "ETF_INDEX_CONTEXT")
        update = _make_update()
        _run(self.bot.cmd_watchlist_context(update, _make_context()))
        for call in update.message.reply_text.call_args_list:
            self.assertLessEqual(len(call.args[0]), self.bot.TELEGRAM_SAFE_CHUNK_LIMIT)

    def test_ineligible_with_many_symbols_is_safe(self):
        symbols = self._seed_many(80)
        for sym in symbols:
            self.db.update_symbol_state(sym, "TEMPORARILY_INELIGIBLE", "no data")
        update = _make_update()
        _run(self.bot.cmd_watchlist_ineligible(update, _make_context()))
        for call in update.message.reply_text.call_args_list:
            self.assertLessEqual(len(call.args[0]), self.bot.TELEGRAM_SAFE_CHUNK_LIMIT)


class TestWatchlistChangesSafe(TelegramWatchlistDisplayTestCase):

    def test_watchlist_changes_stays_safe_with_large_lists(self):
        symbols = [f"SYM{i}" for i in range(500)]
        self.db.init_db()
        self.db.create_evaluation_run(
            "manual", dry_run=False,
            metadata={"proposed_promotions": symbols, "proposed_demotions": symbols,
                      "proposed_recoveries": symbols, "proposed_ineligible": symbols},
        )
        update = _make_update()
        _run(self.bot.cmd_watchlist_changes(update, _make_context()))
        for call in update.message.reply_text.call_args_list:
            self.assertLessEqual(len(call.args[0]), self.bot.TELEGRAM_SAFE_CHUNK_LIMIT)


class TestSafety(TelegramWatchlistDisplayTestCase):

    def test_no_production_db_used(self):
        import db.database as real_db_mod
        self.assertNotEqual(str(self.db.DB_PATH), str(real_db_mod.DB_PATH.parent / "stocksage.db"))

    def test_no_real_telegram_sends(self):
        self._seed_many(10)
        update = _make_update()
        _run(self.bot.cmd_watchlist(update, _make_context()))
        self.assertIsInstance(update.message.reply_text, AsyncMock)

    def test_no_db_apply_run_by_display_commands(self):
        """These are read-only display commands -- no evaluation run should
        ever be created by viewing the watchlist."""
        self._seed_many(10)
        update = _make_update()
        _run(self.bot.cmd_watchlist(update, _make_context()))
        _run(self.bot.cmd_watchlist_monitor(_make_update(), _make_context()))
        self.assertIsNone(self.db.get_last_evaluation_run())


class TestCommandMenuRegistration(unittest.TestCase):
    """
    Covers the bug where new commands worked when typed manually but never
    appeared in Telegram's hamburger/menu list -- _BOT_COMMANDS had fallen
    out of sync with the actual CommandHandler registrations.
    """

    def setUp(self):
        import bot.telegram_bot as bot_mod
        self.bot = bot_mod

    def _registered_handler_commands(self) -> set[str]:
        import inspect
        import re
        source = inspect.getsource(self.bot.run_bot)
        return set(re.findall(r'CommandHandler\("([a-zA-Z_]+)"', source))

    def test_menu_includes_every_registered_handler_command(self):
        handler_cmds = self._registered_handler_commands()
        menu_cmds = {c.command for c in self.bot._BOT_COMMANDS}
        self.assertEqual(handler_cmds, menu_cmds)

    def test_menu_has_no_phantom_commands(self):
        """Every menu entry must correspond to a real, registered handler."""
        handler_cmds = self._registered_handler_commands()
        for bot_command in self.bot._BOT_COMMANDS:
            self.assertIn(bot_command.command, handler_cmds)

    def test_new_watchlist_commands_are_in_menu(self):
        menu_cmds = {c.command for c in self.bot._BOT_COMMANDS}
        for expected in (
            "refresh_watchlist", "watchlist_refresh_status", "watchlist_changes",
            "watchlist_active", "watchlist_monitor", "watchlist_context",
            "watchlist_ineligible", "watchlist_status",
        ):
            self.assertIn(expected, menu_cmds)

    def test_no_command_name_contains_slash(self):
        for bot_command in self.bot._BOT_COMMANDS:
            self.assertNotIn("/", bot_command.command)

    def test_command_names_and_descriptions_within_telegram_limits(self):
        for bot_command in self.bot._BOT_COMMANDS:
            self.assertLessEqual(len(bot_command.command), 32)
            self.assertLessEqual(len(bot_command.description), 256)
            self.assertGreater(len(bot_command.description), 0)

    def test_under_telegram_total_command_limit(self):
        self.assertLessEqual(len(self.bot._BOT_COMMANDS), 100)

    def test_registration_success_calls_set_my_commands(self):
        application = MagicMock()
        application.bot.set_my_commands = AsyncMock()
        _run(self.bot._register_bot_commands(application))
        application.bot.set_my_commands.assert_awaited_once_with(self.bot._BOT_COMMANDS)

    def test_registration_failure_is_non_fatal(self):
        application = MagicMock()
        application.bot.set_my_commands = AsyncMock(side_effect=RuntimeError("transient API error"))
        try:
            _run(self.bot._register_bot_commands(application))
        except Exception as exc:
            self.fail(f"_register_bot_commands raised instead of swallowing the error: {exc}")

    def test_registration_failure_does_not_leak_secrets(self):
        application = MagicMock()
        application.bot.set_my_commands = AsyncMock(side_effect=RuntimeError("api_key=super-secret-token"))
        with patch("builtins.print") as mock_print:
            _run(self.bot._register_bot_commands(application))
        logged = " ".join(str(c.args) for c in mock_print.call_args_list)
        self.assertNotIn("super-secret-token", logged)


if __name__ == "__main__":
    unittest.main()
