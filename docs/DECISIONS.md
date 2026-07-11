# StockSage — Architecture Decisions Record

Decisions made by the owner on 2026-07-05, following the full architectural
audit (four-stack map). These rulings are **locked in** for the consolidation
plan (steps 2–7). Do not re-litigate them in future sessions; if one must
change, update this file first.

---

## D1 — Moving averages: SMA is the source of truth for the 150/200

The Pine Script ("Swing Trade Analyser" v6, Micha Stocks methodology) hybrid
approach is authoritative: **EMA for fast/mid periods, SMA for 150/200.**

The Python live-alert path (`analyzers/technical.py`) currently uses
EMA150/EMA200 — **it is the side that must change**, not the Pine Script and
not `analyzers/cached_indicators.py` (which is already SMA-based).

- Status: **NOT yet implemented.** Scheduled as consolidation step 6.
- Deliberately deferred because it changes live signal behavior; do it when
  before/after comparison on cached history is possible.

## D2 — The scanner stack (Stack C) is the future data backbone

The dormant stack — `data/providers/` (Stooq→yfinance chain),
`data/market_data_service.py`, `data/history_store.py`,
`analyzers/cached_indicators.py`, `scanners/` — was intentionally built to
become the alert engine's data backbone once the scanned universe expands
beyond the current watchlist.

- It is **not dead code** and must not be deprioritized or deleted.
- The fetch-layer merge (consolidation step 4) should converge the live path
  onto `MarketDataService`, not the other way around.
- Active plan: `docs/PLAN_SCANNER_ENGINE.md`.

## D3 — Cooldown/dedup policy: once per symbol per day

Intended behavior: **one alert per symbol per calendar day.** Rationale:
signal-quality validation phase, not live trading — simplicity over cadence.

Current code has three mechanisms that disagree (in-memory per-day dict in
`agent/core.py`, `ALERT_COOLDOWN_HOURS = 2` via `db.was_alerted_recently()`
whose default parameter says 4, and `get_muted_symbols(hours=4)`), plus a
suspected SQLite `datetime('now','utc')` double-conversion bug.

- Status: **NOT yet implemented.** Scheduled as consolidation step 3:
  consolidate to a single DB-backed once-per-day policy and unit-test the
  `'utc'` modifier behavior with frozen timestamps.

## D4 — Empty stub files are kept

All seven zero-byte stubs stay in the tree. Roadmap relevance assessment
(audit of planning docs, 2026-07-05):

| Stub | Assessment |
|------|------------|
| `bot/formatters.py` | **Relevant** — designated destination for all message rendering (consolidation step 5). |
| `agent/decision_engine.py` | **Plausibly relevant** — natural home for the declarative gate engine (step 2/alerting engine). |
| `data/news_fetcher.py` | **Roadmap-adjacent** — news/sentiment named as a missing feature in the decision-logic report, but no concrete plan exists. |
| `analyzers/sentiment.py` | **Roadmap-adjacent** — same as above. |
| `agent/watchlist.py` | **Likely abandoned** — superseded by `services/watchlist_evaluator.py` + DB lifecycle states. |
| `analyzers/price_alerts.py` | **Likely abandoned** — superseded by the unified `check_alerts()` in `agent/core.py`. |
| `db/models.py` | **Likely abandoned** — schema lives inline in `db/database.py` (`init_db`/`migrate_db`). |

## D5 — `/trade`'s 6-month fetch window is an oversight

`bot/telegram_bot.py` fetches `period="6mo"` inside the `/trade` flow while
every other analysis path uses `period="1y"`. With ~126 bars, EMA150 is
computed on fewer bars than its window and EMA200 returns None — the same
symbol scores differently in `/trade` vs `/analyze`. No planning doc suggests
this was deliberate.

- Ruling: **align to `1y`.**
- Status: NOT yet changed (behavior change — excluded from the truth pass).
  Fold into consolidation step 2.
