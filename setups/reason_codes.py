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
