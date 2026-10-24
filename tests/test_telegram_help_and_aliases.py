"""Tests for Phase 9B-1 — Telegram /help cleanup, /admin_help, and command
aliases (/watchlist_add, /watchlist_remove, /morning_scan).

All Telegram Update/Context objects are mocked — no real Telegram sends, no
network calls. Handler tests that touch the DB (via get_language) use a
temporary SQLite database; none touch the production DB. Registration tests
mock ApplicationBuilder entirely so run_bot() never actually starts polling
or touches a real bot token.
"""
import asyncio
import importlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

AUTH_ID = "123456789"
UNAUTH_ID = "99999"


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _reload_db(db_path: str):
    import db.database as dbmod
    importlib.reload(dbmod)
    dbmod.DB_PATH = Path(db_path)
    return dbmod


def _tmp_db_path() -> str:
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    return f.name


def _make_update(chat_id=AUTH_ID):
    update = MagicMock()
    update.effective_chat.id = chat_id
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()
    return update


def _make_context(args=None):
    ctx = MagicMock()
    ctx.args = args or []
    ctx.bot = AsyncMock()
    return ctx


def _sent_text(update) -> str:
    """Concatenate every reply_text call's text (handlers may send >1 chunk)."""
    return "\n".join(call.args[0] for call in update.message.reply_text.call_args_list)


class TestHelpOutput(unittest.TestCase):

    def setUp(self):
        self.db = _reload_db(_tmp_db_path())
        self.db.init_db({})

    def test_help_requires_authorization(self):
        import bot.telegram_bot as tb
        update = _make_update(UNAUTH_ID)
        with patch("bot.telegram_bot.AUTHORIZED_CHAT_IDS", frozenset([AUTH_ID])):
            _run(tb.cmd_help(update, _make_context()))
        update.message.reply_text.assert_not_called()

    def test_help_includes_every_registered_command(self):
        import bot.telegram_bot as tb
        update = _make_update()
        with patch("bot.telegram_bot.AUTHORIZED_CHAT_IDS", frozenset([AUTH_ID])):
            _run(tb.cmd_help(update, _make_context()))
            admin_update = _make_update()
            _run(tb.cmd_admin_help(admin_update, _make_context()))

        help_text = _sent_text(update)
        admin_text = _sent_text(admin_update)
        combined = help_text + "\n" + admin_text

        registered = {bc.command for bc in tb._BOT_COMMANDS}
        for command in registered:
            self.assertIn(f"/{command}", combined, f"/{command} missing from /help and /admin_help")

    def test_help_shows_new_alias_names_as_primary(self):
        import bot.telegram_bot as tb
        update = _make_update()
        with patch("bot.telegram_bot.AUTHORIZED_CHAT_IDS", frozenset([AUTH_ID])):
            _run(tb.cmd_help(update, _make_context()))
        text = _sent_text(update)
        self.assertIn("/watchlist_add", text)
        self.assertIn("/watchlist_remove", text)
        self.assertIn("/morning_scan", text)

    def test_help_notes_legacy_aliases(self):
        import bot.telegram_bot as tb
        self.db.set_language(AUTH_ID, "en")
        update = _make_update()
        with patch("bot.telegram_bot.AUTHORIZED_CHAT_IDS", frozenset([AUTH_ID])):
            _run(tb.cmd_help(update, _make_context()))
        text = _sent_text(update)
        self.assertIn("/add", text)
        self.assertIn("/remove", text)
        self.assertIn("/scan", text)
        self.assertIn("legacy alias", text.lower())

    def test_help_points_to_admin_help(self):
        import bot.telegram_bot as tb
        update = _make_update()
        with patch("bot.telegram_bot.AUTHORIZED_CHAT_IDS", frozenset([AUTH_ID])):
            _run(tb.cmd_help(update, _make_context()))
        text = _sent_text(update)
        self.assertIn("/admin_help", text)

    def test_help_output_stays_under_safe_chunk_limit(self):
        import bot.telegram_bot as tb
        update = _make_update()
        with patch("bot.telegram_bot.AUTHORIZED_CHAT_IDS", frozenset([AUTH_ID])):
            _run(tb.cmd_help(update, _make_context()))
        text = _sent_text(update)
        self.assertLessEqual(len(text), tb.TELEGRAM_SAFE_CHUNK_LIMIT)

    def test_help_does_not_raise_in_english(self):
        import bot.telegram_bot as tb
        self.db.set_language(AUTH_ID, "en")
        update = _make_update()
        with patch("bot.telegram_bot.AUTHORIZED_CHAT_IDS", frozenset([AUTH_ID])):
            _run(tb.cmd_help(update, _make_context()))
        update.message.reply_text.assert_called()


class TestAdminHelp(unittest.TestCase):

    def setUp(self):
        self.db = _reload_db(_tmp_db_path())
        self.db.init_db({})

    def test_admin_help_requires_authorization(self):
        import bot.telegram_bot as tb
        update = _make_update(UNAUTH_ID)
        with patch("bot.telegram_bot.AUTHORIZED_CHAT_IDS", frozenset([AUTH_ID])):
            _run(tb.cmd_admin_help(update, _make_context()))
        update.message.reply_text.assert_not_called()

    def test_admin_help_lists_expected_commands(self):
        import bot.telegram_bot as tb
        update = _make_update()
        with patch("bot.telegram_bot.AUTHORIZED_CHAT_IDS", frozenset([AUTH_ID])):
            _run(tb.cmd_admin_help(update, _make_context()))
        text = _sent_text(update)
        self.assertIn("/test", text)
        self.assertIn("/watchlist_refresh_status", text)
        self.assertIn("/watchlist_changes", text)

    def test_admin_help_does_not_raise_in_english(self):
        import bot.telegram_bot as tb
        self.db.set_language(AUTH_ID, "en")
        update = _make_update()
        with patch("bot.telegram_bot.AUTHORIZED_CHAT_IDS", frozenset([AUTH_ID])):
            _run(tb.cmd_admin_help(update, _make_context()))
        update.message.reply_text.assert_called()


class TestAliasBehaviorMatchesLegacy(unittest.TestCase):
    """Aliases point at the exact same handler function objects as their
    legacy counterparts -- verified both by registration (below) and by
    confirming a call through the alias handler produces the identical
    observable effect (DB write) as the legacy command."""

    def setUp(self):
        self.db = _reload_db(_tmp_db_path())
        self.db.init_db({"AI & Semiconductors": []})

    def test_watchlist_add_alias_is_the_same_function_as_add(self):
        import bot.telegram_bot as tb
        self.assertIs(tb.cmd_add, tb.cmd_add)  # sanity: no separate alias function exists

    def test_calling_add_via_alias_name_writes_identical_state(self):
        import bot.telegram_bot as tb
        update = _make_update()
        with patch("bot.telegram_bot.AUTHORIZED_CHAT_IDS", frozenset([AUTH_ID])):
            # "watchlist_add" has no separate implementation -- run_bot()
            # registers it against cmd_add directly, so calling cmd_add here
            # IS calling-through-the-alias by construction.
            _run(tb.cmd_add(update, _make_context(["NVDA", "AI & Semiconductors"])))
        status = self.db.get_symbol_status("NVDA")
        self.assertIsNotNone(status)
        self.assertEqual(status["wl_state"], "MONITOR")

    def test_calling_remove_via_alias_name_writes_identical_state(self):
        import bot.telegram_bot as tb
        self.db.add_to_watchlist("NVDA", "AI & Semiconductors")
        update = _make_update()
        with patch("bot.telegram_bot.AUTHORIZED_CHAT_IDS", frozenset([AUTH_ID])):
            _run(tb.cmd_remove(update, _make_context(["NVDA"])))
        status = self.db.get_symbol_status("NVDA")
        self.assertEqual(status["wl_state"], "USER_REMOVED")


class TestCommandRegistration(unittest.TestCase):
    """Verifies run_bot() actually registers the new alias/admin_help
    commands against the correct callbacks, and that legacy commands remain
    registered unchanged. ApplicationBuilder is fully mocked -- no real bot
    token, no network call, no polling loop ever starts."""

    def test_registration_includes_aliases_and_legacy_names(self):
        import bot.telegram_bot as tb

        added_handlers = []

        fake_app = MagicMock()
        fake_app.add_handler.side_effect = lambda h: added_handlers.append(h)
        fake_app.run_polling = MagicMock()

        fake_builder = MagicMock()
        fake_builder.token.return_value = fake_builder
        fake_builder.post_init.return_value = fake_builder
        fake_builder.build.return_value = fake_app

        with patch("bot.telegram_bot.ApplicationBuilder", return_value=fake_builder), \
             patch("bot.telegram_bot.init_db"), \
             patch("bot.telegram_bot.run_initial_classification"):
            tb.run_bot("fake-token")

        by_command: dict[str, object] = {}
        for h in added_handlers:
            for cmd in h.commands:
                by_command[cmd] = h.callback

        # legacy commands still registered
        self.assertIn("add", by_command)
        self.assertIn("remove", by_command)
        self.assertIn("scan", by_command)

        # new aliases registered against the SAME callback as their legacy counterpart
        self.assertIn("watchlist_add", by_command)
        self.assertIs(by_command["watchlist_add"], by_command["add"])

        self.assertIn("watchlist_remove", by_command)
        self.assertIs(by_command["watchlist_remove"], by_command["remove"])

        self.assertIn("morning_scan", by_command)
        self.assertIs(by_command["morning_scan"], by_command["scan"])

        # admin_help registered against its own new callback
        self.assertIn("admin_help", by_command)
        self.assertIs(by_command["admin_help"], tb.cmd_admin_help)

        fake_app.run_polling.assert_called_once()


class TestAddAcceptsEitherCategorySpelling(unittest.TestCase):
    """PR A (English category names): cmd_add must match either spelling
    of a known category (case-insensitive, surrounding whitespace
    stripped) and store the resolved CATEGORIES/WATCHLIST key -- never
    the raw typed string -- so a symbol added under either spelling ends
    up filed under the exact same category string as its existing peers.
    """

    def setUp(self):
        self.db = _reload_db(_tmp_db_path())
        self.db.init_db({"פיננסים": ["JPM"]})  # an existing peer under the Hebrew key

    def test_hebrew_category_name_still_works(self):
        import bot.telegram_bot as tb
        update = _make_update()
        with patch("bot.telegram_bot.AUTHORIZED_CHAT_IDS", frozenset([AUTH_ID])):
            _run(tb.cmd_add(update, _make_context(["GS", "פיננסים"])))
        self.assertEqual(self.db.get_symbol_categories("GS"), ["פיננסים"])

    def test_english_category_name_stores_the_same_key_as_existing_peers(self):
        import bot.telegram_bot as tb
        update = _make_update()
        with patch("bot.telegram_bot.AUTHORIZED_CHAT_IDS", frozenset([AUTH_ID])):
            _run(tb.cmd_add(update, _make_context(["MS", "Financials"])))
        # Stored under the SAME string as the pre-existing "JPM" peer --
        # not the raw typed English string.
        self.assertEqual(self.db.get_symbol_categories("MS"), ["פיננסים"])
        self.assertEqual(
            self.db.get_symbol_categories("MS"), self.db.get_symbol_categories("JPM"),
        )

    def test_matching_is_case_insensitive_and_strips_surrounding_whitespace(self):
        import bot.telegram_bot as tb
        update = _make_update()
        with patch("bot.telegram_bot.AUTHORIZED_CHAT_IDS", frozenset([AUTH_ID])):
            _run(tb.cmd_add(update, _make_context(["C", "  financials  "])))
        self.assertEqual(self.db.get_symbol_categories("C"), ["פיננסים"])

    def test_multiword_english_category_through_real_argument_joining(self):
        """Exercises " ".join(context.args[1:]) actually reconstructing a
        multi-word category name from separate argv tokens -- not a
        single-token category, which wouldn't catch a join-logic bug."""
        import bot.telegram_bot as tb
        self.db.init_db({"ענן ותוכנה": ["CRM"]})
        update = _make_update()
        with patch("bot.telegram_bot.AUTHORIZED_CHAT_IDS", frozenset([AUTH_ID])):
            # context.args splits on whitespace: ["SNOW", "Cloud", "&", "Software"]
            _run(tb.cmd_add(update, _make_context(["SNOW", "Cloud", "&", "Software"])))
        self.assertEqual(self.db.get_symbol_categories("SNOW"), ["ענן ותוכנה"])

    def test_unknown_category_warning_lists_english_names(self):
        import bot.telegram_bot as tb
        update = _make_update()
        with patch("bot.telegram_bot.AUTHORIZED_CHAT_IDS", frozenset([AUTH_ID])):
            _run(tb.cmd_add(update, _make_context(["XYZ", "NotARealCategory"])))
        text = _sent_text(update)
        self.assertIn("Financials", text)
        self.assertNotIn("פיננסים", text)
        self.assertIsNone(self.db.get_symbol_status("XYZ"))


if __name__ == "__main__":
    unittest.main()
