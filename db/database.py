import sqlite3
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

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
    if watchlist:
        populate_from_config(watchlist)


def populate_from_config(watchlist: dict) -> None:
    """Populate watchlist from config.py on first run."""
    for category, symbols in watchlist.items():
        for symbol in symbols:
            add_to_watchlist(symbol, category)


# ── Watchlist ────────────────────────────────────────────────────────────────

def add_to_watchlist(symbol: str, category: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO watchlist (symbol, category) VALUES (?, ?)",
            (symbol.upper(), category),
        )


def remove_from_watchlist(symbol: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM watchlist WHERE symbol = ?", (symbol.upper(),))


def get_watchlist() -> dict[str, list[str]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT symbol, category FROM watchlist ORDER BY category, symbol"
        ).fetchall()

    result: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        result[row["category"]].append(row["symbol"])
    return dict(result)


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
