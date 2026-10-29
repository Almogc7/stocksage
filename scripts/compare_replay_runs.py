#!/usr/bin/env python
"""Read-only comparison of two replay-harness results DBs
(docs/SETUP_SPEC.md section 13/14). Used as the acceptance check for
speed-only changes to scripts/replay_setups.py / setups/replay.py: run the
same universe/dates/variants through the OLD code (into db_a) and the NEW
code (into db_b), then diff every replay_candidates row. A speed-only
change must produce zero differences.

Never writes to either DB (opened via a read-only sqlite URI). Introspects
columns via PRAGMA table_info rather than hardcoding a column list, so it
tolerates one DB having columns the other lacks (e.g. a schema migration
between runs) -- comparison is over the columns common to both.

Usage:
    python scripts/compare_replay_runs.py db_a.db db_b.db [--variants V3,V1]
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path


def _open_readonly(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _candidate_columns(conn: sqlite3.Connection) -> list[str]:
    return [row[1] for row in conn.execute("PRAGMA table_info(replay_candidates)").fetchall()]


def _common_comparable_columns(conn_a: sqlite3.Connection, conn_b: sqlite3.Connection) -> list[str]:
    """Every replay_candidates column except run_id (which differs between
    runs by construction -- each run gets its own autoincrement id even
    when comparing the exact same symbol/variant/bound/scan_date)."""
    cols_a = set(_candidate_columns(conn_a))
    cols_b = set(_candidate_columns(conn_b))
    common = cols_a & cols_b
    common.discard("run_id")
    if not common:
        raise RuntimeError("replay_candidates has no comparable columns in common between the two DBs")
    # Deterministic order for stable tuple comparisons and output.
    return sorted(common)


def _variant_bound_pairs(conn: sqlite3.Connection, variants: set[str] | None) -> set[tuple[str, str]]:
    rows = conn.execute("SELECT DISTINCT variant, bound FROM replay_candidates").fetchall()
    pairs = {(r["variant"], r["bound"]) for r in rows}
    if variants is not None:
        pairs = {p for p in pairs if p[0] in variants}
    return pairs


def _rows_for(conn: sqlite3.Connection, columns: list[str], variant: str, bound: str) -> set[tuple]:
    col_list = ", ".join(columns)
    cursor = conn.execute(
        f"SELECT {col_list} FROM replay_candidates WHERE variant = ? AND bound = ?",
        (variant, bound),
    )
    return {tuple(row) for row in cursor.fetchall()}


def compare_replay_runs(
    db_a: Path, db_b: Path, variants: set[str] | None = None,
) -> tuple[int, list[str]]:
    """Returns (exit_code, report_lines). exit_code is 0 iff every
    (variant, bound) pair present in either DB has identical row sets in
    both, over the columns common to both DBs' replay_candidates table."""
    conn_a = _open_readonly(db_a)
    conn_b = _open_readonly(db_b)
    try:
        columns = _common_comparable_columns(conn_a, conn_b)
        pairs = _variant_bound_pairs(conn_a, variants) | _variant_bound_pairs(conn_b, variants)

        lines: list[str] = []
        total_only_a = 0
        total_only_b = 0
        total_rows = 0
        for variant, bound in sorted(pairs):
            rows_a = _rows_for(conn_a, columns, variant, bound)
            rows_b = _rows_for(conn_b, columns, variant, bound)
            only_a = rows_a - rows_b
            only_b = rows_b - rows_a
            total_only_a += len(only_a)
            total_only_b += len(only_b)
            total_rows += len(rows_a | rows_b)
            idx = columns.index("accepted")
            accepted_a = sum(1 for r in rows_a if r[idx])
            accepted_b = sum(1 for r in rows_b if r[idx])
            lines.append(
                f"{variant}/{bound}: only_in_a={len(only_a)} only_in_b={len(only_b)} "
                f"accepted_a={accepted_a} accepted_b={accepted_b}"
            )

        if total_only_a == 0 and total_only_b == 0:
            lines.append(f"A and B match: {total_rows} rows across {len(pairs)} (variant, bound) pairs")
            return 0, lines
        lines.append(
            f"MISMATCH: {total_only_a} rows only in A, {total_only_b} rows only in B "
            f"across {len(pairs)} (variant, bound) pairs"
        )
        return 1, lines
    finally:
        conn_a.close()
        conn_b.close()


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("db_a")
    p.add_argument("db_b")
    p.add_argument("--variants", default=None,
                    help="Comma-separated subset of variants to compare; default: every "
                         "variant present in either DB.")
    return p


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)
    variants = None
    if args.variants is not None:
        variants = {v.strip().upper() for v in args.variants.split(",") if v.strip()}

    exit_code, lines = compare_replay_runs(Path(args.db_a), Path(args.db_b), variants)
    for line in lines:
        print(line)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
