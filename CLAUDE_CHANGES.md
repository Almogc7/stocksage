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

## Entry 11 — Docs: watchlist and alert design proposal

| Field | Value |
|---|---|
| **Files changed** | `WATCHLIST_AND_ALERTS_DESIGN.md` (new), `CLAUDE_CHANGES.md` (updated) |
| **Contents** | 19-section design document covering: Phase 1 verification, current watchlist implementation analysis, symbol classification, multi-level watchlist architecture, eligibility rules, relevance score, promotion/demotion hysteresis, scan schedule, alert lifecycle, example messages, database schema proposal, git-safety design, API performance estimates, test plan, and 20 decisions requiring explicit approval. No production code changed. |
| **Symbol counts found** | 404 config.py entries, 399 unique, 80 in DB (original seed from 2026-05-16), 5 duplicates |
| **Critical finding** | Removed symbols re-appear after restart because `populate_from_config()` uses `INSERT OR IGNORE` without checking a removed-symbols exclusion list |
