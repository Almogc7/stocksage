"""
Scanner run execution/persistence (Phase 8).

Runs a BaseScanner instance (e.g. StrongTrendScanner) over an explicit list
of symbols and persists one scanner_runs row + one scanner_results row per
symbol, using the existing Phase 3 db.database helpers as-is (no schema
changes). Purely a runner: it does not decide which symbols to scan (no
universe selection), does not schedule itself, does not fetch fresh market
data, and is not wired into Telegram or the live alert flow — standalone
and dormant until something else calls it.

Counting conventions (scanner_runs has no symbols_failed column):
  symbols_scanned = total symbols attempted (len(symbols)), including any
                    that raised an exception during scan().
  symbols_passed  = count of results with passed=True (an errored symbol
                    never counts as passed).
  status:
    "completed"             — zero errors (also the empty-list case)
    "completed_with_errors" — some but not all symbols errored
    "failed"                — every symbol errored (non-empty list)
  A per-symbol exception is caught, recorded as its own scanner_results row
  (passed=False, details_json carries the error), and never aborts the run.
"""
from __future__ import annotations

from db.database import create_scanner_run, finish_scanner_run, record_scanner_results
from scanners.base_scanner import BaseScanner


def run_scanner(scanner: BaseScanner, symbols: list[str], *, timeframe: str = "1d") -> dict:
    run_id = create_scanner_run(scanner.name)

    rows: list[dict] = []
    symbols_passed = 0
    errors: dict[str, str] = {}

    for symbol in symbols:
        try:
            result = scanner.scan(symbol, timeframe)
        except Exception as exc:
            error_message = f"{type(exc).__name__}: {exc}"
            errors[symbol.upper()] = error_message
            rows.append({
                "symbol": symbol.upper(),
                "scanner_name": scanner.name,
                "timeframe": timeframe,
                "passed": False,
                "score": None,
                "reason": f"scanner error: {error_message}",
                "details": {
                    "error": True,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                },
            })
            continue

        passed = bool(result.get("passed", False))
        if passed:
            symbols_passed += 1

        rows.append({
            "symbol": result.get("symbol", symbol.upper()),
            "scanner_name": result.get("scanner_name", scanner.name),
            "timeframe": result.get("timeframe", timeframe),
            "passed": passed,
            "score": result.get("score"),
            "reason": result.get("reason"),
            "details": {
                "conditions": result.get("conditions"),
                "indicator_values": result.get("indicator_values"),
                "latest_close": result.get("latest_close"),
            },
        })

    record_scanner_results(run_id, rows)

    total = len(symbols)
    symbols_failed = len(errors)
    if total == 0 or symbols_failed == 0:
        status = "completed"
    elif symbols_failed < total:
        status = "completed_with_errors"
    else:
        status = "failed"

    notes = (
        f"{symbols_failed} symbol(s) failed: {', '.join(sorted(errors))}"
        if errors else None
    )

    finish_scanner_run(
        run_id,
        status=status,
        symbols_scanned=total,
        symbols_passed=symbols_passed,
        notes=notes,
    )

    return {
        "run_id": run_id,
        "status": status,
        "symbols_scanned": total,
        "symbols_passed": symbols_passed,
        "symbols_failed": symbols_failed,
        "errors": errors,
    }
