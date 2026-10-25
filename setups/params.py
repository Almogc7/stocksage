"""Reasoned-default parameters for the new setup system (docs/SETUP_SPEC.md).

Deliberately NOT in config.py: per SETUP_SPEC.md §1, new feature work for
this stack must not touch Stack A/B's own files. Every value here is a
"reasoned default" (SETUP_SPEC.md's own term) to be frozen in the
pre-registration file (§13) before any replay run, not derived from
observed data.
"""
from dataclasses import dataclass, replace

# §4: "rising SMA150" lookback -- compare today's SMA150 to its value this
# many sessions ago. Reasoned default: ~1 trading month.
SLOPE_LOOKBACK_SESSIONS = 20

# New: lookback window for averaging volume/dollar-volume against config.py's
# existing liquidity floors. Not specified in the spec text; mirrors
# data/market_data_validator.py's own lookback_days parameter in shape only
# (not imported -- that module's exact value is its own concern).
LIQUIDITY_LOOKBACK_SESSIONS = 20

# §3: INSUFFICIENT_HISTORY floor. The longest lookback this step needs is
# SMA200's 200-bar window (bigger than SMA150's 150 + SLOPE_LOOKBACK_SESSIONS
# = 170), plus a 20-bar margin so SMA200 isn't evaluated from a single
# barely-computable point -- reusing the same 20-session unit already chosen
# above for the slope check.
MIN_HISTORY_BARS = 220

# Copied, not imported, from services/watchlist_evaluator.py:100 -- keeps
# setups/ decoupled from that module's internals per the task's own
# constraint. A drift-guard test (tests/test_setups_evaluate.py) asserts
# this stays equal to the original.
_NON_STOCK_TYPES = frozenset({"etf", "index", "crypto"})

# Mirrors the watchlist.security_type column's own default
# (db/database.py:144, `"security_type" TEXT DEFAULT 'stock'`) -- named
# here instead of as a bare literal in setups/universe.py so the two stay
# findable as one thing; a test asserts this against db/database.py's
# actual migration source text.
DEFAULT_SECURITY_TYPE = "stock"


# ── Setup definition (§5-9) -- reasoned defaults for V1 ────────────────────
#
# Each is a documented placeholder pending the pre-registration freeze
# (§13), not derived from observed data. `SWING_LOOKBACK_BARS` (§13's V2
# widens it to 120) and `TARGET_MODE` (§13's V3, now promoted to primary --
# see TARGET_MODE_MEASURED_3R_IF_NO_RESISTANCE below -- and a future V4)
# are the two knobs named as future-variant seams.

# §5 item 2/§8: swing-high lookback, shared by find_confirmed_swing_highs()
# and the target search (both read the same confirmed-high list).
SWING_LOOKBACK_BARS = 50

# §5 item 3: pullback-depth floor, in ATR multiples.
PULLBACK_DEPTH_MIN_ATR = 1.0

# §5 item 4: SL recency ceiling, in trading sessions.
PULLBACK_MAX_AGE_SESSIONS = 15

# §5 item 2: SL minimum staleness, in completed trading sessions -- a
# separate, unrelated rule from the swing-high fractal confirmation lag
# (see setups/swings.py's module docstring).
SL_MIN_AGE_SESSIONS = 2

# §7: stop buffer beyond the structural low, in ATR multiples.
STOP_BUFFER_ATR = 0.5

# §7: stop cap = min(STOP_CAP_PCT_OF_ENTRY * entry, STOP_CAP_ATR_MULT * atr).
STOP_CAP_PCT_OF_ENTRY = 0.12
STOP_CAP_ATR_MULT = 5.0

# §8: minimum reward:risk multiple required at setup-definition time.
MIN_RR = 3.0

# §8: target-selection mode. V1 (default) = Option B, reject via
# NO_RESISTANCE when none found. A future V4 ("HIGH_252") variant would
# add a new mode value here -- not branched on yet.
TARGET_MODE = "NEAREST_RESISTANCE"

# §8/§13, V3 (promoted to primary -- the counts-only feasibility sample
# gave V1/V2 zero setups, 2,073 nights dying at the target step alone:
# 848 NO_RESISTANCE, 1,225 RR_BELOW_3). Falls back to a measured-3R
# target (trigger + min_rr * risk) only when NO confirmed swing high
# above the trigger exists within swing_lookback_bars; identical to V1
# otherwise, including RR_BELOW_3 when a confirmed resistance exists but
# is closer than min_rr * risk.
TARGET_MODE_MEASURED_3R_IF_NO_RESISTANCE = "MEASURED_3R_IF_NO_RESISTANCE"

# §8 float-safety: the R:R gate's comparison tolerance. A MEASURED_3R
# target stores rr as exactly params.min_rr (not a float division
# result), but the gate itself also uses this epsilon so the robustness
# doesn't depend on that exact-value choice staying true forever.
RR_EPSILON = 1e-9

# §9: extension veto, in ATR multiples above (unrounded) SMA150.
EXTENSION_VETO_ATR = 6.0

# §6: setup validity window, in trading sessions from the scan date.
VALIDITY_WINDOW_SESSIONS = 5


@dataclass(frozen=True)
class SetupParams:
    """Bundles every §5-9 reasoned default above so define_setup() takes
    one injectable object -- the seam a future V2 (wider swing_lookback_bars)
    or V3/V4 (different target_mode) variant would override, without
    changing define_setup()'s own signature."""
    swing_lookback_bars: int = SWING_LOOKBACK_BARS
    pullback_depth_min_atr: float = PULLBACK_DEPTH_MIN_ATR
    pullback_max_age_sessions: int = PULLBACK_MAX_AGE_SESSIONS
    sl_min_age_sessions: int = SL_MIN_AGE_SESSIONS
    stop_buffer_atr: float = STOP_BUFFER_ATR
    stop_cap_pct_of_entry: float = STOP_CAP_PCT_OF_ENTRY
    stop_cap_atr_mult: float = STOP_CAP_ATR_MULT
    min_rr: float = MIN_RR
    target_mode: str = TARGET_MODE
    extension_veto_atr: float = EXTENSION_VETO_ATR
    validity_window_sessions: int = VALIDITY_WINDOW_SESSIONS


V1_PARAMS = SetupParams()

# §13: V3, promoted to primary (see TARGET_MODE_MEASURED_3R_IF_NO_RESISTANCE
# above) -- identical to V1 except the target step.
V3_PARAMS = replace(V1_PARAMS, target_mode=TARGET_MODE_MEASURED_3R_IF_NO_RESISTANCE)


# ── Outcome simulation (§6, §12) -- reasoned defaults for V1 ───────────────

# §12: 10 bps slippage each way, settled 2026-10-22.
SLIPPAGE_BPS = 10.0

# §12: 30-session hold horizon (distinct from populate_outcomes.py's own
# HORIZON_DAYS=10 -- that module is not touched, per §12).
TIMEOUT_SESSIONS = 30


@dataclass(frozen=True)
class OutcomeParams:
    """Bundles setup-outcome-simulation reasoned defaults for
    simulate_outcome() (setups/outcomes.py). min_rr reuses §8's MIN_RR
    constant directly -- it is the same 3:1 gate, just recomputed at the
    real fill price instead of at setup-definition time (§6)."""
    min_rr: float = MIN_RR
    slippage_bps: float = SLIPPAGE_BPS
    timeout_sessions: int = TIMEOUT_SESSIONS


V1_OUTCOME_PARAMS = OutcomeParams()
