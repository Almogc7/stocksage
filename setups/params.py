"""Reasoned-default parameters for the new setup system (docs/SETUP_SPEC.md).

Deliberately NOT in config.py: per SETUP_SPEC.md §1, new feature work for
this stack must not touch Stack A/B's own files. Every value here is a
"reasoned default" (SETUP_SPEC.md's own term) to be frozen in the
pre-registration file (§13) before any replay run, not derived from
observed data.
"""

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
