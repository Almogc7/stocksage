# StockSage Watchlist and Alert Design

**Date:** 2026-06-17  
**Branch:** `claude/stocksage-review-20260617-1200`  
**Status:** Design proposal — no production changes made yet.  
All findings below are based on direct inspection of the repository.  
Recommendations require your approval before implementation.

---

## 1. Executive Summary

The current watchlist contains **399 unique symbols** in config.py, but the live SQLite database contains only **80 symbols** (seeded 2026-05-16 and not updated since). The two sources are out of sync. When the application next restarts, 319 new symbols from config.py will be seeded into the database automatically.

Five symbols are listed in two categories each. Because the `watchlist` table enforces `UNIQUE(symbol)`, only the first category encountered during seeding is stored — the duplicate entry is silently ignored.

More critically: **if you remove a symbol via `/remove` but it remains in config.py, the next application restart will re-add it to the database.** This is the most important persistence bug in the current design.

The scan infrastructure cannot realistically handle 399 symbols on a 15-minute cycle. The morning scan currently attempts to analyze every non-index, non-ETF symbol sequentially — at 399 symbols, with roughly 376 eligible for scanning, this would take approximately **6–12 minutes per cycle**, longer than the check interval itself.

This document proposes a multi-level architecture, persistence fix, eligibility rules, relevance scoring, scan schedule, and alert lifecycle design.

**Decisions that require your explicit approval before any code is changed are listed in Section 19.**

---

## 2. Verification of Existing Fixes

### 2.1 Branch and commit state

| Item | Result |
|---|---|
| Current branch | `claude/stocksage-review-20260617-1200` |
| Backup branch | `backup/pre-claude-review-20260617-1200` — confirmed intact, untouched |
| Commits since backup | 9 commits (8 fix commits + 1 restoration commit today) |
| Latest commit | `12be44a` — restore AUTHORIZED_CHAT_IDS lost during manual watchlist expansion |

All 9 commits on the working branch since the review began:

```
12be44a  fix: restore AUTHORIZED_CHAT_IDS lost during manual watchlist expansion
7ae2b0c  feat: add Telegram bot authorization check to all command handlers
499287c  fix: use last completed daily candle in Gate 9 green-candle check
9540623  fix: use ta.momentum.rsi in chart_generator to match analysis engine
323bab7  fix: handle None/NaN three_month_average_volume in get_current_price()
79d1e92  fix: correct SQLite cooldown UTC consistency and timestamp format
2df423e  fix: correct RSI fringe-zone signal label
163cd5c  fix: replace deprecated datetime.utcnow()
07babc3  fix: remove duplicate QQQ from indices category
```

### 2.2 Test suite

**80 tests pass, 0 failures, 0 errors.**

Tests cover: RSI label correctness, SQLite cooldown UTC handling, volume spike with None/NaN inputs, RSI consistency between chart and analysis engine, incomplete-candle Gate 9 selection, and Telegram authorization for all 14 handlers.

### 2.3 Smoke test

`test_fetch.py` passes. NVDA analysis completes with a valid score, pivot points, and swing levels.

### 2.4 Telegram authorization

`AUTHORIZED_CHAT_IDS` is active in `config.py`. All 14 handlers call `_check_auth()` as the first line. Unauthorized requests are silently dropped with a log entry. If `AUTHORIZED_CHAT_IDS` is not configured, the bot rejects every command (fail-secure).

> **Security notice discovered during this session:** The `.env.example` file currently contains what appear to be real credentials. This file is tracked by Git. Do not commit it in its current state. Before the next `git add` or push, replace the real values with placeholders (e.g. `your-token-here`). If you need to keep real values locally, move them to `.env` (which is gitignored) and restore `.env.example` to placeholder form.

### 2.5 Accidental regression identified and fixed

During watchlist expansion (adding SOXX additions, bank lists, rare earth symbols), the working-tree `config.py` was edited from a pre-fix version. This silently removed the `AUTHORIZED_CHAT_IDS` block added in commit `7ae2b0c`, causing 20 test errors. The block has been restored in commit `12be44a`. This event demonstrates the primary risk this design document addresses: **hand-editing config.py while it is also the source of truth for runtime behavior is fragile.**

---

## 3. Current Watchlist Implementation

### 3.1 Every relevant file, function, and table

| Location | Role |
|---|---|
| `config.py` — `WATCHLIST` dict | Default seed: 13 categories, 404 entries, 399 unique symbols |
| `config.py` — `CATEGORIES` list | Category names used by `/add` to validate input |
| `db/stocksage.db` — `watchlist` table | Runtime state: currently 80 symbols (original seed) |
| `db/database.py` — `init_db()` | Creates tables; calls `populate_from_config()` if watchlist arg is given |
| `db/database.py` — `populate_from_config()` | Iterates config dict, calls `add_to_watchlist()` for each symbol |
| `db/database.py` — `add_to_watchlist()` | `INSERT OR IGNORE` — adds if symbol not already present |
| `db/database.py` — `remove_from_watchlist()` | `DELETE FROM watchlist WHERE symbol = ?` |
| `db/database.py` — `get_watchlist()` | Returns all rows as `{category: [symbols]}` dict |
| `bot/telegram_bot.py` — `run_bot()` | Calls `init_db(WATCHLIST)` on every startup |
| `bot/telegram_bot.py` — `cmd_add()` | Calls `add_to_watchlist(symbol, category)` — SQLite only |
| `bot/telegram_bot.py` — `cmd_remove()` | Calls `remove_from_watchlist(symbol)` — SQLite only |
| `bot/telegram_bot.py` — `cmd_watchlist()` | Calls `get_watchlist()` — reads from SQLite |
| `agent/core.py` — `check_alerts()` | Calls `get_watchlist()` — reads from SQLite |
| `agent/core.py` — `run_morning_scan()` | Calls `get_watchlist()`, skips categories in `_SCAN_SKIP_CATEGORIES` |
| `agent/core.py` — `_SCAN_SKIP_CATEGORIES` | Currently `{"מדדים", "ETFs"}` — only these two are skipped |

### 3.2 Startup behavior

Every time the application starts, `run_bot()` calls `init_db(WATCHLIST)`.  
`init_db()` calls `populate_from_config(watchlist)`.  
`populate_from_config()` calls `add_to_watchlist()` for every symbol in config.py.  
`add_to_watchlist()` uses `INSERT OR IGNORE`.

**Consequence:**
- Symbols in config.py that are not yet in the DB are **added on next restart**.
- Symbols already in the DB are not touched (their category cannot be updated this way).
- Symbols deleted from the DB via `/remove` are **re-added on next restart** if they still appear in config.py.
- Symbols manually removed from config.py are **not deleted from the DB** (INSERT OR IGNORE does not remove rows).

### 3.3 Persistence matrix

| Change | Config.py | SQLite | Survives restart | Survives git pull |
|---|---|---|---|---|
| Add via `/add` | No | Yes | ✅ Yes | ✅ Yes (DB not tracked) |
| Remove via `/remove` | No | Yes | ✅ Yes | ❌ **Re-added if still in config.py** |
| Add to config.py manually | Yes | No (until restart) | Added on next restart | ✅ |
| Remove from config.py manually | Yes | No effect | Not removed from DB | N/A |
| `git pull` replacing config.py | Overwritten | Not affected | Next startup may add new config symbols | Depends |

### 3.4 Critical bug: removed symbols reappear after restart

If you run `/remove NVDA`, NVDA is deleted from the database. But NVDA remains in config.py. On the next restart, `populate_from_config()` runs and `INSERT OR IGNORE` adds NVDA back to the database. The removal does not persist.

### 3.5 Multi-category support

The `watchlist` table enforces `UNIQUE(symbol)`. A symbol can only exist in one category in the database. When config.py lists the same symbol in two categories, `INSERT OR IGNORE` stores the first one and silently discards the second. The five current duplicates (QQQ, DDOG, NEE, NUKZ, UUUU) are all affected.

### 3.6 What the agent and bot scan

Both `check_alerts()` and `run_morning_scan()` call `get_watchlist()`, which reads from SQLite. They do not read config.py directly at runtime. The morning scan skips categories named exactly `"מדדים"` and `"ETFs"`, and skips symbols starting with `^`. Crypto (BTC-USD, ETH-USD) is **not** skipped.

---

## 4. Current Watchlist Inventory

The database currently holds 80 symbols (original seed). Config.py holds 399 unique symbols. The 319 symbols not yet in the database will be added on next restart.

### 4.1 Symbol counts by category

| Category (ASCII) | Config.py count | In DB now |
|---|---|---|
| ????? (Indices / מדדים) | 6 | 5 (QQQ missing — seeded as ETF) |
| ?????? (Crypto / קריפטו) | 2 | 2 |
| ETFs | 11 | 10 (QQQ already counted above) |
| AI & Semiconductors | 31 | 8 |
| ??? ?? (Mega Tech / מגה טק) | 7 | 7 |
| ??? ?????? (Cloud/Software / ענן ותוכנה) | 89 | 5 |
| ????? (Cyber / סייבר) | 7 | 6 |
| ?????? Data Center | 10 | 10 |
| ??? (Space/Defense / חלל) | 31 | 8 |
| ?????? (Energy / אנרגיה) | 4 | 4 |
| ????? (Nuclear / גרעין) | 33 | 10 |
| ?????? ????? (Green Energy / אנרגיה ירוקה) | 4 | 2 |
| ??????? (Financials / פיננסים) | 149 | 3 |
| ????? ??? (Materials / חומרי גלם) | 20 | 0 |

### 4.2 Duplicate symbols

| Symbol | Appears in categories | DB stores |
|---|---|---|
| QQQ | מדדים (indices), ETFs | ETFs (seeded first) |
| DDOG | ענן ותוכנה (cloud), סייבר (cyber) | ענן ותוכנה (seeded first) |
| NEE | גרעין (nuclear), אנרגיה ירוקה (green energy) | גרעין (seeded first) |
| NUKZ | ETFs, גרעין (nuclear) | ETFs (seeded first) |
| UUUU | גרעין (nuclear), חומרי גלם (materials) | גרעין (seeded first) |

Note: QQQ was removed from the indices category in commit `07babc3` but reappeared when the user manually expanded config.py. It is currently listed in both categories again. This is confirmed as a duplicate and does not cause a crash, but the second entry is silently ignored by the database.

### 4.3 Symbol classification

The table below classifies all symbols by security type. Classifications marked ⚠️ require API-level data confirmation before acting on them.

**Market Indices (not tradeable, context-only)**

| Symbol | Name | Notes |
|---|---|---|
| ^GSPC | S&P 500 Index | yfinance works; full_analysis possible but not actionable |
| ^IXIC | Nasdaq Composite | Same |
| ^DJI | Dow Jones Industrial | Same |
| ^RUT | Russell 2000 | Same |
| ^VIX | CBOE Volatility Index | Prices only — history format differs; no OHLCV bars for scoring |

**Broad Market ETFs (context-only, not stock candidates)**

| Symbol | Name | Notes |
|---|---|---|
| SPY | SPDR S&P 500 ETF | High liquidity, full history — good for market regime detection |
| VOO | Vanguard S&P 500 ETF | Same |
| QQQ | Invesco Nasdaq 100 ETF | Listed twice (indices + ETFs); treat as ETF context |

**Sector and Thematic ETFs**

| Symbol | Name | Category tag | Notes |
|---|---|---|---|
| VGT | Vanguard IT ETF | ETFs | High liquidity |
| XLK | SPDR Tech Sector | ETFs | High liquidity |
| SOXX | iShares Semiconductor ETF | ETFs | High liquidity |
| CIBR | First Trust Cybersecurity | ETFs | Moderate liquidity |
| ARKK | ARK Innovation ETF | ETFs | Moderate liquidity; high volatility |
| SCHG | Schwab Large-Cap Growth | ETFs | High liquidity |
| UFO | Procure Space ETF | ETFs | ⚠️ Very low AUM, low liquidity |
| NUKZ | Range Nuclear ETF | ETFs + Nuclear | Duplicate in two categories |
| URA | Global X Uranium ETF | Nuclear | Moderate liquidity |
| URNM | Sprott Uranium Miners ETF | Nuclear | Moderate liquidity |
| NLR | VanEck Uranium+Nuclear ETF | Nuclear | Lower liquidity |
| REMX | VanEck Rare Earth ETF | Materials | Lower liquidity |
| COPX | Global X Copper Miners ETF | Materials | Moderate liquidity |
| CPER | US Copper Index Fund | Materials | Lower liquidity |
| SLX | VanEck Steel ETF | Nuclear | ⚠️ Misplaced in nuclear category |

**Crypto (different market structure — 24/7, no OHLCV bars via yfinance daily)**

| Symbol | Notes |
|---|---|
| BTC-USD | No US market hours constraint; full_analysis should work but RSI/MACD context differs |
| ETH-USD | Same |

**Mega-cap and Large-cap Tech (high-quality candidates)**

| Symbol | Name | Notes |
|---|---|---|
| NVDA | NVIDIA | Core AI candidate |
| AMD | Advanced Micro Devices | Core candidate |
| AVGO | Broadcom | Core candidate |
| ARM | Arm Holdings | IPO 2023; full history available |
| QCOM | Qualcomm | Core candidate |
| MRVL | Marvell Technology | Core candidate |
| ANET | Arista Networks | Core candidate |
| INTC | Intel | Underperformer; still analyzable |
| ASML | ASML Holding | Trades on Nasdaq; full history available |
| TSM | Taiwan Semiconductor | Trades on NYSE; full history available |
| MU | Micron Technology | Core candidate |
| MPWR | Monolithic Power | Core candidate |
| KLAC | KLA Corporation | Core candidate |
| LRCX | Lam Research | Core candidate |
| AMAT | Applied Materials | Core candidate |
| TXN | Texas Instruments | Core candidate |
| ADI | Analog Devices | Core candidate |
| GOOGL | Alphabet | Mega-cap |
| AMZN | Amazon | Mega-cap |
| MSFT | Microsoft | Mega-cap |
| META | Meta Platforms | Mega-cap |
| AAPL | Apple | Mega-cap |
| TSLA | Tesla | High volatility; valid candidate |

**Potentially problematic symbols (⚠️ verify before including)**

| Symbol | Concern | Recommended action |
|---|---|---|
| CBRS | Cerebras Systems — recent IPO, status unclear | Verify exchange listing and data availability |
| CEZ | CEZ Group (Czech utility) — trades Prague Stock Exchange | Likely not available via yfinance US; verify |
| KAP | Kazatomprom — listed London Stock Exchange | Likely available only as KAP.L, not KAP; verify |
| ELE | Endesa — Spanish utility | May trade as ELEZF (OTC); verify symbol |
| BOE | Labeled "Brookfield Renewable Partners" but BEP is already in the list | BOE is a different entity; verify intended ticker |
| YCA | Yellow Cake — UK-listed uranium company | Trades as YCA.L on LSE; likely not available as YCA on US data feeds |
| DYL | Deep Yellow — Australian uranium company | Trades as DYL.AX; may have limited yfinance US data |
| PDN | Paladin Energy — Australian | Trades on TSX and ASX; verify yfinance availability |
| FCU | Fission Uranium — Canadian | TSX-listed; verify yfinance availability |
| USAR | USA Rare Earth — OTCQX | Very low liquidity, limited data |
| CRML | Critical Metals Corp — OTC | Very low liquidity |
| AREC | American Rare Earths — OTC | Very low liquidity |
| NB | NioCorp Developments — micro-cap | Very low liquidity |
| ONDS | Ondas Holdings — micro-cap drone/defense | Very low liquidity; high risk |
| SOUN | SoundHound AI — micro-cap | High volatility, limited history |
| CCC | Listed under Cloud/Software as "Claros Mortgage" | ⚠️ This is a commercial mortgage REIT, not software |
| LIF | Life360 — primarily ASX-listed (360) | Check if US OTC data available via yfinance |
| AUR | Aurora Innovation — autonomous vehicle SPAC | Very volatile, small float |
| UFO | Procure Space ETF | Very low AUM (~$26M); wide spreads likely |
| 149 bank stocks | Small/regional US banks | Many have very low daily dollar volume; most are unsuitable for swing trading |

---

## 5. Current Persistence and Git Risks

### Risk 1 — Removed symbols re-appear after restart (critical)

**Mechanism:** `/remove SYMBOL` deletes from DB. But `run_bot()` always calls `init_db(WATCHLIST)` on startup, which calls `INSERT OR IGNORE` for every config.py symbol. If SYMBOL is still in config.py, it is re-inserted.

**Impact:** Any symbol you deliberately remove from monitoring via Telegram reappears the next time the application starts. This is not a data loss, but it defeats the purpose of the `/remove` command.

**Fix required:** Either (a) track intentional removals in a `removed_symbols` table and exclude them from the reseed logic, or (b) change `populate_from_config()` to only seed if the DB is empty (i.e. run once, not every startup).

### Risk 2 — Config.py edits during review accidentally dropped committed fixes

This occurred today with `AUTHORIZED_CHAT_IDS`. If config.py is also the runtime watchlist source, any hand-edit risks overwriting code-level changes.

**Fix required:** Move the watchlist data out of config.py and into a dedicated, easily-editable file (e.g. `watchlist_seed.yaml`) that is not entangled with Python module-level code.

### Risk 3 — 319 new symbols will be seeded into the DB on next restart

The DB currently has 80 symbols. Config.py has 399 unique symbols. On the next startup, `populate_from_config()` will attempt to add all 399. The 80 already present are ignored; the 319 new ones will be inserted. **The scanner will then attempt to analyze all 399 symbols.**

This is not a crash, but the morning scan will take 6–12 minutes and the 15-minute alert cycle will not complete before the next one starts.

### Risk 4 — .env.example contains real credentials

`.env.example` is tracked by Git. It currently contains what appear to be real values for TELEGRAM_TOKEN and TELEGRAM_CHAT_ID. These will be committed and pushed if not replaced with placeholders. **Replace with placeholder strings before the next commit.**

### Risk 5 — DB is not version-controlled

The SQLite file lives at `db/stocksage.db` and is (correctly) gitignored. But there is no migration system. If the schema needs to change (as proposed in Section 13), there is no automated way to upgrade existing databases.

---

## 6. Proposed Watchlist Architecture

### 6.1 Data-flow diagram

```
config.py / watchlist_seed.yaml
   │
   │ (startup, seed-once only)
   ▼
┌─────────────────────────────────────────────────────┐
│              SQLite — watchlist table               │
│                                                     │
│  symbol | type | category | list_tier | state |    │
│  source | enabled | exclusion_reason | ...          │
│                                                     │
│  MASTER_LIST  ←───── all retained symbols          │
│  ACTIVE_LIST  ←───── current scan candidates       │
│  MONITOR_LIST ←───── watch but scan less often     │
│  CONTEXT_LIST ←───── ETFs/indices for regime info  │
│  EXCLUDED     ←───── temporary or permanent skip   │
└─────────────────────────────────────────────────────┘
         │                    │
         │                    │
   ┌─────▼──────┐       ┌─────▼───────────────┐
   │  /add       │       │ Eligibility engine   │
   │  /remove    │       │ (daily evaluation)   │
   │  /promote   │       │ → promotes to ACTIVE │
   │  /demote    │       │ → demotes to MONITOR │
   └─────────────┘       └─────────────────────┘
                                  │
              ┌───────────────────┴──────────────────┐
              │                                       │
     ┌────────▼────────┐               ┌─────────────▼──────┐
     │ Active scan      │               │ Monitor scan        │
     │ every 15 min     │               │ every 60 min        │
     │ during market    │               │ during market       │
     └────────┬─────────┘               └────────────────────┘
              │
     ┌────────▼─────────┐
     │ Alert lifecycle   │
     │ engine            │
     │ state-transition  │
     │ based alerts only │
     └───────────────────┘
```

### 6.2 Five list tiers

| Tier | Purpose | Initial scan target | Who manages it |
|---|---|---|---|
| MASTER_LIST | Every symbol you want to retain forever | Never auto-deleted | You |
| ACTIVE_LIST | Current swing-trade candidates | 20–35 symbols | Eligibility engine |
| MONITOR_LIST | Interesting but no active setup | All non-active | Eligibility engine |
| CONTEXT_LIST | ETFs, indices, VIX | Separate cadence | Config only |
| EXCLUDED | Temporarily or permanently skip | Never scanned | Engine + manual |

---

## 7. Active Watchlist Eligibility Rules

The following rules determine whether a symbol belongs in the ACTIVE_LIST.  
All thresholds are recommended starting points. You must approve each one before implementation.

| # | Rule | Formula | Initial threshold | Missing-data behavior | Configurable | Reason |
|---|---|---|---|---|---|---|
| E1 | Valid ticker | yfinance returns non-empty data | Required | Exclude temporarily | Yes | Cannot analyze without data |
| E2 | Sufficient history | Daily bars available | ≥ 200 bars | Exclude temporarily | Yes | EMA200 requires 200 bars minimum |
| E3 | Data freshness | Last bar date vs today | ≤ 3 trading days old | Exclude temporarily | Yes | Stale data produces wrong signals |
| E4 | Minimum price | Current price | ≥ $3.00 | Exclude | Yes | Below this: wide spreads, manipulation risk |
| E5 | Minimum avg volume | 3-month average daily volume | ≥ 300,000 shares/day | Exclude | Yes | Below this: execution is difficult; signals are noisy |
| E6 | Minimum dollar volume | Price × avg daily volume | ≥ $5,000,000/day | Exclude | Yes | Ensures realistic entry and exit at scale |
| E7 | Price above EMA150 | close > EMA(150) | Required | Exclude | No | Core strategy gate; already in scoring |
| E8 | EMA150 trend | EMA150 > EMA200 | Required for promotion | Exclude | Yes | Structural uptrend requirement |
| E9 | RSI in range | RSI(14) | 35–75 | Watchonly | Yes | Veto condition already in scoring; use same bounds |
| E10 | Not overbought extended | Price vs 52-week high | < 115% of 52-week high | Warn, do not exclude | Yes | Reduces chasing already-extended moves |
| E11 | ATR% reasonable | ATR(14) / price | 1.5%–8.0% | Warn | Yes | Too low = no setup; too high = unsuitable volatility |
| E12 | No upcoming binary event | Earnings within N days | Warn if ≤ 5 days | Warn only | Yes | Binary events invalidate technical setups |
| E13 | Security type | Not a market index | `^` prefix excluded | Always exclude from active | No | Indices are not tradeable |
| E14 | Security type | ETF context list | In CONTEXT_LIST | Move to CONTEXT_LIST | No | ETFs serve a different role |

**Rule E5 and E6 detail:**  
The 149 bank stocks in the financial category include many community and regional banks with average daily volume well below 100,000 shares and daily dollar volume below $2M. These are fundamentally unsuitable for swing trading alerts: a 1,000-share position could move the price significantly, and the bid-ask spread may be 0.1–0.5%. Recommended threshold of 300,000 shares/day with $5M/day dollar volume would exclude the majority of the bank list automatically, without manual deletion.

**Rule E12 detail:**  
Earnings data is not currently fetched. Adding earnings-date awareness requires a data source (Yahoo Finance calendar via yfinance `.calendar`, or a dedicated API). This rule cannot be implemented without a data source change.

---

## 8. Watchlist Relevance Score

A separate score (0–100) determines ACTIVE vs MONITOR tier placement.  
This is distinct from the opportunity score (which rates entry quality) and the alert score (which gates alert firing).

### 8.1 Components

| Component | Weight | Measures |
|---|---|---|
| Data quality | 15 | History length, freshness, no missing fields, no NaN in critical columns |
| Liquidity | 20 | Avg daily volume vs threshold, dollar volume, price floor |
| Trend position | 25 | Price vs EMA150, EMA150 vs EMA200, distance from each |
| Momentum improvement | 20 | RSI direction over last 5 evaluations, MACD histogram trend |
| Setup proximity | 15 | Distance from nearest support/resistance, Bollinger Band position |
| Event risk penalty | -5 | Earnings within 5 days |
| Sector concentration | -5 penalty when active has >30% in one sector | Prevents sector pile-on |

### 8.2 Score bands

| Score | Meaning |
|---|---|
| 75–100 | Strong active candidate — promote to ACTIVE if not already |
| 55–74 | Monitor with attention — promote if sustained ≥ 2 evaluations |
| 35–54 | Low relevance — monitor at reduced frequency |
| 0–34 | Inactive — move to MASTER_LIST only, do not scan regularly |

### 8.3 Normalization

Each component is normalized to 0–1 before applying weight. Final score = sum of (component × weight), rounded to integer, capped at 100. Missing data for any component assigns 0 to that component (does not propagate NaN).

---

## 9. Promotion, Demotion, and Hysteresis

### 9.1 Promotion to ACTIVE

- Relevance score ≥ 70 **on two consecutive daily evaluations**
- All mandatory eligibility rules (E1–E4, E7, E13, E14) pass
- No active exclusion
- ACTIVE list has room (see maximum size, Section 19)

### 9.2 Demotion to MONITOR

- Relevance score < 45 **on three consecutive daily evaluations**
- OR mandatory eligibility rule fails (immediate demotion)
- OR permanent exclusion triggered

**Rationale for asymmetric thresholds:** Promotion requires 70, demotion triggers at 45. The 25-point gap provides hysteresis — a symbol oscillating between 50 and 65 stays in ACTIVE without thrashing between lists.

### 9.3 Minimum dwell time

- A symbol promoted to ACTIVE stays for at least **5 trading days** before demotion is evaluated, unless a mandatory eligibility rule fails.
- This prevents a single off-day (data gap, low-volume session) from causing premature demotion.

### 9.4 Maximum ACTIVE list size

When ACTIVE is full (at maximum size) and a new promotion candidate scores higher than the lowest-scoring current ACTIVE symbol, the lowest-scoring symbol is demoted to make room, subject to the minimum dwell time above.

### 9.5 Tie-breaking

When two symbols have equal relevance scores, the one with higher average daily dollar volume takes priority (better liquidity).

---

## 10. Scan Schedule

### 10.1 Data freshness note

yfinance does **not** provide true real-time data. It provides:
- `fast_info` / `info`: delayed approximately 15 minutes for most US exchanges
- `history(period="1d", interval="1m")`: intraday bars, delayed ~15 minutes
- `history(period="1y", interval="1d")`: end-of-day daily bars (previous close confirmed)

**The StockSage alerts are based on delayed data, not real-time data.** This should not be described as real-time to users. The alert messages currently do not include a data-delay disclosure. This is a design recommendation for the alert lifecycle (Section 11).

### 10.2 Proposed schedule

| List | Market hours | Outside market hours | Analysis depth | Telegram alerts |
|---|---|---|---|---|
| CONTEXT_LIST | Every 15 min | Once per hour | Price + VIX level only | Regime warnings only |
| ACTIVE_LIST | Every 15 min | Once per day (pre-market summary) | Full 9-gate analysis | State-transition alerts |
| MONITOR_LIST | Every 60 min | Not scanned | Score check only | Promotion alerts only |
| MASTER_LIST | Daily evaluation (after market close) | Yes | Eligibility check | None (internal only) |

### 10.3 yfinance throughput estimate

**Current state (399 symbols, full scan):**

| Operation | Symbols | Time estimate |
|---|---|---|
| `get_multiple_prices()` (batch) | 399 | ~3–8 seconds |
| `get_historical()` per symbol | Up to 399 | ~0.5–2s each |
| Full morning scan | 376 eligible | **3–12 minutes** |
| 15-minute alert cycle | 399 (all) | May exceed 15 minutes |

**Proposed state (20–35 active symbols):**

| Operation | Symbols | Time estimate |
|---|---|---|
| `get_multiple_prices()` (batch) | 35 active + 20 context | ~2–4 seconds |
| `get_historical()` per symbol | 35 (active only) | ~18–70 seconds |
| Full active scan | 35 | **Under 2 minutes** |
| Context scan | 20 | **Under 30 seconds** |
| Monitor scan (60 min) | ~100 | ~50–200 seconds |

**yfinance throttling risk:**  
Rapid repeated calls to yfinance (especially `get_historical()` in a tight loop) can trigger HTTP 429 rate-limiting. With 399 symbols fetched every 15 minutes, this risk is high. With 35 active symbols, the risk is low. Consider adding a 0.5-second sleep between sequential `get_historical()` calls and caching results that are less than 5 minutes old.

### 10.4 Historical data caching

`get_historical(symbol, period="1y")` downloads approximately 252 rows of daily OHLCV per symbol. This data changes only once per trading day. Currently it is re-downloaded on every scan cycle. A simple in-memory or file-based cache (keyed by `symbol + date`) would eliminate redundant downloads and dramatically reduce scan time.

---

## 11. Telegram Alert Lifecycle

### 11.1 Alert state machine

Each symbol should have a persistent state:

```
NO_SETUP → WATCH → SETUP_FORMING → SETUP_CONFIRMED → ALERT_FIRED
              ↑______________↓           ↑___________↓
              (score rises)  (score drops)
```

States stored in the `watchlist` table (or a new `symbol_state` table).

### 11.2 Alert types and triggers

| Alert type | Trigger | Cooldown |
|---|---|---|
| `NEW_SETUP` | Score crosses from < 50 to ≥ 60 | 4 hours |
| `SETUP_IMPROVED` | Score increases ≥ 10 points while in setup range | 2 hours |
| `SETUP_CONFIRMED` | All 9 gates pass for the first time this session | 2 hours |
| `SETUP_INVALIDATED` | Score drops below 40 after being ≥ 60 | 1 hour |
| `RISK_WARNING` | Earnings within 3 days while in ACTIVE | Once per symbol per earnings date |
| `PROMOTED_TO_ACTIVE` | Symbol promoted from MONITOR | Once per promotion |
| `DEMOTED_TO_MONITOR` | Symbol demoted from ACTIVE | Once per demotion |
| `DATA_UNAVAILABLE` | yfinance returns None/error for 3 consecutive cycles | Once per incident |
| `SYSTEM_WARNING` | Scan took > 10 minutes; VIX spike > 25 | Configurable |
| `DAILY_SUMMARY` | Once per day, after market close | Once per day |

### 11.3 Alert fingerprint

Before sending any alert, compute a fingerprint:

```
fingerprint = hash(symbol + alert_type + score_bucket + date)
```

Where `score_bucket = (score // 10) * 10` (e.g. 73 → 70).

If the fingerprint matches the last-sent fingerprint for this symbol, suppress the alert. This prevents identical alerts from being re-sent if the system restarts mid-session.

### 11.4 Per-symbol cooldown

Current cooldown: 2 hours for all alert types on the same symbol.  
Proposed: per-alert-type cooldown stored separately. A `RISK_WARNING` should not be blocked by a prior `SETUP_CONFIRMED` cooldown.

### 11.5 Minimum change requirements

An alert should only fire if:
- Score changed by ≥ 10 points since the last alert **OR** the alert type is different from the last alert
- Price has not reversed more than 1.5× ATR since the initial trigger

### 11.6 Global Telegram rate limit

The Telegram Bot API allows a maximum of 30 messages per second and 20 messages per minute to the same chat. With 35 active symbols potentially all crossing their threshold in one scan, this could hit rate limits. The alert sender should include a minimum 2-second delay between consecutive messages and implement exponential backoff on `RetryAfter` exceptions.

---

## 12. Telegram Message Examples

All values below are **entirely fictional** and used only to demonstrate format.

### 12.1 SETUP_CONFIRMED (current alert style, enhanced)

```
🚨 Alert — EXMPL | BUY [72/100]
━━━━━━━━━━━━━━━━━━━
💰 Price: $147.35 (+1.8% today)
📊 Score: 72/100 | RSI: 58.4 ✅
📈 Above EMA150 ✅ | Volume spike ✅
✅ EMA trend  ✅ MACD cross  ✅ VWAP
━━━━━━━━━━━━━━━━━━━
🛑 Stop: $141.20 | 🎯 TP: $156.90
⚖️ Risk/Reward: 1:1.6
⏰ Data as of 16:35 ET (15-min delayed)
📌 Research only — not financial advice
━━━━━━━━━━━━━━━━━━━
💡 /analyze EXMPL for full breakdown
```

### 12.2 SETUP_INVALIDATED

```
⚪ Setup cleared — EXMPL
Score dropped: 72 → 38
RSI moved to 78.1 (overbought zone)
Last alert was 2h 15m ago.
```

### 12.3 PROMOTED_TO_ACTIVE

```
📈 EXMPL moved to Active Watchlist
Relevance score: 68 (was 41)
Sector: Technology — Semiconductors
Will be scanned every 15 minutes.
```

### 12.4 DAILY_SUMMARY

```
📋 Daily Summary — 2026-06-17
━━━━━━━━━━━━━━━━━━━
Active: 28 symbols scanned
Alerts sent: 3 (EXMPL, EXMP2, EXMP3)
Setups forming: 5 symbols
Promotions: 2 | Demotions: 1
━━━━━━━━━━━━━━━━━━━
Market closed. Next scan: 09:30 ET tomorrow.
```

---

## 13. Database and Persistence Design

### 13.1 Proposed schema changes

The following changes are proposed. **Do not execute this migration until you approve it.**

**New columns on `watchlist` table:**

```sql
ALTER TABLE watchlist ADD COLUMN security_type  TEXT DEFAULT 'stock';
ALTER TABLE watchlist ADD COLUMN list_tier      TEXT DEFAULT 'MONITOR';
ALTER TABLE watchlist ADD COLUMN state          TEXT DEFAULT 'NO_SETUP';
ALTER TABLE watchlist ADD COLUMN source         TEXT DEFAULT 'config';
ALTER TABLE watchlist ADD COLUMN enabled        INTEGER DEFAULT 1;
ALTER TABLE watchlist ADD COLUMN eligibility_score INTEGER DEFAULT NULL;
ALTER TABLE watchlist ADD COLUMN opportunity_score  INTEGER DEFAULT NULL;
ALTER TABLE watchlist ADD COLUMN last_evaluated TIMESTAMP DEFAULT NULL;
ALTER TABLE watchlist ADD COLUMN last_promoted  TIMESTAMP DEFAULT NULL;
ALTER TABLE watchlist ADD COLUMN last_demoted   TIMESTAMP DEFAULT NULL;
ALTER TABLE watchlist ADD COLUMN exclusion_reason TEXT DEFAULT NULL;
ALTER TABLE watchlist ADD COLUMN reeval_date    DATE DEFAULT NULL;
ALTER TABLE watchlist ADD COLUMN consec_promote_count INTEGER DEFAULT 0;
ALTER TABLE watchlist ADD COLUMN consec_demote_count  INTEGER DEFAULT 0;
ALTER TABLE watchlist ADD COLUMN dwell_days     INTEGER DEFAULT 0;
```

**New `removed_symbols` table (prevents reseed of intentionally removed symbols):**

```sql
CREATE TABLE IF NOT EXISTS removed_symbols (
    symbol      TEXT PRIMARY KEY,
    removed_at  TIMESTAMP NOT NULL,
    removed_by  TEXT DEFAULT 'telegram',
    reason      TEXT DEFAULT ''
);
```

**New `alert_state` table (replaces per-symbol fields scattered in `alerts`):**

```sql
CREATE TABLE IF NOT EXISTS alert_state (
    symbol          TEXT PRIMARY KEY,
    last_score      INTEGER DEFAULT NULL,
    last_state      TEXT DEFAULT 'NO_SETUP',
    last_alert_type TEXT DEFAULT NULL,
    last_alert_at   TIMESTAMP DEFAULT NULL,
    last_fingerprint TEXT DEFAULT NULL,
    consec_no_data  INTEGER DEFAULT 0
);
```

### 13.2 Reseed protection logic

Change `populate_from_config()` to skip symbols present in `removed_symbols`:

```python
def populate_from_config(watchlist: dict) -> None:
    removed = {r[0] for r in conn.execute("SELECT symbol FROM removed_symbols").fetchall()}
    for category, symbols in watchlist.items():
        for symbol in symbols:
            if symbol.upper() not in removed:
                add_to_watchlist(symbol, category)
```

Change `remove_from_watchlist()` to insert into `removed_symbols`:

```python
def remove_from_watchlist(symbol: str) -> None:
    conn.execute("DELETE FROM watchlist WHERE symbol = ?", (symbol.upper(),))
    conn.execute(
        "INSERT OR IGNORE INTO removed_symbols (symbol, removed_at) VALUES (?, ?)",
        (symbol.upper(), datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))
    )
```

### 13.3 Migration strategy

For each proposed schema change, a migration function runs once at startup (before `populate_from_config()`), checking if the column exists and adding it if not. This is safe for both fresh installs and existing databases. Example pattern:

```python
def migrate_db() -> None:
    with _connect() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(watchlist)").fetchall()}
        if "list_tier" not in cols:
            conn.execute("ALTER TABLE watchlist ADD COLUMN list_tier TEXT DEFAULT 'MONITOR'")
```

---

## 14. Git-Safe Runtime Design

### 14.1 The problem

Config.py serves two purposes: Python module configuration (alert thresholds, intervals, credentials) and watchlist seed data (13 categories, 399 symbols). These are entangled. Editing the watchlist risks modifying module code and vice versa.

### 14.2 Proposed split

**Keep in config.py** (alert thresholds, timing, credentials):
- All scalar settings: `ALERT_MIN_SCORE`, `ALERT_COOLDOWN_HOURS`, `CHECK_INTERVAL_MINUTES`, etc.
- Credential references: `TELEGRAM_TOKEN`, `AUTHORIZED_CHAT_IDS`, etc.

**Move to `watchlist_seed.yaml`** (watchlist seed data only):
```yaml
# watchlist_seed.yaml — default symbols loaded into SQLite if not already present.
# Edit this freely. Runtime changes via /add and /remove are stored in SQLite.
מדדים:
  - "^GSPC"
  - "^IXIC"
ETFs:
  - "SPY"
  - "QQQ"
# ... etc.
```

**Startup behavior:** `init_db()` reads `watchlist_seed.yaml` once per startup, inserts missing symbols via `INSERT OR IGNORE`, respects `removed_symbols` table.

**Git pull safety:** A `git pull` can update `watchlist_seed.yaml` with new symbols. Those symbols are added to the DB on next restart. Symbols you removed via `/remove` are protected by `removed_symbols`. Your personal changes (added via `/add`) survive because they are in the DB, not in the YAML.

### 14.3 Categories list

`CATEGORIES` in config.py is currently `list(WATCHLIST.keys())`. After the split, `CATEGORIES` would be read from `watchlist_seed.yaml` keys at import time, so `/add` can still validate category names.

---

## 15. API and Performance Estimate

### 15.1 Current state (399 symbols)

| Metric | Estimate |
|---|---|
| Full morning scan | 376 API calls to `get_historical()` + 1 batch price call |
| Estimated scan duration | 6–12 minutes |
| 15-minute alert cycle (all symbols) | 1 batch + up to 376 individual calls |
| API calls per hour (market hours) | ~1,600–4,000 |
| API calls per trading day | ~12,000–30,000 |
| yfinance throttle risk | **High** — consecutive calls without delay will eventually trigger 429 |
| Scan completing before next cycle | **Not guaranteed** at current symbol count |

### 15.2 Proposed state (35 active + 100 monitor + 25 context)

| Metric | Estimate |
|---|---|
| Active scan (every 15 min) | 1 batch + 35 individual calls |
| Estimated active scan duration | 20–70 seconds |
| Monitor scan (every 60 min) | 1 batch + 100 individual calls |
| Context scan (every 15 min) | 1 batch only (price data only) |
| API calls per hour (market hours) | ~200–400 |
| API calls per trading day | ~1,600–3,200 |
| yfinance throttle risk | **Low** with 0.5-second inter-call delay |
| Scan completing before next cycle | ✅ Comfortably within 15 minutes |

### 15.3 Caching recommendation

Daily OHLCV data (the 252-row `get_historical()` result) changes only once per day. Cache it keyed by `(symbol, date)`. On the next scan cycle within the same day, serve from cache. This reduces `get_historical()` calls from 35 per active-scan cycle to 0 on the second through fourth cycles of the day (the data does not change during market hours).

---

## 16. Test Plan

The following tests are needed for the new features, in addition to existing tests.

| Test group | What to test | Mock/real |
|---|---|---|
| Reseed protection | Removed symbol not re-added after populate_from_config() | In-memory SQLite |
| Reseed protection | Symbol added via /add survives multiple seeding runs | In-memory SQLite |
| Multi-tier DB | list_tier field updated on promotion/demotion | In-memory SQLite |
| Hysteresis | Symbol at score 60 stays ACTIVE; only demotes at score < 45 for 3 cycles | Unit test |
| Hysteresis | Symbol needs score ≥ 70 for 2 cycles to promote | Unit test |
| Alert fingerprint | Duplicate fingerprint suppresses alert | Unit test |
| Alert lifecycle | Score drop from 72 to 38 triggers SETUP_INVALIDATED | Unit test |
| Scan skip | ETF category skipped from alert scan | Unit test with mock get_watchlist() |
| Symbol eligibility | Symbol below E4 (price < $3) is excluded | Unit test with mock price |
| Symbol eligibility | Symbol below E5 (volume < 300k) is excluded | Unit test with mock volume |
| Context scan | ^VIX fetched and parsed without error | Mock yfinance |
| Rate limiting | Telegram RetryAfter handled with backoff | Mock bot.send_message() |
| Schema migration | migrate_db() adds new columns without data loss | In-memory SQLite with pre-existing schema |
| YAML seed | watchlist_seed.yaml parsed correctly | File-based test with temp file |

---

## 17. Proposed File Changes

| File | Change type | Description |
|---|---|---|
| `config.py` | Modified | Remove `WATCHLIST` dict; load categories from watchlist_seed.yaml |
| `watchlist_seed.yaml` | Created | All 399 current symbols in YAML format |
| `db/database.py` | Modified | Add `removed_symbols` table; update `remove_from_watchlist()`; add `populate_from_config()` protection; add `migrate_db()`; add list_tier and state management functions |
| `agent/core.py` | Modified | Query only `ACTIVE_LIST` symbols in 15-minute scan; add `MONITOR_LIST` 60-minute scan; add alert state machine; add alert fingerprinting |
| `agent/eligibility.py` | Created | New module: daily eligibility evaluation, promotion/demotion logic, relevance score calculation |
| `bot/telegram_bot.py` | Modified | Add `/promote`, `/demote`, `/tier` commands; update `/watchlist` to show tiers; add data-delay disclosure to messages |
| `analyzers/cache.py` | Created | Simple in-memory or file-based cache for `get_historical()` results |
| `tests/test_reseed_protection.py` | Created | Tests for removed_symbols protection |
| `tests/test_eligibility.py` | Created | Tests for promotion/demotion logic |
| `tests/test_alert_lifecycle.py` | Created | Tests for alert state machine and fingerprinting |
| `tests/test_schema_migration.py` | Created | Tests for migrate_db() |
| `CLAUDE_CHANGES.md` | Updated | Log all changes |
| `.env.example` | Updated | Replace real credentials with placeholders |

**Deprecated (no immediate deletion):**
- The `WATCHLIST` dict in config.py becomes a seed source that is read from YAML instead

**Files NOT changed:**
- `analyzers/technical.py` — scoring unchanged
- `data/fetcher.py` — data layer unchanged
- `analyzers/chart_generator.py` — chart unchanged

---

## 18. Prioritized Implementation Plan

### Immediate (blocking — must be done before anything else)

| Item | Reason | Benefit | Complexity | DB migration |
|---|---|---|---|---|
| I1. Replace `.env.example` placeholders | Real credentials in git-tracked file | Security | Low | No |
| I2. Fix reseed bug | Removed symbols re-appear after restart | Persistence correctness | Low | Yes (new `removed_symbols` table) |
| I3. Add `migrate_db()` pattern | Required for all future DB changes | Safe upgrades | Low | Yes |

### High Priority (needed before the watch list expands further)

| Item | Reason | Benefit | Complexity | DB migration | Alerts may change |
|---|---|---|---|---|---|
| H1. Create `watchlist_seed.yaml` | Decouple watchlist data from Python code | Prevents config.py regressions like today | Medium | No | No |
| H2. Add `list_tier` to watchlist table | Core of multi-level architecture | Selective scanning | Medium | Yes | No |
| H3. Implement active-only 15-min scan | 399 symbols will overwhelm the scanner | Performance | Medium | No | Yes — only active symbols generate alerts |
| H4. Add eligibility rules E1–E6 | 149 banks + many illiquid symbols need filtering | Reduces noise | Medium | No | Yes — excluded symbols stop alerting |
| H5. Add symbol classification to DB | Know which symbols are ETF, index, stock | Routing logic | Medium | Yes | No |

### Medium Priority

| Item | Reason | Benefit | Complexity | DB migration |
|---|---|---|---|---|
| M1. Historical data cache | Prevents repeated downloads per cycle | Performance, throttle avoidance | Medium | No |
| M2. Alert state machine | Prevents repeated same-state alerts | Reduces noise | High | Yes |
| M3. Alert fingerprinting | Prevents duplicate messages on restart | Reliability | Low | Yes |
| M4. Per-type alert cooldown | RISK_WARNING and SETUP_CONFIRMED should have independent cooldowns | Precision | Low | Yes |
| M5. Daily summary message | End-of-day digest | User experience | Low | No |
| M6. Monitor-list 60-min scan | Symbols in MONITOR need periodic check for promotion | Completeness | Medium | No |
| M7. Relevance score calculation | Drives promotion/demotion decisions | Architecture | High | Yes |

### Optional

| Item | Reason | Benefit | Complexity |
|---|---|---|---|
| O1. Earnings-date warning | Avoid setups before binary events | Risk reduction | High (new data source) |
| O2. Sector concentration limit | Avoid 5 semiconductors firing at once | Portfolio context | Medium |
| O3. VIX regime filter | Suppress alerts during high-fear periods | Risk reduction | Medium |
| O4. Telegram `/tier` command | Show which tier each symbol is in | User experience | Low |
| O5. `/promote` and `/demote` commands | Manual override of tier placement | Control | Low |
| O6. YAML-based category templates | Prebuilt category configurations | Flexibility | Low |

---

## 19. Decisions Requiring My Approval

The following choices involve trade-offs that only you can resolve. No implementation will proceed until you confirm each one.

| # | Decision | Options | My recommendation | Reason |
|---|---|---|---|---|
| D1 | Maximum ACTIVE list size | 20 / 25 / 30 / 35 / custom | **30** | Balances coverage vs scan speed; fits comfortably in 15-minute cycle |
| D2 | ACTIVE scan frequency | 5 min / 10 min / 15 min | **15 min** | yfinance data is ~15-min delayed anyway; scanning more often adds no informational value |
| D3 | MONITOR scan frequency | 30 min / 60 min / 2 hours | **60 min** | Low urgency; reduces API load |
| D4 | Minimum price (E4) | $1 / $3 / $5 / $10 | **$3.00** | Avoids penny stocks and most OTC illiquid names |
| D5 | Minimum avg volume (E5) | 100k / 300k / 500k / 1M | **300,000 shares/day** | Excludes most community banks; keeps small-cap growth stocks |
| D6 | Minimum dollar volume (E6) | $1M / $5M / $10M | **$5,000,000/day** | Ensures realistic execution; excludes micro-caps |
| D7 | Promotion threshold | Score ≥ 65 / 70 / 75, sustained 2–3 days | **≥ 70 for 2 consecutive days** | Reduces false promotions |
| D8 | Demotion threshold | Score < 40 / 45 / 50, sustained 2–4 days | **< 45 for 3 consecutive days** | Gives struggling symbols a recovery window |
| D9 | Minimum dwell time | 3 / 5 / 7 / 10 trading days | **5 trading days** | Prevents thrashing on volatile symbols |
| D10 | Earnings behavior | Suppress alerts / warn / ignore | **Warn in message, do not suppress** | Requires earnings data source; until then, warn manually |
| D11 | VIX regime behavior | Suppress if VIX > 25 / 30 / ignore | **Warn if VIX > 25, do not auto-suppress** | VIX suppression can miss real setups; prefer human judgment |
| D12 | ETF alert behavior | Never alert / alert like stocks / context only | **Context only — never send BUY alerts for ETFs** | ETFs behave differently; swing-trade signals may mislead |
| D13 | Crypto alert behavior | Same as stocks / separate rules / exclude | **Exclude from 9-gate alerts; include in /analyze only** | Market hours mismatch makes alert timing unpredictable |
| D14 | Intraday provisional alerts | Allow / disallow | **Disallow** until data-delay disclosure is implemented | Could mislead if flagged as real-time |
| D15 | Daily summary time | Market close (16:00 ET) / Israel evening (23:30 IL) | **23:30 IL (after market close + 30-min buffer)** | Allows confirmed final prices |
| D16 | How to handle the 5 duplicates | Keep both / move to one canonical category / deduplicate | **Move to one canonical category, document decision** | Fixes silent data loss in DB |
| D17 | Bank stock fate | Keep all 149 / filter by volume / move to separate list | **Filter by E5/E6; most will land in EXCLUDED automatically** | Avoids manual deletion; rules do the work |
| D18 | Potentially invalid symbols (CEZ, KAP, YCA, etc.) | Keep and let E1/E3 handle / manually remove | **Keep in MASTER_LIST; E1/E3 will auto-exclude on data failure** | No manual deletion required until verified |
| D19 | watchlist_seed.yaml vs keeping WATCHLIST in config.py | YAML file / keep in config.py / JSON file | **YAML file** | Decouples editable data from Python module code |
| D20 | When to run the eligibility evaluation | Daily at market close / weekly / on-demand | **Daily after market close** | Keeps tiers current without excessive API use |

---

*This document reflects a confirmed investigation of the actual repository state as of 2026-06-17. No production code was changed during Phases 2–11 of this document. All recommendations require approval before implementation.*
