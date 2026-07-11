# Scanner Engine — Implementation Plan

Status: planning only, no code changes yet.

## 1. Current Architecture Summary

StockSage V2 runs two concurrent runtimes from `main.py`:

1. **Background agent** (`agent/core.py`) — a daemon thread driven by the `schedule`
   library. Every `CHECK_INTERVAL_MINUTES` (15) during US market hours it runs
   `check_alerts()`, a 9-gate pipeline (price move, in-memory dedup, DB cooldown,
   EMA150, RSI band, volume spike, score/verdict, green candle) over
   `get_active_watchlist()` — **the ACTIVE tier only (≤30 symbols)**. A separate
   daily job, `run_morning_scan()`, does a lighter scan of the same ACTIVE tier and
   sends a ranked top-N summary.
2. **Telegram bot** (`bot/telegram_bot.py`) — blocking `Application.run_polling()`
   on the main thread, fully async (`python-telegram-bot` v21+).

**Watchlist:** `db/database.py`'s `watchlist` table has 12 lifecycle columns
(`wl_state`, `relevance_score`, `consec_promote_count`, etc.). States: `ACTIVE`,
`MONITOR`, `ETF_INDEX_CONTEXT`, `TEMPORARILY_INELIGIBLE`, `USER_REMOVED`.
`analyzers/eligibility.py` scores relevance; `services/watchlist_evaluator.py` +
`services/watchlist_scheduler.py` run the daily promotion/demotion evaluation
(17:30 ET), writing atomically via `db.apply_evaluation_changes()` with rollback
support (`evaluation_run_changes` table).

**Market data:**
- `data/fetcher.py` — direct, uncached `yfinance` calls (`get_current_price`,
  `get_historical`, `get_multiple_prices`, `get_52week_high_low`,
  `is_market_open`). No provider abstraction.
- `data/market_data_validator.py` — a more rigorous single-provider client
  (`MarketDataClient`) with batching, in-memory caching, bounded retry/backoff,
  and a `ProviderStatus` classification (14 categories). All fetch calls funnel
  through `_fetch_single()` (yfinance only).
- `ALPHA_VANTAGE_KEY` / `NEWS_API_KEY` were removed from `config.py` in the
  Step 1 truth pass (never read anywhere). Reintroduce `ALPHA_VANTAGE_KEY` if/when
  an Alpha Vantage fallback provider is actually built.

**Technical indicators:** `analyzers/technical.py:full_analysis(symbol, df,
current_price) -> dict` is the single source of truth (EMA150, RSI14, MACD,
Bollinger, ATR, pivots, buy_score, verdict, `triggered_signals`). Both `/analyze`
and the agent call this same function — no duplicated indicator logic anywhere.

**Database:** SQLite at `db/stocksage.db`, managed entirely through
`db/database.py`. Schema changes are additive and idempotent, applied in
`migrate_db()` (currently at schema v5) on every startup. Tables: `watchlist`,
`trades`, `alerts`, `user_preferences`, `symbol_categories`, `evaluation_runs`,
`evaluation_run_changes`.

**Telegram alerts:** `send_alert()` / `send_alert_with_chart()` in
`agent/core.py` bridge the sync `schedule` thread to the async Telegram API via
`asyncio.run()` — this is the mandated pattern per `CLAUDE.md` for any new
alert-sending code. Cooldown/dedup uses `was_alerted_recently()` (DB) plus an
in-memory `_alerted_this_session` guard.

**Key gap this project addresses:** all live scanning today is hardcoded to the
ACTIVE watchlist tier and a single data provider, with no persisted scan
results or scan-specific alert channel.

## 2. Proposed Architecture

```
                              ┌──────────────────────────┐
                              │   Provider Layer (NEW)   │
                              │ data/providers/base.py   │
                              │ data/providers/yfinance_ │
                              │   provider.py            │
                              │ data/providers/alpha_    │
                              │   vantage_provider.py    │
                              └──────────┬────────────────┘
                                         │ fallback-ordered
                              ┌──────────▼────────────────┐
                              │ MarketDataClient (existing,│
                              │ modified to use providers) │
                              └──────────┬────────────────┘
                                         │
                              ┌──────────▼────────────────┐
                              │ data/history_store.py (NEW)│
                              │ price_history table (NEW)  │
                              └──────────┬────────────────┘
                                         │ OHLCV DataFrame
                              ┌──────────▼────────────────┐
                              │ analyzers/technical.py     │
                              │ full_analysis()  (existing,│
                              │ unchanged)                 │
                              └──────────┬────────────────┘
                                         │ analysis dict
                              ┌──────────▼────────────────┐
                              │ services/scanner_engine.py │
                              │ (NEW) — universe selection, │
                              │ scan criteria, ranking      │
                              └──────────┬────────────────┘
                                         │
                     ┌───────────────────┼────────────────────┐
                     ▼                                        ▼
        ┌────────────────────────┐              ┌──────────────────────────┐
        │ scanner_runs /          │              │ Telegram scan alerts     │
        │ scanner_results tables  │              │ (agent/core.py additions,│
        │ (NEW)                   │              │ asyncio.run bridge)      │
        └────────────────────────┘              └──────────────────────────┘
                     ▲
                     │ new scheduled job (own cadence/flag),
                     │ independent of CHECK_INTERVAL_MINUTES
        ┌────────────┴────────────┐
        │ services/scanner_        │
        │ scheduler.py (NEW)       │
        └──────────────────────────┘
```

Design principles carried over from the existing codebase:
- The scanner engine is a **new, additive path**. It never modifies
  `check_alerts()` or the 15-minute ACTIVE-tier alert cycle.
- Everything funnels through the existing `full_analysis()` — no second
  indicator implementation.
- New tables follow the exact shape already proven by
  `evaluation_runs`/`evaluation_run_changes` (a "run" header row + per-symbol
  detail rows).
- New scheduled jobs are disabled by default via a config flag, matching the
  `WATCHLIST_SCHEDULE_APPLY` precedent — nothing fires automatically until
  explicitly turned on.
- All async Telegram sends go through the same `asyncio.run()` bridge pattern
  mandated by `CLAUDE.md`.

## 3. Files to Add

| File | Purpose |
|---|---|
| `db/migrations` (no new dir — continue in `db/database.py`) | schema v6/v7 additions (see §5) |
| `data/providers/__init__.py` | package marker |
| `data/providers/base.py` | `Provider` protocol: `get_history()`, `get_current_price()`, provider name/status contract |
| `data/providers/yfinance_provider.py` | wraps existing yfinance calls behind the `Provider` interface |
| `data/providers/alpha_vantage_provider.py` | fallback provider (would reintroduce `ALPHA_VANTAGE_KEY` in config) |
| `data/history_store.py` | `get_or_fetch_history()` — DB-first historical price cache, backed by `price_history` table |
| `services/scanner_engine.py` | universe selection, per-symbol scan pipeline, ranking, skip-reason reporting |
| `services/scanner_scheduler.py` | scan cadence + market-hours/holiday gating (reuses calendar logic already built for `services/watchlist_scheduler.py`) |
| `scripts/run_scanner.py` | manual CLI entry point (dry-run by default), mirroring `scripts/dry_run_evaluation.py` |
| `tests/test_providers.py` | provider interface + fallback ordering tests (mocked) |
| `tests/test_history_store.py` | history cache read/write tests (temp DB only) |
| `tests/test_scanner_engine.py` | scan pipeline tests (fake provider/client, no network) |
| `tests/test_scanner_results_db.py` | `scanner_runs`/`scanner_results` persistence tests (temp DB only) |
| `tests/test_scanner_scheduler.py` | cadence/gating tests |
| `tests/test_scanner_telegram_alerts.py` | formatter + send-path tests (mocked bot) |

## 4. Files to Modify

| File | Change |
|---|---|
| `data/market_data_validator.py` | `MarketDataClient._fetch_single()` (and batch equivalent) delegate to the provider layer instead of calling `yf.download` directly; behavior-preserving for existing callers (yfinance stays first in the fallback order) |
| `data/fetcher.py` | optionally re-expressed as a thin compatibility layer over `YFinanceProvider` (no signature changes) — only if needed to avoid duplicated yfinance logic; existing callers (`agent/core.py`, bot commands) untouched |
| `db/database.py` | add `migrate_db()` v6/v7 blocks (new tables only — no existing table altered); add narrow helper functions (`insert_price_history_rows`, `create_scanner_run`, `record_scanner_results`, `get_scanner_run`, `list_recent_scanner_results`, etc.) |
| `config.py` | add scanner-specific config constants (universe selection, `SCANNER_MIN_SCORE`, `SCANNER_SCHEDULE_ENABLED` default False, provider fallback order/timeouts) — additive only, no existing constants changed |
| `agent/core.py` | add a new formatter + new async send function for scan-passed alerts, and a new scheduled job registration in `start_agent()`; existing `check_alerts()`/`run_morning_scan()`/15-min job untouched |
| `README.md` / `CLAUDE.md` | document new tables, provider fallback behavior, and scanner config flags (documentation-phase only, at the end) |

No existing function signatures change as part of this plan; all modifications are additive (new functions/branches) unless explicitly noted above.

## 5. Database Migration Plan

Continue the existing idempotent `migrate_db()` pattern in `db/database.py`
(currently at schema v5). No destructive changes; every step is
`CREATE TABLE IF NOT EXISTS`.

**v6 — `price_history`**
```
price_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol      TEXT NOT NULL,
    date        DATE NOT NULL,
    open        REAL, high REAL, low REAL, close REAL,
    volume      INTEGER,
    source      TEXT NOT NULL DEFAULT 'yfinance',
    fetched_at  TIMESTAMP NOT NULL,
    UNIQUE(symbol, date)
)
CREATE INDEX idx_price_history_symbol_date ON price_history(symbol, date)
```

**v7 — `scanner_runs` / `scanner_results`** (mirrors `evaluation_runs` /
`evaluation_run_changes`)
```
scanner_runs (
    run_id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    run_type                 TEXT NOT NULL CHECK(run_type IN ('manual','scheduled','dry_run')),
    started_at               TIMESTAMP NOT NULL,
    completed_at             TIMESTAMP,
    duration_seconds         REAL,
    universe_size            INTEGER,
    symbols_scanned          INTEGER DEFAULT 0,
    symbols_passed           INTEGER DEFAULT 0,
    symbols_failed           INTEGER DEFAULT 0,
    provider_error_count     INTEGER DEFAULT 0,
    status                   TEXT NOT NULL DEFAULT 'started'
                             CHECK(status IN ('started','success','failed','partial_failure','cancelled')),
    triggered_by             TEXT,
    error_summary            TEXT,
    metadata_json            TEXT
)

scanner_results (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        INTEGER NOT NULL,
    symbol        TEXT NOT NULL,
    score         INTEGER,
    verdict       TEXT,
    signals_json  TEXT,
    price         REAL,
    passed        INTEGER NOT NULL DEFAULT 0,
    skip_reason   TEXT,
    alert_sent    INTEGER NOT NULL DEFAULT 0,
    computed_at   TIMESTAMP NOT NULL
)
CREATE INDEX idx_scanner_results_run_id ON scanner_results(run_id)
CREATE INDEX idx_scanner_results_symbol ON scanner_results(symbol, computed_at)
```

**Migration safety rules (carried over from prior phases):**
- Every migration step wrapped the same way existing v1–v5 steps are —
  checked for existence before altering, run inside `migrate_db()`, safe to
  run against a DB that already has v5.
- No column is ever removed or renamed; no existing row is ever rewritten as
  part of this plan.
- Validate against a **timestamped, gitignored copy** of the real DB before
  ever running against `db/stocksage.db` itself (same procedure used for the
  watchlist evaluator phases) — never run schema changes or smoke tests
  against the real production DB directly.

## 6. Testing Plan

Follows the standing project rule: **no automated test or smoke test ever
touches `db/stocksage.db` directly** — temp SQLite files/fixtures only, and if
a real-DB validation is ever needed it must be a manual, explicitly-labeled,
read-only (or gitignored-copy) step, not part of the automated suite.

- **Providers** — mock each provider's network call; test fallback ordering
  (yfinance fails transiently → falls back to Alpha Vantage), and that
  `INVALID_SYMBOL`-type failures do not trigger fallback/retry (matches
  existing `MarketDataClient` semantics).
- **History store** — temp DB; test cache hit/miss, upsert idempotency
  (re-fetching the same day doesn't duplicate rows), and gap-filling behavior.
- **Scanner engine** — fully mocked/fake provider and fake DB; test universe
  selection, that it calls `full_analysis()` (not a reimplementation), scan
  threshold filtering, ranking order, and per-symbol skip-reason reporting.
- **Scanner results persistence** — temp DB; test `scanner_runs` +
  `scanner_results` writes, status transitions, and that a run failure
  midway leaves a well-formed `partial_failure`/`failed` row rather than a
  half-written state.
- **Scheduler** — deterministic clock injection (same technique used in
  `tests/test_watchlist_scheduler.py`); test cadence, market-hours/holiday
  gating, and the disabled-by-default flag.
- **Telegram alerts** — mocked `Bot`; test formatter output, cooldown/dedup
  reuse, and that failures fall back to text (matching
  `send_alert_with_chart`'s existing fallback behavior).
- **Manual validation step** (once per phase, not automated): run
  `scripts/run_scanner.py` in dry-run mode against a timestamped copy of the
  real DB, confirm zero writes to the original file (mtime/row-count diff),
  same procedure already used for the watchlist evaluator and rollback work.

## 7. Rollback Plan

- **Schema:** all new tables (`price_history`, `scanner_runs`,
  `scanner_results`) are additive and independent of existing tables — they
  can be dropped entirely with no impact on `watchlist`/`trades`/`alerts`/
  `evaluation_runs` if the feature is abandoned. No existing table's schema is
  altered by this plan, so there is nothing to reverse on those tables.
- **Code:** each phase (§8) lands as its own commit on an isolated branch
  (see open question in §8, Phase 0). Reverting a phase is a single
  `git revert` of that phase's commit(s) since no phase modifies code paths
  used by earlier phases or by the existing alert/watchlist system.
- **Runtime:** the new scheduled scan job is gated by a config flag
  (`SCANNER_SCHEDULE_ENABLED`, default `False`) exactly like
  `WATCHLIST_SCHEDULE_APPLY` — disabling it is a one-line env var change with
  no code rollback needed, and the existing 15-minute ACTIVE-tier alert cycle
  and morning scan are never modified, so they are unaffected regardless of
  scanner rollback state.
- **Data:** if a scanner run writes bad data, it is confined to
  `scanner_runs`/`scanner_results` rows tagged with that `run_id` — safe to
  delete by `run_id` without touching `watchlist` state (the scanner engine
  in this plan does not write to `watchlist` at all, only reads it as one
  possible universe source).

## 8. Step-by-Step Implementation Phases

**Phase 0 — Branch decision (blocking, needs user input)**
Confirm which branch this work happens on (`feature/scanner-engine`, a new
branch, or `main` directly) before any commit is made.

**Phase 1 — Historical price storage**
- Add `price_history` table (migration v6).
- Add `data/history_store.py` with `get_or_fetch_history()`.
- Tests: temp DB only.

**Phase 2 — Provider abstraction + fallback**
- Add `data/providers/` package (`base.py`, `yfinance_provider.py`,
  `alpha_vantage_provider.py`).
- Modify `MarketDataClient` to route through the provider layer, yfinance
  first, Alpha Vantage as fallback on transient failure classes only.
- Tests: mocked providers, no network calls.

**Phase 3 — Scanner engine**
- Add `services/scanner_engine.py`: configurable universe (not hardcoded to
  ACTIVE tier), calls Phase 1/2 layer for data, calls existing
  `full_analysis()`, applies scan criteria, returns ranked results +
  skip reasons.
- No scheduling or Telegram wiring yet — importable/testable in isolation.

**Phase 4 — Scanner results persistence**
- Add `scanner_runs` + `scanner_results` tables (migration v7).
- Scanner engine writes one run header + per-symbol result rows per
  invocation.

**Phase 5 — Telegram alerts for passed scans**
- New formatter + new async send function in `agent/core.py`, following the
  existing `asyncio.run()` bridge pattern.
- Cooldown/dedup reusing `was_alerted_recently()`/`log_alert()`, scoped so it
  cannot interfere with the existing 15-minute alert cooldown usage.

**Phase 6 — Scheduling wiring**
- Add `services/scanner_scheduler.py` and a new job registration in
  `agent/core.py:start_agent()`, on its own cadence, gated by
  `SCANNER_SCHEDULE_ENABLED` (default `False`).

**Phase 7 — Documentation**
- Update `README.md` / `CLAUDE.md` with new tables, provider fallback
  behavior, and scanner config flags.

Each phase lands as its own commit with its own tests passing before the next
phase begins, matching the discipline used for the prior dynamic-watchlist-
lifecycle work on this project.
