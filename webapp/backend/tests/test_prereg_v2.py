import unittest

from app.patterns import prereg_v2


def _row(n, distinct_symbols=200, distinct_months=60, ci_lower=0.01):
    return {"n": n, "distinct_symbols": distinct_symbols, "distinct_months": distinct_months, "ci_lower": ci_lower}


def _year(n, mean_abnormal):
    return {"n": n, "mean_abnormal": mean_abnormal}


_GOOD_YEARS = [_year(150, 0.02), _year(150, 0.01), _year(150, -0.01), _year(150, 0.03)]  # 4 qualifying, 3/4 positive


class PassesRuleTests(unittest.TestCase):
    def test_passes_when_every_condition_clears(self):
        row = _row(n=600)
        self.assertTrue(prereg_v2.passes_pass1(row, _GOOD_YEARS))
        self.assertTrue(prereg_v2.passes_pass2(row, _GOOD_YEARS))

    def test_fails_on_small_n(self):
        row = _row(n=499)
        self.assertFalse(prereg_v2._passes(row, _GOOD_YEARS))

    def test_fails_on_too_few_distinct_symbols(self):
        row = _row(n=600, distinct_symbols=99)
        self.assertFalse(prereg_v2._passes(row, _GOOD_YEARS))

    def test_fails_on_too_few_distinct_months(self):
        row = _row(n=600, distinct_months=39)
        self.assertFalse(prereg_v2._passes(row, _GOOD_YEARS))

    def test_fails_when_ci_lower_not_strictly_positive(self):
        row = _row(n=600, ci_lower=0.0)
        self.assertFalse(prereg_v2._passes(row, _GOOD_YEARS))

    def test_fails_when_ci_lower_is_nan(self):
        row = _row(n=600, ci_lower=float("nan"))
        self.assertFalse(prereg_v2._passes(row, _GOOD_YEARS))

    def test_fails_with_fewer_than_min_qualifying_years(self):
        row = _row(n=600)
        years = [_year(150, 0.02), _year(150, 0.01), _year(150, 0.03)]  # only 3 qualifying (<4)
        self.assertFalse(prereg_v2._passes(row, years))

    def test_years_below_min_detections_per_year_dont_count_as_qualifying(self):
        row = _row(n=600)
        years = list(_GOOD_YEARS) + [_year(10, -0.9)]  # 4th extra year, too few detections, strongly negative
        self.assertTrue(prereg_v2._passes(row, years))  # unaffected: still 4 qualifying, 3/4 positive

    def test_fails_when_positive_years_are_not_a_strict_majority(self):
        row = _row(n=600)
        years = [_year(150, 0.02), _year(150, -0.01), _year(150, 0.01), _year(150, -0.02)]  # exactly half
        self.assertFalse(prereg_v2._passes(row, years))

    def test_fails_with_no_yearly_data(self):
        row = _row(n=600)
        self.assertFalse(prereg_v2._passes(row, []))


class ClassifyResultTests(unittest.TestCase):
    def test_pass1_fails_is_no_evidence(self):
        self.assertEqual(prereg_v2.classify_result(False, False), "NO EVIDENCE (held-out)")
        self.assertEqual(prereg_v2.classify_result(False, True), "NO EVIDENCE (held-out)")

    def test_pass1_only_is_trend_momentum_not_pullback_timing(self):
        self.assertEqual(prereg_v2.classify_result(True, False), "trend/momentum effect, not pullback timing")

    def test_both_pass_is_supported_out_of_sample(self):
        self.assertEqual(prereg_v2.classify_result(True, True), "supported out of sample (informational only)")


if __name__ == "__main__":
    unittest.main()
