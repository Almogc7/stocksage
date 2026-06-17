"""Tests for the watchlist eligibility scoring engine."""
import unittest
import pandas as pd
from datetime import datetime, timezone, timedelta


def _fresh_df():
    now = datetime.now(timezone.utc)
    idx = pd.DatetimeIndex([now - timedelta(days=1)])
    return pd.DataFrame(
        {'close': [100.0], 'open': [99.0], 'high': [101.0], 'low': [98.0], 'volume': [1_000_000]},
        index=idx,
    )


class TestDataQuality(unittest.TestCase):

    def test_none_price_data_returns_zero(self):
        from analyzers.eligibility import compute_data_quality
        self.assertEqual(compute_data_quality(None, None), 0.0)

    def test_zero_price_has_no_has_price_contribution(self):
        from analyzers.eligibility import compute_data_quality
        result = compute_data_quality({'price': 0, 'volume': 100_000}, None)
        # has_price = 0, has_volume = 0.35 (volume > 0), data_fresh = 0 (no df)
        self.assertAlmostEqual(result, 0.35, places=5)

    def test_zero_price_and_zero_volume_returns_zero(self):
        from analyzers.eligibility import compute_data_quality
        self.assertEqual(compute_data_quality({'price': 0, 'volume': 0}, None), 0.0)

    def test_fresh_data_adds_freshness_score(self):
        from analyzers.eligibility import compute_data_quality
        df = _fresh_df()
        result = compute_data_quality({'price': 100.0, 'volume': 1_000_000}, df)
        # has_price=0.35, has_volume=0.35, data_fresh=0.30 → total=1.0
        self.assertAlmostEqual(result, 1.0, places=5)


class TestLiquidityScore(unittest.TestCase):

    def test_zero_volume_returns_zero(self):
        from analyzers.eligibility import compute_liquidity_score
        self.assertEqual(compute_liquidity_score(0, 100.0), 0.0)

    def test_volume_and_dollar_vol_at_threshold_returns_one(self):
        from analyzers.eligibility import compute_liquidity_score
        from config import ELIGIBILITY_MIN_AVG_VOLUME, ELIGIBILITY_MIN_DOLLAR_VOL
        vol = ELIGIBILITY_MIN_AVG_VOLUME
        price = ELIGIBILITY_MIN_DOLLAR_VOL / vol
        self.assertAlmostEqual(compute_liquidity_score(vol, price), 1.0, places=5)

    def test_volume_10x_capped_at_one(self):
        from analyzers.eligibility import compute_liquidity_score
        from config import ELIGIBILITY_MIN_AVG_VOLUME, ELIGIBILITY_MIN_DOLLAR_VOL
        vol = ELIGIBILITY_MIN_AVG_VOLUME * 10
        price = (ELIGIBILITY_MIN_DOLLAR_VOL / ELIGIBILITY_MIN_AVG_VOLUME) * 10
        self.assertAlmostEqual(compute_liquidity_score(vol, price), 1.0, places=5)

    def test_half_threshold_volume_returns_half(self):
        from analyzers.eligibility import compute_liquidity_score
        from config import ELIGIBILITY_MIN_AVG_VOLUME, ELIGIBILITY_MIN_DOLLAR_VOL
        vol = ELIGIBILITY_MIN_AVG_VOLUME // 2
        # dollar vol also at 50%: price = threshold_dvol / threshold_vol
        price = ELIGIBILITY_MIN_DOLLAR_VOL / ELIGIBILITY_MIN_AVG_VOLUME
        result = compute_liquidity_score(vol, price)
        # volume_score=0.5, dvol_score=0.5 → 0.5
        self.assertAlmostEqual(result, 0.5, places=4)


class TestRelevanceScore(unittest.TestCase):

    def test_none_price_data_returns_zero(self):
        from analyzers.eligibility import compute_relevance_score
        self.assertEqual(compute_relevance_score('NVDA', None, None, None, 0), 0)

    def test_zero_price_returns_zero(self):
        from analyzers.eligibility import compute_relevance_score
        self.assertEqual(
            compute_relevance_score('NVDA', {'price': 0, 'volume': 0}, None, None, 0), 0
        )

    def test_score_within_bounds(self):
        from analyzers.eligibility import compute_relevance_score
        df = _fresh_df()
        score = compute_relevance_score('NVDA', {'price': 100.0, 'volume': 1_000_000}, df, None, 1_000_000)
        self.assertGreaterEqual(score, 0)
        self.assertLessEqual(score, 100)

    def test_missing_data_cannot_score_higher_than_with_data(self):
        from analyzers.eligibility import compute_relevance_score
        df = _fresh_df()
        score_with = compute_relevance_score('NVDA', {'price': 100.0, 'volume': 1_000_000}, df, None, 1_000_000)
        score_none = compute_relevance_score('NVDA', None, None, None, 0)
        self.assertGreaterEqual(score_with, score_none)

    def test_score_is_integer(self):
        from analyzers.eligibility import compute_relevance_score
        df = _fresh_df()
        score = compute_relevance_score('NVDA', {'price': 100.0, 'volume': 1_000_000}, df, None, 1_000_000)
        self.assertIsInstance(score, int)


class TestDetermineStateChange(unittest.TestCase):

    def _base(self):
        return dict(
            new_score=70,
            consec_promote=2,
            consec_demote=0,
            dwell_days=10,
            security_type='stock',
            price_data={'price': 100.0, 'volume': 1_000_000},
            avg_volume=500_000,
            is_bank=False,
            active_count=5,
            active_bank_count=0,
            lowest_active_score=50,
        )

    def test_etf_always_gets_etf_index_context(self):
        from analyzers.eligibility import determine_state_change
        kw = self._base()
        kw['security_type'] = 'etf'
        state, _ = determine_state_change('MONITOR', **kw)
        self.assertEqual(state, 'ETF_INDEX_CONTEXT')

    def test_index_always_gets_etf_index_context(self):
        from analyzers.eligibility import determine_state_change
        kw = self._base()
        kw['security_type'] = 'index'
        state, _ = determine_state_change('ACTIVE', **kw)
        self.assertEqual(state, 'ETF_INDEX_CONTEXT')

    def test_high_score_one_consecutive_stays_monitor(self):
        from analyzers.eligibility import determine_state_change
        kw = self._base()
        kw['consec_promote'] = 0  # 0+1=1 < PROMOTION_CONSEC_REQUIRED(2)
        state, _ = determine_state_change('MONITOR', **kw)
        self.assertEqual(state, 'MONITOR')

    def test_high_score_two_consecutive_promotes_to_active(self):
        from analyzers.eligibility import determine_state_change
        kw = self._base()
        kw['consec_promote'] = 1  # 1+1=2 >= PROMOTION_CONSEC_REQUIRED(2)
        state, _ = determine_state_change('MONITOR', **kw)
        self.assertEqual(state, 'ACTIVE')

    def test_low_score_one_evaluation_stays_active(self):
        from analyzers.eligibility import determine_state_change
        kw = self._base()
        kw['new_score'] = 30
        kw['consec_demote'] = 0  # 0+1=1 < DEMOTION_CONSEC_REQUIRED(2)
        state, _ = determine_state_change('ACTIVE', **kw)
        self.assertEqual(state, 'ACTIVE')

    def test_low_score_two_evaluations_demotes_to_monitor(self):
        from analyzers.eligibility import determine_state_change
        kw = self._base()
        kw['new_score'] = 30
        kw['consec_demote'] = 1  # 1+1=2 >= DEMOTION_CONSEC_REQUIRED(2)
        state, _ = determine_state_change('ACTIVE', **kw)
        self.assertEqual(state, 'MONITOR')

    def test_no_price_data_immediately_ineligible(self):
        from analyzers.eligibility import determine_state_change
        kw = self._base()
        kw['price_data'] = None
        state, _ = determine_state_change('MONITOR', **kw)
        self.assertEqual(state, 'TEMPORARILY_INELIGIBLE')

    def test_user_removed_never_changes(self):
        from analyzers.eligibility import determine_state_change
        kw = self._base()
        state, _ = determine_state_change('USER_REMOVED', **kw)
        self.assertEqual(state, 'USER_REMOVED')

    def test_30th_symbol_accepted(self):
        from analyzers.eligibility import determine_state_change
        kw = self._base()
        kw['active_count'] = 29  # 30th slot available
        kw['consec_promote'] = 1
        state, _ = determine_state_change('MONITOR', **kw)
        self.assertEqual(state, 'ACTIVE')

    def test_31st_symbol_without_margin_stays_monitor(self):
        from analyzers.eligibility import determine_state_change
        kw = self._base()
        kw['active_count'] = 30   # full
        kw['consec_promote'] = 1
        kw['new_score'] = 70
        kw['lowest_active_score'] = 68  # 70 < 68+5=73
        state, _ = determine_state_change('MONITOR', **kw)
        self.assertEqual(state, 'MONITOR')

    def test_replacement_margin_beats_lowest_active(self):
        from analyzers.eligibility import determine_state_change
        kw = self._base()
        kw['active_count'] = 30
        kw['consec_promote'] = 1
        kw['new_score'] = 80
        kw['lowest_active_score'] = 74  # 80 >= 74+5=79 ✓
        state, _ = determine_state_change('MONITOR', **kw)
        self.assertEqual(state, 'ACTIVE')

    def test_bank_cap_9th_bank_goes_to_monitor(self):
        from analyzers.eligibility import determine_state_change
        kw = self._base()
        kw['is_bank'] = True
        kw['active_bank_count'] = 8  # at cap
        kw['consec_promote'] = 1
        state, _ = determine_state_change('MONITOR', **kw)
        self.assertEqual(state, 'MONITOR')

    def test_dwell_min_prevents_demotion(self):
        from analyzers.eligibility import determine_state_change
        from config import DWELL_MIN_DAYS
        kw = self._base()
        kw['new_score'] = 30
        kw['consec_demote'] = 1  # would normally demote
        kw['dwell_days'] = DWELL_MIN_DAYS - 1  # not yet eligible
        state, _ = determine_state_change('ACTIVE', **kw)
        self.assertEqual(state, 'ACTIVE')


class TestClassifySecurityType(unittest.TestCase):

    def test_caret_prefix_is_index(self):
        from analyzers.eligibility import classify_security_type
        self.assertEqual(classify_security_type('^GSPC'), 'index')

    def test_known_etf_is_etf(self):
        from analyzers.eligibility import classify_security_type
        self.assertEqual(classify_security_type('SPY'), 'etf')
        self.assertEqual(classify_security_type('QQQ'), 'etf')

    def test_usd_suffix_is_crypto(self):
        from analyzers.eligibility import classify_security_type
        self.assertEqual(classify_security_type('BTC-USD'), 'crypto')

    def test_stock_symbol_is_stock(self):
        from analyzers.eligibility import classify_security_type
        self.assertEqual(classify_security_type('NVDA'), 'stock')
        self.assertEqual(classify_security_type('AAPL'), 'stock')


if __name__ == '__main__':
    unittest.main()
