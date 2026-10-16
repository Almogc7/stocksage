"""
Backtest Research Agent — ask free-form questions about the composite-engine
backtest (composite_backtest_signals/composite_backtest_runs) and the live
composite_signals table in plain language, instead of writing SQL by hand.

Claude Sonnet 5, manual tool-use loop (not the SDK's automatic Tool Runner):
this tool executes real SQL against db/stocksage.db, so every tool call is
validated and logged BEFORE it runs, in a loop this script owns end to end —
not handed off to an SDK helper that dispatches automatically.

Observation/analysis only. No write access, ever:
  1. The DB connection is opened as a genuine OS-level read-only handle
     (sqlite3 URI "?mode=ro") — even a validation bypass cannot write.
  2. Every query is statement-gated: must be a single SELECT/WITH statement
     (comments stripped, no stacked statements via ';'), and may not contain
     ATTACH/PRAGMA/DETACH/VACUUM/etc — mode=ro only protects the MAIN
     database file, not a second database opened via ATTACH, so that has to
     be blocked explicitly.
  3. Every query is table-gated: only composite_backtest_signals,
     composite_backtest_runs, composite_signals, and sqlite_master (for
     schema introspection) may appear after FROM/JOIN — alerts, watchlist,
     trades, etc. are structurally unreachable, not just discouraged.
  4. Every accepted AND rejected query is logged
     (stocksage.research_agent logger) with the raw SQL and outcome.
  5. Results are capped at 500 rows (a LIMIT is appended if the agent's
     query didn't include one) so a runaway SELECT * can't blow the context
     window.

v1 is numbers-only (no chart rendering) — plain-language answers grounded in
the query results. CLI only; not wired into the Telegram bot.

Usage (from repo root):
    python scripts/research_agent.py "What's the win rate for BULL regime signals?"
    python scripts/research_agent.py "Which layer correlates best with r_multiple?" --verbose
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anthropic
from dotenv import load_dotenv

from db.database import DB_PATH
from logging_setup import setup_logging

load_dotenv()

logger = logging.getLogger("stocksage.research_agent")

MODEL = "claude-sonnet-5"
MAX_ITERATIONS = 8
MAX_ROWS = 500

ALLOWED_TABLES = frozenset({
    "composite_backtest_signals", "composite_backtest_runs",
    "composite_signals", "sqlite_master",
})

_FORBIDDEN_KEYWORDS = re.compile(
    r"\b(insert|update|delete|drop|alter|attach|detach|pragma|vacuum|"
    r"replace|create|reindex|analyze|begin|commit|rollback|savepoint)\b",
    re.IGNORECASE,
)
_TABLE_REF = re.compile(r"\b(?:from|join)\s+([a-zA-Z_][a-zA-Z0-9_]*)", re.IGNORECASE)
_CTE_NAME = re.compile(r"(?:^with\s+|,\s*)([a-zA-Z_][a-zA-Z0-9_]*)\s*(?:\([^)]*\))?\s+as\s*\(", re.IGNORECASE)
_LINE_COMMENT = re.compile(r"--.*?(?=\n|$)")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_HAS_LIMIT = re.compile(r"\blimit\s+\d+\s*$", re.IGNORECASE)

SCHEMA_DOC = """
Tables you may query (read-only):

composite_signals — live nightly observation-mode logging of the composite
engine's flag_buy=True fires for the ACTIVE watchlist tier.
  id, symbol, signal_date, regime, total_score, required_score,
  trend_pts, momentum_pts, volume_pts, rs_pts, rs_ratio, required_rs,
  price, atr, stop_price, stop_multiplier, logged_at

composite_backtest_runs — one row per backtest run (append-only; a run_id
identifies which walk-forward pass produced a given set of signals).
  run_id, started_at, finished_at, period_years, universe_size,
  symbols_ok, symbols_failed, total_signals, notes

composite_backtest_signals — historical walk-forward composite signals +
their resolved forward outcomes (same look-ahead discipline as the live
engine). One row per (run_id, symbol, signal_date).
  id, run_id, symbol, signal_date, regime, total_score, required_score,
  trend_pts, momentum_pts, volume_pts, rs_pts, rs_ratio, required_rs,
  price, atr, stop_price, stop_multiplier,
  close_t1, close_t3, close_t5, close_t10, max_adverse_excursion,
  first_barrier_hit ('stop_loss' or 'none'), r_multiple,
  spy_close_at_signal, spy_close_t1, spy_close_t3, spy_close_t5, spy_close_t10,
  computed_at

Notes:
  * r_multiple is NULL until the signal is fully resolved (close_t10 known,
    or a stop_loss barrier was hit early) — filter WHERE r_multiple IS NOT
    NULL for win-rate / correlation questions.
  * SPY-relative excess return at horizon N = (close_tN/price - 1) -
    (spy_close_tN/spy_close_at_signal - 1).
  * Signals from the same symbol are NOT independent trials (overlapping
    windows, shared trend) — flag this if a finding rests on a handful of
    signals from one or two symbols.
  * composite_backtest_signals may hold MULTIPLE runs (multiple run_ids) —
    filter by run_id (composite_backtest_runs.run_id, most recent =
    MAX(started_at)) unless the question is explicitly about comparing runs.
"""

SYSTEM_PROMPT = f"""You are a research assistant for a stock-alerting \
system's composite scoring engine. You answer questions about backtest and \
live-observation results by querying a SQLite database via the \
run_sql_query tool. You may call it multiple times — e.g. explore what \
values/columns exist before computing a real answer.

{SCHEMA_DOC}

Rules:
- Only SELECT/WITH queries are accepted; anything else is rejected before \
it reaches the database.
- Ground every claim in an actual query result — never guess a number.
- When a finding rests on a small sample (single digits) or a handful of \
signals from one symbol, say so explicitly rather than stating it as a \
solid conclusion.
- Give a plain-language final answer with the concrete numbers that \
support it. No chart rendering in this version — describe magnitudes in \
words/numbers.
"""

TOOL_DEF = {
    "name": "run_sql_query",
    "description": (
        "Run a read-only SELECT/WITH query against the backtest database "
        "and return the matching rows as JSON. Only composite_signals, "
        "composite_backtest_runs, composite_backtest_signals, and "
        "sqlite_master may be queried."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "A single SELECT or WITH SQL statement."},
        },
        "required": ["query"],
        "additionalProperties": False,
    },
}


class RejectedQuery(ValueError):
    pass


def validate_select(sql: str) -> str:
    """Return the sanitized single statement, or raise RejectedQuery."""
    no_comments = _BLOCK_COMMENT.sub(" ", _LINE_COMMENT.sub(" ", sql))
    parts = [p.strip() for p in no_comments.split(";") if p.strip()]
    if len(parts) != 1:
        raise RejectedQuery("only a single SQL statement is allowed (no ';'-stacked statements)")
    stmt = parts[0]

    if not re.match(r"^(select|with)\b", stmt, re.IGNORECASE):
        raise RejectedQuery("only SELECT/WITH statements are allowed")
    if _FORBIDDEN_KEYWORDS.search(stmt):
        raise RejectedQuery("statement contains a forbidden keyword")

    referenced = {m.group(1).lower() for m in _TABLE_REF.finditer(stmt)}
    cte_names = {m.group(1).lower() for m in _CTE_NAME.finditer(stmt)}
    disallowed = referenced - {t.lower() for t in ALLOWED_TABLES} - cte_names
    if disallowed:
        raise RejectedQuery(f"query references disallowed table(s): {sorted(disallowed)}")

    if not _HAS_LIMIT.search(stmt):
        stmt = f"{stmt}\nLIMIT {MAX_ROWS}"
    return stmt


def run_sql_query(conn: sqlite3.Connection, raw_sql: str, verbose: bool) -> dict:
    try:
        stmt = validate_select(raw_sql)
    except RejectedQuery as e:
        logger.warning("[research_agent] REJECTED query: %s | reason=%s", raw_sql, e)
        print(f"  [sql] REJECTED: {e}")
        return {"error": str(e)}

    try:
        cur = conn.execute(stmt)
        rows = [dict(r) for r in cur.fetchmany(MAX_ROWS)]
    except sqlite3.Error as e:
        logger.info("[research_agent] query error: %s | sql=%s", e, stmt)
        print(f"  [sql] ERROR: {e}")
        return {"error": str(e)}

    logger.info("[research_agent] OK (%d rows): %s", len(rows), stmt.replace("\n", " "))
    print(f"  [sql] {len(rows)} row(s){' -- ' + stmt.splitlines()[0][:100] if verbose else ''}")
    if verbose:
        for line in stmt.splitlines():
            print(f"        {line}")
    return {"rows": rows, "row_count": len(rows)}


def ask(question: str, verbose: bool = False, effort: str = "medium",
        max_iterations: int = MAX_ITERATIONS) -> str:
    client = anthropic.Anthropic()
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    messages: list[dict] = [{"role": "user", "content": question}]

    try:
        for _ in range(max_iterations):
            response = client.messages.create(
                model=MODEL,
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                tools=[TOOL_DEF],
                thinking={"type": "adaptive"},
                output_config={"effort": effort},
                messages=messages,
            )

            if response.stop_reason != "tool_use":
                return "".join(b.text for b in response.content if b.type == "text")

            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                query = block.input.get("query", "")
                result = run_sql_query(conn, query, verbose)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result),
                    "is_error": "error" in result,
                })
            messages.append({"role": "user", "content": tool_results})

        return ("Stopped after the maximum number of query steps without a final answer. "
                "Try narrowing the question.")
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("question", help="Your question, in plain language")
    parser.add_argument("--verbose", action="store_true", help="Print every SQL statement executed")
    parser.add_argument("--effort", default="medium", choices=["low", "medium", "high", "xhigh", "max"])
    parser.add_argument("--max-iterations", type=int, default=MAX_ITERATIONS)
    args = parser.parse_args()

    setup_logging()
    if not DB_PATH.exists():
        print(f"No database found at {DB_PATH}.")
        return 1

    answer = ask(args.question, verbose=args.verbose, effort=args.effort, max_iterations=args.max_iterations)
    print(f"\n{answer}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
