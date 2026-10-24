"""Reason codes produced by the setups/ pipeline (docs/SETUP_SPEC.md §12).

Non-exhaustive per the spec -- later steps (swing detection, fill/trigger,
outcomes) add their own codes here as they're implemented.
"""

# Universe exclusions (§2)
EXCLUDED_WL_STATE = "EXCLUDED_WL_STATE"
EXCLUDED_CATEGORY = "EXCLUDED_CATEGORY"
EXCLUDED_SECURITY_TYPE = "EXCLUDED_SECURITY_TYPE"

# Data validation (§3) -- MALFORMED_INDEX and INSUFFICIENT_HISTORY are new,
# not named in §12's text, added to stop bad frames from reaching indicator
# math at all.
MALFORMED_INDEX = "MALFORMED_INDEX"
INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
DATA_NOT_FINAL = "DATA_NOT_FINAL"
DATA_FETCH_FAILED = "DATA_FETCH_FAILED"
MISSING_OHLCV = "MISSING_OHLCV"
ZERO_VOLUME_LAST_BAR = "ZERO_VOLUME_LAST_BAR"

# Trend / eligibility filter (§4, §12)
TREND_FILTER_FAILED = "TREND_FILTER_FAILED"
LIQUIDITY_VETO = "LIQUIDITY_VETO"

# Setup definition (§5), stop/target (§7-8), vetoes (§9)
NO_SWING_STRUCTURE = "NO_SWING_STRUCTURE"
# New: the true global-minimum pullback low sits in the last 2 bars (not
# yet "confirmed" 2-sessions-old, §5 item 2) -- rejected outright, never
# re-anchored to an older, higher (non-minimal) low.
PULLBACK_LOW_UNCONFIRMED = "PULLBACK_LOW_UNCONFIRMED"
PULLBACK_TOO_SHALLOW = "PULLBACK_TOO_SHALLOW"  # §5 item 3
PULLBACK_TOO_OLD = "PULLBACK_TOO_OLD"  # §5 item 4
PRICE_OUTSIDE_RANGE = "PRICE_OUTSIDE_RANGE"  # §5 item 5 (both directions)
STOP_CAP_EXCEEDED = "STOP_CAP_EXCEEDED"  # §7
NO_RESISTANCE = "NO_RESISTANCE"  # §8, Option B
# New: the setup-DEFINITION-time 3:1 gate (§8) -- deliberately distinct
# from a future CHASE_RR_BELOW_3 (§6's post-FILL recompute), a different
# pipeline stage this step does not implement.
RR_BELOW_3 = "RR_BELOW_3"
EXTENSION_VETO = "EXTENSION_VETO"  # §9
REGIME_VETO = "REGIME_VETO"  # §9

# Outcome simulation (§6, §12) -- fill/trigger/outcome statuses and events,
# produced by setups/outcomes.py.
INVALIDATED_BEFORE_FILL = "INVALIDATED_BEFORE_FILL"  # §6
CHASE_RR_BELOW_3 = "CHASE_RR_BELOW_3"  # §6, post-fill recompute (distinct from RR_BELOW_3)
EXPIRED_UNFILLED = "EXPIRED_UNFILLED"  # §6, validity window elapsed, never crossed trigger
FILLED_STOPPED_SAME_DAY = "FILLED_STOPPED_SAME_DAY"  # §6, event code, included in every sample
DATA_GAP = "DATA_GAP"  # §12, excluded, counted separately
OPEN_AT_END = "OPEN_AT_END"  # §12, excluded, counted separately

# first_barrier_hit enum (§12) -- mirrors alert_outcomes' CHECK-constraint
# convention (db/database.py:350-364).
BARRIER_STOP = "stop"
BARRIER_TARGET = "target"
BARRIER_TIMEOUT_30 = "timeout_30"
