"""The ONLY door to the bot database: read-only, one connection per call."""
import json
import re
import sqlite3
from contextlib import contextmanager
from typing import Iterator

from . import settings

FROM_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@contextmanager
def read_conn() -> Iterator[sqlite3.Connection]:
    uri = settings.bot_db_path().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=2.0)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")  # second guard beyond mode=ro
        yield conn
    finally:
        conn.close()


def fetch_watchlist() -> list[dict]:
    with read_conn() as conn:
        rows = conn.execute(
            "SELECT symbol, category, wl_state, pinned FROM watchlist"
            " WHERE enabled = 1 ORDER BY category, symbol"
        ).fetchall()
    return [
        {
            "symbol": r["symbol"],
            "category": r["category"],
            "wl_state": r["wl_state"],
            "pinned": bool(r["pinned"]),
        }
        for r in rows
    ]


def ping() -> None:
    with read_conn() as conn:
        conn.execute("SELECT 1").fetchone()


def _outcome(row: sqlite3.Row) -> dict:
    pending = row["close_t10"] is None
    return {
        "pending": pending,
        "close_t1": row["close_t1"],
        "close_t3": row["close_t3"],
        "close_t5": row["close_t5"],
        "close_t10": row["close_t10"],
        "max_adverse_excursion": row["max_adverse_excursion"],
        "first_barrier_hit": row["first_barrier_hit"],
        "r_multiple": row["r_multiple"],
    }


def _fetch_alerts(conn: sqlite3.Connection, symbol: str, from_date: str | None) -> list[dict]:
    rows = conn.execute(
        """SELECT a.id, a.alert_type, a.message, a.triggered_at,
                  s.score, s.verdict, s.price_at_alert, s.rsi, s.atr,
                  s.stop_loss, s.take_profit, s.triggered_signals,
                  o.close_t1, o.close_t3, o.close_t5, o.close_t10,
                  o.max_adverse_excursion, o.first_barrier_hit, o.r_multiple
           FROM alerts a
           LEFT JOIN alert_signals s ON s.alert_id = a.id
           LEFT JOIN alert_outcomes o ON o.alert_id = a.id
           WHERE a.symbol = ?
             AND (? IS NULL OR DATE(a.triggered_at) >= ?)
           ORDER BY a.triggered_at ASC""",
        (symbol, from_date, from_date),
    ).fetchall()
    out = []
    for r in rows:
        out.append({
            "id": r["id"],
            "triggered_at": r["triggered_at"],
            "alert_type": r["alert_type"],
            "message": r["message"],
            "score": r["score"],
            "verdict": r["verdict"],
            "price_at_alert": r["price_at_alert"],
            "rsi": r["rsi"],
            "atr": r["atr"],
            "stop_loss": r["stop_loss"],
            "take_profit": r["take_profit"],
            "triggered_signals": json.loads(r["triggered_signals"]) if r["triggered_signals"] else [],
            "outcome": _outcome(r),
        })
    return out


def _fetch_composite_signals(
    conn: sqlite3.Connection, symbol: str, from_date: str | None
) -> list[dict]:
    rows = conn.execute(
        """SELECT cs.id, cs.signal_date, cs.regime, cs.total_score,
                  cs.required_score, cs.trend_pts, cs.momentum_pts,
                  cs.volume_pts, cs.rs_pts, cs.rs_ratio, cs.required_rs,
                  cs.price, cs.atr, cs.stop_price, cs.stop_multiplier,
                  co.close_t1, co.close_t3, co.close_t5, co.close_t10,
                  co.max_adverse_excursion, co.first_barrier_hit, co.r_multiple
           FROM composite_signals cs
           LEFT JOIN composite_signal_outcomes co ON co.signal_id = cs.id
           WHERE cs.symbol = ?
             AND (? IS NULL OR cs.signal_date >= ?)
           ORDER BY cs.signal_date ASC""",
        (symbol, from_date, from_date),
    ).fetchall()
    out = []
    for r in rows:
        out.append({
            "id": r["id"],
            "signal_date": r["signal_date"],
            "regime": r["regime"],
            "total_score": r["total_score"],
            "required_score": r["required_score"],
            "trend_pts": r["trend_pts"],
            "momentum_pts": r["momentum_pts"],
            "volume_pts": r["volume_pts"],
            "rs_pts": r["rs_pts"],
            "rs_ratio": r["rs_ratio"],
            "required_rs": r["required_rs"],
            "price": r["price"],
            "atr": r["atr"],
            "stop_price": r["stop_price"],
            "stop_multiplier": r["stop_multiplier"],
            "outcome": _outcome(r),
        })
    return out


def _latest_finished_backtest_run_id(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        """SELECT run_id FROM composite_backtest_runs
           WHERE finished_at IS NOT NULL
           ORDER BY started_at DESC LIMIT 1"""
    ).fetchone()
    return row["run_id"] if row else None


def _fetch_backtest_signals(
    conn: sqlite3.Connection, symbol: str, from_date: str | None, run_id: str
) -> list[dict]:
    rows = conn.execute(
        """SELECT id, signal_date, regime, total_score, required_score,
                  trend_pts, momentum_pts, volume_pts, rs_pts, rs_ratio,
                  required_rs, price, atr, stop_price, stop_multiplier,
                  close_t1, close_t3, close_t5, close_t10,
                  max_adverse_excursion, first_barrier_hit, r_multiple
           FROM composite_backtest_signals
           WHERE run_id = ? AND symbol = ?
             AND (? IS NULL OR signal_date >= ?)
           ORDER BY signal_date ASC""",
        (run_id, symbol, from_date, from_date),
    ).fetchall()
    out = []
    for r in rows:
        out.append({
            "id": r["id"],
            "signal_date": r["signal_date"],
            "regime": r["regime"],
            "total_score": r["total_score"],
            "required_score": r["required_score"],
            "trend_pts": r["trend_pts"],
            "momentum_pts": r["momentum_pts"],
            "volume_pts": r["volume_pts"],
            "rs_pts": r["rs_pts"],
            "rs_ratio": r["rs_ratio"],
            "required_rs": r["required_rs"],
            "price": r["price"],
            "atr": r["atr"],
            "stop_price": r["stop_price"],
            "stop_multiplier": r["stop_multiplier"],
            "outcome": _outcome(r),
        })
    return out


def fetch_signals_for_symbol(
    symbol: str, from_date: str | None, include_backtest: bool
) -> dict:
    with read_conn() as conn:
        alerts = _fetch_alerts(conn, symbol, from_date)
        composite_signals = _fetch_composite_signals(conn, symbol, from_date)
        backtest_run_id = None
        backtest_signals: list[dict] = []
        if include_backtest:
            backtest_run_id = _latest_finished_backtest_run_id(conn)
            if backtest_run_id is not None:
                backtest_signals = _fetch_backtest_signals(
                    conn, symbol, from_date, backtest_run_id
                )
    return {
        "symbol": symbol,
        "from": from_date,
        "alerts": alerts,
        "composite_signals": composite_signals,
        "backtest_run_id": backtest_run_id,
        "backtest_signals": backtest_signals,
    }
