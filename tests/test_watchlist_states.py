"""Tests for watchlist state persistence, multi-category, and Telegram tier handlers."""
import asyncio
import importlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _reload_db(db_path: str):
    """Reload db.database and point its DB_PATH at db_path."""
    import db.database as dbmod
    importlib.reload(dbmod)
    dbmod.DB_PATH = Path(db_path)
    return dbmod


class TestStatePersistence(unittest.TestCase):

    def _tmp(self):
        f = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        f.close()
        return f.name

    def test_state_change_survives_reload(self):
        path = self._tmp()
        db = _reload_db(path)
        db.init_db({'AI & Semiconductors': ['NVDA']})
        db.update_symbol_state('NVDA', 'ACTIVE')

        db2 = _reload_db(path)
        status = db2.get_symbol_status('NVDA')
        self.assertEqual(status['wl_state'], 'ACTIVE')

    def test_get_active_watchlist_excludes_monitor(self):
        path = self._tmp()
        db = _reload_db(path)
        db.init_db({'AI & Semiconductors': ['NVDA', 'AMD']})
        db.update_symbol_state('NVDA', 'ACTIVE')
        wl = db.get_active_watchlist()
        all_syms = [s for syms in wl.values() for s in syms]
        self.assertIn('NVDA', all_syms)
        self.assertNotIn('AMD', all_syms)

    def test_get_symbols_by_state(self):
        path = self._tmp()
        db = _reload_db(path)
        db.init_db({'AI & Semiconductors': ['NVDA', 'AMD']})
        db.update_symbol_state('NVDA', 'ACTIVE')
        self.assertIn('AMD', db.get_symbols_by_state('MONITOR'))
        self.assertIn('NVDA', db.get_symbols_by_state('ACTIVE'))

    def test_update_symbol_state_with_reason(self):
        path = self._tmp()
        db = _reload_db(path)
        db.init_db({'AI & Semiconductors': ['NVDA']})
        db.update_symbol_state('NVDA', 'TEMPORARILY_INELIGIBLE', 'no data')
        status = db.get_symbol_status('NVDA')
        self.assertEqual(status['wl_state'], 'TEMPORARILY_INELIGIBLE')
        self.assertEqual(status['exclusion_reason'], 'no data')

    def test_add_category_tag(self):
        path = self._tmp()
        db = _reload_db(path)
        db.init_db({'AI & Semiconductors': ['NVDA']})
        db.add_category_tag('NVDA', 'מגה טק')
        cats = db.get_symbol_categories('NVDA')
        self.assertIn('מגה טק', cats)
        self.assertIn('AI & Semiconductors', cats)

    def test_multi_category_seed_both_entries(self):
        path = self._tmp()
        db = _reload_db(path)
        db.init_db({'AI & Semiconductors': ['NVDA'], 'מגה טק': ['NVDA']})
        cats = db.get_symbol_categories('NVDA')
        self.assertIn('AI & Semiconductors', cats)
        self.assertIn('מגה טק', cats)

    def test_no_duplicate_in_active_watchlist(self):
        """A symbol in two categories should appear exactly once in active list."""
        path = self._tmp()
        db = _reload_db(path)
        db.init_db({'Cat A': ['NVDA'], 'Cat B': ['NVDA']})
        db.update_symbol_state('NVDA', 'ACTIVE')
        wl = db.get_active_watchlist()
        all_syms = [s for syms in wl.values() for s in syms]
        self.assertEqual(all_syms.count('NVDA'), 1)

    def test_remove_sets_user_removed_state(self):
        path = self._tmp()
        db = _reload_db(path)
        db.init_db({'AI & Semiconductors': ['NVDA']})
        db.remove_from_watchlist('NVDA')
        status = db.get_symbol_status('NVDA')
        self.assertEqual(status['wl_state'], 'USER_REMOVED')
        self.assertEqual(status['enabled'], 0)

    def test_add_after_remove_restores_monitor(self):
        path = self._tmp()
        db = _reload_db(path)
        db.init_db({'AI & Semiconductors': ['NVDA']})
        db.remove_from_watchlist('NVDA')
        db.add_to_watchlist('NVDA', 'AI & Semiconductors')
        status = db.get_symbol_status('NVDA')
        self.assertEqual(status['wl_state'], 'MONITOR')
        self.assertEqual(status['enabled'], 1)

    def test_run_initial_classification_etfs(self):
        path = self._tmp()
        db = _reload_db(path)
        db.init_db({'ETFs': ['SPY', 'QQQ'], 'AI & Semiconductors': ['NVDA']})
        db.run_initial_classification({'ETFs': ['SPY', 'QQQ'], 'AI & Semiconductors': ['NVDA']})
        self.assertEqual(db.get_symbol_status('SPY')['wl_state'], 'ETF_INDEX_CONTEXT')
        # NVDA is in INITIAL_ACTIVE_SET
        self.assertEqual(db.get_symbol_status('NVDA')['wl_state'], 'ACTIVE')

    def test_run_initial_classification_user_removed_preserved(self):
        path = self._tmp()
        db = _reload_db(path)
        db.init_db({'AI & Semiconductors': ['NVDA']})
        db.remove_from_watchlist('NVDA')
        db.run_initial_classification({'AI & Semiconductors': ['NVDA']})
        status = db.get_symbol_status('NVDA')
        self.assertEqual(status['wl_state'], 'USER_REMOVED')

    def test_increment_dwell_days(self):
        path = self._tmp()
        db = _reload_db(path)
        db.init_db({'AI & Semiconductors': ['NVDA']})
        db.update_symbol_state('NVDA', 'ACTIVE')
        db.increment_dwell_days()
        status = db.get_symbol_status('NVDA')
        self.assertEqual(status['dwell_days'], 1)

    def test_get_watchlist_summary(self):
        path = self._tmp()
        db = _reload_db(path)
        db.init_db({'AI & Semiconductors': ['NVDA'], 'ETFs': ['SPY']})
        db.update_symbol_state('NVDA', 'ACTIVE')
        db.update_symbol_state('SPY', 'ETF_INDEX_CONTEXT')
        summary = db.get_watchlist_summary()
        self.assertGreaterEqual(summary.get('ACTIVE', 0), 1)
        self.assertGreaterEqual(summary.get('ETF_INDEX_CONTEXT', 0), 1)


class TestTierHandlerAuth(unittest.TestCase):
    """New watchlist tier handlers must reject unauthorized callers silently."""

    AUTH_IDS = frozenset(['123456789'])

    def _make_update(self, chat_id='99999'):
        update = MagicMock()
        update.effective_chat.id = chat_id
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()
        return update

    def _make_context(self, args=None):
        ctx = MagicMock()
        ctx.args = args or []
        ctx.bot = AsyncMock()
        return ctx

    def _assert_rejected(self, handler, args=None):
        """Unauthorized call must result in reply_text never being called."""
        update = self._make_update(chat_id='99999')
        ctx = self._make_context(args=args)
        with patch('bot.telegram_bot.AUTHORIZED_CHAT_IDS', frozenset(['123456789'])):
            _run(handler(update, ctx))
        update.message.reply_text.assert_not_called()

    def test_watchlist_active_rejects_unauthorized(self):
        from bot.telegram_bot import cmd_watchlist_active
        self._assert_rejected(cmd_watchlist_active)

    def test_watchlist_monitor_rejects_unauthorized(self):
        from bot.telegram_bot import cmd_watchlist_monitor
        self._assert_rejected(cmd_watchlist_monitor)

    def test_watchlist_context_rejects_unauthorized(self):
        from bot.telegram_bot import cmd_watchlist_context
        self._assert_rejected(cmd_watchlist_context)

    def test_watchlist_ineligible_rejects_unauthorized(self):
        from bot.telegram_bot import cmd_watchlist_ineligible
        self._assert_rejected(cmd_watchlist_ineligible)

    def test_watchlist_status_rejects_unauthorized(self):
        from bot.telegram_bot import cmd_watchlist_status
        self._assert_rejected(cmd_watchlist_status, args=['NVDA'])


if __name__ == '__main__':
    unittest.main()
