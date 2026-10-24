"""Tests for config.py's HEBREW_TO_ENGLISH_CATEGORY / canonical_category()
(PR A: English category names, rollout step 1 of 3 -- expand only, no
WATCHLIST key renames yet)."""
import unittest

import config
from config import HEBREW_TO_ENGLISH_CATEGORY, canonical_category

_NATIVE_ENGLISH_KEYS = {"ETFs", "AI & Semiconductors", "Quantum Computing"}


class TestMappingCoverage(unittest.TestCase):

    def test_mapping_covers_every_non_english_watchlist_key_exactly(self):
        """Drift guard: fails loudly if a Hebrew/mixed category is ever
        added to config.WATCHLIST without a corresponding mapping entry
        (or if the mapping has a stale entry for a key that no longer
        exists)."""
        expected = {k for k in config.WATCHLIST if k not in _NATIVE_ENGLISH_KEYS}
        self.assertEqual(set(HEBREW_TO_ENGLISH_CATEGORY.keys()), expected)


class TestCanonicalCategory(unittest.TestCase):

    def test_each_hebrew_name_maps_to_its_english_name(self):
        expected = {
            "מדדים": "Indices",
            "קריפטו": "Crypto",
            "מגה טק": "Mega Cap Tech",
            "ענן ותוכנה": "Cloud & Software",
            "סייבר": "Cybersecurity",
            "תשתיות Data Center": "Data Center Infrastructure",
            "חלל": "Space & Defense",
            "אנרגיה": "Energy",
            "גרעין": "Nuclear",
            "אנרגיה ירוקה": "Green Energy",
            "פיננסים": "Financials",
            "חומרי גלם": "Raw Materials",
        }
        for hebrew, english in expected.items():
            with self.subTest(hebrew=hebrew):
                self.assertEqual(canonical_category(hebrew), english)

    def test_native_english_keys_pass_through_unchanged(self):
        for key in _NATIVE_ENGLISH_KEYS:
            with self.subTest(key=key):
                self.assertEqual(canonical_category(key), key)

    def test_english_names_are_idempotent(self):
        for english in HEBREW_TO_ENGLISH_CATEGORY.values():
            with self.subTest(english=english):
                self.assertEqual(canonical_category(english), english)

    def test_unknown_string_passes_through_unchanged(self):
        self.assertEqual(canonical_category("Some Made-Up Category"), "Some Made-Up Category")

    def test_non_string_input_passes_through_unchanged_not_raise(self):
        self.assertIsNone(canonical_category(None))
        self.assertEqual(canonical_category(123), 123)
        # Also must not raise on an unhashable value (dict.get() would).
        self.assertEqual(canonical_category([1, 2]), [1, 2])


if __name__ == "__main__":
    unittest.main()
