import sqlite3
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

_KNOWN_INDICES_MIGRATION: frozenset[str] = frozenset({"^GSPC", "^IXIC", "^DJI", "^RUT", "^VIX"})
_KNOWN_ETFS_MIGRATION: frozenset[str] = frozenset({
    "SPY", "VOO", "QQQ", "VGT", "XLK", "SOXX", "CIBR", "ARKK", "SCHG",
    "UFO", "NUKZ", "URA", "URNM", "NLR", "REMX", "COPX", "CPER", "SLX",
})

DB_PATH = Path(__file__).parent / "stocksage.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(watchlist: dict | None = None) -> None:
    with _connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS watchlist (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol     TEXT NOT NULL UNIQUE,
                category   TEXT NOT NULL,
                added_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS trades (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                action     TEXT NOT NULL CHECK(action IN ('BUY', 'SELL')),
                symbol     TEXT NOT NULL,
                quantity   REAL NOT NULL,
                price      REAL NOT NULL,
                note       TEXT DEFAULT '',
                traded_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS alerts (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol       TEXT NOT NULL,
                alert_type   TEXT NOT NULL,
                message      TEXT NOT NULL,
                triggered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS user_preferences (
                chat_id   TEXT PRIMARY KEY,
                language  TEXT DEFAULT 'he'
            );
        """)
    # Migrate existing schema to add soft-delete columns (safe for existing DBs)
    migrate_db()
    if watchlist:
        populate_from_config(watchlist)


def migrate_db() -> None:
    """
    Add new columns to existing databases without data loss.
    Called automatically by init_db() before any seeding. Idempotent.

    Columns added in v1:
      enabled    — 1 = active, 0 = user-removed (soft delete)
      removed_at — UTC timestamp of when the user removed the symbol

    Columns added in v2 (watchlist architecture):
      wl_state, security_type, relevance_score, last_evaluated,
      last_promoted, last_demoted, exclusion_reason, reeval_date,
      consec_promote_count, consec_demote_count, dwell_days, source

    New table in v2:
      symbol_categories — many-to-many symbol ↔ category mapping
    """
    with _connect() as conn:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(watchlist)").fetchall()}

        # v1 columns
        if "enabled" not in existing:
            conn.execute(
                "ALTER TABLE watchlist ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1"
            )
        if "removed_at" not in existing:
            conn.execute(
                "ALTER TABLE watchlist ADD COLUMN removed_at TIMESTAMP DEFAULT NULL"
            )

        # v2 columns
        v2_cols = [
            ("wl_state",             "TEXT NOT NULL DEFAULT 'MONITOR'"),
            ("security_type",        "TEXT DEFAULT 'stock'"),
            ("relevance_score",      "INTEGER DEFAULT NULL"),
            ("last_evaluated",       "TIMESTAMP DEFAULT NULL"),
            ("last_promoted",        "TIMESTAMP DEFAULT NULL"),
            ("last_demoted",         "TIMESTAMP DEFAULT NULL"),
            ("exclusion_reason",     "TEXT DEFAULT NULL"),
            ("reeval_date",          "DATE DEFAULT NULL"),
            ("consec_promote_count", "INTEGER DEFAULT 0"),
            ("consec_demote_count",  "INTEGER DEFAULT 0"),
            ("dwell_days",           "INTEGER DEFAULT 0"),
            ("source",               "TEXT DEFAULT 'config'"),
        ]
        for col_name, col_def in v2_cols:
            if col_name not in existing:
                conn.execute(f"ALTER TABLE watchlist ADD COLUMN {col_name} {col_def}")

        # v2 table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS symbol_categories (
                symbol   TEXT NOT NULL,
                category TEXT NOT NULL,
                PRIMARY KEY (symbol, category)
            )
        """)

        # Backfill symbol_categories from existing watchlist rows
        conn.execute("""
            INSERT OR IGNORE INTO symbol_categories (symbol, category)
            SELECT symbol, category FROM watchlist
        """)

        # Set initial wl_state/security_type for pre-existing rows
        rows = conn.execute("SELECT symbol, enabled FROM watchlist").fetchall()
        for row in rows:
            symbol = row["symbol"]
            enabled = row["enabled"]
            if not enabled:
                conn.execute(
                    "UPDATE watchlist SET wl_state = 'USER_REMOVED' WHERE symbol = ?",
                    (symbol,),
                )
            elif symbol.startswith("^") or symbol in _KNOWN_INDICES_MIGRATION:
                conn.execute(
                    "UPDATE watchlist SET wl_state = 'ETF_INDEX_CONTEXT', security_type = 'index'"
                    " WHERE symbol = ?",
                    (symbol,),
                )
            elif symbol in _KNOWN_ETFS_MIGRATION:
                conn.execute(
                    "UPDATE watchlist SET wl_state = 'ETF_INDEX_CONTEXT', security_type = 'etf'"
                    " WHERE symbol = ?",
                    (symbol,),
                )
            elif symbol.upper().endswith("-USD"):
                conn.execute(
                    "UPDATE watchlist SET wl_state = 'ETF_INDEX_CONTEXT', security_type = 'crypto'"
                    " WHERE symbol = ?",
                    (symbol,),
                )


# ── Watchlist ────────────────────────────────────────────────────────────────

def add_to_watchlist(symbol: str, category: str) -> None:
    """
    Add a symbol or re-enable one that was previously removed.

    Used by the /add Telegram command. Unlike the seed path, this always
    sets enabled=1 and updates the category, so a user can deliberately
    re-add a symbol they had removed and optionally move it to a new
    category at the same time. Also resets wl_state to MONITOR so the
    eligibility engine can re-evaluate the symbol on the next cycle.
    """
    sym = symbol.upper()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO watchlist (symbol, category, enabled, wl_state) VALUES (?, ?, 1, 'MONITOR')"
            " ON CONFLICT(symbol) DO UPDATE SET"
            "   enabled = 1,"
            "   category = excluded.category,"
            "   removed_at = NULL,"
            "   wl_state = 'MONITOR'",
            (sym, category),
        )
        conn.execute(
            "INSERT OR IGNORE INTO symbol_categories (symbol, category) VALUES (?, ?)",
            (sym, category),
        )


def _seed_symbol(symbol: str, category: str) -> None:
    """
    Insert a symbol only if it does not already exist (enabled or removed).

    INSERT OR IGNORE means:
    - New symbol     → inserted with enabled=1
    - Already exists (enabled=1) → row unchanged
    - Already exists (enabled=0, user-removed) → row unchanged (NOT re-enabled)

    This is the only path used during populate_from_config(). It is never
    used by user-facing commands so that seed data can never restore
    symbols the user has intentionally removed.
    """
    sym = symbol.upper()
    with _connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO watchlist (symbol, category, enabled) VALUES (?, ?, 1)",
            (sym, category),
        )
        conn.execute(
            "INSERT OR IGNORE INTO symbol_categories (symbol, category) VALUES (?, ?)",
            (sym, category),
        )


def remove_from_watchlist(symbol: str) -> None:
    """
    Soft-delete: mark symbol as removed without deleting the row.

    The row is kept with enabled=0 and a removed_at timestamp so that
    future calls to populate_from_config() (via INSERT OR IGNORE) see the
    existing row and leave it disabled. This prevents removed symbols from
    reappearing after application restarts or git pulls.
    """
    with _connect() as conn:
        conn.execute(
            "UPDATE watchlist SET enabled = 0, removed_at = ?, wl_state = 'USER_REMOVED'"
            " WHERE symbol = ?",
            (datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), symbol.upper()),
        )


def get_watchlist() -> dict[str, list[str]]:
    """Return all enabled (non-removed) symbols grouped by category."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT symbol, category FROM watchlist"
            " WHERE enabled = 1"
            " ORDER BY category, symbol"
        ).fetchall()

    result: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        result[row["category"]].append(row["symbol"])
    return dict(result)


def get_active_watchlist() -> dict[str, list[str]]:
    """Return symbols in ACTIVE state (enabled=1), grouped by canonical category, de-duplicated."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT symbol, category FROM watchlist"
            " WHERE wl_state = 'ACTIVE' AND enabled = 1"
            " ORDER BY category, symbol"
        ).fetchall()

    result: dict[str, list[str]] = defaultdict(list)
    seen: set[str] = set()
    for row in rows:
        sym = row["symbol"]
        if sym not in seen:
            seen.add(sym)
            result[row["category"]].append(sym)
    return dict(result)


# ── State / eligibility functions ─────────────────────────────────────────────

def update_symbol_state(symbol: str, state: str, reason: str = "") -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE watchlist SET wl_state = ?, exclusion_reason = ? WHERE symbol = ?",
            (state, reason, symbol.upper()),
        )


def get_symbols_by_state(state: str) -> list[str]:
    """Return enabled=1 symbols in the given wl_state (USER_REMOVED: enabled=0 but state matches)."""
    with _connect() as conn:
        if state == "USER_REMOVED":
            rows = conn.execute(
                "SELECT symbol FROM watchlist WHERE wl_state = ? ORDER BY symbol",
                (state,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT symbol FROM watchlist WHERE wl_state = ? AND enabled = 1 ORDER BY symbol",
                (state,),
            ).fetchall()
    return [row["symbol"] for row in rows]


def get_watchlist_summary() -> dict[str, int]:
    """Return state → count for all rows (both enabled and disabled)."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT wl_state, COUNT(*) AS cnt FROM watchlist GROUP BY wl_state"
        ).fetchall()
    return {row["wl_state"]: row["cnt"] for row in rows}


def add_category_tag(symbol: str, category: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO symbol_categories (symbol, category) VALUES (?, ?)",
            (symbol.upper(), category),
        )


def get_symbol_categories(symbol: str) -> list[str]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT category FROM symbol_categories WHERE symbol = ? ORDER BY category",
            (symbol.upper(),),
        ).fetchall()
    return [row["category"] for row in rows]


def update_eligibility(
    symbol: str,
    *,
    score: int | None,
    security_type: str,
    state: str,
    reason: str = "",
    now_str: str | None = None,
) -> None:
    ts = now_str or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with _connect() as conn:
        conn.execute(
            """UPDATE watchlist
               SET relevance_score = ?, security_type = ?, wl_state = ?,
                   exclusion_reason = ?, last_evaluated = ?
               WHERE symbol = ?""",
            (score, security_type, state, reason, ts, symbol.upper()),
        )


def update_hysteresis(symbol: str, *, promote_delta: int, demote_delta: int) -> None:
    """
    promote_delta=+1: increment consec_promote_count, reset consec_demote_count
    promote_delta=-1: reset consec_promote_count
    demote_delta=+1: increment consec_demote_count, reset consec_promote_count
    demote_delta=-1: reset consec_demote_count
    """
    sym = symbol.upper()
    with _connect() as conn:
        if promote_delta == 1:
            conn.execute(
                "UPDATE watchlist SET consec_promote_count = consec_promote_count + 1,"
                " consec_demote_count = 0 WHERE symbol = ?",
                (sym,),
            )
        elif promote_delta == -1:
            conn.execute(
                "UPDATE watchlist SET consec_promote_count = 0 WHERE symbol = ?", (sym,)
            )
        if demote_delta == 1:
            conn.execute(
                "UPDATE watchlist SET consec_demote_count = consec_demote_count + 1,"
                " consec_promote_count = 0 WHERE symbol = ?",
                (sym,),
            )
        elif demote_delta == -1:
            conn.execute(
                "UPDATE watchlist SET consec_demote_count = 0 WHERE symbol = ?", (sym,)
            )


def record_state_change(symbol: str, new_state: str) -> None:
    """Set wl_state, update promotion/demotion timestamps, reset counters, set dwell_days=0."""
    sym = symbol.upper()
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with _connect() as conn:
        if new_state == "ACTIVE":
            conn.execute(
                "UPDATE watchlist SET wl_state = ?, last_promoted = ?,"
                " consec_promote_count = 0, consec_demote_count = 0, dwell_days = 0"
                " WHERE symbol = ?",
                (new_state, now_str, sym),
            )
        elif new_state == "MONITOR":
            conn.execute(
                "UPDATE watchlist SET wl_state = ?, last_demoted = ?,"
                " consec_promote_count = 0, consec_demote_count = 0, dwell_days = 0"
                " WHERE symbol = ?",
                (new_state, now_str, sym),
            )
        else:
            conn.execute(
                "UPDATE watchlist SET wl_state = ?,"
                " consec_promote_count = 0, consec_demote_count = 0, dwell_days = 0"
                " WHERE symbol = ?",
                (new_state, sym),
            )


def get_symbol_status(symbol: str) -> dict | None:
    """Return all columns for one symbol plus its categories list."""
    sym = symbol.upper()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM watchlist WHERE symbol = ?", (sym,)
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        cats = conn.execute(
            "SELECT category FROM symbol_categories WHERE symbol = ? ORDER BY category",
            (sym,),
        ).fetchall()
        result["categories"] = [c["category"] for c in cats]
    return result


def increment_dwell_days() -> None:
    """Daily: increment dwell_days for all ACTIVE symbols."""
    with _connect() as conn:
        conn.execute(
            "UPDATE watchlist SET dwell_days = dwell_days + 1"
            " WHERE wl_state = 'ACTIVE' AND enabled = 1"
        )


def run_initial_classification(watchlist_config: dict) -> dict:
    """
    Assign initial wl_state values using config data and known symbol lists.
    No API calls — uses only data already in the DB.

    Rules (in order):
    1. enabled=0 → USER_REMOVED (already set by migrate_db/remove_from_watchlist)
    2. Invalid/foreign symbols → TEMPORARILY_INELIGIBLE with reason
    3. ETF/index/crypto symbols → ETF_INDEX_CONTEXT
    4. Symbols in INITIAL_ACTIVE_SET → ACTIVE
    5. Everything else → MONITOR

    Also reclassifies CCC from cloud/software to financials.
    Returns a summary dict: symbol → assigned_state.
    """
    from analyzers.eligibility import classify_security_type

    INITIAL_ACTIVE_SET: frozenset[str] = frozenset({
        "NVDA", "AMD", "AVGO", "ASML", "TSM", "AMAT", "KLAC", "LRCX", "MU",
        "GOOGL", "MSFT", "META", "AMZN", "AAPL", "TSLA",
        "PLTR", "CRWD", "PANW", "NET", "SNOW", "DDOG",
        "CEG", "VST", "GEV",
        "RKLB", "AXON", "RTX",
        "VRT", "EQIX", "CBRS",
    })

    INELIGIBLE_REASONS: dict[str, str] = {
        "CEZ":  "Foreign exchange primary listing — no reliable US volume",
        "KAP":  "No US data — LSE primary listing (KAP.L)",
        "YCA":  "No US data — AIM London listing (YCA.L)",
        "DYL":  "No US data — ASX primary listing (DYL.AX)",
        "FCU":  "No US data — TSX primary listing (FCU.TO)",
        "PDN":  "Near-zero US volume — ASX primary listing",
        "BOE":  "Unconfirmed ticker mapping — entity not verified",
        "AREC": "Price below minimum ($3.00 floor)",
        "FFWM": "No data — possible delistment",
        "CADE": "No data — possible data feed issue",
        "MOFG": "No data — possible data feed issue",
    }

    with _connect() as conn:
        rows = conn.execute("SELECT symbol, enabled FROM watchlist").fetchall()

    summary: dict[str, str] = {}

    for row in rows:
        symbol: str = row["symbol"]
        enabled: int = row["enabled"]

        if not enabled:
            summary[symbol] = "USER_REMOVED"
            continue

        # CCC reclassification: mortgage REIT, not cloud/software
        if symbol == "CCC":
            with _connect() as conn:
                conn.execute(
                    "UPDATE watchlist SET category = 'פיננסים' WHERE symbol = 'CCC'"
                )
                conn.execute(
                    "DELETE FROM symbol_categories WHERE symbol = 'CCC'"
                )
                conn.execute(
                    "INSERT OR IGNORE INTO symbol_categories (symbol, category)"
                    " VALUES ('CCC', 'פיננסים')"
                )

        if symbol in INELIGIBLE_REASONS:
            reason = INELIGIBLE_REASONS[symbol]
            with _connect() as conn:
                conn.execute(
                    "UPDATE watchlist SET wl_state = 'TEMPORARILY_INELIGIBLE',"
                    " exclusion_reason = ? WHERE symbol = ?",
                    (reason, symbol),
                )
            summary[symbol] = "TEMPORARILY_INELIGIBLE"
            continue

        sec_type = classify_security_type(symbol)
        if sec_type in ("etf", "index", "crypto"):
            with _connect() as conn:
                conn.execute(
                    "UPDATE watchlist SET wl_state = 'ETF_INDEX_CONTEXT',"
                    " security_type = ? WHERE symbol = ?",
                    (sec_type, symbol),
                )
            summary[symbol] = "ETF_INDEX_CONTEXT"
            continue

        if symbol in INITIAL_ACTIVE_SET:
            with _connect() as conn:
                conn.execute(
                    "UPDATE watchlist SET wl_state = 'ACTIVE', security_type = 'stock'"
                    " WHERE symbol = ?",
                    (symbol,),
                )
            summary[symbol] = "ACTIVE"
        else:
            with _connect() as conn:
                conn.execute(
                    "UPDATE watchlist SET wl_state = 'MONITOR', security_type = 'stock'"
                    " WHERE symbol = ?",
                    (symbol,),
                )
            summary[symbol] = "MONITOR"

    return summary


def populate_from_config(watchlist: dict) -> None:
    """
    Seed the watchlist from the default configuration.

    Uses _seed_symbol() (INSERT OR IGNORE) so that:
    - On a fresh empty database: all symbols are inserted.
    - On a database with existing rows: existing rows are untouched.
    - On a database with removed rows (enabled=0): removed symbols are NOT
      re-enabled. The user's removal decision is preserved.

    Safe to call on every startup — idempotent by design.
    """
    for category, symbols in watchlist.items():
        for symbol in symbols:
            _seed_symbol(symbol, category)


# ── Trades ───────────────────────────────────────────────────────────────────

def log_trade(
    action: str,
    symbol: str,
    quantity: float,
    price: float,
    note: str = "",
) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO trades (action, symbol, quantity, price, note) VALUES (?, ?, ?, ?, ?)",
            (action.upper(), symbol.upper(), quantity, price, note),
        )


def get_trades(symbol: str | None = None) -> list[dict]:
    query = "SELECT * FROM trades"
    params: tuple = ()
    if symbol:
        query += " WHERE symbol = ?"
        params = (symbol.upper(),)
    query += " ORDER BY traded_at DESC"

    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def delete_trade(trade_id: int) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM trades WHERE id = ?", (trade_id,))


def get_trade_summary(symbol: str) -> dict:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT action, quantity, price FROM trades WHERE symbol = ?",
            (symbol.upper(),),
        ).fetchall()

    total_bought = 0.0
    total_cost = 0.0
    total_sold = 0.0
    total_revenue = 0.0

    for row in rows:
        if row["action"] == "BUY":
            total_bought += row["quantity"]
            total_cost += row["quantity"] * row["price"]
        else:
            total_sold += row["quantity"]
            total_revenue += row["quantity"] * row["price"]

    net_quantity = total_bought - total_sold
    avg_buy_price = (total_cost / total_bought) if total_bought else 0.0
    realized_pnl = total_revenue - (total_sold * avg_buy_price)

    return {
        "symbol": symbol.upper(),
        "avg_buy_price": round(avg_buy_price, 4),
        "total_quantity": round(net_quantity, 4),
        "realized_pnl": round(realized_pnl, 4),
    }


# ── Alerts ───────────────────────────────────────────────────────────────────

def log_alert(symbol: str, alert_type: str, message: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO alerts (symbol, alert_type, message, triggered_at) VALUES (?, ?, ?, ?)",
            # Store in SQLite-native UTC format "YYYY-MM-DD HH:MM:SS" (space, not T) so
            # that datetime('now', 'utc', ...) comparisons work correctly via string
            # ordering. isoformat() produces a "T" separator which sorts above " " and
            # would make every stored timestamp appear permanently within cooldown.
            (symbol.upper(), alert_type, message,
             datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")),
        )


def was_alerted_recently(symbol: str, hours: int = 4) -> bool:
    """True if this symbol has any alert logged within the last `hours` hours."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM alerts WHERE symbol = ?"
            " AND triggered_at >= datetime('now', 'utc', ? || ' hours')"
            " LIMIT 1",
            (symbol.upper(), f"-{hours}"),
        ).fetchone()
    return row is not None


def get_muted_symbols(hours: int = 4) -> list[str]:
    """Return distinct symbols that have been alerted within the last `hours` hours."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT symbol FROM alerts"
            " WHERE triggered_at >= datetime('now', 'utc', ? || ' hours')",
            (f"-{hours}",),
        ).fetchall()
    return [row["symbol"] for row in rows]


# ── User preferences ─────────────────────────────────────────────────────────

def get_language(chat_id: str) -> str:
    with _connect() as conn:
        row = conn.execute(
            "SELECT language FROM user_preferences WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()
    return row["language"] if row else "he"


def set_language(chat_id: str, lang: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO user_preferences (chat_id, language) VALUES (?, ?)"
            " ON CONFLICT(chat_id) DO UPDATE SET language = excluded.language",
            (chat_id, lang),
        )


def get_today_alerts() -> list[dict]:
    today = date.today().isoformat()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM alerts WHERE DATE(triggered_at) = ? ORDER BY triggered_at DESC",
            (today,),
        ).fetchall()
    return [dict(row) for row in rows]
