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

    def test_ccc_reclassified_to_live_financials_key(self):
        """PR A (English category names): the CCC special case resolves
        the live Financials spelling dynamically from config.WATCHLIST
        (today still "פיננסים") -- never a hardcoded literal in
        production code, so this test resolves the expected value the
        same dynamic way rather than hardcoding it either."""
        import config
        expected_key = next(
            k for k in config.WATCHLIST if config.canonical_category(k) == "Financials"
        )
        path = self._tmp()
        db = _reload_db(path)
        db.init_db({'ענן ותוכנה': ['CCC']})  # seeded as cloud/software, same as the real bug it fixes
        db.run_initial_classification({'ענן ותוכנה': ['CCC']})
        status = db.get_symbol_status('CCC')
        self.assertEqual(status['category'], expected_key)
        self.assertEqual(db.get_symbol_categories('CCC'), [expected_key])

    def test_ccc_repeated_classification_passes_create_no_duplicate_rows(self):
        path = self._tmp()
        db = _reload_db(path)
        db.init_db({'ענן ותוכנה': ['CCC']})
        db.run_initial_classification({'ענן ותוכנה': ['CCC']})
        # Force a second pass over the same, now-already-Financials row
        # (simulating wl_classified being reset, e.g. by a future reseed)
        # -- the skip-write guard must leave symbol_categories with
        # exactly one row, never a duplicate.
        with db._connect() as conn:
            conn.execute("UPDATE watchlist SET wl_classified = 0 WHERE symbol = 'CCC'")
        db.run_initial_classification({'ענן ותוכנה': ['CCC']})
        self.assertEqual(db.get_symbol_categories('CCC'), ['פיננסים'])

    def test_ccc_already_under_english_financials_spelling_is_left_untouched(self):
        """A CCC row already stored under the ENGLISH "Financials"
        spelling (simulating a later world where that's live, or a
        manual edit) must be left completely untouched -- no UPDATE, no
        DELETE+INSERT -- since it already canonicalizes to Financials."""
        path = self._tmp()
        db = _reload_db(path)
        db.init_db({'ענן ותוכנה': ['CCC']})
        with db._connect() as conn:
            conn.execute(
                "UPDATE watchlist SET category = 'Financials', wl_classified = 0"
                " WHERE symbol = 'CCC'"
            )
            conn.execute("DELETE FROM symbol_categories WHERE symbol = 'CCC'")
            conn.execute(
                "INSERT INTO symbol_categories (symbol, category) VALUES ('CCC', 'Financials')"
            )
        db.run_initial_classification({'ענן ותוכנה': ['CCC']})
        status = db.get_symbol_status('CCC')
        self.assertEqual(status['category'], 'Financials')
        self.assertEqual(db.get_symbol_categories('CCC'), ['Financials'])

class TestAddToWatchlistCanonicalizeOnWrite(unittest.TestCase):
    """PR A.2: add_to_watchlist() must never overwrite an existing stored
    category spelling with the other spelling of the SAME category --
    config.WATCHLIST's keys stay Hebrew until a later PR, so without
    this, every /add (or dashboard add-UI) call on an already-migrated
    symbol would silently re-Hebraize it."""

    def _tmp(self):
        f = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        f.close()
        return f.name

    def test_new_symbol_keeps_the_given_spelling(self):
        path = self._tmp()
        db = _reload_db(path)
        db.init_db({})
        db.add_to_watchlist('XOM', 'Financials')
        status = db.get_symbol_status('XOM')
        self.assertEqual(status['category'], 'Financials')
        self.assertEqual(db.get_symbol_categories('XOM'), ['Financials'])

    def test_existing_symbol_hebrew_seed_english_call_keeps_hebrew(self):
        path = self._tmp()
        db = _reload_db(path)
        db.init_db({'פיננסים': ['JPM']})
        db.add_to_watchlist('JPM', 'Financials')
        status = db.get_symbol_status('JPM')
        self.assertEqual(status['category'], 'פיננסים')
        self.assertEqual(db.get_symbol_categories('JPM'), ['פיננסים'])

    def test_existing_symbol_english_seed_hebrew_call_keeps_english(self):
        """Synthetic "already migrated" fixture: watchlist.category is
        directly set to the English spelling (simulating a world after
        PR B has run), then called with the Hebrew spelling."""
        path = self._tmp()
        db = _reload_db(path)
        db.init_db({'פיננסים': ['GS']})
        with db._connect() as conn:
            conn.execute("UPDATE watchlist SET category = 'Financials' WHERE symbol = 'GS'")
            conn.execute("DELETE FROM symbol_categories WHERE symbol = 'GS'")
            conn.execute("INSERT INTO symbol_categories (symbol, category) VALUES ('GS', 'Financials')")
        db.add_to_watchlist('GS', 'פיננסים')
        status = db.get_symbol_status('GS')
        self.assertEqual(status['category'], 'Financials')
        self.assertEqual(db.get_symbol_categories('GS'), ['Financials'])

    def test_genuinely_different_category_moves_the_symbol(self):
        path = self._tmp()
        db = _reload_db(path)
        db.init_db({'AI & Semiconductors': ['MSTR']})
        db.add_to_watchlist('MSTR', 'Financials')
        status = db.get_symbol_status('MSTR')
        self.assertEqual(status['category'], 'Financials')

    def test_secondary_tag_other_spelling_keeps_stored_spelling_no_duplicate(self):
        """Primary category A, a SECONDARY symbol_categories tag under
        one spelling of category B -- calling add_to_watchlist with the
        OTHER spelling of B must keep the stored tag's spelling and
        create no duplicate-spelling row."""
        path = self._tmp()
        db = _reload_db(path)
        db.init_db({'AI & Semiconductors': ['NVDA'], 'מגה טק': ['NVDA']})
        db.add_to_watchlist('NVDA', 'Mega Cap Tech')
        cats = db.get_symbol_categories('NVDA')
        self.assertIn('מגה טק', cats)
        self.assertNotIn('Mega Cap Tech', cats)
        self.assertEqual(cats.count('מגה טק'), 1)
        # Primary category moves to the (existing-spelling) match, per
        # the preference-order rule: watchlist.category had no match,
        # so a symbol_categories tag match is used instead.
        self.assertEqual(db.get_symbol_status('NVDA')['category'], 'מגה טק')

    def test_primary_category_with_no_symbol_categories_row(self):
        """Data-drift fixture: a watchlist row exists but has NO
        corresponding symbol_categories row at all (as if seeded by a
        direct SQL edit, bypassing add_to_watchlist/_seed_symbol).
        Calling under the other spelling must keep the stored primary
        spelling and backfill exactly one symbol_categories row under
        THAT spelling, not two."""
        path = self._tmp()
        db = _reload_db(path)
        db.init_db({})
        with db._connect() as conn:
            conn.execute(
                "INSERT INTO watchlist (symbol, category, enabled) VALUES ('C', 'פיננסים', 1)"
            )
        self.assertEqual(db.get_symbol_categories('C'), [])  # confirm the drift fixture
        db.add_to_watchlist('C', 'Financials')
        status = db.get_symbol_status('C')
        self.assertEqual(status['category'], 'פיננסים')
        self.assertEqual(db.get_symbol_categories('C'), ['פיננסים'])

    def test_symbol_already_under_both_spellings_is_not_touched_into_a_third_state(self):
        """A symbol that already has BOTH spellings in
        symbol_categories (as CCC could, pre-PR-B) must not be touched
        into a third state -- the existing duplicate is left exactly as
        is; collapsing it is PR B's job, not this function's."""
        path = self._tmp()
        db = _reload_db(path)
        db.init_db({'פיננסים': ['SYM']})
        with db._connect() as conn:
            conn.execute(
                "INSERT INTO symbol_categories (symbol, category) VALUES ('SYM', 'Financials')"
            )
        db.add_to_watchlist('SYM', 'Financials')
        status = db.get_symbol_status('SYM')
        self.assertEqual(status['category'], 'פיננסים')  # primary wins the preference order
        cats = sorted(db.get_symbol_categories('SYM'))
        self.assertEqual(cats, ['Financials', 'פיננסים'])  # unchanged dual state, no third spelling

    def test_dashboard_style_call_path(self):
        """dashboard.py's add-UI calls add_to_watchlist directly with a
        value taken from config.CATEGORIES (st.selectbox(options=
        CATEGORIES)) -- same call shape, no Telegram involved. Proves
        the fix works identically regardless of caller."""
        import config
        path = self._tmp()
        db = _reload_db(path)
        db.init_db({'פיננסים': ['BAC']})
        dashboard_selected_category = next(
            c for c in config.CATEGORIES if config.canonical_category(c) == 'Financials'
        )
        db.add_to_watchlist('BAC', dashboard_selected_category)
        status = db.get_symbol_status('BAC')
        self.assertEqual(status['category'], 'פיננסים')
        self.assertEqual(db.get_symbol_categories('BAC'), ['פיננסים'])


class TestSeedSymbolCanonicalizeOnWrite(unittest.TestCase):
    """PR A.3: _seed_symbol() (the populate_from_config() path, run on
    every bot restart) must resolve which spelling to tag in
    symbol_categories the same way add_to_watchlist() does, so a restart
    can never re-create a stale-spelled row for a category a symbol is
    already tagged under -- see TestAddToWatchlistCanonicalizeOnWrite
    above for the /add-path equivalent of these scenarios."""

    def _tmp(self):
        f = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        f.close()
        return f.name

    def _counts(self, db):
        with db._connect() as conn:
            w = conn.execute("SELECT COUNT(*) c FROM watchlist").fetchone()['c']
            t = conn.execute("SELECT COUNT(*) c FROM symbol_categories").fetchone()['c']
        return w, t

    def test_brand_new_symbol_seeds_with_incoming_spelling(self):
        path = self._tmp()
        db = _reload_db(path)
        db.init_db({})
        db.populate_from_config({'Financials': ['XOM']})
        status = db.get_symbol_status('XOM')
        self.assertEqual(status['category'], 'Financials')
        self.assertEqual(db.get_symbol_categories('XOM'), ['Financials'])

    def test_seed_resolves_to_existing_primary_spelling_when_canonical_match(self):
        path = self._tmp()
        db = _reload_db(path)
        db.init_db({'Financials': ['JPM']})
        db.populate_from_config({'פיננסים': ['JPM']})
        status = db.get_symbol_status('JPM')
        self.assertEqual(status['category'], 'Financials')
        self.assertEqual(db.get_symbol_categories('JPM'), ['Financials'])

    def test_seed_resolves_to_existing_tag_spelling_when_only_tag_matches(self):
        path = self._tmp()
        db = _reload_db(path)
        db.init_db({'AI & Semiconductors': ['NVDA']})
        db.add_category_tag('NVDA', 'Mega Cap Tech')
        db.populate_from_config({'מגה טק': ['NVDA']})
        cats = db.get_symbol_categories('NVDA')
        self.assertIn('Mega Cap Tech', cats)
        self.assertNotIn('מגה טק', cats)
        self.assertEqual(cats.count('Mega Cap Tech'), 1)
        self.assertEqual(db.get_symbol_status('NVDA')['category'], 'AI & Semiconductors')

    def test_seed_backfills_missing_tag_using_primary_spelling(self):
        path = self._tmp()
        db = _reload_db(path)
        db.init_db({})
        with db._connect() as conn:
            conn.execute(
                "INSERT INTO watchlist (symbol, category, enabled) VALUES ('C', 'Financials', 1)"
            )
        self.assertEqual(db.get_symbol_categories('C'), [])  # confirm the drift fixture
        db.populate_from_config({'פיננסים': ['C']})
        status = db.get_symbol_status('C')
        self.assertEqual(status['category'], 'Financials')
        self.assertEqual(db.get_symbol_categories('C'), ['Financials'])

    def test_seed_keeps_two_distinct_tags_for_symbol_in_two_categories(self):
        path = self._tmp()
        db = _reload_db(path)
        db.init_db({})
        db.populate_from_config({'AI & Semiconductors': ['NVDA'], 'Mega Cap Tech': ['NVDA']})
        cats = sorted(db.get_symbol_categories('NVDA'))
        self.assertEqual(cats, ['AI & Semiconductors', 'Mega Cap Tech'])

    def test_seed_removed_symbol_stays_removed(self):
        path = self._tmp()
        db = _reload_db(path)
        db.init_db({'AI & Semiconductors': ['NVDA']})
        db.remove_from_watchlist('NVDA')
        db.populate_from_config({'AI & Semiconductors': ['NVDA']})
        status = db.get_symbol_status('NVDA')
        self.assertEqual(status['enabled'], 0)
        self.assertEqual(db.get_symbol_categories('NVDA'), ['AI & Semiconductors'])

    def test_reseeding_twice_is_idempotent(self):
        path = self._tmp()
        db = _reload_db(path)
        db.init_db({})
        wl = {'AI & Semiconductors': ['NVDA'], 'ETFs': ['SPY']}
        db.populate_from_config(wl)
        counts_1 = self._counts(db)
        db.populate_from_config(wl)
        counts_2 = self._counts(db)
        self.assertEqual(counts_1, counts_2)

    def test_full_watchlist_reseed_after_category_migration_is_clean(self):
        """Whole-config integration test against the REAL config.WATCHLIST:
        seed + classify first (today's real startup sequence -- this is
        what puts CCC under the live, still-Hebrew "Financials" spelling
        via run_initial_classification's own CCC special case, and sets
        wl_classified=1 for every row), THEN simulate a future migration
        of all 12 Hebrew category names to English (plain SQL, as a
        one-off migration script would -- this now also covers CCC's
        freshly-classified Financials row), THEN simulate two more
        restarts (populate_from_config + run_initial_classification, the
        exact pair run_bot() calls on every process start -- the second
        call is a true no-op since wl_classified is already 1 everywhere)
        and assert no Hebrew spelling comes back and nothing grows."""
        import config
        path = self._tmp()
        db = _reload_db(path)
        db.init_db({})
        db.populate_from_config(config.WATCHLIST)
        db.run_initial_classification(config.WATCHLIST)

        with db._connect() as conn:
            for hebrew, english in config.HEBREW_TO_ENGLISH_CATEGORY.items():
                conn.execute(
                    "UPDATE watchlist SET category = ? WHERE category = ?",
                    (english, hebrew),
                )
                conn.execute(
                    "INSERT OR IGNORE INTO symbol_categories (symbol, category) "
                    "SELECT symbol, ? FROM symbol_categories WHERE category = ?",
                    (english, hebrew),
                )
                conn.execute(
                    "DELETE FROM symbol_categories WHERE category = ?", (hebrew,)
                )

        db.populate_from_config(config.WATCHLIST)
        db.run_initial_classification(config.WATCHLIST)
        db.populate_from_config(config.WATCHLIST)
        db.run_initial_classification(config.WATCHLIST)

        # The invariant _seed_symbol's fix actually guarantees: no symbol
        # ever carries two symbol_categories tags that canonically
        # resolve to the SAME category (that would be a duplicate
        # Hebrew/English spelling of one category -- exactly what PR A.3
        # prevents).
        with db._connect() as conn:
            rows = conn.execute("SELECT symbol, category FROM symbol_categories").fetchall()
        by_symbol = {}
        for r in rows:
            by_symbol.setdefault(r['symbol'], []).append(r['category'])
        for sym, cats in by_symbol.items():
            canon = [config.canonical_category(c) for c in cats]
            self.assertEqual(
                len(canon), len(set(canon)),
                f"{sym} has duplicate-spelling tags for the same category: {cats}",
            )

        # NOTE on CCC: config.WATCHLIST still lists CCC under the Hebrew
        # "ענן ותוכנה" (Cloud & Software) key, while
        # run_initial_classification's CCC special case moves it to
        # Financials on first classification (deleting the Cloud &
        # Software tag). Because wl_classified then gates CCC out of
        # run_initial_classification on every later call, but
        # populate_from_config has no such gate -- it reseeds every
        # WATCHLIST membership on every restart regardless -- CCC's
        # Hebrew Cloud & Software tag is silently RE-ADDED by
        # populate_from_config on the very next restart after
        # classification, and never removed again. This reproduces
        # identically on pre-fix _seed_symbol (plain INSERT OR IGNORE
        # also re-inserts it, since the exact (CCC, 'ענן ותוכנה') row was
        # deleted and no longer exists to no-op against) -- it is a
        # separate, pre-existing populate_from_config-vs-
        # run_initial_classification interaction, not a duplicate-
        # spelling regression, and out of scope for a fix confined to
        # _seed_symbol. Flagged here rather than hidden: it means a
        # literal "zero Hebrew rows anywhere" or "row counts never grow"
        # assertion does not hold for CCC specifically, even after this
        # fix.
        ccc_cats = db.get_symbol_categories('CCC')
        self.assertIn('Financials', ccc_cats)
        self.assertNotIn('פיננסים', ccc_cats)
        self.assertEqual(db.get_symbol_status('CCC')['category'], 'Financials')

        # Every OTHER symbol: no Hebrew spelling reappears for a category
        # it was already migrated to English under.
        hebrew_names = set(config.HEBREW_TO_ENGLISH_CATEGORY.keys())
        for sym, cats in by_symbol.items():
            if sym == 'CCC':
                continue  # see NOTE above
            stray = hebrew_names.intersection(cats)
            self.assertFalse(stray, f"{sym} has stray Hebrew tags: {stray}")
        with db._connect() as conn:
            placeholders = ",".join("?" * len(hebrew_names))
            stray_watchlist = conn.execute(
                f"SELECT COUNT(*) c FROM watchlist WHERE category IN ({placeholders}) "
                "AND symbol != 'CCC'",
                tuple(hebrew_names),
            ).fetchone()['c']
        self.assertEqual(stray_watchlist, 0)

        # find at least one symbol genuinely listed under two WATCHLIST
        # categories (config.WATCHLIST has a handful per the
        # entries-vs-unique-symbols count) and confirm it kept exactly
        # that many distinct, English-only tags.
        seen = {}
        for category, symbols in config.WATCHLIST.items():
            for sym in symbols:
                seen.setdefault(sym, set()).add(config.canonical_category(category))
        dual_symbol, dual_cats = next(
            ((sym, cats) for sym, cats in seen.items() if len(cats) > 1), (None, None)
        )
        self.assertIsNotNone(dual_symbol, "expected at least one symbol under two WATCHLIST categories")
        dual_tags = db.get_symbol_categories(dual_symbol)
        self.assertEqual(len(dual_tags), len(dual_cats))
        self.assertEqual({config.canonical_category(t) for t in dual_tags}, dual_cats)
        for tag in dual_tags:
            self.assertNotIn(tag, hebrew_names)


class TestRestartAndReclassification(unittest.TestCase):
    """Restart/reclassification-survival tests, split out from
    TestStatePersistence above purely by insertion point (PR A.2's new
    TestAddToWatchlistCanonicalizeOnWrite class was added between them) --
    no behavioral change to any test below."""

    def _tmp(self):
        f = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        f.close()
        return f.name

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
