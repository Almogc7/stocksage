"""Round B: pre-registered, held-out evaluation of trend_pullback/long.

THIS FILE IS FROZEN before scripts/eval_trend_pullback_holdout.py is ever
run against held-out data — exactly the same discipline as config.py's own
freeze rule (bump PREREG_VERSION, document why, re-run fresh; never edit a
value in place after a reported run). The evaluation script refuses to run
if this file OR config.py has uncommitted changes.

SINGLE HYPOTHESIS (pre-registered, not chosen after seeing held-out data):
trend_pullback/long, stock tiers pooled (excludes ETF_INDEX_CONTEXT), has
positive mean abnormal return vs. same-date stock peers (baseline B) at
PRIMARY_HORIZON. CONTEXT_HORIZONS are printed for context only and never
decide anything.

HELD-OUT PERIOD: strictly before v1's own fetch window. v1
(scripts/backtest_patterns.py) fetches period="5y" ending whenever it is
run, i.e. its window start is "today - 5y". HELD_OUT_PERIOD_END is chosen
with a large margin before that (see the dated reasoning below) — the
margin can only GROW over time (a later real run only pushes v1's window
start later too), so these fixed dates stay valid indefinitely into the
future without needing to be recomputed.
  - Chosen on a day in the 2026-10 range: v1's window start was then
    ~2021-10-19. HELD_OUT_PERIOD_END = 2021-06-30 sits ~111 calendar days
    before that — well past the required 30-day minimum, with margin to
    spare.
  - HELD_OUT_PERIOD_START = 2016-01-04 gives a ~5.5y span ("about 5-6
    years"), with SMA_WARMUP_BARS=200 (~10 months, ~14% of the window)
    comfortably absorbed, leaving most of the span free for detections.

WHY THESE THRESHOLDS: PREREG_MIN_N / MIN_DISTINCT_SYMBOLS /
MIN_DISTINCT_MONTHS / MIN_QUALIFYING_YEARS / MIN_DETECTIONS_PER_YEAR were
judged by comparing against the sample sizes actually observed in the v1
backtest report and the round-1 baseline diagnostic report — a DIFFERENT
period (v1's own trailing 5y window, not this 2016-2021 held-out window)
— and general reasoning about what counts as "enough of a sample," NOT by
looking at any held-out data. PREREG_MIN_PEERS is reasoned the same way:
a same-date peer pool under ~20 stocks is too thin for a single day's
cross-sectional mean to be a meaningful benchmark, regardless of pattern.
These numbers are 15-20x stricter than v1's own INCONCLUSIVE_MIN_N=30 /
MIN_DISTINCT_SYMBOLS=10 / MIN_QUALIFYING_YEARS=3 (at
MIN_DETECTIONS_PER_YEAR=5) — whether a ~340-symbol, ~5.5y pooled
trend_pullback/long sample actually clears them depends on how many real
trending vs. range-bound years fall in 2016-2021, which is unknown without
looking at data.

SURVIVORSHIP (a stated limit of this test, not fixed by the baselines):
survivorship bias is STRONGER going backward here than it was for v1 —
today's watchlist membership is used for the ENTIRE 2016-2021 window,
meaning only symbols on the watchlist TODAY (chosen using information
through the present) are evaluated against a period years in the past,
when some of today's members may not have existed, been liquid, or been
anything like their current selves — and symbols that existed then but
aren't on the watchlist now are invisible to this test entirely.
Baselines B/B-trend only PARTLY absorb this: they control for what the
ordinary peer in TODAY'S SURVIVING universe did on that date, not for the
universe that actually existed in 2016. This is a limit of the test, not
something B/B-trend fix.

NO STOPS, FIXED HORIZON: same rationale as app/patterns/baseline.py
(reused unmodified) — entry = next bar's open after confirmed_on, exit =
close of the Nth bar, no stop-loss mechanics, so pattern days and ordinary
peer days are measured on the same yardstick.

PASS-1 / PASS-2 / CONSEQUENCES (fixed in advance, cannot be adjusted after
seeing results):
  PASS-1: at PRIMARY_HORIZON, ALL of —
    - the lower bound of a 90%, config.BOOTSTRAP_SEED-seeded,
      PREREG_BOOTSTRAP_RESAMPLES-resample, CALENDAR-MONTH-cluster
      bootstrap interval of mean abnormal_B is > 0
    - n >= PREREG_MIN_N
    - distinct_symbols >= PREREG_MIN_DISTINCT_SYMBOLS
    - distinct_months >= PREREG_MIN_DISTINCT_MONTHS
    - at least PREREG_MIN_QUALIFYING_YEARS calendar years with
      >= PREREG_MIN_DETECTIONS_PER_YEAR detections each, and a STRICT
      majority of those qualifying years have positive mean abnormal_B
  PASS-2: the identical conditions, evaluated against abnormal_B-trend
    instead of abnormal_B (its own n/distinct_symbols/distinct_months/
    yearly rows, which may differ from PASS-1's since B-trend's peer pool
    is a stricter subset and PREREG_MIN_PEERS can exclude more detections
    from it than from B).
  Consequences, fixed:
    - PASS-1 fails -> "NO EVIDENCE (held-out)". No pattern is promoted.
    - PASS-1 passes, PASS-2 fails -> "trend/momentum effect, not pullback
      timing".
    - Both pass -> "supported out of sample (informational only)".
  In every case patterns stay informational: no alerts, no scoring, no
  bot tables (webapp/CLAUDE.md rule 9). Anything beyond this needs a
  separate forward paper-tracking test.
"""

PREREG_VERSION = "v1"

# ---- held-out period (fixed, not data-chosen — see docstring) ----------
HELD_OUT_PERIOD_START = "2016-01-04"
HELD_OUT_PERIOD_END = "2021-06-30"

# ---- horizons ------------------------------------------------------------
PRIMARY_HORIZON = 10  # decides PASS-1/PASS-2
CONTEXT_HORIZONS = (5, 20)  # printed for context only, never decide anything

# ---- bootstrap -------------------------------------------------------------
# config.BOOTSTRAP_SEED is imported (not redefined) by the evaluation
# script, per instruction; the resample count is restated here explicitly
# so this file is self-contained and frozen independent of config.py's own
# value ever changing.
PREREG_BOOTSTRAP_RESAMPLES = 2000

# ---- pre-registered decision thresholds (see "WHY THESE THRESHOLDS") -----
PREREG_MIN_N = 500
PREREG_MIN_DISTINCT_SYMBOLS = 100
PREREG_MIN_DISTINCT_MONTHS = 40
PREREG_MIN_QUALIFYING_YEARS = 4
PREREG_MIN_DETECTIONS_PER_YEAR = 100
PREREG_MIN_PEERS = 20  # per-detection floor on same-date peer count, for B and B-trend independently


def _passes(primary_row: dict, yearly_rows: list[dict]) -> bool:
    """Shared PASS-1/PASS-2 rule, evaluated against whichever
    (primary_row, yearly_rows) pair the caller supplies — primary_row must
    carry "n", "distinct_symbols", "distinct_months", "ci_lower"; each
    yearly_row must carry "n" and "mean_abnormal"."""
    if primary_row["n"] < PREREG_MIN_N:
        return False
    if primary_row["distinct_symbols"] < PREREG_MIN_DISTINCT_SYMBOLS:
        return False
    if primary_row["distinct_months"] < PREREG_MIN_DISTINCT_MONTHS:
        return False
    ci_lower = primary_row["ci_lower"]
    if not (ci_lower == ci_lower and ci_lower > 0):  # NaN-safe
        return False

    qualifying_years = [r for r in yearly_rows if r["n"] >= PREREG_MIN_DETECTIONS_PER_YEAR]
    if len(qualifying_years) < PREREG_MIN_QUALIFYING_YEARS:
        return False
    positive_years = sum(1 for r in qualifying_years if r["mean_abnormal"] > 0)
    return positive_years > len(qualifying_years) / 2


def passes_pass1(primary_row_b: dict, yearly_rows_b: list[dict]) -> bool:
    return _passes(primary_row_b, yearly_rows_b)


def passes_pass2(primary_row_b_trend: dict, yearly_rows_b_trend: list[dict]) -> bool:
    return _passes(primary_row_b_trend, yearly_rows_b_trend)


def classify_result(pass1: bool, pass2: bool) -> str:
    """The three fixed, pre-registered outcome strings — never assembled
    from runtime data."""
    if not pass1:
        return "NO EVIDENCE (held-out)"
    if not pass2:
        return "trend/momentum effect, not pullback timing"
    return "supported out of sample (informational only)"


INFORMATIONAL_ONLY_NOTE = (
    "In every case patterns stay informational: no alerts, no scoring, no bot tables "
    "(rule 9). Anything beyond this needs a separate forward paper-tracking test."
)
