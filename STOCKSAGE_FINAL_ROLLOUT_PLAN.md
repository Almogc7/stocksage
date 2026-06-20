# StockSage Final Rollout Plan

**Date:** 2026-06-20
**Branch:** `claude/stocksage-review-20260617-1200`
**Status:** Final hardening and documentation pass before merge consideration.

This document is the single reference for reviewing, merging, and safely
rolling out everything built on this branch. It assumes the reader has not
read the individual phase reports.

## 1. Executive Summary

This branch took StockSage from a single-tier, config-driven watchlist with
no runtime persistence to a fully persistent, multi-tier, dry-run-first
watchlist lifecycle with audit and rollback support — without ever touching
the real production database or sending a real Telegram message during
development.

**Security and correctness fixes** (earliest commits): replaced example
credentials with placeholders, restored `AUTHORIZED_CHAT_IDS` after it was
accidentally lost, fixed a deprecated `datetime.utcnow()` UTC-consistency
bug in the SQLite alert-cooldown check, fixed an RSI signal-label bug, fixed
a None/NaN volume crash in `get_current_price()`, fixed an incomplete-bar
bias in the Gate-9 green-candle check, fixed an RSI-calculation mismatch
between the chart generator and the analysis engine, and removed a
duplicate watchlist entry.

**Telegram authorization**: every command handler requires an authorized
chat ID (fail-secure — if no IDs are configured, every request is
rejected); this was already in place before the watchlist work and remains
the foundation every new command in this branch builds on.

**Watchlist persistence and multi-tier architecture**: the watchlist moved
from "re-seeded from `config.py` on every restart" to a real state machine
persisted in SQLite — `ACTIVE` (scanned for alerts, capped at 30, max 8
bank symbols), `MONITOR` (tracked, not scanned), `ETF_INDEX_CONTEXT`
(price-context only, never alerted on), `TEMPORARILY_INELIGIBLE` (data
problems, with a retry timestamp), and `USER_REMOVED` (soft-deleted,
never auto-reactivated). A `wl_classified` flag ensures the one-time
startup classifier never resets dynamically-managed state again after a
restart — this was a real, confirmed bug that existed before this branch's
work and is now fixed and regression-tested.

**Relevance scoring**: a six-component 0–100 score (data quality 25%,
liquidity 25%, trend 20%, momentum 15%, setup proximity 10%, volatility
5%) drives promotion/demotion decisions via a hysteresis state machine
(two consecutive qualifying passes required to promote or demote) —
existing, unchanged stock-scoring logic (`analyzers/technical.py`'s
opportunity score) was never modified.

**Market-data validation layer** (`data/market_data_validator.py`):
classifies every yfinance fetch into one of 14 explicit `ProviderStatus`
values (OK, STALE_DATA, INVALID_SYMBOL, RATE_LIMITED, etc.) instead of a
single generic error, with batching, in-memory caching, and bounded
retry/backoff — distinguishing a temporary provider hiccup from a
permanently invalid ticker from a legitimate data-quality problem.

**Dry-run evaluator** (`services/watchlist_evaluator.py`): computes
proposed promotions/demotions/recoveries/ineligible-transitions without
ever writing to the watchlist table, respecting the ACTIVE cap, bank cap,
and 5-point replacement margin with deterministic tie-breaking, and
detecting broad provider outages to suppress mass demotions.

**Apply mode**: the same computation, writing the result to the watchlist
table in one atomic transaction — verified, live, on a copied production
database, that the very first apply run only warms up hysteresis counters
(zero promotions, as designed) and a second run then promotes the
qualifying symbols.

**Evaluation-run tracking, audit log, and rollback**: every run (dry-run,
apply, scheduled, or manual) is recorded in `evaluation_runs`; every
symbol an apply run actually changes gets an audit row in
`evaluation_run_changes` with its previous and new values; a successful
apply run can be rolled back by run ID — refusing entirely (writing
nothing) if anything has changed since, rather than silently overwriting
a later manual edit.

**Scheduler logic**: decides whether it's safe to run an evaluation right
now (after-market-close, not a weekend/holiday, not already run today,
no other run in progress), using an algorithmically-computed US market
holiday calendar (no new dependency). Nothing runs automatically yet —
this is a library and CLI only.

**Telegram refresh commands**: `/refresh_watchlist`,
`/watchlist_refresh_status`, `/watchlist_changes` — all dry-run by
default, apply gated behind both a config flag and an explicit
confirmation word.

## 2. Current Branch Status

| Item | Value |
|---|---|
| Current branch | `claude/stocksage-review-20260617-1200` |
| Backup branch | `backup/pre-claude-review-20260617-1200` (untouched — still at `cbb21b9`) |
| Pushed to GitHub? | **No** — nothing on this branch has been pushed at any point |
| Commits added on this branch (vs. `main`) | **30** |
| Current test count | **349 tests, all passing** |
| Production DB touched? | **No** — verified throughout every phase (mtime/row-counts unchanged); re-verified for this final report |
| Real Telegram messages sent? | **No** — every Telegram-related test and validation used a mocked `Update`/`Context`; no bot process was started |

## 3. Major Behavior Changes

- **Scans now use ACTIVE symbols only** — `agent/core.py`'s alert scanner
  calls `get_active_watchlist()` instead of scanning all ~400 config
  symbols every cycle.
- **Watchlist runtime state lives in SQLite**, not `config.py`. `config.py`
  is only the initial seed/default source — once a symbol's row exists, its
  tier/score/counters are managed entirely in the database.
- **`/remove` persists across restarts** — a removed symbol's `enabled=0`
  row is never re-inserted by `populate_from_config()`'s `INSERT OR IGNORE`
  seeding.
- **Restart no longer resets dynamic state** — the `wl_classified` flag
  (Phase 1) means the startup classifier only ever touches a symbol once.
- **Telegram commands require authorization** — unchanged from before this
  branch, but every new command follows the identical pattern.
- **`/refresh_watchlist` defaults to dry-run** — no flag, or `dry_run`, is
  always safe.
- **Telegram apply is disabled unless explicitly configured** —
  `TELEGRAM_ALLOW_WATCHLIST_APPLY=true` AND the literal word `confirm`.
- **Scheduler apply is disabled unless explicitly configured** —
  `WATCHLIST_SCHEDULE_APPLY=true` — and even then nothing runs
  automatically; something must call `run_scheduled_evaluation()`.
- **Apply mode has audit and rollback support** — every applied change is
  recorded with before/after values and can be undone by run ID via the
  CLI, with conflict detection if something else touched the row since.

## 4. Environment Variables

| Variable | Required? | Safe default | Notes |
|---|---|---|---|
| `TELEGRAM_TOKEN` | Yes | — | Bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | Yes | — | Personal chat ID for alerts |
| `AUTHORIZED_CHAT_IDS` | Optional | falls back to `TELEGRAM_CHAT_ID` | Comma-separated authorized chat IDs |
| `ANTHROPIC_API_KEY` | Optional | — | Loaded, not actively used |
| `ALPHA_VANTAGE_KEY` / `NEWS_API_KEY` | Optional | — | Reserved, unused |
| `TELEGRAM_ALLOW_WATCHLIST_APPLY` | Optional | **`false`** | Gates `/refresh_watchlist apply confirm` |
| `WATCHLIST_SCHEDULE_APPLY` | Optional | **`false`** | Gates what an unattended scheduled run would do |
| `WATCHLIST_SCHEDULE_HOUR_ET` / `WATCHLIST_SCHEDULE_MINUTE_ET` | Optional | `17` / `30` | Daily evaluation time, America/New_York |
| `WATCHLIST_SCHEDULE_STUCK_RUN_TIMEOUT_MINUTES` | Optional | `60` | Stuck-run sweep timeout |
| `WATCHLIST_EXTRA_HOLIDAY_DATES` | Optional | empty | Extra one-off market closures (`YYYY-MM-DD,...`) |
| `WATCHLIST_PROVIDER_OUTAGE_THRESHOLD_PCT` | Optional | `0.4` | Provider-degraded detection threshold |
| `ACTIVE_MAX_SIZE` / `ACTIVE_BANK_MAX` | Optional | `30` / `8` | Tier caps |
| `PROMOTION_THRESHOLD` / `DEMOTION_THRESHOLD` | Optional | `60` / `45` | Score thresholds |
| `PROMOTION_CONSEC_REQUIRED` / `DEMOTION_CONSEC_REQUIRED` | Optional | `2` / `2` | Consecutive-pass hysteresis |
| `MARKET_DATA_*` (8 constants) | Optional | see `config.py` | yfinance batching/retry/cache tuning |

**Confirmed:** `.env` was not modified at any point this session (mtime
unchanged throughout). `.env.example` was updated to document the two new
apply-gating variables, and contains **only placeholder strings** — no
real tokens, IDs, or secrets (verified by reading the file: every value is
either a `your-...-here` placeholder, a literal `false`, or blank).

## 5. Database Migration Plan

On the first `init_db()` call after merging (i.e. the first time `main.py`
or `run_bot()` starts), `migrate_db()` runs automatically and idempotently:

- **v1**: `enabled`, `removed_at` columns (soft-delete) — already existed
  before this branch.
- **v2**: `wl_state`, `security_type`, `relevance_score`, `last_evaluated`,
  `last_promoted`, `last_demoted`, `exclusion_reason`, `reeval_date`,
  `consec_promote_count`, `consec_demote_count`, `dwell_days`, `source`
  columns + `symbol_categories` table.
- **v3**: `wl_classified` column, **backfilled to 1 for every existing row**
  in the same migration step — this is what prevents an upgrade from
  resetting your live ACTIVE/MONITOR/etc. assignments back to the
  hardcoded 30-symbol seed list.
- **v4**: `evaluation_runs` table (bookkeeping only, never touches
  `watchlist`).
- **v5**: `evaluation_run_changes` table (apply-mode audit trail, never
  touches `watchlist` either).

**Existing rows are always preserved** — every migration step is either an
additive `ALTER TABLE ... ADD COLUMN` or `CREATE TABLE IF NOT EXISTS`;
nothing is ever dropped, renamed, or destructively rewritten.

**Exact pre-migration backup command:**
```bash
cp db/stocksage.db "db/stocksage_backup_$(date +%Y%m%d_%H%M%S).db"
```
(this naming pattern is already covered by `.gitignore`'s `db/*.db` rule)

**Exact post-migration verification commands:**
```bash
python -c "
import db.database as db
print(db.get_watchlist_summary())
print('rows:', sum(db.get_watchlist_summary().values()))
"
sqlite3 db/stocksage.db ".tables"
sqlite3 db/stocksage.db "SELECT COUNT(*) FROM watchlist WHERE wl_classified = 1"
```
The last command's count should equal your total watchlist row count
immediately after the first post-merge startup — confirming the backfill
ran and nothing will be reclassified on the next restart.

## 6. Safe Production Rollout Steps

1. Stop the running bot (`Ctrl+C` the `python main.py` process, or stop the
   scheduled task/service running it).
2. Back up the production DB: `cp db/stocksage.db db/stocksage_backup_$(date +%Y%m%d_%H%M%S).db`
3. Back up `.env`: `cp .env .env.backup_$(date +%Y%m%d_%H%M%S)` (keep this
   backup **outside** the repo directory or ensure it matches the `.env*`
   gitignore pattern — verify with `git check-ignore -v .env.backup_...`
   before assuming it's safe).
4. Confirm working tree is clean: `git status` (no uncommitted changes you
   don't intend to merge).
5. Merge the branch into main (see exact command in §7).
6. Pull latest `main` on the home computer running the bot.
7. Install/update dependencies if `requirements.txt` changed:
   `pip install -r requirements.txt`.
8. Run tests if practical: `python -m pytest -q` (or at minimum
   `python test_fetch.py` as the existing smoke test).
9. Run the migration safely — it runs automatically on the next
   `python main.py` start, OR run it standalone first to verify before
   starting the bot:
   ```bash
   python -c "import db.database as db; db.migrate_db(); print(db.get_watchlist_summary())"
   ```
10. Start the bot in safe mode (default — apply flags unset/false):
    `python main.py`.
11. Test Telegram authorization: send any command from an unauthorized
    chat and confirm silence; from your authorized chat, confirm `/help`
    responds.
12. Run `/watchlist_refresh_status` — confirm it reports "No watchlist
    evaluation has run yet" (expected on a fresh post-merge DB) without
    error.
13. Run `/refresh_watchlist` (dry-run, the default) — review the summary.
14. Review the output carefully — expect **zero promotions on the first
    run** (hysteresis warm-up, not a bug).
15. **Do not enable apply yet** — leave `TELEGRAM_ALLOW_WATCHLIST_APPLY`
    and `WATCHLIST_SCHEDULE_APPLY` unset.
16. Let the normal ACTIVE-tier alert scans continue running as before —
    this branch did not change what gets scanned for alerts beyond
    Phase pre-1's "ACTIVE only" change, which was already live.
17. Only after reviewing several days of dry-run `/refresh_watchlist`
    output, consider a manual CLI apply (never via Telegram first) —
    see §9 and the "What Not To Enable Yet" section.

## 7. Safe Commands

```bash
# Back up the production DB
cp db/stocksage.db "db/stocksage_backup_$(date +%Y%m%d_%H%M%S).db"

# Confirm .env is gitignored (should print ".gitignore:1:.env  .env")
git check-ignore -v .env

# Run the full test suite
python -m pytest -q

# Run a dry-run evaluation against the REAL db (read-only watchlist; writes
# one evaluation_runs row only)
python scripts/dry_run_evaluation.py --db db/stocksage.db

# Run a dry-run evaluation against a COPY (recommended for first-time use)
cp db/stocksage.db db/stocksage_copy.db
python scripts/dry_run_evaluation.py --db db/stocksage_copy.db

# Apply-mode validation against a COPY only (never the real DB without
# explicit, separate approval)
python scripts/dry_run_evaluation.py --db db/stocksage_copy.db --apply --yes

# Roll back a specific run on a COPY
python scripts/rollback_evaluation_run.py --db db/stocksage_copy.db --run-id <ID> --yes

# Clean up a validation copy when done
rm db/stocksage_copy.db

# Start the bot
python main.py

# Check git status
git status

# Revert a specific commit if needed (creates a new commit, does not rewrite history)
git revert <commit-hash>
```

## 8. Telegram Command Guide

| Command | What it does | Safety |
|---|---|---|
| `/refresh_watchlist` | Dry-run evaluation (default) | Always safe — never writes |
| `/refresh_watchlist dry_run` | Same as above, explicit | Always safe |
| `/refresh_watchlist apply` | Requests apply mode | **Refused** unless `TELEGRAM_ALLOW_WATCHLIST_APPLY=true` |
| `/refresh_watchlist apply confirm` | Actually applies | **Refused** unless both the config flag is true AND this exact wording is used |
| `/watchlist_refresh_status` | Shows the last run's status/counts | Read-only |
| `/watchlist_changes` | Shows latest applied (or dry-run) changes | Read-only |
| `/watchlist_changes N` | Same, capped at N symbols per category | Read-only |
| `/watchlist_changes run ID` | Same, for a specific run | Read-only |
| `/watchlist_active` / `/watchlist_monitor` / `/watchlist_context` / `/watchlist_ineligible` | List symbols in each tier | Read-only |
| `/watchlist_status SYMBOL` | Full detail for one symbol | Read-only |

**Recommended initial posture:**
- Dry-run via Telegram is safe to use immediately.
- Telegram apply (`TELEGRAM_ALLOW_WATCHLIST_APPLY`) should remain **off**
  initially.
- Rollback is **CLI-only** by design — there is no Telegram rollback
  command. Find the run ID via `/watchlist_changes` or
  `/watchlist_refresh_status`, then run
  `scripts/rollback_evaluation_run.py` from the machine running the bot.

## 9. Rollback Plan

### Git rollback
To return to the pre-watchlist-work state: `git checkout backup/pre-claude-review-20260617-1200`.
To undo a specific merge commit on `main` without rewriting history:
`git revert -m 1 <merge-commit-hash>`.

### Database rollback
Restore the most recent pre-change backup:
```bash
cp db/stocksage_backup_<timestamp>.db db/stocksage.db
```
Use this when something is wrong at the **schema or whole-database**
level (e.g. a bad migration), not for undoing a single evaluation run.

### Evaluation-run rollback
Use this when a **specific apply run** promoted/demoted the wrong
symbols, but the rest of the database is fine:
```bash
python scripts/rollback_evaluation_run.py --db db/stocksage.db --run-id <ID> --yes
```
This restores only the symbols that specific run touched, atomically, and
refuses entirely (writing nothing) if any of those symbols have been
changed by something else since — find the run ID via
`/watchlist_changes` or `/watchlist_refresh_status`.

**When to use which:** evaluation-run rollback first (it's surgical and
safe); database rollback only if the evaluation-run rollback itself
reports a conflict you can't resolve, or the problem predates apply mode
entirely; git rollback only as a last resort affecting code, not data.

## 10. What Not To Enable Yet

Leave these disabled initially:

- `TELEGRAM_ALLOW_WATCHLIST_APPLY` — keep `false`/unset.
- `WATCHLIST_SCHEDULE_APPLY` — keep `false`/unset.
- Automatic scheduled apply — nothing calls `run_scheduled_evaluation()`
  automatically yet regardless, but don't wire it into a cron/Task
  Scheduler entry yet either.
- Real Telegram apply — use the CLI for the first several real applies.
- Any broker/trading automation — out of scope entirely, not built.

**The safest first production rollout is:** dry-run via Telegram for
visibility, manual CLI apply only after personally reviewing dry-run
results, and no automatic apply (Telegram or scheduled) until you've
watched several consecutive successful dry-run days with sensible
promotion/demotion proposals.

## 11. Final Validation Results

- **Full test suite:** 349/349 passing (`python -m pytest -q`, this session).
- **Copied-DB dry-run:** 62/62 symbols evaluated, 0 failures, 0 provider
  degradation, 2 yfinance requests (batched), 0 promotions (expected —
  hysteresis warm-up on a never-evaluated DB copy).
- **Copied-DB apply (run #1, warm-up):** identical counts to dry-run, 0
  promotions, all 62 symbols' `consec_promote_count` incremented 0→1.
- **Copied-DB apply (run #2):** 30 promotions (`VRT, MRVL, AAPL, AMD, ANET,
  APLD, AVGO, BA, CCJ, CEG, CRWD, CSCO, DDOG, DOCN, ETN, FTNT, GLW, GOOGL,
  JPM, NEE, NET, NVDA, QCOM, SNOW, TSLA, VST, ENPH, OKLO, RKLB, SMR`).
  ACTIVE: 0→30 (exactly at cap), bank-ACTIVE: 1 (well under cap of 8).
- **Copied-DB rollback (of run #2):** `status=success`, 62 symbols
  restored; watchlist summary returned to exactly the post-run-#1 state
  (`{ETF_INDEX_CONTEXT: 18, MONITOR: 62}`, zero ACTIVE).
- **Production DB untouched:** mtime and every table's row count identical
  before and after this entire session's validation — re-verified
  immediately before writing this report.
- **No real Telegram messages:** every Telegram test/validation this
  branch ever ran used a mocked `Update`/`Context` with `AsyncMock`
  `reply_text` — confirmed via dedicated tests; no bot process was started.
- **Backup branch untouched:** `backup/pre-claude-review-20260617-1200`
  remains at `cbb21b9`, unchanged since before this branch's work began.

## 12. Remaining Risks

- **yfinance reliability**: the market-data layer handles failures
  explicitly, but yfinance itself is an unofficial, occasionally-rate-limited
  API with no SLA.
- **No exact market-calendar dependency**: the holiday calendar is computed
  algorithmically (documented, conservative) — rare unscheduled NYSE
  closures aren't known automatically; use `WATCHLIST_EXTRA_HOLIDAY_DATES`.
- **No fundamentals yet** — relevance score is purely technical/liquidity-based.
- **No backtesting yet** — thresholds (60/45/30/8/etc.) are reasoned
  defaults, not backtested.
- **First apply warm-up produces zero promotions** — expected, but worth
  remembering so it isn't mistaken for a bug on day one in production too.
- **Scheduler apply disabled by default** — nothing will auto-promote
  until you explicitly wire up and enable a real scheduled trigger.
- **No automatic undo except run-level rollback after apply** — there is
  no "undo everything since merge" button; rollback is per-run.
- **Telegram apply should remain off initially** — see §10.

## 13. Recommended Next Work After Rollout

(Not implemented now — future work only.)

- Portfolio holdings category/design.
- VIX/market-regime filter for alert suppression.
- Earnings-date warnings.
- Backtesting framework.
- Fundamentals integration.
- A risk score separate from the opportunity score.
- Historical data cache (persisted, not just in-memory per-run).
- Scheduled apply, once dry-run stability is proven over time.

## 14. Final Merge Recommendation

**The branch appears ready to merge into `main`**, subject to the
conditions below all being true:

1. You have personally reviewed this report and the per-phase
   `CLAUDE_CHANGES.md` entries (16–24).
2. You back up the production `db/stocksage.db` and `.env` before merging
   (§6, step 2–3) — not because this branch is expected to corrupt
   anything, but because it's the correct discipline for any schema
   migration.
3. You merge with both apply flags left at their safe defaults (`false`).
4. You follow the rollout steps in §6 in order — in particular, running
   `/refresh_watchlist` dry-run and reviewing its output **before** any
   consideration of enabling apply.
5. You're comfortable that "first apply run promotes nothing" is expected
   behavior, not a regression, should you choose to test apply later.

No blockers were found. If any of the above conditions cannot be met
(e.g. you want to test apply before merging), do that on a copy first
using the commands in §7, exactly as this branch's development process
did.
