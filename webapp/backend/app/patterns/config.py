"""Every numeric threshold used anywhere in app/patterns/, in one place.

THRESHOLDS ARE FROZEN once a real backtest has run against them (via
scripts/backtest_patterns.py). To change any value here: bump
CONFIG_VERSION below, document why in this docstring, and re-run the
backtest fresh against the new version. Never edit a value in place after
a reported run — a silent edit makes every prior report's numbers
unreproducible and lets the backtest implicitly "tune" the detector
against the same data it's supposed to be evaluating.

v1 (initial): every value below is a first-pass, reasoned default — none
was chosen by looking at any real-data backtest output (none has run
yet), per the round-1 hard build rule.

WHY ATR-MULTIPLES, NOT RAW PERCENTAGES: a fixed percentage threshold
(e.g. "within 3%") means something very different on a calm utility stock
than on a volatile small-cap — the same chart *shape* would pass on one
and fail on the other purely because of its baseline volatility, which
has nothing to do with whether it's actually the pattern in question.
Expressing range/tolerance/depth thresholds as a multiple of ATR instead
makes a detection mean the same thing — "this move is small/large
relative to this stock's own normal daily range" — regardless of what
that stock's normal range happens to be. BASE_MAX_RANGE_ATR,
PEAK_TOLERANCE_ATR and MIN_TROUGH_ATR are therefore all stated as ATR
multiples, computed from ATR at the bar that confirms the pattern (never
a later bar, so this stays no-look-ahead): starting values are reasoned
from a typical liquid stock's ATR running around 2.5% of price (so e.g.
"6 ATR" roughly maps to "~15% of price" for a typical name, which is
where the old flat 15% base-range threshold came from) — not fit to any
observed data.

NAMED TIE-BREAK RULE (flag_breakout, same-bar ties): when several base
windows independently confirm a breakout on the exact same bar, exactly
one must be kept — its base_low/base_high becomes the fixed stop. This is
a fixed, documented selection, not arbitrary insertion order: widest
window (most `base_bars`) wins, ties broken by tightest range (smallest
`base_range_atr`), final tie broken by earliest `start_date`. All three
fields are already fixed by each candidate's own `confirmed_on`, so this
never looks ahead. Implemented in
app/patterns/flag_breakout.py::_select_survivor()/_dedupe_same_bar().

MULTIPLE COMPARISONS: a single backtest run produces on the order of 18
(pattern, direction, tier) cells (3 patterns x 2 directions x up to ~3
tiers with any data). At a one-sided 5% significance level, testing 18
independent cells means roughly one PASS is expected by chance alone even
if nothing in this entire module has any real edge (18 x 0.05 ~= 0.9).
A single cell clearing n/distinct_symbols/the bootstrap interval is
therefore not, by itself, trustworthy — this is exactly why PASS also
requires robustness across several separate calendar years (below) and
why the tier rule (also below) requires corroboration outside the single,
selection-biased ACTIVE sample: a result that only clears the bar in one
cell, one year, or one tier is indistinguishable from the ~1-in-18 chance
false positive this many comparisons already predicts.

PRE-REGISTERED DECISION RULE (fixed before any real backtest is run, so
it cannot be adjusted after seeing results): within a single tier, a
(pattern, direction) combination is marked PASS only if ALL of the
following hold, all at PRIMARY_HORIZON:
  - n >= INCONCLUSIVE_MIN_N
  - distinct_symbols >= INCONCLUSIVE_MIN_DISTINCT_SYMBOLS
  - the lower bound of the BOOTSTRAP_CONFIDENCE_PCT% bootstrap interval of
    mean excess return is > 0
  - YEAR RULE: counting only calendar years with at least
    MIN_DETECTIONS_PER_YEAR de-duplicated detections ("qualifying
    years"), there are at least MIN_QUALIFYING_YEARS such years, and a
    (strict) majority of them have positive mean excess return at
    PRIMARY_HORIZON. Fewer than MIN_QUALIFYING_YEARS qualifying years is
    NO EVIDENCE outright, regardless of the other three conditions — a
    thin, lucky year (or two) can't carry a pattern that hasn't actually
    been seen often enough, in enough different years, to judge.
Anything that doesn't clear all four bars is reported as "NO EVIDENCE" —
not a claim the pattern doesn't work, only that this backtest didn't
produce strong/broad enough evidence either way. See
app/patterns/stats.py::passes_decision_rule().

TIER RULE (cross-tier labeling, computed after every tier has been
scored): a (pattern, direction) that PASSes in the ACTIVE tier but in no
non-ACTIVE tier is labeled "PROMISING (ACTIVE only, selection-biased)" —
ACTIVE membership is itself chosen using data up to today (see the
ACTIVE-bias caveat the report always prints), so a PASS there alone is
read as an upper bound, not independent evidence. A (pattern, direction)
is labeled "SUPPORTED" when it PASSes in at least one non-ACTIVE tier
(corroboration from a tier that wasn't selected for already trending) —
whether or not it also PASSes in ACTIVE. Anything that PASSes nowhere is
labeled "NO EVIDENCE". See
app/patterns/stats.py::label_pattern_direction(), and the report's final
per-(pattern, direction) summary table (scripts/backtest_patterns.py).

RATIONALE PER VALUE (one line each, reasoned not fit):
  ATR_PERIOD=14: the standard Wilder ATR lookback; nothing here depends
    on exactly 14, just a stable multi-week volatility read.
  ZIGZAG_ATR_MULT=2.0: a pivot needs a genuinely new swing (2 average
    daily ranges of reversal) to confirm, not daily noise.
  SMA_SLOPE_LOOKBACK_BARS=10: two trading weeks — enough bars that the
    SMA slope reflects a real trend, not one day's wiggle.
  PULLBACK_LOOKBACK_BARS=5: one trading week — a pullback should resolve
    quickly; a "touch" more than a week old isn't the same setup anymore.
  PULLBACK_ATR_MULT=1.0: "near the SMA" means within one average daily
    range of it, not touching it to the penny.
  BASE_MIN_BARS=10 / BASE_MAX_BARS=60: a base needs at least two weeks to
    be a real consolidation, and loses relevance to a breakout beyond
    about a quarter.
  BASE_MAX_RANGE_ATR=6: a tight base's full high-low range should be a
    handful of average daily ranges, not many multiples of it (see the
    ATR-multiple rationale above).
  BREAKOUT_ATR_MULT=0.25: a breakout needs to clear the base edge by a
    meaningful fraction of a daily range, not by a rounding error.
  BREAKOUT_RELVOL_MIN=1.5: a real breakout usually brings above-average
    participation; 1.5x the base's own average volume is a mild, not
    extreme, bar.
  PEAK_TOLERANCE_ATR=1.0: the two peaks/troughs of a double top/bottom
    should be "about the same price" — within one average daily range of
    each other (see the ATR-multiple rationale above).
  MIN_TROUGH_ATR=2.0: the neckline needs to be a real pullback, not a
    shallow wiggle — at least two average daily ranges deep (see the
    ATR-multiple rationale above).
  TREND_PULLBACK_STOP_ATR_MULT=0.5: a stop just beyond the pullback
    extreme, with a small buffer so ordinary noise doesn't stop it out
    the instant the position opens.
  COOLDOWN_BARS=20: one trading month — long enough that two detections
    within it are "the same setup still developing," not independent
    signals.
  FORWARD_HORIZONS=(5,10,20): roughly one week, two weeks, one month —
    a short, medium and longer read on the same trade.
  PRIMARY_HORIZON=10: the two-week mark is the single headline number the
    PASS/NO EVIDENCE rule is decided on; 5 and 20 are reported as context
    only, not as alternate decision points to cherry-pick from.
  MIN_DETECTIONS_PER_YEAR=5: fewer than a handful of detections in a given
    year isn't enough to say that year's mean excess return means
    anything — such a year doesn't count toward the year rule either way.
  MIN_QUALIFYING_YEARS=3: a pattern needs to have shown up, with enough
    detections to matter, in at least three separate years before its
    multi-year consistency can be judged at all.
  BOOTSTRAP_RESAMPLES=2000 / BOOTSTRAP_CONFIDENCE_PCT=90: standard
    bootstrap defaults, large enough to stabilize the percentile estimate
    without being slow.
  BOOTSTRAP_SEED: fixed so every run of the same data reproduces the same
    interval — a seed is arbitrary by nature, so its specific value
    carries no "tuning" signal either way.
  INCONCLUSIVE_MIN_N=30 / INCONCLUSIVE_MIN_DISTINCT_SYMBOLS=10: the
    smallest sample sizes anyone could call "a sample" rather than
    anecdotes — not a statistically derived power calculation, just a
    floor for honesty.
  CONCENTRATION_WARN_FRACTION=0.3: flags a pattern/direction whenever a
    SINGLE symbol contributes more than this fraction of its total
    (de-duplicated) detections — one name dominating the sample that
    heavily means the result is really a statement about that symbol, not
    the pattern in general.
  BACKTEST_PERIOD=5y: long enough to span more than one market regime
    (not just one bull run), short enough to still batch-fetch cheaply.
  UNIVERSE_FETCH_CHUNK_SIZE=50 / UNIVERSE_FETCH_FALLBACK_CAP=20: practical
    batching/rate-limit sizing for a one-off script run, not statistical
    choices.
  SMA_PERIODS=(150,200): the two moving averages pattern 1 is defined
    against, matching the live app's existing SMA150/SMA200 chart lines.
  SMA_WARMUP_BARS=max(SMA_PERIODS): derived, not a separate number — the
    longer of the two trend_pullback SMA periods is the binding warm-up
    constraint before any detector can fire on a symbol's earliest bars.
"""

CONFIG_VERSION = "v1"

# ---- zigzag / pivot detection ----------------------------------------
ATR_PERIOD = 14
ZIGZAG_ATR_MULT = 2.0

# ---- pattern 1: trend_pullback ----------------------------------------
SMA_PERIODS = (150, 200)
SMA_SLOPE_LOOKBACK_BARS = 10  # N: compare SMA now vs. N bars ago
PULLBACK_LOOKBACK_BARS = 5    # K: window to look for a touch of the SMA
PULLBACK_ATR_MULT = 1.0       # "within atr_mult_sma * ATR of the SMA"

# ---- pattern 2: flag_breakout ----------------------------------------
BASE_MIN_BARS = 10
BASE_MAX_BARS = 60
BASE_MAX_RANGE_ATR = 6.0      # (max_high - min_low) <= this many ATR, at the base's confirming bar
BREAKOUT_ATR_MULT = 0.25      # close must clear base_high/base_low by this much ATR
BREAKOUT_RELVOL_MIN = 1.5     # breakout-bar volume vs. avg base volume

# ---- pattern 3: double_top_bottom --------------------------------------
PEAK_TOLERANCE_ATR = 1.0      # the two peaks/troughs must be within this many ATR of each other
MIN_TROUGH_ATR = 2.0          # neckline must be at least this many ATR below/above both extremes

# ---- fixed stop levels (stops.py) --------------------------------------
TREND_PULLBACK_STOP_ATR_MULT = 0.5  # pullback_low - 0.5*ATR (long), mirrored for short

# ---- backtest: de-duplication / cooldown -------------------------------
COOLDOWN_BARS = 20  # same (symbol, pattern, direction): no new detection within this many bars

# ---- backtest: forward-return horizons ---------------------------------
FORWARD_HORIZONS = (5, 10, 20)  # trading bars from the entry bar
PRIMARY_HORIZON = 10            # the decision-rule horizon; others are context only

# ---- backtest: small-sample statistics ---------------------------------
BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_CONFIDENCE_PCT = 90  # -> 5th/95th percentile
BOOTSTRAP_SEED = 20260101      # fixed for reproducibility; the value itself is arbitrary
INCONCLUSIVE_MIN_N = 30
INCONCLUSIVE_MIN_DISTINCT_SYMBOLS = 10
MIN_DETECTIONS_PER_YEAR = 5    # a calendar year needs at least this many to "qualify" for the year rule
MIN_QUALIFYING_YEARS = 3       # fewer than this many qualifying years -> NO EVIDENCE outright
CONCENTRATION_WARN_FRACTION = 0.3  # max_detections_per_symbol / n above this gets flagged

# ---- backtest: universe fetch -------------------------------------------
BACKTEST_PERIOD = "5y"
UNIVERSE_FETCH_CHUNK_SIZE = 50
UNIVERSE_FETCH_FALLBACK_CAP = 20

# ---- indicators ----------------------------------------------------------
SMA_WARMUP_BARS = max(SMA_PERIODS)  # the binding warm-up constraint (vs. ATR_PERIOD's 14)
