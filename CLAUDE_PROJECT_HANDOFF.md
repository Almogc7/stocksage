# StockSage V2 — Project Handoff

Snapshot taken at the end of Phase 9B-1. Use this file to resume work in a new
session without re-deriving context.

## 1. Current Branch

`main`

## 2. Current Git Status (at time of writing)

```
On branch main
Your branch is ahead of 'origin/main' by 8 commits.

Changes not staged for commit:
	modified:   .claude/settings.local.json   (pre-existing, unrelated to scanner work)

Untracked files:
	PLAN_SCANNER_ENGINE.md            (planning doc from Phase 3)
	STOCKSAGE_DECISION_LOGIC_REPORT.md (pre-existing, unrelated)
```

Nothing has been pushed to `origin/main`. All phase commits below are local-only.

## 3. Latest Commits (most recent first)

```
c276d46 Clean up Telegram command help menu               (Phase 9B-1)
366cdca Add manual strong trend scanner CLI                (Phase 9A)
118c662 Persist scanner run results                        (Phase 8)
1318511 Add strong trend scanner                           (Phase 7)
abb05a9 Add cached technical indicators from stored prices (Phase 6)
7e6d1f0 Add market data provider fallback service           (Phase 5)
5b3c543 Add historical price storage service                (Phase 4)
c6819eb Add scanner database schema and historical price tables (Phase 3)
2e4cc96 fix: make watchlist status EMA veto explanation accurate (pre-scanner-work, watchlist project)
```

## 4–7. Completed Phases — What Each Added, Key Files, Tests

### Phase 3 — DB schema for stock_prices, scanner_runs, scanner_results
- Extended `db/database.py`'s existing `migrate_db()` mechanism with schema v6 (`stock_prices`) and v7 (`scanner_runs`, `scanner_results`) — all additive, idempotent, no existing table altered.
- Added narrow helper functions: `insert_stock_prices`, `get_stock_prices`, `get_latest_stock_price`, `create_scanner_run`, `finish_scanner_run`, `get_scanner_run`, `record_scanner_result(s)`, `get_scanner_results`, `get_latest_scanner_results_for_symbol`.
- **Files:** `db/database.py` (modified)
- **Tests:** `tests/test_schema_migration_v6_v7.py` (27 tests)

### Phase 4 — Historical price storage
- New module wrapping the existing yfinance-based `data.fetcher.get_historical()` to fetch and upsert OHLCV history into `stock_prices`.
- **Files:** `data/history_store.py` (new)
- **Tests:** `tests/test_history_store.py` (14 tests)

### Phase 5 — Provider abstraction: Stooq primary, yfinance fallback
- New provider interface (`MarketDataProvider`), `StooqProvider` (keyless CSV endpoint, best-effort symbol mapping), `YFinanceProvider` (wraps existing `get_historical`), and `MarketDataService` orchestrating fallback with explicit failure classification (exception / empty / missing columns / insufficient candles).
- `data/history_store.py` updated to fetch through `MarketDataService` instead of calling yfinance directly; `source` column now reflects whichever provider actually succeeded.
- **Files:** `data/providers/{__init__,base,stooq_provider,yfinance_provider}.py` (new), `data/market_data_service.py` (new), `data/history_store.py` (modified), `tests/test_history_store.py` (modified)
- **Tests:** `tests/test_providers.py` (20 tests), `tests/test_market_data_service.py` (9 tests)

### Phase 6 — Cached indicators from stock_prices
- New DB-only indicator module: SMA20/50/150/200 plus a rising-vs-N-rows-ago check, computed purely from already-cached `stock_prices` rows (via `data.history_store.get_latest_prices()`) — no network calls. Deliberately separate from `analyzers/technical.py` (the live-fetch production path).
- **Files:** `analyzers/cached_indicators.py` (new)
- **Tests:** `tests/test_cached_indicators.py` (23 tests)

### Phase 7 — StrongTrendScanner
- First scanner: `BaseScanner` abstract interface + `StrongTrendScanner`, evaluating 8 conditions (≥220 candles, close above SMA20/50, SMA20>50>150>200 stacked, SMA150/200 rising vs. 5 rows ago) using Phase 6's cached indicators. Returns a structured result dict (`passed`, `score`, `reason`, `conditions`, `indicator_values`, `latest_close`). Standalone/dormant — not wired into anything yet.
- **Files:** `scanners/{__init__,base_scanner,strong_trend_scanner}.py` (new)
- **Tests:** `tests/test_strong_trend_scanner.py` (13 tests)

### Phase 8 — Scanner run/result persistence
- `scanners/scanner_runner.py`: `run_scanner(scanner, symbols, timeframe="1d")` runs a scanner over an explicit symbol list, persists one `scanner_runs` row + one `scanner_results` row per symbol (via Phase 3 helpers), catches per-symbol exceptions without aborting the run, and derives `status` (`completed`/`completed_with_errors`/`failed`).
- **Files:** `scanners/scanner_runner.py` (new)
- **Tests:** `tests/test_scanner_runner.py` (10 tests)

### Phase 9A — Manual CLI scanner execution
- `scripts/run_strong_trend_scan.py`: CLI entry point running `StrongTrendScanner` via the Phase 8 runner for explicit symbols, with `--dry-run` (skips persistence), `--timeframe`, `--db` override, `--top-n`, and CLI-layer symbol de-duplication. Not scheduled, not auto-run from `main.py`.
- **Files:** `scripts/run_strong_trend_scan.py` (new)
- **Tests:** `tests/test_run_strong_trend_scan_script.py` (8 tests)

### Phase 9B-1 — Telegram help/menu cleanup
- Audited all 22 existing Telegram commands (analysis-only, no code — see conversation history for the full audit report). Then: rewrote `/help` to be accurate and grouped (General/Analysis/Watchlist/Trades/Alerts), added `/admin_help` for technical/operational commands, added backward-compatible aliases `/watchlist_add`→`cmd_add`, `/watchlist_remove`→`cmd_remove`, `/morning_scan`→`cmd_scan` (same handler functions, zero logic duplication). All legacy commands (`/add`, `/remove`, `/scan`) remain fully registered and functional.
- **Files:** `bot/telegram_bot.py` (modified)
- **Tests:** `tests/test_telegram_help_and_aliases.py` (14 tests)

## 8. Current Test Status

**549 / 549 tests passing**, 0 failures, 0 regressions (as of the last full run, immediately before writing this file).

```
549 passed, 502 warnings in 37.61s
```

## 9. Things Intentionally NOT Changed (across all phases above)

- `agent/core.py` — the live 15-minute alert cycle (`check_alerts`, `run_morning_scan`, `send_alert`, `send_alert_with_chart`) is untouched by every phase above. No scanner logic has been wired into it.
- Telegram automatic/proactive alerts — no phase added any unsolicited message; every Telegram handler (old or new) only replies to the chat that invoked it.
- `analyzers/technical.py` — the production live-analysis path (`full_analysis()`, used by `/analyze` and the live alert agent) has never been modified. `analyzers/cached_indicators.py` (Phase 6) is a deliberately separate, DB-only module.
- `data/fetcher.py`, `data/market_data_validator.py`, `dashboard.py` — untouched across every phase (repeatedly verified via `git diff --name-only` after each phase).
- `db/stocksage.db` — never touched by any test in any phase. Every test uses a `tempfile`-backed SQLite DB via the `DB_PATH` override pattern established in Phase 3. File mtime/size (151,552 bytes, `Jun 20 13:13`) has been re-verified unchanged after every single phase's test run.
- No scanner logic has ever run against real, live-fetched market data in an automated test — all scanner/indicator tests use synthetic seeded price data.

## 10. Known Risks / Limitations Carried Forward

- **Adjusted vs. unadjusted price mismatch** (flagged since Phase 5): yfinance fetches use `auto_adjust=True` (split+dividend adjusted); Stooq's daily CSV is typically split-only adjusted. A symbol whose `stock_prices` history mixes both providers across different date ranges could show a spurious SMA/trend discontinuity at the provider seam — this becomes consequential wherever indicators/scanners make pass/fail decisions (Phases 6–8).
- **Stooq symbol mapping is intentionally naive** (Phase 5) — indices/crypto/foreign tickers often fail to resolve on Stooq and silently fall back to yfinance.
- **Duplicate symbols in a `run_scanner()` input list can undercount `symbols_failed`** (Phase 8 finding) — the CLI script (Phase 9A) mitigates this locally via de-duplication, but `scanners/scanner_runner.py` itself remains unguarded for any other future caller.
- **No mid-run crash recovery** for `scanner_runs` stuck at `status='running'` (Phase 8) — no "stuck run" sweep exists yet, unlike the precedent in `services/watchlist_scheduler.py` for `evaluation_runs`.
- **`record_scanner_results()` is one all-or-nothing transaction** — a single malformed row would roll back the entire batch, yet `finish_scanner_run()` currently runs unconditionally afterward regardless of whether anything was actually persisted (Phase 8 finding, not yet exercised by any test).
- **No fresh-data fetch triggered by the scanner or CLI** — both only read whatever is already cached in `stock_prices`; staleness is possible if `history_store.fetch_and_store_history()` hasn't run recently for a symbol.
- **No admin/privilege tier exists in the Telegram bot** — `AUTHORIZED_CHAT_IDS` is a single binary gate; `/admin_help` (Phase 9B-1) is a documentation convenience only, not an authorization boundary. Relevant for Phase 9B-2: a Telegram-facing scanner command has no human-in-the-loop safety net equivalent to the CLI script's `--db`/warning-print convention.
- **Naming/overlap risk**: `/analyze` (full_analysis buy_score/verdict), `/watchlist_status` (eligibility relevance/opportunity score), and a future `/scan_strong_trend` (StrongTrendScanner's 8-condition score) are three different scoring systems for "give me a signal for one symbol" — Phase 9B-2 should make clear in its reply text that it's a distinct, fourth signal, not a replacement for the other two.

## 11. Next Recommended Phase

**Phase 9B-2 — manual Telegram command `/scan_strong_trend`**

Add a Telegram-facing command that runs `StrongTrendScanner` via the Phase 8 `scanner_runner.run_scanner()` for explicit symbols passed as command arguments (mirroring `scripts/run_strong_trend_scan.py`'s logic but as a bot command instead of a CLI script). Per the Phase 9B-1 command audit, it should:
- Live in a new "Scanners" section of `/help` (not yet present — `/help` currently ends at Alerts + the `/admin_help` pointer).
- Imitate `/refresh_watchlist`'s UX conventions: run ID in the reply, structured pass/failed/error summary, no raw exceptions ever shown to the user.
- Not reuse `/scan`, `/morning_scan`, `/analyze`, or `/watchlist_status` — needs its own distinct name (e.g. `/scan_strong_trend`) and should briefly clarify in its reply that it's a separate signal from `/analyze`/`/watchlist_status`.
- Decide whether any additional authorization gate is needed beyond the existing `AUTHORIZED_CHAT_IDS` check, given this bot has no privilege tiers and no CLI-style `--db`/warning safety net.
- Still must not: schedule itself, send unsolicited/proactive alerts, touch `agent/core.py`'s live alert flow, or touch `db/stocksage.db` in tests.

## 12. Exact Prompt to Continue Phase 9B-2 Later

```
We completed and committed Phase 9B-1 (Telegram help/menu cleanup).

Now plan Phase 9B-2 only: manual Telegram command /scan_strong_trend.

Goal:
Add a Telegram command that runs StrongTrendScanner (Phase 7) via the
Phase 8 scanner runner (scanners/scanner_runner.py) for explicit symbols
passed as command arguments, mirroring scripts/run_strong_trend_scan.py's
logic (Phase 9A) but as a bot command instead of a CLI script.

Current context:
- Phase 3 added stock_prices, scanner_runs, scanner_results DB support.
- Phase 4 added historical price storage.
- Phase 5 added provider abstraction (Stooq primary, yfinance fallback).
- Phase 6 added cached indicators from stock_prices.
- Phase 7 added StrongTrendScanner.
- Phase 8 added scanner run/result persistence (scanners/scanner_runner.py).
- Phase 9A added a manual CLI script (scripts/run_strong_trend_scan.py).
- Phase 9B-1 cleaned up /help, added /admin_help, and added backward-
  compatible aliases (/watchlist_add, /watchlist_remove, /morning_scan).
- The live alert flow (agent/core.py) must remain untouched.
- The bot has no admin/privilege tier -- AUTHORIZED_CHAT_IDS is a single
  binary gate; every authorized user can run every command.

Scope:
- Do not modify agent/core.py.
- Do not modify analyzers/technical.py.
- Do not modify data/fetcher.py.
- Do not modify data/market_data_validator.py.
- Do not modify dashboard.py.
- Do not change the live alert flow.
- Do not send unsolicited/proactive alerts.
- Do not schedule anything.
- Do not touch db/stocksage.db in tests.
- Tests must use mocks/temp DBs only, no network calls.

Requirements:
1. Inspect scanners/scanner_runner.py, scanners/strong_trend_scanner.py,
   scripts/run_strong_trend_scan.py, and bot/telegram_bot.py's
   /refresh_watchlist implementation (the UX template to imitate).
2. Propose the exact command name, argument parsing, and reply format.
3. Decide where it fits in /help's grouping (new "Scanners" section).
4. Decide whether any additional authorization/confirmation gate is needed.
5. Add tests (mocked Update/Context, temp DB, no network).

Before coding:
- Return an implementation plan only.
- List exact files you expect to add or modify.
- List risks.
- List acceptance criteria.
- Do not implement until I approve.
```
