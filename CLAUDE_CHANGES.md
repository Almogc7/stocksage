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

## Entry 19 — Feat: add dry-run watchlist evaluator (Phase 4)

| Field | Value |
|---|---|
| **Commit** | (pending — see `git log` after commit) |
| **Files changed** | `services/watchlist_evaluator.py` (new), `services/__init__.py` (new), `scripts/dry_run_evaluation.py` (new), `db/database.py` (read-only `get_unclassified_symbols()`), `config.py`, `tests/test_watchlist_evaluator.py` (new), `CLAUDE_CHANGES.md` |
| **New test count** | 240 total (28 new) — all pass |

Connects SQLite watchlist state + evaluation_runs tracking (Phase 2) +
MarketDataClient (Phase 3) + the existing eligibility/hysteresis engine
into one dry-run evaluation pass. **No watchlist row is ever written by
this phase** — every change is computed and reported as "proposed" only.
The only database write performed is one `evaluation_runs` row per run
(dry_run=True), which was always the documented Phase 2 design.

**What dry-run means here:** the evaluator reads current state, fetches
live (or mocked) market data, computes what relevance score and what
state transition *would* happen under the existing hysteresis rules, and
returns a `DryRunEvaluationResult` with the full proposed picture —
proposed ACTIVE/MONITOR/TEMPORARILY_INELIGIBLE counts, per-symbol
promotions/demotions/recoveries/ineligible-transitions, and warnings. It
never calls `update_symbol_state`/`update_eligibility`/`update_hysteresis`.
Phase 5 will take this same `DryRunEvaluationResult` and apply the
proposed changes transactionally to the real watchlist table.

**New module — `services/watchlist_evaluator.py`:**
- `SymbolEvalResult` / `DryRunEvaluationResult` dataclasses (fields per the
  Phase 4 spec: counts before/after every tier, proposed promotions/
  demotions/ineligible/recoveries, provider/cache/request stats, warnings,
  fatal_error).
- `run_dry_run_evaluation(client=None, triggered_by="manual", now=None)` —
  main entrypoint. Accepts any object implementing `validate_batch()` /
  `get_history()` (duck-typed like `MarketDataClient`), so tests inject a
  fully-controlled fake with zero network calls.
- `_ActiveTracker` — simulates the proposed ACTIVE set as promotion/
  demotion decisions are made, enforcing the 30 cap, 8 bank cap, and
  5-point replacement margin with deterministic (score desc, symbol asc)
  tie-breaking.

**Universe selection (with skip_reason recorded for every excluded
symbol):** evaluates ACTIVE+MONITOR stock symbols, never-classified stock
symbols (`wl_classified=0`, via the new read-only `get_unclassified_symbols()`),
and TEMPORARILY_INELIGIBLE symbols whose `reeval_date` has passed (NULL
`reeval_date` is treated as always-due, since nothing sets this column
yet). Skips USER_REMOVED, ETF_INDEX_CONTEXT, ETF/index/crypto security
types, disabled rows, and not-yet-due TEMPORARILY_INELIGIBLE symbols.

**Recovery semantics (documented design decision):** `determine_state_change()`
in `analyzers/eligibility.py` has no TEMPORARILY_INELIGIBLE → MONITOR
recovery branch — it only promotes from `current_state == 'MONITOR'`. Per
spec, a recovered symbol must land in MONITOR, not be promoted directly to
ACTIVE in the same cycle. This recovery transition is implemented in the
evaluator itself, not by modifying the shared, already-tested
`determine_state_change()` contract used elsewhere.

**Provider-outage handling:** if the fraction of evaluated symbols
returning RATE_LIMITED/PROVIDER_ERROR/TEMPORARY_FAILURE reaches
`config.WATCHLIST_PROVIDER_OUTAGE_THRESHOLD_PCT` (default 0.4), the run is
marked `provider_degraded` and every ACTIVE→MONITOR demotion proposal
based on a legitimately low score is reverted back to "stay ACTIVE" with
an explanatory reason; the evaluation_runs row is finalized as
`partial_failure`. Symbols kept ACTIVE this way are tracked internally
with a protected sentinel score so they can never be evicted by a
same-run promotion's replacement-margin check. **Documented scope
limitation:** outage suppression only covers score-driven demotions —
data-quality failures (STALE_DATA, MISSING_OHLCV, etc.) are treated as
symbol-specific per the spec's literal wording and are not suppressed.

**Database change:** `db/database.py` gained one new read-only helper,
`get_unclassified_symbols()` (plain `SELECT`, no writes).

**New config constant:** `WATCHLIST_PROVIDER_OUTAGE_THRESHOLD_PCT` (0.4),
overridable via env var.

**CLI script — `scripts/dry_run_evaluation.py`:** manual, opt-in entrypoint.
`--db <path>` points it at a temp/test SQLite file instead of the real
`db/stocksage.db`; `--mock` skips all network calls. Prints a plain-text
summary; never sends a Telegram message (does not import the bot); cannot
write to the watchlist table (that capability doesn't exist yet). **Not
run against the real production DB this session** — only exercised against
a temporary DB with `--mock` per the user's explicit instruction to ask
before running anything against production.

**Testing note:** all 28 new tests use a `FakeMarketDataClient` (duck-typed
fully-controlled fake, no `yf.download` involved at all) and a temporary
SQLite file per test. Zero production DB usage, zero live network calls,
zero Telegram imports (asserted by a dedicated test).

**Revert command:**
```
git revert <commit-hash-of-this-entry>
```
Note: purely additive (new module/script, one new read-only DB helper, one
new config constant); does not modify any existing function's behavior
used by other code, so reverting is safe.

**Affects production behavior** | No — nothing calls `run_dry_run_evaluation()`
automatically yet; it is only reachable via the new manual CLI script or
direct import. Phase 5 will be the first phase that can change a real
watchlist row.

## Entry 20 — Feat: apply watchlist evaluation state changes (Phase 5)

| Field | Value |
|---|---|
| **Commit** | (pending — see `git log` after commit) |
| **Files changed** | `services/watchlist_evaluator.py`, `db/database.py` (+`apply_evaluation_changes`), `scripts/dry_run_evaluation.py`, `tests/test_watchlist_evaluator_apply.py` (new), `CLAUDE_CHANGES.md` |
| **New test count** | 265 total (25 new) — all pass |

Adds real, transactional application of the Phase 4 evaluator's proposed
changes. The dry-run computation is unchanged — `run_dry_run_evaluation()`
is now a thin alias for the new `run_watchlist_evaluation(apply=False)`.
`run_watchlist_evaluation(apply=True)` runs the identical computation and
then writes the result to the `watchlist` table in **one atomic
transaction**.

**Database write — `db.apply_evaluation_changes(updates: list[dict])`:**
opens a single connection/transaction for the whole batch; every column
key is validated against an explicit allowlist *before* any write begins,
and any exception during the loop rolls back everything written so far in
that call (sqlite3's `with conn:` semantics) — no partial promotion/
demotion state can survive a failed apply.

**Per-symbol persistence — `_build_db_fields()` in `services/watchlist_evaluator.py`:**
mirrors the threshold semantics `analyzers.eligibility.determine_state_change()`
deliberately leaves to its caller ("does NOT update the DB — callers are
responsible for persistence"):
- No transition this pass: increments/resets `consec_promote_count` or
  `consec_demote_count` based on whether the score crossed
  `PROMOTION_THRESHOLD`/`DEMOTION_THRESHOLD`; increments `dwell_days` for
  retained ACTIVE symbols.
- A real transition: resets all three counters/dwell_days to 0 and stamps
  `last_promoted`/`last_demoted` as appropriate.
- → TEMPORARILY_INELIGIBLE: writes `exclusion_reason` and `reeval_date`
  (reusing `MarketDataResult.retry_after_utc` from Phase 3 when available,
  falling back to `MARKET_DATA_DATA_QUALITY_RETRY_HOURS` otherwise).
- TEMPORARILY_INELIGIBLE → MONITOR (recovery): clears `exclusion_reason`/
  `reeval_date`, never promotes directly to ACTIVE.
- Always sets `wl_classified = 1` (the symbol has now had a real apply
  pass; Phase 1's startup classifier must never touch it again).
- A symbol with a `provider_transient` failure this run gets **no write
  at all** — `db_fields` stays `None` — its last-known-good score/state is
  left completely untouched.
- USER_REMOVED and ETF_INDEX_CONTEXT rows are never gathered as candidates
  in the first place (same universe-selection logic as Phase 4), so they
  are structurally never written by apply mode either.

**Provider outage in apply mode:** identical suppression logic to Phase 4 —
score-driven ACTIVE→MONITOR demotions are reverted to "stay ACTIVE" when
the run is provider-degraded, and that reversion is what actually gets
written (i.e. nothing changes for those symbols). The evaluation_runs row
is still finalized `partial_failure`.

**CLI (`scripts/dry_run_evaluation.py`):** default remains dry-run.
Applying requires **both** `--apply` and `--yes` — `--apply` alone prints a
warning and falls back to dry-run. Prints `MODE: APPLY (REAL CHANGES WRITTEN)`
vs `MODE: DRY-RUN (NO CHANGES WRITTEN)` prominently. Still never imports
the bot.

**Manual validation (real yfinance, copied DB only — not the production DB):**
ran apply twice against a timestamped copy of `db/stocksage.db`.
- **Apply #1:** 62/62 symbols evaluated, 0 promotions (`consec_promote_count`
  0→1 for all qualifying symbols) — expected hysteresis warm-up, see
  `WATCHLIST_LIVE_DRY_RUN_REPORT.md`.
- **Apply #2:** 30 promotions (`VRT, MRVL, AAPL, AMD, ANET, APLD, AVGO, BA,
  CCJ, CEG, CRWD, CSCO, DDOG, DOCN, ETN, FTNT, GLW, GOOGL, JPM, NEE, NET,
  NVDA, QCOM, SNOW, TSLA, VST, ENPH, OKLO, RKLB, SMR`) — exactly the top 30
  scorers from the Phase 4.5 live report. ACTIVE: 0→30 (at the cap, not
  exceeded), bank-ACTIVE: 1 (JPM, well under the cap of 8), 0 duplicate
  tickers, `last_promoted` correctly stamped, counters reset to 0.
- Confirmed the **real** `db/stocksage.db` mtime and every table's row
  count were identical before and after both runs; the copy was deleted
  afterward and was never committed (`db/*.db` is gitignored).

**Revert command:**
```
git revert <commit-hash-of-this-entry>
```
Note: adds a new DB function and extends the evaluator; does not modify
any existing column or remove anything, so reverting is safe. A DB that
already had apply mode run against it keeps its applied state — reverting
only removes the *capability* to apply again, it does not undo prior
writes (use the standard watchlist `/remove`+`/add` or direct SQL if a
specific applied change needs manual correction).

**Affects production behavior** | Not yet — nothing calls
`run_watchlist_evaluation(apply=True)` automatically. It is only reachable
via direct import or the CLI script with explicit `--apply --yes`, and was
not run against the real production DB this session.

## Entry 21 — Feat: add rollback support for watchlist evaluation runs (Phase 5.5)

| Field | Value |
|---|---|
| **Commit** | (pending — see `git log` after commit) |
| **Files changed** | `db/database.py` (v5 migration + `apply_evaluation_changes` extended + `get_changes_for_run`/`apply_rollback`), `services/watchlist_evaluator.py` (audit capture + `rollback_evaluation_run`), `scripts/rollback_evaluation_run.py` (new), `tests/test_watchlist_rollback.py` (new), `CLAUDE_CHANGES.md` |
| **New test count** | 288 total (23 new) — all pass |

A successful Phase 5 apply run previously had no built-in undo — only
manual SQL. This phase adds a real audit trail and a safe, conflict-aware
rollback.

**Schema (v5 migration, idempotent):** new `evaluation_run_changes` table
— `run_id`, `symbol`, `change_type` (promotion/demotion/ineligible/
recovery/score_update/counter_update/metadata_update), `previous_values_json`,
`new_values_json`, `changed_columns_json`, `created_at`, `dry_run`,
`triggered_by`, `rollback_available`, `rolled_back_at`, `rollback_run_id`,
`rollback_status`. Indexed on `run_id`. Never written for dry-run runs —
only apply mode creates audit rows, one per symbol actually written
(symbols left untouched due to a transient provider failure get no audit
row either, matching Phase 5's "leave it alone" behavior for those).

**Atomicity (the core safety property):** `db.apply_evaluation_changes()`
now writes the watchlist UPDATEs **and** the matching audit INSERTs in the
**same transaction** — confirmed live during manual validation: a copy
that hadn't yet run the v5 migration caused the audit INSERT to fail
mid-transaction, and the watchlist UPDATE that would have preceded it was
correctly rolled back too (`relevance_score` stayed `NULL`). Rollback
itself (`db.apply_rollback()`) restores the watchlist rows and marks the
audit rows `rolled_back` in one transaction the same way.

**`services/watchlist_evaluator.rollback_evaluation_run(run_id)`:**
1. Loads the run and its audit rows; raises `RollbackError` for an unknown
   run_id, a dry-run run_id, or a run already marked rolled back.
2. For every audited symbol, compares its **current** watchlist values
   against the audit row's `new_values` — if anything differs (a manual
   edit, or a later run, touched that row since), the **entire** rollback
   is aborted with `status='conflict'` and every conflicting symbol/column
   reported. Nothing is written when there's a conflict.
3. If clean, restores every symbol's `previous_values` and marks the audit
   rows rolled back atomically, then records a new `evaluation_runs` row
   (`metadata_json: {"rollback_of_run_id": run_id}`) representing the
   rollback action itself.

**Change-type classification:** promotion (→ACTIVE), demotion (ACTIVE→MONITOR),
ineligible (→TEMPORARILY_INELIGIBLE), recovery (TEMPORARILY_INELIGIBLE→MONITOR),
counter_update (no transition, hysteresis counters changed — the common
case for a healthy retained symbol), score_update (fallback, no counter
change). `metadata_update` is reserved for future use (not currently
reachable — Phase 5.5 never changes category tags or other pure metadata).

**CLI — `scripts/rollback_evaluation_run.py`:** `--run-id` is required;
without `--yes` it only previews which symbols would be restored and
writes nothing. Never imports the bot.

**Manual validation (real yfinance, copied DB only):**
1. Copied `db/stocksage.db` → ran `migrate_db()` on the **copy only** (the
   real file doesn't have the v5 table yet — confirmed after cleanup, see
   below).
2. **Apply #1** (run_id 2): 62/62 evaluated, 62 audit rows created
   (`counter_update`), 0 promotions (hysteresis warm-up, as in Phase 4.5/5).
3. **Apply #2** (run_id 3): 30 promotions (same top-30 set as Phase 5's
   validation). 62 audit rows for this run too.
4. **Rollback run_id 3**: `status=success`, 62 symbols restored — `NVDA`,
   `VRT`, and all 30 promoted symbols confirmed back at `MONITOR`;
   `get_watchlist_summary()` returned to `{ETF_INDEX_CONTEXT: 18, MONITOR: 62}`,
   exactly the post-apply-#1 state.
5. Confirmed the **real** `db/stocksage.db` was untouched throughout: same
   mtime, same row counts, and it still doesn't even have the
   `evaluation_run_changes` table (proving `migrate_db()` was never run
   against it this session). The copy was deleted afterward and never
   committed (`db/*.db` is gitignored).

**Limitations:**
- Conflict detection compares exact column values; it cannot distinguish
  "someone reverted to the same value on purpose" from "nothing changed" —
  both look identical and are correctly treated as no-conflict.
- Rollback is all-or-nothing per run: a single conflicting symbol blocks
  rolling back any of that run's other (non-conflicting) symbols, by
  design — partial rollback was explicitly disallowed by the spec.
- There is no "rollback of a rollback" helper yet; the rollback action's
  own `evaluation_runs` row has no audit trail of its own (it doesn't
  change watchlist rows beyond what it restores, so nothing further to
  audit).

**Revert command:**
```
git revert <commit-hash-of-this-entry>
```
Note: adds a new table and extends one function's signature with an
optional parameter (backward compatible — existing single-argument calls
still work); does not modify any existing column, so reverting is safe.

**Affects production behavior** | Not yet — nothing calls
`rollback_evaluation_run()` automatically, and apply mode was not run
against the real production DB this session (only its gitignored copy).

## Entry 22 — Feat: add watchlist evaluation scheduler logic (Phase 6)

| Field | Value |
|---|---|
| **Commit** | (pending — see `git log` after commit) |
| **Files changed** | `services/watchlist_scheduler.py` (new), `services/watchlist_evaluator.py` (run_type/extra_metadata params + timestamp-injection fix), `db/database.py` (`started_at`/`completed_at` override params), `scripts/dry_run_evaluation.py` (+`--schedule-check`/`--scheduled-dry-run`/`--scheduled-apply`), `config.py` (+5 constants), `tests/test_watchlist_scheduler.py` (new), `CLAUDE_CHANGES.md` |
| **New test count** | 323 total (35 new) — all pass |

Decides *when* it's safe to run a watchlist eligibility evaluation. Pure
library + CLI — **nothing in this phase runs automatically in the
background**. `agent/core.py` and `main.py` are untouched; something (a
human, or a later real scheduler integration) must call
`run_scheduled_evaluation()` explicitly.

**Bug found and fixed while building this phase:** `db.create_evaluation_run()`
and the `_finalize_evaluation_run()` family always used the real wall-clock
time for `started_at`/`completed_at`, ignoring any `now` injected into
`run_watchlist_evaluation()`. This silently broke the scheduler's
run-once-per-market-day check for every test (and for any future caller
that injects a specific `now`) — a run's `started_at` would record the
real current time instead of the logical time being tested. Fixed by
adding optional `started_at`/`completed_at` override parameters to those
`db/database.py` functions (default unchanged — real wall-clock time —
so this is backward compatible) and threading them through from
`run_watchlist_evaluation()`. `duration_seconds` still measures genuine
wall-clock execution time; only the *stored timestamp* is anchored to the
caller's logical `now` plus that real elapsed time.

**Market calendar (`services/watchlist_scheduler.py`, no new dependency):**
`is_us_market_day()`/`us_market_holidays()` compute the standard NYSE
fixed-date and nth-weekday-of-month holidays algorithmically — New Year's
Day, MLK Day, Presidents Day, Good Friday (Gregorian Easter algorithm),
Memorial Day, Juneteenth, Independence Day, Labor Day, Thanksgiving,
Christmas — with Saturday/Sunday observed-date shifting. **Documented
limitation:** this cannot know about rare unscheduled NYSE closures; use
`config.WATCHLIST_EXTRA_HOLIDAY_DATES` (comma-separated ISO dates) to add
those manually. `is_early_close_day()` flags the two predictable early
closes (day after Thanksgiving, Christmas Eve) but does **not** change
scheduling — the default 17:30 America/New_York threshold is already
safely after both a normal (16:00) and an early (~13:00) close.

**Scheduling decision:** `should_run_watchlist_evaluation(now_utc)` →
`(bool, reason)`. Refuses naive datetimes (`ValueError`). Checks, in
order: weekend → holiday → before-close → already-ran-today. Reuses DST
correctly via `zoneinfo` (verified at both the March DST-transition Monday
and across summer/winter offsets in tests).

**Run-once-per-market-day:** a *scheduled* run (`run_type='scheduled'`)
that completed `success` or `partial_failure` for a given America/New_York
market date blocks a same-day repeat. A **failed** scheduled run does
**not** block a retry (the whole point of detecting a stuck/crashed run is
that it should be retriable the same day). Manual or dry-run-CLI-triggered
runs (`run_type` in `manual`/`dry_run`) never count toward this check at
all — only a genuine scheduled run satisfies "today's scheduled run
already happened." **Skipped attempts write no `evaluation_runs` row** —
documented as a deliberate choice to keep that table free of "checked, not
due" noise; the decision and reason are only visible in the CLI output /
return value of `run_scheduled_evaluation()`.

**Concurrency guard:** `can_start_evaluation_run()` first sweeps any
`started`-status run older than `WATCHLIST_SCHEDULE_STUCK_RUN_TIMEOUT_MINUTES`
(default 60) via `mark_stuck_runs_failed()` (marks it `failed` with an
explanatory `error_summary`), then refuses to start only if a genuinely
fresh run is still in progress. No distributed locking — single-process
scope, per spec.

**Apply-mode safety:** `run_scheduled_evaluation(apply=None)` resolves to
`config.WATCHLIST_SCHEDULE_APPLY` (default `False` — env var
`WATCHLIST_SCHEDULE_APPLY=true` to change). An explicit `apply=True/False`
argument (e.g. a deliberate CLI `--scheduled-apply --yes`) always overrides
the config default — that's an explicit human action, not the "would an
unattended process apply" question the config governs.

**New config constants:** `WATCHLIST_SCHEDULE_HOUR_ET` (17),
`WATCHLIST_SCHEDULE_MINUTE_ET` (30), `WATCHLIST_SCHEDULE_APPLY` (false),
`WATCHLIST_SCHEDULE_STUCK_RUN_TIMEOUT_MINUTES` (60),
`WATCHLIST_EXTRA_HOLIDAY_DATES` (empty).

**CLI additions (`scripts/dry_run_evaluation.py`):**
- `--schedule-check` — read-only: due-now?, next due time, concurrency
  guard status, last successful scheduled run, current
  `WATCHLIST_SCHEDULE_APPLY` value. Writes nothing.
- `--scheduled-dry-run` — runs `run_scheduled_evaluation(apply=False)`;
  only actually evaluates if due and not blocked.
- `--scheduled-apply` — same, `apply=True`; refuses without `--yes`.

**Manual safe-CLI validation (temp DB only):** `--schedule-check` against a
fresh temp DB on the real "today" (2026-06-20, a Saturday) correctly
reported `due now: False (2026-06-20 is a weekend)` and computed
`next due (UTC): 2026-06-22 21:30:00` (Monday 17:30 ET). `--scheduled-dry-run`
on the same DB correctly skipped with the same weekend reason and wrote no
`evaluation_runs` row. `--scheduled-apply` without `--yes` correctly
refused. Scheduled apply was **not** exercised against any real "due"
window or the production DB this session (today is a weekend) — the "due"
path is covered by the 35 mocked-time unit tests instead.

**Revert command:**
```
git revert <commit-hash-of-this-entry>
```
Note: the `db/database.py` timestamp-override parameters default to the
previous (wall-clock) behavior, and `run_watchlist_evaluation`'s new
parameters default to the previous behavior too — both backward
compatible. Reverting removes the scheduler module/CLI flags only.

**Affects production behavior** | No — nothing calls
`run_scheduled_evaluation()` automatically; `agent/core.py`/`main.py` are
untouched. Only reachable via direct import or the new CLI flags.
