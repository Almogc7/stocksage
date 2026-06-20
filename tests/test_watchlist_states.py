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

    def test_restart_does_not_reset_promoted_symbol_to_monitor(self):
        """A MONITOR symbol dynamically promoted to ACTIVE must survive a restart."""
        path = self._tmp()
        db = _reload_db(path)
        wl = {'AI & Semiconductors': ['CRM']}  # not in INITIAL_ACTIVE_SET
        db.init_db(wl)
        db.run_initial_classification(wl)
        self.assertEqual(db.get_symbol_status('CRM')['wl_state'], 'MONITOR')

        db.update_symbol_state('CRM', 'ACTIVE')

        db2 = _reload_db(path)
        db2.init_db(wl)
        db2.run_initial_classification(wl)
        self.assertEqual(db2.get_symbol_status('CRM')['wl_state'], 'ACTIVE')

    def test_restart_does_not_reset_demoted_seed_symbol_to_active(self):
        """A hardcoded-seed ACTIVE symbol dynamically demoted must stay demoted on restart."""
        path = self._tmp()
        db = _reload_db(path)
        wl = {'AI & Semiconductors': ['NVDA']}  # in INITIAL_ACTIVE_SET
        db.init_db(wl)
        db.run_initial_classification(wl)
        self.assertEqual(db.get_symbol_status('NVDA')['wl_state'], 'ACTIVE')

        db.update_symbol_state('NVDA', 'MONITOR')

        db2 = _reload_db(path)
        db2.init_db(wl)
        db2.run_initial_classification(wl)
        self.assertEqual(db2.get_symbol_status('NVDA')['wl_state'], 'MONITOR')

    def test_restart_preserves_scores_and_hysteresis_counters(self):
        path = self._tmp()
        db = _reload_db(path)
        wl = {'AI & Semiconductors': ['NVDA']}
        db.init_db(wl)
        db.run_initial_classification(wl)
        db.update_eligibility('NVDA', score=77, security_type='stock', state='ACTIVE')
        db.update_hysteresis('NVDA', promote_delta=1, demote_delta=0)

        db2 = _reload_db(path)
        db2.init_db(wl)
        db2.run_initial_classification(wl)
        status = db2.get_symbol_status('NVDA')
        self.assertEqual(status['relevance_score'], 77)
        self.assertEqual(status['consec_promote_count'], 1)

    def test_run_initial_classification_is_idempotent_per_symbol(self):
        """Calling it twice in a row must not re-flip a symbol back to its seed state."""
        path = self._tmp()
        db = _reload_db(path)
        wl = {'AI & Semiconductors': ['CRM']}
        db.init_db(wl)
        db.run_initial_classification(wl)
        db.update_symbol_state('CRM', 'ACTIVE')

        db.run_initial_classification(wl)
        self.assertEqual(db.get_symbol_status('CRM')['wl_state'], 'ACTIVE')

    def test_new_symbol_added_to_config_gets_classified_without_disturbing_others(self):
        """A symbol newly added to config.py after go-live gets classified; existing rows untouched."""
        path = self._tmp()
        db = _reload_db(path)
        wl = {'AI & Semiconductors': ['CRM']}
        db.init_db(wl)
        db.run_initial_classification(wl)
        db.update_symbol_state('CRM', 'ACTIVE')

        wl2 = {'AI & Semiconductors': ['CRM'], 'ETFs': ['SPY']}
        db.populate_from_config(wl2)
        db.run_initial_classification(wl2)

        self.assertEqual(db.get_symbol_status('CRM')['wl_state'], 'ACTIVE')
        self.assertEqual(db.get_symbol_status('SPY')['wl_state'], 'ETF_INDEX_CONTEXT')

    def test_db_upgrade_backfills_classified_without_reclassifying(self):
        """Simulates upgrading a real DB that predates wl_classified: a row with
        a dynamically-promoted ACTIVE state, built on the v2 schema (no
        wl_classified column yet), must keep its ACTIVE state — not be reset
        to MONITOR by the hardcoded seed rules — once migrate_db() adds the
        column and backfills it."""
        import sqlite3
        path = self._tmp()

        conn = sqlite3.connect(path)
        conn.execute("""
            CREATE TABLE watchlist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL UNIQUE,
                category TEXT NOT NULL,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                enabled INTEGER NOT NULL DEFAULT 1,
                removed_at TIMESTAMP DEFAULT NULL,
                wl_state TEXT NOT NULL DEFAULT 'MONITOR',
                security_type TEXT DEFAULT 'stock',
                relevance_score INTEGER DEFAULT NULL,
                last_evaluated TIMESTAMP DEFAULT NULL,
                last_promoted TIMESTAMP DEFAULT NULL,
                last_demoted TIMESTAMP DEFAULT NULL,
                exclusion_reason TEXT DEFAULT NULL,
                reeval_date DATE DEFAULT NULL,
                consec_promote_count INTEGER DEFAULT 0,
                consec_demote_count INTEGER DEFAULT 0,
                dwell_days INTEGER DEFAULT 0,
                source TEXT DEFAULT 'config'
            )
        """)
        conn.execute(
            "INSERT INTO watchlist (symbol, category, wl_state) VALUES ('CRM', 'AI & Semiconductors', 'ACTIVE')"
        )
        conn.commit()
        conn.close()

        db = _reload_db(path)
        db.init_db({'AI & Semiconductors': ['CRM']})

        status = db.get_symbol_status('CRM')
        self.assertEqual(status['wl_classified'], 1)
        self.assertEqual(status['wl_state'], 'ACTIVE')

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
