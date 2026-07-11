# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with
code in this repository. Last full rewrite: 2026-07-05 (post-audit truth pass).
Binding architecture rulings live in `docs/DECISIONS.md` — read them before
proposing structural changes.

## Commands

All commands run from the repo root (`stocksage/`).

```bash
# Run the full application (bot + background agent)
python main.py

# Run the Streamlit dashboard (separate process)
streamlit run dashboard.py

# Run the test suite (28 unittest-based test files; pytest is NOT installed)
python -m unittest discover tests

# Data-pipeline smoke test (live yfinance fetch + analysis)
python test_fetch.py

# Watchlist lifecycle: dry-run evaluation / rollback a run
python scripts/dry_run_evaluation.py
python scripts/rollback_evaluation_run.py

# Manual strong-trend scanner (Stack C, reads cached stock_prices only)
python scripts/run_strong_trend_scan.py
```

**Testing rule: never run tests or smoke tests against the real
`db/stocksage.db`.** Tests must use temp databases (see `tests/fixtures.py`).

## Architecture — four stacks

The system is four loosely-coupled vertical stacks. They intentionally do NOT
yet share a data layer or indicator engine; consolidation is planned (see
"Consolidation status" below).

### Stack A — Live alerting (the running product)

```
main.py
 ├─ agent/core.py  start_agent()              [daemon thread, `schedule` lib]
 │   └─ every 15 min in US market hours → check_alerts()
 │        ├─ db.get_active_watchlist()        ACTIVE tier only, ≤30 symbols
 │        ├─ data/fetcher.py                  raw yfinance, no retries
 │        ├─ analyzers/technical.py           full_analysis() → opportunity
 │        │                                   score 0–100 + verdict
 │        ├─ 9 alert gates, inline            price move, dedup, cooldown,
 │        │                                   EMA150, RSI band, volume spike,
 │        │                                   score/verdict, green candle
 │        └─ send via Telegram + db.log_alert()   (alert_type: BUY_SIGNAL only)
 │   └─ run_morning_scan() once per weekday ~16:35 IL (hand-rolled time check)
 └─ bot/telegram_bot.py  run_polling()        [main thread, blocking, async]
     /analyze /scan /add /remove /trade /watchlist_* /refresh_watchlist ...
```

- `full_analysis()` verdicts: `STRONG BUY` (≥75) / `BUY` (≥55) / `WATCH`
  (≥35) / `NEUTRAL`. Nothing emits `WEAK BUY`, `AVOID`, or `NO_BUY` — some
  formatters/comments still reference those; they are stale.
- Sync→async bridge: the scheduler thread wraps each tick in
  `asyncio.run()` with a fresh `Bot` context. Any new alert-sending code from
  the scheduler thread must follow this pattern — never `await` directly.
- Known fragility: exceptions escaping a scheduler tick kill the daemon
  thread silently while the bot keeps running.

### Stack B — Watchlist lifecycle (runs daily)

```
services/watchlist_scheduler.py               17:30 ET, holiday-aware
 └─ services/watchlist_evaluator.py           dry-run by default; apply is
      ├─ data/market_data_validator.py        gated (env + "confirm" word)
      │    MarketDataClient — its own yfinance client with retries,
      │    batching, and 14-category ProviderStatus classification
      ├─ analyzers/eligibility.py             relevance score 0–100
      │    (liquidity/data-quality/trend/momentum — NOT a buy signal;
      │     calls full_analysis() internally for the technical components)
      └─ db: watchlist.wl_state transitions + evaluation_runs /
             evaluation_run_changes (atomic apply, rollback support)
```

States: `ACTIVE`, `MONITOR`, `ETF_INDEX_CONTEXT`, `TEMPORARILY_INELIGIBLE`,
`USER_REMOVED`. Promotion/demotion uses hysteresis (consecutive evaluations,
dwell days, replacement margin, bank-sector cap) — all knobs in `config.py`.

### Stack C — Scanner engine (dormant today; the FUTURE data backbone)

```
scripts/run_strong_trend_scan.py              manual CLI only, no scheduler
 └─ scanners/ (base_scanner, scanner_runner, strong_trend_scanner)
      ├─ analyzers/cached_indicators.py       SMA20/50/150/200 + rising flags,
      │                                       DB-only, no network
      └─ data/history_store.py
           └─ data/market_data_service.py     provider chain: Stooq primary →
                └─ data/providers/            yfinance fallback
      └─ db: stock_prices cache, scanner_runs, scanner_results
```

Per decision D2 (`docs/DECISIONS.md`): this stack was deliberately built to
become the alert engine's data backbone when the scan universe expands beyond
the watchlist. **Treat it as a real dependency, not dead code.** Active plan:
`docs/PLAN_SCANNER_ENGINE.md`.

### Stack D — Dashboard (separate process)

`dashboard.py` (Streamlit) calls `fetcher` + `full_analysis` + `db` directly.
Note: it displays the FULL seeded watchlist (`get_watchlist()`), while the
agent scans only the ACTIVE tier — the two views intentionally differ.

### External companion

TradingView Pine Script "Swing Trade Analyser" v6 — hybrid MAs (EMA fast/mid,
SMA 150/200). Per decision D1 this hybrid is the methodology source of truth;
Python's EMA150/200 is scheduled to be aligned to SMA (consolidation step 6,
not yet done).

## Database

SQLite at `db/stocksage.db`, managed entirely via `db/database.py` (~1,450
lines; all schema changes are additive+idempotent in `migrate_db()`).
Tables: `watchlist` (with lifecycle columns), `trades`, `alerts`,
`user_preferences`, `symbol_categories`, `evaluation_runs`,
`evaluation_run_changes`, `stock_prices`, `scanner_runs`, `scanner_results`.

`populate_from_config()` seeds the watchlist from `config.py`'s `WATCHLIST`
dict on startup; after seeding, **the DB is the source of truth** for
watchlist membership and tiers — the config dict is only a seed.

## Key configuration (`config.py`)

Alerting: `ALERT_MIN_SCORE=65`, `ALERT_VERDICTS=[BUY, STRONG BUY]`,
`ALERT_MIN_PRICE_CHANGE=0.5`, `ALERT_RSI_MIN/MAX=45/68`,
`ALERT_REQUIRE_GREEN_CANDLE`, `CHECK_INTERVAL_MINUTES=15`.
Alert cooldown is NOT configurable: once per symbol per UTC day, DB-backed
(`db.was_alerted_today()`, decision D3).
Morning scan: `SCAN_MIN_SCORE=50`, `SCAN_TOP_N=5`, 16:35 IL trigger.
Watchlist lifecycle: `ACTIVE_MAX_SIZE=30`, promotion/demotion thresholds and
hysteresis, eligibility liquidity floors.
Telegram auth: `AUTHORIZED_CHAT_IDS` allowlist (falls back to
`TELEGRAM_CHAT_ID`).

Note: RSI/MACD indicator parameters are hardcoded in `analyzers/technical.py`
(veto <35 / >75, ideal 45–65), not driven by config.

## Empty placeholder files

Seven zero-byte stubs are kept deliberately (decision D4, with per-file
relevance assessment in `docs/DECISIONS.md`): `agent/decision_engine.py`,
`agent/watchlist.py`, `analyzers/price_alerts.py`, `analyzers/sentiment.py`,
`bot/formatters.py`, `data/news_fetcher.py`, `db/models.py`.

## Environment

Credentials in `.env` at repo root: `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`,
optional `AUTHORIZED_CHAT_IDS`.
Never print, log, or commit credentials or the real DB.

## Consolidation status (post-audit)

Completed:
- **Step 1 — truth pass (2026-07-05):** this rewrite; stale planning docs
  moved to `docs/archive/`; dead config keys and dead dashboard branches
  removed; decisions recorded in `docs/DECISIONS.md`.
- **D5 (2026-07-11):** `/trade` ATR window aligned 6mo→1y.
- **Step 3 / D3 (2026-07-11):** single cooldown policy — one alert per
  symbol per UTC day, DB-backed (`was_alerted_today()`); fixed the
  `datetime('now','utc')` double-conversion; `ALERT_COOLDOWN_HOURS` removed.

Planned (in order — see audit + `docs/DECISIONS.md`):
- Step 2 (remaining): unify thresholds into config; alert loop consumes
  `full_analysis()` outputs only.
- Step 4: fetch-layer merge onto `MarketDataService` (D2 — Stack C wins).
- Step 5: all message formatting into `bot/formatters.py`; market calendar
  out of `data/fetcher.py`.
- Step 6: EMA→SMA alignment for 150/200 (D1) — behavior change, do with
  before/after comparison on cached history.
- Step 7: split `db/database.py` by domain.

## Known inconsistencies (flagged, intentionally not yet fixed)

- Three fetch layers (`fetcher.py`, `MarketDataClient`, `MarketDataService`)
  and two indicator engines (`technical.py` EMA vs `cached_indicators.py`
  SMA) coexist until steps 4/6.
- `is_market_open()` (in `data/fetcher.py`) ignores US holidays; the
  16:35-IL morning-scan trigger breaks during US/IL DST mismatch weeks.
- `bot/telegram_bot.py`'s `_rec_emoji` maps verdicts that are never emitted.
- Incomplete-bar handling is inconsistent: Gate 9 (green candle) uses the
  last completed candle during market hours, but volume spike / MACD / RSI
  still evaluate the in-progress bar.
