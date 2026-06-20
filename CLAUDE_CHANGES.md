# CLAUDE_CHANGES.md

All modifications made by Claude on branch `claude/stocksage-review-20260617-1200`.

---

## Entry 1 — Review documentation created

| Field | Value |
|---|---|
| **Date and time** | 2026-06-17 |
| **Commit hash** | `8b5305b` |
| **Files changed** | `STOCKSAGE_REVIEW.md` (new), `CLAUDE_CHANGES.md` (new) |
| **Reason** | Comprehensive project review per user instructions |
| **Previous behavior** | No review documentation existed |
| **New behavior** | `STOCKSAGE_REVIEW.md` contains full 18-section review; `CLAUDE_CHANGES.md` tracks all modifications |
| **Tests executed** | `python test_fetch.py` (integration smoke test) |
| **Test results** | PASSED — all sections completed successfully, NVDA analysis score=50 matches manual calculation |
| **Known limitations** | Test requires live internet access and Yahoo Finance availability |
| **Revert command** | `git checkout main -- STOCKSAGE_REVIEW.md CLAUDE_CHANGES.md && git rm STOCKSAGE_REVIEW.md CLAUDE_CHANGES.md` |
| **Affects stock rankings** | No — documentation only |
| **Affects historical comparability** | No |

---

---

## Entry 2 — Fix: remove duplicate QQQ from indices category

| Field | Value |
|---|---|
| **Commit hash** | `07babc3` |
| **Files changed** | `config.py` |
| **Change** | Removed `"QQQ"` from `"מדדים"` (indices). QQQ already appears in `"ETFs"` — the correct location. |
| **Tests** | 80 tests — all pass |

---

## Entry 3 — Fix: replace deprecated datetime.utcnow()

| Field | Value |
|---|---|
| **Commit hash** | `163cd5c` |
| **Files changed** | `agent/core.py`, `db/database.py` |
| **Change** | Replaced all `datetime.utcnow()` calls with `datetime.now(timezone.utc)`. The deprecated form raises a `DeprecationWarning` in Python 3.12 and will be removed in a future version. |
| **Tests** | 80 tests — all pass |

---

## Entry 4 — Fix: correct RSI fringe-zone signal label

| Field | Value |
|---|---|
| **Commit hash** | `2df423e` |
| **Files changed** | `analyzers/technical.py`, `agent/core.py` |
| **Change** | The `else` branch of the RSI scoring block (fringe zone: 35–44 or 66–75) was emitting `"rsi_healthy_range"` — misleading name. Renamed to `"rsi_acceptable_zone"`. Added corresponding display label in `_SIGNAL_LABELS`. |
| **Affects alert output** | Yes — `/analyze` and alert messages now show "RSI acceptable" instead of "RSI healthy" for fringe-zone RSI readings |
| **Tests** | `test_rsi_label.py` (12 tests) — all pass |

---

## Entry 5 — Fix: correct SQLite cooldown UTC consistency and timestamp format

| Field | Value |
|---|---|
| **Commit hash** | `79d1e92` |
| **Files changed** | `db/database.py` |
| **Change** | (a) `log_alert()` now stores timestamps as `strftime("%Y-%m-%d %H:%M:%S")` instead of `isoformat(timespec="seconds")`. The ISO format uses a `T` separator (e.g. `2026-06-17T10:30:00`) while SQLite's `datetime()` uses a space (e.g. `2026-06-17 10:30:00`). String comparison of `T` > ` ` meant every stored timestamp permanently appeared newer than any `datetime('now', ...)` output, making the DB-level cooldown non-functional since the project's beginning. (b) `get_muted_symbols()` now includes `'utc'` modifier to match `was_alerted_recently()`. |
| **Impact** | Critical — the DB cooldown was silently broken. The in-memory `_alerted_this_session` dict was the only functional dedup guard. Now both guards work correctly. |
| **Tests** | `test_sqlite_cooldown.py` (14 tests) — all pass |

---

## Entry 6 — Fix: handle None/NaN three_month_average_volume in get_current_price()

| Field | Value |
|---|---|
| **Commit hash** | `323bab7` |
| **Files changed** | `data/fetcher.py` |
| **Change** | `yfinance fast_info.three_month_average_volume` returns `None` for indices (`^VIX`, `^GSPC`) and occasionally `NaN` for other symbols. The previous code called `int(avg_vol)` directly, raising `TypeError` on `None` and `ValueError` on `NaN`. Now wrapped in try/except with fallback to 0. |
| **Tests** | `test_volume.py` (12 tests) — all pass |

---

## Entry 7 — Fix: use ta.momentum.rsi in chart_generator to match analysis engine

| Field | Value |
|---|---|
| **Commit hash** | `9540623` |
| **Files changed** | `analyzers/chart_generator.py` |
| **Change** | Chart RSI was calculated with `rolling(14).mean()` (simple moving average), while `technical.py` uses `ta.momentum.rsi()` (Wilder's exponential smoothing). The two formulas produce meaningfully different values. Both now use `ta.momentum.rsi()`. |
| **Affects chart output** | Yes — RSI line on alert charts now matches the RSI value shown in the alert score |
| **Tests** | `test_rsi_consistency.py` (8 tests) — all pass |

---

## Entry 8 — Fix: use last completed daily candle in Gate 9 green-candle check

| Field | Value |
|---|---|
| **Commit hash** | `499287c` |
| **Files changed** | `agent/core.py` |
| **Change** | When the US market is open, yfinance includes the current in-progress session as the last row of the daily DataFrame. Gate 9 was reading `df.iloc[-1]` unconditionally. An in-progress session that is currently green may close red. Fix: when `is_market_open()` is True and `len(df) >= 2`, use `df.iloc[-2]` (the last confirmed close). When market is closed, `df.iloc[-1]` is the completed session. |
| **Affects alert output** | Yes — Gate 9 now correctly skips symbols where the last completed candle is red, even if the current intraday snapshot appears green |
| **Tests** | `test_incomplete_candle.py` (8 tests) — all pass |

---

## Entry 9 — Feat: add Telegram bot authorization check to all command handlers

| Field | Value |
|---|---|
| **Commit hash** | `7ae2b0c` |
| **Files changed** | `bot/telegram_bot.py`, `config.py`, `.env.example` (new file) |
| **Change** | Added `AUTHORIZED_CHAT_IDS` to `config.py` (parsed from env var, falls back to `TELEGRAM_CHAT_ID`). Added `_check_auth()` async helper. Added `if not await _check_auth(update): return` as the first line of all 14 command handlers. Created `.env.example` as a safe template. |
| **Behavior if unconfigured** | Fail-secure — rejects all commands if `AUTHORIZED_CHAT_IDS` is empty |
| **Tests** | `test_telegram_auth.py` (26 tests) — all pass. Total: 80 tests pass. |

---

## Entry 10 — Fix: restore AUTHORIZED_CHAT_IDS lost during manual watchlist expansion

| Field | Value |
|---|---|
| **Commit hash** | `12be44a` |
| **Files changed** | `config.py` |
| **Reason** | The user manually expanded config.py (adding SOXX, bank, nuclear, and materials symbols). The working-tree file was edited from a pre-fix version, which dropped the `AUTHORIZED_CHAT_IDS` block added in `7ae2b0c`. This caused 20 test failures. The block has been restored verbatim. |
| **Tests** | 80 tests — all pass |

---

## Entry 12 — Security: replace example credentials with placeholders

| Field | Value |
|---|---|
| **Commit hash** | `ef00728` |
| **Files changed** | `.env.example` |
| **Finding** | Real credentials were written into `.env.example` (a git-tracked file) but were NEVER staged or committed. Confirmed via `git log -S <token>` — zero commits contain the real values. The file was in the working tree only. |
| **Action** | Restored safe placeholder values; added explicit security rules at the top of the file. |
| **What you must do** | If the Telegram bot token was ever used outside this machine or shared, rotate it via @BotFather. The numeric chat ID is not a secret but is personal data. |
| **Revert command** | `git revert ef00728` — **NOT recommended**: reverting would restore a file containing real credentials into the git-tracked template. |
| **Tests** | 94/94 — all pass |

---

## Entry 13 — Fix: preserve runtime watchlist removals across restarts

| Field | Value |
|---|---|
| **Commit hash** | `5f6e699` |
| **Files changed** | `db/database.py`, `tests/test_reseed_protection.py` (new) |
| **Bug** | `remove_from_watchlist()` used `DELETE FROM watchlist WHERE symbol = ?`. On the next application startup, `populate_from_config()` called `INSERT OR IGNORE` for every symbol in config.py. If the deleted symbol was still in config.py, the UNIQUE constraint no longer blocked it — the symbol was silently re-inserted. The `/remove` command appeared to work but its effect did not survive restarts. |
| **Fix design** | Soft-delete: `remove_from_watchlist()` now sets `enabled = 0` and records `removed_at` timestamp instead of deleting the row. `INSERT OR IGNORE` during seeding sees the existing row (enabled=0) and leaves it untouched. `get_watchlist()` filters `WHERE enabled = 1`. `add_to_watchlist()` uses `INSERT ... ON CONFLICT DO UPDATE SET enabled = 1` so `/add` explicitly re-enables a previously removed symbol. |
| **Migration** | `migrate_db()` adds `enabled INTEGER NOT NULL DEFAULT 1` and `removed_at TIMESTAMP DEFAULT NULL` to existing `watchlist` tables. Idempotent — safe to run on every startup. Existing rows default to `enabled = 1`. |
| **Survival matrix** | `/add` → survives restart, git pull ✅; `/remove` → survives restart, git pull ✅; new config symbol → added on next restart ✅; removed config symbol → stays removed ✅ |
| **Tests added** | 14 tests in `test_reseed_protection.py` covering all 12 user-specified scenarios plus 2 bonus assertions |
| **Revert command** | `git revert 5f6e699` — safe; existing enabled=0 rows would stay disabled; DB state unaffected beyond re-enabling DELETE behavior in remove_from_watchlist() |
| **Affects stock rankings** | No |
| **Affects alert output** | No |

---

## Entry 14 — Docs: watchlist decision package

| Field | Value |
|---|---|
| **Files changed** | `WATCHLIST_DECISION_PACKAGE.md` (new), `CLAUDE_CHANGES.md` (updated) |
| **Contents** | Live volume/liquidity analysis of all 399 symbols; D5/D6 threshold analysis with actual percentile distributions; D16 duplicate resolution plan; D18 investigation of 18 flagged symbols; bank category handling recommendation; proposed 30-symbol ACTIVE list; proposed 5-tier classification for every symbol; 20-decision summary table; revert commands; final status table |
| **API calls made** | yfinance fast_info fetched for all 399 symbols to compute avg daily volume and dollar volume. No paid APIs used. |
| **Secrets displayed** | None |

---

## Entry 15 — Feat: multi-tier watchlist architecture (Commits 1–5)

| Field | Value |
|---|---|
| **Commit 1 (DB migration)** | `d31b7e2` |
| **Commit 2 (eligibility engine)** | `9f61940` |
| **Commit 3 (scanner update)** | `5edd243` |
| **Commit 4 (Telegram commands)** | `4f8570e` |
| **Commit 5 (tests)** | `197676d` |
| **Files changed** | `db/database.py`, `config.py`, `analyzers/eligibility.py` (new), `agent/core.py`, `bot/telegram_bot.py`, `tests/test_eligibility.py` (new), `tests/test_watchlist_states.py` (new), `tests/test_schema_migration_v2.py` (new) |
| **New test count** | 150 total (56 new) — all pass |

**Changes by file:**

`config.py`:
- Added 16 new watchlist architecture constants: ACTIVE_MAX_SIZE (30), ACTIVE_BANK_MAX (8), ELIGIBILITY_MIN_AVG_VOLUME (250000), ELIGIBILITY_MIN_DOLLAR_VOL (10M), ELIGIBILITY_MIN_PRICE (3.0), ELIGIBILITY_LOOKBACK_DAYS (63), ELIGIBILITY_STALE_DAYS (3), PROMOTION_THRESHOLD (60), PROMOTION_CONSEC_REQUIRED (2), DEMOTION_THRESHOLD (45), DEMOTION_CONSEC_REQUIRED (2), DWELL_MIN_DAYS (5), REPLACEMENT_MARGIN (5), ETF_ALERTS_ENABLED (false), BANK_CATEGORIES (frozenset)
- All overridable via environment variables

`db/database.py`:
- `migrate_db()` extended with 12 new watchlist columns and `symbol_categories` table (both idempotent)
- Initial wl_state assignment on migration: ETF/index/crypto → ETF_INDEX_CONTEXT, disabled → USER_REMOVED, stocks → MONITOR
- `_seed_symbol()` and `add_to_watchlist()` now also insert into `symbol_categories`
- `remove_from_watchlist()` now sets `wl_state = 'USER_REMOVED'`
- `add_to_watchlist()` now resets `wl_state = 'MONITOR'` when re-enabling
- 15 new functions: `get_active_watchlist`, `update_symbol_state`, `get_symbols_by_state`, `get_watchlist_summary`, `add_category_tag`, `get_symbol_categories`, `update_eligibility`, `update_hysteresis`, `record_state_change`, `get_symbol_status`, `increment_dwell_days`, `run_initial_classification`

`analyzers/eligibility.py` (new):
- 6 component score functions (data_quality, liquidity, trend, momentum, proximity, volatility)
- `compute_relevance_score()`: master 0–100 integer, missing data = 0 for that component
- `determine_state_change()`: hysteresis state machine, no DB writes, returns (state, reason)
- `evaluate_symbol_eligibility()`: orchestrates full evaluation per symbol

`agent/core.py`:
- `check_alerts()` and `run_morning_scan()` use `get_active_watchlist()` instead of `get_watchlist()`
- Only ACTIVE-state symbols are scanned for alerts/morning scan

`bot/telegram_bot.py`:
- 5 new authorized commands: /watchlist_active, /watchlist_monitor, /watchlist_context, /watchlist_ineligible, /watchlist_status
- /watchlist now shows tier summary line
- /add now calls `update_symbol_state(MONITOR)` to enter eligibility cycle
- `run_bot()` now calls `run_initial_classification()` on startup

**Watchlist states:**
- `ACTIVE`: scanned for alerts, shown in /watchlist_active (max 30)
- `MONITOR`: not scanned, shown in /watchlist_monitor
- `ETF_INDEX_CONTEXT`: ETFs/indices/crypto, no BUY alerts, shown in /watchlist_context
- `TEMPORARILY_INELIGIBLE`: no data/foreign symbols, shown in /watchlist_ineligible
- `USER_REMOVED`: soft-deleted, only re-enabled via explicit /add

**Revert commands:**
```
git revert 197676d  # tests
git revert 4f8570e  # Telegram commands
git revert 5edd243  # scanner update
git revert 9f61940  # eligibility engine
git revert d31b7e2  # DB migration
```
Note: DB schema changes require manual column removal or DB reset if reverting.

| **Affects stock alerts** | Yes — only ACTIVE symbols (≤30) are now scanned instead of all 399 |
| **Affects historical comparability** | No — opportunity score formula unchanged |

---

## Entry 11 — Docs: watchlist and alert design proposal

| Field | Value |
|---|---|
| **Files changed** | `WATCHLIST_AND_ALERTS_DESIGN.md` (new), `CLAUDE_CHANGES.md` (updated) |
| **Contents** | 19-section design document covering: Phase 1 verification, current watchlist implementation analysis, symbol classification, multi-level watchlist architecture, eligibility rules, relevance score, promotion/demotion hysteresis, scan schedule, alert lifecycle, example messages, database schema proposal, git-safety design, API performance estimates, test plan, and 20 decisions requiring explicit approval. No production code changed. |
| **Symbol counts found** | 404 config.py entries, 399 unique, 80 in DB (original seed from 2026-05-16), 5 duplicates |
| **Critical finding** | Removed symbols re-appear after restart because `populate_from_config()` uses `INSERT OR IGNORE` without checking a removed-symbols exclusion list |

## Entry 16 — Fix: startup classification no longer overwrites dynamic watchlist state

| Field | Value |
|---|---|
| **Commit** | (pending — see `git log` after commit) |
| **Files changed** | `db/database.py`, `tests/test_watchlist_states.py` |
| **New test count** | 156 total (6 new) — all pass |

**Audit finding:** `run_initial_classification()` ran unconditionally on every
application startup (`run_bot()` → `init_db()` → `run_initial_classification()`)
and unconditionally rewrote every row's `wl_state` based only on a hardcoded
30-symbol `INITIAL_ACTIVE_SET`, a hardcoded ineligible-symbol dict, and
`classify_security_type()`. It never checked the row's existing `wl_state`.
This meant any dynamic promotion/demotion performed by the eligibility engine
(not yet wired into a live schedule, but exercised manually or by future
code) would be silently discarded on the next restart — ACTIVE symbols not
in the hardcoded seed list would revert to MONITOR, and the hardcoded seed
symbols would always be forced back to ACTIVE regardless of current state.

**Fix:**
- Added a new `wl_classified` column (v3 migration, idempotent `ALTER TABLE`).
  Rows that already existed when the column is added are backfilled to
  `wl_classified = 1` in the same migration step, so upgrading a live
  production DB does not re-run the hardcoded classifier over real,
  dynamically-managed state.
- `run_initial_classification()` now only selects and classifies rows where
  `wl_classified = 0`, and sets `wl_classified = 1` on every branch
  (USER_REMOVED, TEMPORARILY_INELIGIBLE, ETF_INDEX_CONTEXT, ACTIVE, MONITOR).
  Already-classified rows are left completely untouched on subsequent calls.
- `add_to_watchlist()` and `remove_from_watchlist()` now also set
  `wl_classified = 1`, since both are explicit, intentional state changes
  that should not be immediately re-evaluated by the hardcoded seed rules on
  the next restart.

**Verified:**
- A MONITOR symbol manually promoted to ACTIVE survives a simulated restart.
- A hardcoded-seed ACTIVE symbol manually demoted to MONITOR survives a
  simulated restart (does not get forced back to ACTIVE).
- `relevance_score` and hysteresis counters (`consec_promote_count`) survive
  a simulated restart.
- Calling `run_initial_classification()` twice in a row is a no-op for
  already-classified symbols.
- A symbol newly added to `config.py` after go-live still gets classified
  on the next startup, without disturbing already-classified rows.
- Upgrading a pre-`wl_classified` database (simulated via a hand-built v2
  schema row) backfills `wl_classified = 1` and preserves the existing
  `ACTIVE` state instead of resetting it.

**Revert command:**
```
git revert <commit-hash-of-this-entry>
```
Note: this only adds a column and changes write-guards; it does not remove
or rename any existing column, so reverting is safe without a DB reset. A
reverted DB will resume overwriting dynamic state on every restart (the
original bug) but will not lose data.

**Affects production behavior** | Yes — once deployed, dynamic watchlist
state (manual or future eligibility-engine promotions/demotions, scores,
counters) will now survive application restarts instead of being reset to
the hardcoded 30-symbol seed list every time.

## Entry 17 — Feat: add watchlist evaluation run tracking (Phase 2)

| Field | Value |
|---|---|
| **Commit** | (pending — see `git log` after commit) |
| **Files changed** | `db/database.py`, `tests/test_evaluation_runs.py` (new), `CLAUDE_CHANGES.md` |
| **New test count** | 179 total (23 new) — all pass |

Pure bookkeeping infrastructure for future watchlist eligibility refresh runs.
No live yfinance evaluation, no promotion/demotion wiring, no scheduler, no
Telegram commands — those are later phases. This phase never reads market
data and never modifies the `watchlist` table.

**Schema (v4 migration, idempotent — `CREATE TABLE IF NOT EXISTS`):**
- New table `evaluation_runs`: `run_id`, `run_type` (manual/scheduled/dry_run/startup),
  `status` (started/success/failed/partial_failure/cancelled), `started_at`,
  `completed_at`, `duration_seconds`, per-tier before/after counts
  (`active_before/after`, `monitor_before/after`, `context_count`,
  `ineligible_before/after`, `user_removed_count`), run outcome counts
  (`promotions_count`, `demotions_count`, `recovered_count`,
  `newly_ineligible_count`), provider/data-quality counts
  (`provider_error_count`, `stale_data_count`, `invalid_symbol_count`,
  `cache_hits`, `cache_misses`, `yfinance_request_count`), `dry_run` flag,
  `triggered_by`, `error_summary`, `metadata_json`.
- Index on `(status, started_at)` for fast last-run/in-progress lookups.
- Timestamps use the same UTC space-separated format as the rest of the
  codebase (`YYYY-MM-DD HH:MM:SS`, not ISO `T`-separated) so future
  `datetime('now','utc',...)` comparisons stay consistent with `log_alert()`.

**Helper functions added (`db/database.py`):**
- `create_evaluation_run(run_type, *, dry_run=False, triggered_by=None, metadata=None, **counts)` → run_id
- `update_evaluation_run_success(run_id, **counts)`
- `update_evaluation_run_failure(run_id, error_summary, **counts)`
- `update_evaluation_run_partial_failure(run_id, error_summary, **counts)`
- `cancel_evaluation_run(run_id, reason="")`
- `record_evaluation_run_counts(run_id, **counts)` — incremental update without finalizing
- `get_evaluation_run(run_id)`, `get_last_evaluation_run()`,
  `get_last_successful_evaluation_run()`, `list_recent_evaluation_runs(limit=10)`,
  `get_in_progress_evaluation_run()` (returns the latest `started` row, if any;
  no locking — a later phase decides staleness from `started_at` age)

All count-column writes go through an explicit allowlist
(`_EVAL_RUN_COUNT_COLUMNS`) so `**counts`/`**kwargs` can't silently write to
`status`/`started_at`/etc.; unknown keys raise `ValueError`.

**Verified:**
- Migration creates the table and is idempotent (calling `migrate_db()`
  twice does not duplicate it).
- started → success / failed / partial_failure / cancelled transitions all
  stamp `completed_at` and compute `duration_seconds`.
- `get_last_successful_evaluation_run()` correctly skips failed runs.
- `list_recent_evaluation_runs()` orders newest-first and respects `limit`.
- `dry_run` flag and `metadata_json` (dict round-trip via JSON) stored correctly.
- `get_in_progress_evaluation_run()` detects a `started` run and stops
  detecting it once finalized.
- Existing watchlist rows are provably unchanged before/after creating and
  finalizing runs (`get_symbol_status()` snapshot compared byte-for-byte).
- Ran against the real production DB via `test_fetch.py` (smoke test):
  `evaluation_runs` table created with 0 rows, all 80 existing watchlist
  rows kept their current `wl_state`/`wl_classified` values unchanged.

**Revert command:**
```
git revert <commit-hash-of-this-entry>
```
Note: this only adds a new table and an index; it does not touch any
existing table, so reverting (or never adopting it) is safe without a DB
reset.

**Affects production behavior** | No — purely additive bookkeeping table,
nothing yet calls these functions outside of tests.

## Entry 18 — Feat: add market data validation layer (Phase 3)

| Field | Value |
|---|---|
| **Commit** | (pending — see `git log` after commit) |
| **Files changed** | `data/market_data_validator.py` (new), `config.py`, `tests/test_market_data_validator.py` (new), `CLAUDE_CHANGES.md` |
| **New test count** | 212 total (33 new) — all pass, all mocked, zero live network calls |

Pure data-quality/retrieval layer for the dynamic watchlist lifecycle. Does
**not** decide promotion/demotion, does not write to any database table,
does not schedule anything, and does not send Telegram messages.

**Existing-fetcher audit (done before writing any code):**
1. yfinance is used only in `data/fetcher.py`.
2. Existing functions: `get_current_price` (yf.Ticker.fast_info), `get_historical`
   (yf.download, single symbol), `get_multiple_prices` (yf.download batch,
   2-day window, group_by="ticker"), `get_52week_high_low` (yf.download, 1y).
3. None were reused directly — `get_historical`'s single-symbol shape was
   close but lacks retry/backoff/classification; `get_multiple_prices`'s
   batch shape was the model for the new batch fetcher but only fetches a
   2-day window (too short for a 60+ day liquidity lookback) and silently
   coerces NaN to 0 rather than reporting a data-quality status.
4. Existing code uses **adjusted** prices everywhere (`auto_adjust=True`).
   The new module follows the same convention for consistency.
5. Existing code handles missing data ad hoc (NaN→0 fallback in
   `get_multiple_prices`, `None` return in `get_historical`) without a
   structured status; the new module replaces "ad hoc" with explicit
   `ProviderStatus` categories.
6. Indexes/ETFs: only handled via a one-off `three_month_average_volume`
   None-check in `get_current_price`; no broader safety net.
7. **No existing caching anywhere** in `data/fetcher.py`.
8. **Yes**, excessive requests are possible today: a full classification
   pass over ~400 symbols would mean ~400 individual `get_historical()`
   calls with no batching and no cache — exactly the gap this phase closes.

**New module — `data/market_data_validator.py`:**
- `ProviderStatus` enum: OK, INVALID_SYMBOL, UNSUPPORTED_SECURITY_TYPE,
  EMPTY_HISTORY, INSUFFICIENT_HISTORY, MISSING_OHLCV, STALE_DATA,
  INCOMPLETE_DAILY_CANDLE, ZERO_VOLUME, MISSING_VOLUME, PROVIDER_ERROR,
  RATE_LIMITED, TEMPORARY_FAILURE, UNKNOWN_ERROR.
- `MarketDataResult` dataclass — symbol/security_type/provider_status,
  is_valid/is_stale/is_complete_daily_candle/has_sufficient_history/
  has_required_ohlcv, latest_close/volume, average_daily_volume,
  average_daily_dollar_volume, history_days_available,
  data_timestamp_utc, latest_completed_candle_date, failure_type,
  failure_reason, retry_after_utc, warnings, raw_metadata (always empty
  in this phase — no secrets).
- `summarize_history(symbol, df, ...)` — pure function, no network calls;
  does all classification/validation given an already-fetched DataFrame
  (or `None`). This is what almost all tests exercise directly.
- `MarketDataClient` — stateful per-evaluation-run client: `get_history`/
  `get_history_batch` (chunked, cached, bounded-retry), `validate`/
  `validate_batch`, `.stats` (cache_hits/cache_misses/
  yfinance_request_count/provider_error_count).

**Completed-daily-candle rule:** reuses the exact convention already
tested in `tests/test_incomplete_candle.py` (Gate 9 in `agent/core.py`):
when the market is open, drop the last (forming) row; when closed, keep
it. **Documented limitation:** no market-holiday calendar — this is a
conservative Mon-Fri rule only, no new dependency was added.

**Freshness rule:** gap between the most recent *expected* weekday and the
latest *completed* candle must not exceed `config.ELIGIBILITY_STALE_DAYS`
(reused, default 3) or the result is `STALE_DATA`. A single missing
trading day (e.g. an undetected holiday) is tolerated by this threshold —
documented, not a bug. Stale data is explicitly `is_valid=True` (the
ticker is real) but `provider_status != OK` (not safe to evaluate now).

**Liquidity formulas:** `average_daily_volume` = mean of valid (non-NaN,
non-negative, numeric-coerced) volume over the lookback window
(`config.ELIGIBILITY_LOOKBACK_DAYS`, reused, default 63, configurable).
`average_daily_dollar_volume` = mean of (close × volume) per completed
day over the same window — not `avg_volume × latest_close` — documented
as more robust against price drift.

**Batching/caching/retries:** `get_history_batch` chunks by
`config.MARKET_DATA_BATCH_SIZE` (default 50), degrades a whole-chunk
failure to per-symbol fallback fetches, and preserves the caller's symbol
order. `MarketDataClient` caches successful fetches in memory for its own
lifetime (one evaluation run); failures are never cached. `_fetch_single`
retries up to `config.MARKET_DATA_MAX_RETRIES` (default 2) with
`base * 2**attempt` backoff for transient/rate-limited errors only —
`INVALID_SYMBOL` is never retried.

**New config constants:** `MARKET_DATA_MIN_HISTORY_DAYS` (30),
`MARKET_DATA_HISTORY_PERIOD` ("6mo"), `MARKET_DATA_MAX_RETRIES` (2),
`MARKET_DATA_RETRY_BACKOFF_SECONDS` (1.5), `MARKET_DATA_BATCH_SIZE` (50),
`MARKET_DATA_PROVIDER_ERROR_RETRY_HOURS` (4),
`MARKET_DATA_INVALID_SYMBOL_RETRY_HOURS` (168),
`MARKET_DATA_DATA_QUALITY_RETRY_HOURS` (24) — all overridable via env vars.

**Testing note (correction from Phase 2):** no smoke test was run against
the real database this phase. `test_fetch.py` was deliberately **not**
run because it calls `init_db()` against the real production DB. Instead,
a one-off manual check imported the module and called `MarketDataClient.validate()`
with `yf.download` mocked — zero network calls, zero DB writes — confirming
the public API works end-to-end. The 33 automated tests mock `yf.download`
exclusively and never import `db.database` (asserted by a dedicated test).

**Revert command:**
```
git revert <commit-hash-of-this-entry>
```
Note: purely additive (new module, new config constants); does not modify
any existing function used by other code, so reverting is safe.

**Affects production behavior** | No — nothing in `bot/telegram_bot.py` or
`agent/core.py` calls this module yet. It is unused until Phase 4 wires it
into a live daily evaluator.
