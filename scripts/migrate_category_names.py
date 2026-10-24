"""One-shot migration: rename the 12 Hebrew watchlist.category /
symbol_categories.category values to their English names
(config.HEBREW_TO_ENGLISH_CATEGORY). PR B of "English category names"
(PR A / PR A.2 already shipped -- every live read path already accepts
either spelling via config.canonical_category(); this script makes the
stored data agree).

Grepped db/database.py's own CREATE TABLE statements and column list
first: the only two columns anywhere in this database that store a
category string are `watchlist.category` and `symbol_categories.category`.
(`evaluation_run_changes` has JSON snapshot columns, but
services/watchlist_evaluator.py's `_build_db_fields()` never includes
`category` among the fields it snapshots -- confirmed by reading it --
so no historical audit JSON embeds a category string either.) Nothing
else needs touching.

Deliberately does NOT import db.database -- this script is a standalone
tool, not part of the live app's import graph, and must work even if
db.database's own module-level state (DB_PATH, etc.) has been swapped by
something else in-process. The default DB path below is computed the
same way db/database.py computes its own (relative to this file's
location in the repo), without importing that module.

Dry run by default. --apply is required to write anything, and writing
against the DEFAULT (repo) path additionally requires --i-have-a-backup
-- a second, explicit human acknowledgment on top of this script's own
mandatory, verified online backup, so a bare --apply can never land on
the live file from muscle memory. --db-path points at a different file
(e.g. a disposable copy) and does not need --i-have-a-backup.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Put the repo root on sys.path BEFORE importing config -- running this
# file directly (`python scripts/migrate_category_names.py ...`) does not
# otherwise put the repo root on sys.path (only the script's own
# directory, scripts/, is added automatically), so `import config` would
# raise ModuleNotFoundError regardless of the current working directory.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from config import HEBREW_TO_ENGLISH_CATEGORY  # noqa: E402 -- must follow the sys.path fix above

import argparse
import sqlite3
from datetime import datetime, timezone

# The 15 real categories, matched under either spelling -- used by
# --audit to find anything that isn't one of them.
_KNOWN_CATEGORIES = frozenset(HEBREW_TO_ENGLISH_CATEGORY.keys()) | frozenset(
    HEBREW_TO_ENGLISH_CATEGORY.values()
) | frozenset({"ETFs", "AI & Semiconductors", "Quantum Computing"})

_LOCK_TIMEOUT_SECONDS = 5.0
_LOCKED_MARKER = "database is locked"


def _default_db_path() -> Path:
    """db/stocksage.db, computed the same way db/database.py computes
    its own DB_PATH -- without importing that module."""
    return _REPO_ROOT / "db" / "stocksage.db"


def _reconfigure_stdout_for_utf8() -> None:
    """Windows console safety: category names (Hebrew) must never raise
    UnicodeEncodeError on a cp1252-ish console. reconfigure() is a no-op
    on streams that don't support it (e.g. when stdout is already piped
    through something that doesn't have the method) -- guarded."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def _escaped(name: str) -> str:
    return name.encode("unicode_escape").decode("ascii")


def _fmt_category(name: str) -> str:
    """Shows both the native text and its unicode-escape form, so a
    Hebrew name is always identifiable even if the terminal it's
    actually displayed on still mangles the glyphs."""
    return f"{name} ({_escaped(name)})"


def _open_readonly(db_path: Path) -> sqlite3.Connection:
    """mode=ro -- the connection physically cannot write, regardless of
    what SQL is sent to it (dry run / --audit use only this)."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=_LOCK_TIMEOUT_SECONDS)
    conn.row_factory = sqlite3.Row
    return conn


def _open_writable(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=_LOCK_TIMEOUT_SECONDS, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


def _watchlist_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) AS n FROM watchlist").fetchone()["n"]


def _symbol_categories_distinct_symbols(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT COUNT(DISTINCT symbol) AS n FROM symbol_categories"
    ).fetchone()["n"]


def _symbol_categories_row_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) AS n FROM symbol_categories").fetchone()["n"]


def _category_counts(conn: sqlite3.Connection, table: str, category: str) -> int:
    return conn.execute(
        f"SELECT COUNT(*) AS n FROM {table} WHERE category = ?", (category,)
    ).fetchone()["n"]


# ── Count table (dry run and post-apply reporting share this) ────────────────

def _print_count_table(conn: sqlite3.Connection, predicted: bool) -> None:
    header = "PREDICTED after --apply" if predicted else "ACTUAL after --apply"
    print(f"{'category (old -> new)':<70} {'watchlist':>10} {'symbol_categories':>18}")
    print("-" * 100)
    for old, new in sorted(HEBREW_TO_ENGLISH_CATEGORY.items()):
        wl_old = _category_counts(conn, "watchlist", old)
        wl_new = _category_counts(conn, "watchlist", new)
        sc_old = _category_counts(conn, "symbol_categories", old)
        sc_new = _category_counts(conn, "symbol_categories", new)

        if predicted:
            colliding = conn.execute(
                "SELECT COUNT(*) AS n FROM symbol_categories a"
                " WHERE a.category = ? AND EXISTS ("
                "   SELECT 1 FROM symbol_categories b"
                "   WHERE b.symbol = a.symbol AND b.category = ?"
                " )",
                (old, new),
            ).fetchone()["n"]
            renameable = sc_old - colliding
            wl_new_after = wl_new + wl_old
            sc_new_after = sc_new + renameable
            wl_old_after = 0
            sc_old_after = 0
        else:
            wl_new_after, sc_new_after = wl_new, sc_new
            wl_old_after, sc_old_after = wl_old, sc_old

        label = f"{_fmt_category(old)} -> {_fmt_category(new)}"
        print(f"{label:<70} before: wl={wl_old:>3} sc={sc_old:>3}")
        print(f"{'':<70} {header}: wl={wl_old_after:>3}->{wl_new_after:<3} "
              f"sc={sc_old_after:>3}->{sc_new_after:<3}")
    print("-" * 100)


# ── Dry run ────────────────────────────────────────────────────────────────

def run_dry_run(db_path: Path) -> int:
    conn = _open_readonly(db_path)
    try:
        print(f"DRY RUN against {db_path} (read-only connection -- cannot write)\n")
        _print_count_table(conn, predicted=True)
        print("\nNo changes written. Re-run with --apply to write.")
    finally:
        conn.close()
    return 0


# ── Audit ────────────────────────────────────────────────────────────────────

def run_audit(db_path: Path) -> int:
    conn = _open_readonly(db_path)
    try:
        print(f"AUDIT against {db_path} (read-only connection -- cannot write, never fixes anything)\n")
        stray_found = False
        for table in ("watchlist", "symbol_categories"):
            rows = conn.execute(f"SELECT DISTINCT category FROM {table}").fetchall()
            stray = sorted(r["category"] for r in rows if r["category"] not in _KNOWN_CATEGORIES)
            if stray:
                stray_found = True
                print(f"{table}: {len(stray)} stray categor{'y' if len(stray) == 1 else 'ies'} found:")
                for cat in stray:
                    print(f"  - {_fmt_category(cat)}")
            else:
                print(f"{table}: no stray categories.")
        if not stray_found:
            print("\nNothing outside the 15 known categories (either spelling).")
    finally:
        conn.close()
    return 0


# ── Apply ────────────────────────────────────────────────────────────────────

def _backup_database(db_path: Path) -> Path:
    """Online backup via the sqlite3 backup API (safe against a live
    writer, unlike a plain file copy), to a timestamped sibling file.
    Verified afterward -- never trusted blind -- by: opening it read-only,
    running PRAGMA integrity_check (must return exactly "ok"), and
    comparing BOTH watchlist and symbol_categories row counts against the
    live source.
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    backup_path = db_path.with_name(f"{db_path.name}.bak-{timestamp}")

    src_conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=_LOCK_TIMEOUT_SECONDS)
    src_conn.row_factory = sqlite3.Row
    try:
        expected_watchlist = _watchlist_count(src_conn)
        expected_symbol_categories = _symbol_categories_row_count(src_conn)
        dest_conn = sqlite3.connect(str(backup_path))
        try:
            src_conn.backup(dest_conn)
        finally:
            dest_conn.close()
    finally:
        src_conn.close()

    verify_conn = sqlite3.connect(f"file:{backup_path}?mode=ro", uri=True)
    verify_conn.row_factory = sqlite3.Row
    try:
        integrity = verify_conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(
                f"Backup verification FAILED: PRAGMA integrity_check on {backup_path} "
                f"returned {integrity!r}, not 'ok'. Refusing to proceed."
            )
        actual_watchlist = _watchlist_count(verify_conn)
        actual_symbol_categories = _symbol_categories_row_count(verify_conn)
    finally:
        verify_conn.close()

    if actual_watchlist != expected_watchlist:
        raise RuntimeError(
            f"Backup verification FAILED: source watchlist has {expected_watchlist} rows, "
            f"backup at {backup_path} has {actual_watchlist}. Refusing to proceed."
        )
    if actual_symbol_categories != expected_symbol_categories:
        raise RuntimeError(
            f"Backup verification FAILED: source symbol_categories has "
            f"{expected_symbol_categories} rows, backup at {backup_path} has "
            f"{actual_symbol_categories}. Refusing to proceed."
        )
    return backup_path


def _verify_migration(
    conn: sqlite3.Connection,
    expected_watchlist_count: int,
    expected_distinct_symbols: int,
) -> list[str]:
    """Run INSIDE the open transaction, before commit. Returns a list of
    mismatch descriptions (empty = all clear). Never raises itself --
    the caller decides what to do with a non-empty result (roll back)."""
    problems = []
    for old in HEBREW_TO_ENGLISH_CATEGORY:
        for table in ("watchlist", "symbol_categories"):
            remaining = _category_counts(conn, table, old)
            if remaining != 0:
                problems.append(
                    f"{table} still has {remaining} row(s) under the old spelling {_fmt_category(old)}"
                )

    actual_watchlist_count = _watchlist_count(conn)
    if actual_watchlist_count != expected_watchlist_count:
        problems.append(
            f"watchlist row count changed: expected {expected_watchlist_count}, "
            f"got {actual_watchlist_count}"
        )

    actual_distinct_symbols = _symbol_categories_distinct_symbols(conn)
    if actual_distinct_symbols != expected_distinct_symbols:
        problems.append(
            f"symbol_categories distinct symbol count changed: expected "
            f"{expected_distinct_symbols}, got {actual_distinct_symbols}"
        )
    return problems


def run_apply(db_path: Path, *, is_default_path: bool, have_backup_flag: bool) -> int:
    if is_default_path and not have_backup_flag:
        print(
            "Refusing to --apply against the default repo DB path without "
            "--i-have-a-backup. This script always takes its own online "
            "backup regardless -- this flag is a second, explicit human "
            "acknowledgment, specifically so a bare --apply can never land "
            "on the live file by accident. Pass --db-path to target a "
            "different (e.g. disposable copy) file without this flag.",
            file=sys.stderr,
        )
        return 1

    try:
        backup_path = _backup_database(db_path)
    except sqlite3.OperationalError as e:
        if _LOCKED_MARKER in str(e):
            print(f"Database is locked -- refusing to proceed. ({e})", file=sys.stderr)
            return 1
        raise
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 1
    print(f"Backup created and verified: {backup_path}\n")

    try:
        conn = _open_writable(db_path)
    except sqlite3.OperationalError as e:
        if _LOCKED_MARKER in str(e):
            print(f"Database is locked -- refusing to proceed. ({e})", file=sys.stderr)
            return 1
        raise

    try:
        try:
            conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as e:
            if _LOCKED_MARKER in str(e):
                print(f"Database is locked -- refusing to proceed. ({e})", file=sys.stderr)
                return 1
            raise

        expected_watchlist_count = _watchlist_count(conn)
        expected_distinct_symbols = _symbol_categories_distinct_symbols(conn)

        for old, new in HEBREW_TO_ENGLISH_CATEGORY.items():
            conn.execute(
                "UPDATE watchlist SET category = ? WHERE category = ?", (new, old)
            )
            conn.execute(
                "UPDATE OR IGNORE symbol_categories SET category = ? WHERE category = ?",
                (new, old),
            )
            conn.execute(
                "DELETE FROM symbol_categories WHERE category = ?", (old,)
            )

        problems = _verify_migration(conn, expected_watchlist_count, expected_distinct_symbols)
        if problems:
            conn.execute("ROLLBACK")
            print("Verification FAILED -- rolled back, nothing was written:", file=sys.stderr)
            for p in problems:
                print(f"  - {p}", file=sys.stderr)
            return 1

        conn.execute("COMMIT")
    finally:
        conn.close()

    print("Migration applied and verified successfully.\n")
    conn = _open_readonly(db_path)
    try:
        _print_count_table(conn, predicted=False)
    finally:
        conn.close()
    return 0


# ── CLI ──────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    _reconfigure_stdout_for_utf8()

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write changes (default: dry run).")
    parser.add_argument("--audit", action="store_true", help="List categories outside the known 15 (read-only, never fixes).")
    parser.add_argument("--db-path", type=Path, default=None, help="Target a specific DB file instead of the repo default.")
    parser.add_argument(
        "--i-have-a-backup", action="store_true",
        help="Required alongside --apply when targeting the default repo DB path.",
    )
    args = parser.parse_args(argv)

    is_default_path = args.db_path is None
    db_path = args.db_path if args.db_path is not None else _default_db_path()

    if args.audit:
        return run_audit(db_path)
    if args.apply:
        return run_apply(db_path, is_default_path=is_default_path, have_backup_flag=args.i_have_a_backup)
    return run_dry_run(db_path)


if __name__ == "__main__":
    sys.exit(main())
