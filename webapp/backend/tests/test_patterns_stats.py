import unittest

from app.patterns import config
from app.patterns.stats import bootstrap_mean_ci, label_pattern_direction, passes_decision_rule, summarize_pattern


def _scored(symbol, excess_return_t10):
    return {"symbol": symbol, "excess_return_by_horizon": {5: excess_return_t10 / 2, 10: excess_return_t10, 20: excess_return_t10 * 1.5}}


class BootstrapMeanCiTests(unittest.TestCase):
    def test_is_deterministic_for_a_fixed_seed(self):
        values_by_symbol = {"AAA": [0.05, 0.03], "BBB": [-0.02], "CCC": [0.10, 0.08, 0.06]}
        a = bootstrap_mean_ci(values_by_symbol, n_resamples=500, seed=42)
        b = bootstrap_mean_ci(values_by_symbol, n_resamples=500, seed=42)
        self.assertEqual(a, b)

    def test_interval_brackets_the_observed_mean(self):
        values_by_symbol = {"AAA": [0.05, 0.03], "BBB": [-0.02], "CCC": [0.10, 0.08, 0.06]}
        mean, lower, upper = bootstrap_mean_ci(values_by_symbol, n_resamples=2000, seed=1)
        self.assertLessEqual(lower, mean)
        self.assertLessEqual(mean, upper)

    def test_empty_input_returns_nan_without_raising(self):
        mean, lower, upper = bootstrap_mean_ci({}, seed=1)
        self.assertTrue(mean != mean)  # NaN != NaN


class SummarizePatternTests(unittest.TestCase):
    def test_small_sample_is_labeled_inconclusive(self):
        scored = [_scored(f"S{i}", 0.02) for i in range(5)]  # n=5, 5 distinct symbols
        row = summarize_pattern("trend_pullback", "long", scored, horizon=10, seed=1)
        self.assertEqual(row["n"], 5)
        self.assertTrue(row["inconclusive"])

    def test_large_enough_sample_is_not_labeled_inconclusive(self):
        scored = []
        for i in range(15):
            scored.append(_scored(f"S{i}", 0.02))
            scored.append(_scored(f"S{i}", 0.01))  # 2 detections per symbol -> n=40 across 15 symbols
        scored = scored[:40]
        row = summarize_pattern("trend_pullback", "long", scored, horizon=10, seed=1)
        self.assertGreaterEqual(row["n"], 30)
        self.assertGreaterEqual(row["distinct_symbols"], 10)
        self.assertFalse(row["inconclusive"])

    def test_concentration_flag_fires_when_one_symbol_dominates(self):
        scored = [_scored("DOMINANT", 0.02) for _ in range(20)] + [_scored(f"S{i}", 0.01) for i in range(5)]
        row = summarize_pattern("flag_breakout", "long", scored, horizon=10, seed=1)
        self.assertTrue(row["concentrated"])
        self.assertEqual(row["max_detections_per_symbol"], 20)

    def test_hit_rate_counts_positive_excess_returns(self):
        scored = [_scored("A", 0.05), _scored("B", -0.02), _scored("C", 0.01)]
        row = summarize_pattern("double_top_bottom", "short", scored, horizon=10, seed=1)
        self.assertAlmostEqual(row["hit_rate"], 2 / 3)


def _row(n, distinct_symbols, ci_lower):
    return {"n": n, "distinct_symbols": distinct_symbols, "ci_lower": ci_lower}


def _year_row(mean_excess_return, n=config.MIN_DETECTIONS_PER_YEAR):
    return {"mean_excess_return": mean_excess_return, "n": n}


# A clean set of qualifying years (n >= MIN_DETECTIONS_PER_YEAR each),
# majority positive -- the baseline "everything else passes" fixture the
# non-year-rule tests reuse so each test isolates its own condition.
_GOOD_YEARS = [_year_row(0.02), _year_row(0.01), _year_row(-0.01)]  # 2/3 positive


class PassesDecisionRuleTests(unittest.TestCase):
    def test_passes_when_every_condition_clears(self):
        row = _row(n=40, distinct_symbols=15, ci_lower=0.01)
        self.assertTrue(passes_decision_rule(row, _GOOD_YEARS))

    def test_fails_on_small_n_even_if_everything_else_passes(self):
        row = _row(n=10, distinct_symbols=15, ci_lower=0.01)
        self.assertFalse(passes_decision_rule(row, _GOOD_YEARS))

    def test_fails_on_too_few_distinct_symbols(self):
        row = _row(n=40, distinct_symbols=5, ci_lower=0.01)
        self.assertFalse(passes_decision_rule(row, _GOOD_YEARS))

    def test_fails_when_ci_lower_is_not_strictly_positive(self):
        row = _row(n=40, distinct_symbols=15, ci_lower=0.0)
        self.assertFalse(passes_decision_rule(row, _GOOD_YEARS))

    def test_fails_when_ci_lower_is_nan(self):
        row = _row(n=40, distinct_symbols=15, ci_lower=float("nan"))
        self.assertFalse(passes_decision_rule(row, _GOOD_YEARS))

    def test_fails_when_positive_years_are_not_a_strict_majority(self):
        row = _row(n=40, distinct_symbols=15, ci_lower=0.01)
        years = [_year_row(0.02), _year_row(-0.01), _year_row(0.01), _year_row(-0.02)]  # exactly half positive
        self.assertFalse(passes_decision_rule(row, years))

    def test_fails_with_no_yearly_data_at_all(self):
        row = _row(n=40, distinct_symbols=15, ci_lower=0.01)
        self.assertFalse(passes_decision_rule(row, []))

    def test_fails_with_fewer_than_min_qualifying_years_even_if_all_positive(self):
        row = _row(n=40, distinct_symbols=15, ci_lower=0.01)
        years = [_year_row(0.02), _year_row(0.03)]  # only 2 qualifying years, both positive
        self.assertFalse(passes_decision_rule(row, years))

    def test_years_below_min_detections_per_year_dont_count_as_qualifying(self):
        row = _row(n=40, distinct_symbols=15, ci_lower=0.01)
        # 3 qualifying years, 2/3 positive -> would PASS on its own.
        years = list(_GOOD_YEARS)
        # A 4th year with too few detections, strongly negative: must NOT
        # be counted (neither toward the qualifying-year total nor the
        # majority vote), so the outcome is unchanged.
        years.append(_year_row(-0.5, n=config.MIN_DETECTIONS_PER_YEAR - 1))
        self.assertTrue(passes_decision_rule(row, years))

    def test_a_qualifying_negative_year_can_tip_a_majority(self):
        row = _row(n=40, distinct_symbols=15, ci_lower=0.01)
        # 3 qualifying years, 2 positive / 1 negative -> PASS.
        years = [_year_row(0.02), _year_row(0.01), _year_row(-0.01)]
        self.assertTrue(passes_decision_rule(row, years))
        # Flip one of the positives to negative -> no longer a majority.
        years = [_year_row(-0.02), _year_row(0.01), _year_row(-0.01)]
        self.assertFalse(passes_decision_rule(row, years))


class LabelPatternDirectionTests(unittest.TestCase):
    def test_active_only_pass_is_promising_selection_biased(self):
        label = label_pattern_direction({"ACTIVE": True, "MONITOR": False})
        self.assertEqual(label, "PROMISING (ACTIVE only, selection-biased)")

    def test_pass_in_a_non_active_tier_is_supported(self):
        label = label_pattern_direction({"ACTIVE": True, "MONITOR": True})
        self.assertEqual(label, "SUPPORTED")

    def test_pass_in_non_active_tier_alone_is_still_supported(self):
        # Corroboration from outside the biased sample counts even if
        # ACTIVE itself didn't PASS.
        label = label_pattern_direction({"ACTIVE": False, "MONITOR": True})
        self.assertEqual(label, "SUPPORTED")

    def test_no_pass_anywhere_is_no_evidence(self):
        label = label_pattern_direction({"ACTIVE": False, "MONITOR": False})
        self.assertEqual(label, "NO EVIDENCE")

    def test_empty_tier_dict_is_no_evidence(self):
        self.assertEqual(label_pattern_direction({}), "NO EVIDENCE")

    def test_any_single_non_active_pass_among_several_tiers_is_enough(self):
        label = label_pattern_direction(
            {"ACTIVE": False, "MONITOR": False, "ETF_INDEX_CONTEXT": True, "TEMPORARILY_INELIGIBLE": False}
        )
        self.assertEqual(label, "SUPPORTED")


if __name__ == "__main__":
    unittest.main()
