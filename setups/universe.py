"""Live/replay scan universe query (docs/SETUP_SPEC.md §2).

No existing accessor supports this definition: get_watchlist()
(db/database.py:611-623) filters only enabled=1, no wl_state column at all;
get_active_watchlist() (:626-642) hardcodes wl_state='ACTIVE', not IN (...);
get_symbols_by_state() (:680-693) takes one state, not a list. This is new,
additive work, not a modification of any of them.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from config import canonical_category
from setups.params import DEFAULT_SECURITY_TYPE, _NON_STOCK_TYPES
from setups.reason_codes import (
    EXCLUDED_CATEGORY,
    EXCLUDED_SECURITY_TYPE,
    EXCLUDED_WL_STATE,
)

# §2: category keys to exclude -- confirmed present in config.WATCHLIST by
# tests/test_setups_universe.py (guards a typo'd/reversed-direction key).
# Canonical (English) form -- a row stored under either spelling (the
# WATCHLIST key is still Hebrew today; PR A only widens matching, see
# config.canonical_category()) is excluded identically.
_EXCLUDED_CATEGORIES = frozenset({"Indices", "ETFs"})

_ALLOWED_WL_STATES = frozenset({"ACTIVE", "MONITOR"})


@dataclass
class UniverseResult:
    included: list[str] = field(default_factory=list)
    excluded: list[dict] = field(default_factory=list)


def get_setup_universe(conn: sqlite3.Connection) -> UniverseResult:
    """Takes an injected sqlite3.Connection (not db.database's module-level
    DB_PATH singleton) so this function is trivially testable against a
    temp DB. Deliberately does NOT depend on `conn.row_factory` being set
    to `sqlite3.Row` -- rows are unpacked positionally, in the exact order
    of the SELECT columns below, so this works against a plain connection
    with the default tuple row factory too. Row order of checks (not
    specified by §2's text): wl_state, then category, then security_type
    -- first match wins.
    """
    cursor = conn.execute(
        "SELECT symbol, category, security_type, wl_state FROM watchlist WHERE enabled = 1"
    )
    result = UniverseResult()
    for symbol, category, security_type, wl_state in cursor.fetchall():
        # A NULL/empty security_type is the column's own default
        # (db/database.py:144) -- never treated as a reason to exclude.
        security_type = security_type or DEFAULT_SECURITY_TYPE

        if wl_state not in _ALLOWED_WL_STATES:
            result.excluded.append({
                "symbol": symbol, "reason_code": EXCLUDED_WL_STATE, "wl_state": wl_state,
            })
            continue
        if canonical_category(category) in _EXCLUDED_CATEGORIES:
            result.excluded.append({
                "symbol": symbol, "reason_code": EXCLUDED_CATEGORY, "category": category,
            })
            continue
        if security_type in _NON_STOCK_TYPES:
            result.excluded.append({
                "symbol": symbol, "reason_code": EXCLUDED_SECURITY_TYPE,
                "security_type": security_type,
            })
            continue
        result.included.append(symbol)

    return result
