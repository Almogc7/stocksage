# StockSage — Watchlist Decision Package

**Date:** 2026-06-17  
**Branch:** `claude/stocksage-review-20260617-1200`  
**Status:** Awaiting your approval on all 20 decisions before any automation is built.

Priority 0 and Priority 1 are complete. This document is Priority 2.

---

## Completed work (this session)

| Commit | Change |
|---|---|
| `ef00728` | security: replace example credentials with safe placeholders |
| `5f6e699` | fix: preserve runtime watchlist removals across restarts |

**Priority 0 findings:**  
The credentials in `.env.example` were **never committed**. They existed only in the working tree. Git history confirms zero commits contain the real token or chat ID. The file has been restored to safe placeholders.

**What you must do now regardless:** If your Telegram bot token was ever shared, used in another application, or stored outside this machine in plaintext, rotate it via @BotFather. Replacing it in `.env.example` does not invalidate the live token. The numeric TELEGRAM_CHAT_ID is not a secret (it cannot control your bot), but it is personal data.

**Priority 1 findings:**  
The reseed bug is fixed. `remove_from_watchlist()` now soft-deletes (sets `enabled=0`, records `removed_at`) instead of deleting the row. The seed path (`INSERT OR IGNORE`) sees the existing disabled row and leaves it alone. 14 new tests all pass. Total: 94/94 tests pass.

---

## Volume and liquidity analysis (live data, 2026-06-17)

### Raw share volume distribution (392 tradeable symbols, excluding indices and crypto)

| Percentile | Avg daily volume |
|---|---|
| Minimum | 0 |
| 10th percentile | 138,923 shares/day |
| 25th percentile | 374,557 shares/day |
| Median | 1,399,478 shares/day |
| 75th percentile | 5,737,771 shares/day |
| 90th percentile | 14,141,021 shares/day |
| 99th percentile | 64,032,709 shares/day |

| Share volume threshold | Symbols excluded | Percentage |
|---|---|---|
| < 100,000 shares/day | 21 | 5% |
| < 250,000 shares/day | 66 | 17% |
| < 500,000 shares/day | 114 | 29% |
| < 1,000,000 shares/day | 167 | 43% |

### Dollar volume distribution

| Percentile | Avg daily dollar volume |
|---|---|
| 10th percentile | $6.4M/day |
| 25th percentile | $17.4M/day |
| Median | $69.9M/day |
| 75th percentile | $408.7M/day |
| 90th percentile | $2.26B/day |

| Dollar volume threshold | Symbols excluded | Percentage |
|---|---|---|
| < $5M/day | 21 | 5% |
| < $10M/day | 61 | 16% |
| < $20M/day | 109 | 28% |
| < $50M/day | 169 | 43% |

### Bank category breakdown (149 symbols)

| Dollar volume threshold | Banks excluded | Percentage |
|---|---|---|
| < $5M/day | 13 | 9% |
| < $10M/day | 46 | 31% |
| < $20M/day | 85 | 57% |
| < $50M/day | 119 | 80% |

---

## D5 — Minimum average daily share volume

**Recommendation: 250,000 shares/day**

At 250K, 66 symbols (17%) are excluded. These are almost entirely:
- Small community banks (most below 100K daily volume)
- Micro-cap rare earth miners
- Foreign-exchange-primary stocks with thin US ADR volume

At 500K, 114 symbols are excluded (29%), including some legitimate mid-cap names. That is too aggressive for an initial threshold.

At 100K, only 21 symbols are excluded — too permissive; allows many thinly-traded names that produce noisy signals.

**Initial value: 250,000 shares/day, configurable as `ELIGIBILITY_MIN_AVG_VOLUME` in config.py.**

---

## D6 — Minimum average daily dollar volume

**Recommendation: $10M/day**

Dollar volume is more meaningful than raw share volume because it measures economic liquidity independently of share price. At $10M:
- 61 symbols excluded (16%)
- Excludes almost all micro-caps and OTC stocks
- Keeps borderline names like ELE ($5.4M → excluded) and BOE ($1.5M → excluded)
- Keeps legitimate small-cap growth stocks like ONDS ($643M → kept, despite low price/OTC concerns)

At $5M, only 21 symbols are excluded — this matches the 100K volume threshold and is too permissive.

At $20M, 109 symbols excluded — reasonable but aggressive; removes some valid small-caps.

**Initial value: $10M/day, configurable as `ELIGIBILITY_MIN_DOLLAR_VOL` in config.py.**

Note: symbols excluded by D5 or D6 should be placed in MONITOR or TEMPORARILY_INELIGIBLE, never deleted from MASTER.

---

## D16 — Duplicate symbols

| Symbol | Categories | Canonical | Secondary | Action |
|---|---|---|---|---|
| QQQ | מדדים (indices), ETFs | ETFs | None | Move to ETFs only; QQQ is an ETF, not an index |
| NUKZ | ETFs, גרעין (nuclear) | ETFs | None | Move to ETFs only; NUKZ is an ETF |
| DDOG | ענן ותוכנה (cloud), סייבר (cyber) | ענן ותוכנה | סייבר | Both tags are legitimate; scan only once |
| NEE | גרעין (nuclear), אנרגיה ירוקה (green energy) | גרעין | אנרגיה ירוקה | Both tags are legitimate; scan only once |
| UUUU | גרעין (nuclear), חומרי גלם (materials) | גרעין | חומרי גלם | Both tags are legitimate; scan only once |

**Schema recommendation:** A `symbol_tags` text column (comma-separated secondary categories) added to the watchlist table is the simplest non-breaking fix. A full many-to-many join table (`symbol_categories`) would be cleaner architecturally but requires a more complex migration and all queries that join categories. For now, the `symbol_tags` column gives you multi-category display without changing the alert scanning logic.

**Do not migrate the schema yet.** This is flagged as pending your approval (see Section 19 item D16 of the main design document).

For immediate purposes: the alert scanner calls `get_watchlist()` which returns `{category: [symbols]}`. If a symbol appears in two categories, it appears twice in the scan loop. With the current `INSERT OR IGNORE` seeding, the second category is simply never stored — so DDOG, NEE, and UUUU are only scanned once (in whichever category was seeded first). This is the correct behavior for scanning. The only loss is the display-level category information.

---

## D18 — Possibly invalid symbols

Investigation results based on live data fetch (no data = yfinance returned nothing):

| Symbol | Current label | Price | Volume | Finding | Action |
|---|---|---|---|---|---|
| CEZ | CEZ Group | $27.61 | 0 | CEZ Group trades on the Prague Stock Exchange (PSE). yfinance may return a price (from OTC Pink market or stale data) but volume is 0. Full technical analysis would fail — no reliable OHLCV history. | Keep in MASTER; mark TEMPORARILY_INELIGIBLE |
| KAP | Kazatomprom | $0.00 | 0 | Kazatomprom trades on the London Stock Exchange (KAP.L) and Astana Stock Exchange. `KAP` as a US ticker may refer to an unrelated or delisted entity. No data returned. | Keep in MASTER; mark TEMPORARILY_INELIGIBLE; note correct ticker is KAP.L if LSE |
| YCA | Yellow Cake | $0.00 | 0 | Yellow Cake plc trades on AIM (London). Ticker YCA.L. `YCA` returns no US data. | Keep in MASTER; mark TEMPORARILY_INELIGIBLE |
| DYL | Deep Yellow | $0.00 | 0 | Deep Yellow Limited is an Australian uranium company (DYL.AX on ASX). No US data. | Keep in MASTER; mark TEMPORARILY_INELIGIBLE |
| FCU | Fission Uranium | $0.00 | 0 | Fission Uranium Corp. trades on TSX (FCU.TO) and OTC (FCUUF). The plain `FCU` ticker returns no data from yfinance US. | Keep in MASTER; use `FCU.TO` for Canadian data or `FCUUF` for OTC, pending your confirmation |
| PDN | Paladin Energy | $46.61 | 25,292 | Price returned but almost no US volume (25K shares/day). Paladin Energy trades primarily on ASX (PDN.AX) and JSE. US OTC volume is negligible; full analysis would produce unreliable signals. | Keep in MASTER; mark TEMPORARILY_INELIGIBLE |
| ELE | Endesa | $16.72 | 320,490 | Endesa S.A. (Spanish utility) appears to trade as an ADR in US markets. Volume is borderline (320K shares/day, $5.4M/day). Technical analysis is possible but signal reliability may be lower due to ADR premium/discount effects. | Keep in MASTER; MONITOR tier |
| BOE | Brookfield Renewable Partners? | $11.95 | 128,634 | Labeled "Brookfield Renewable Partners" in the nuclear category, but BEP (already in the list) is the correct ticker for Brookfield Renewable. `BOE` appears to be an unrelated company (possibly a small energy firm). Insufficient liquidity ($1.5M/day). | Keep in MASTER; mark TEMPORARILY_INELIGIBLE; confirm intended ticker |
| CBRS | Cerebras Systems | $212.25 | 8,167,565 | Cerebras Systems appears to have gone public and trades with excellent liquidity ($1.73B/day). The ticker is valid and data is clean. Despite being labeled "hot IPO" in the config, it is fully analyzable. | Valid; recommend ACTIVE tier |
| CRML | Critical Metals Corp | $9.56 | 11,910,184 | Data available. Very high share volume for a materials stock ($113.9M/day) but the company is a small critical metals miner. Verify whether the company name and strategy match your thesis. | Keep in MASTER; MONITOR tier pending verification |
| USAR | USA Rare Earth | $21.74 | 16,702,464 | Data available. $363M/day dollar volume is substantial for a rare earth company. Passes liquidity filters. | Valid; MONITOR tier |
| AREC | American Rare Earths | $2.14 | 2,931,314 | Price below $3.00 (fails E4 minimum price rule). Dollar volume is $6.3M (borderline). | Keep in MASTER; TEMPORARILY_INELIGIBLE until price recovers above $3 |
| NB | NioCorp Developments | $5.19 | 4,145,095 | Dollar volume $21.5M/day; passes liquidity filters. | Keep in MASTER; MONITOR tier |
| ONDS | Ondas Holdings | $9.21 | 69,871,100 | Surprisingly high volume ($643M/day) for a micro-cap defense/drone company. Verify whether this is real institutional interest or speculative trading. Passes volume filters. | Keep in MASTER; MONITOR tier; flag as volatile |
| CCC | Claros Mortgage Trust | $4.71 | 10,884,571 | CCC is a commercial real estate mortgage REIT, not a cloud/software company. Listed under "ענן ותוכנה" — this is a misclassification. The company's fundamentals are entirely unrelated to the tech strategy. | Keep in MASTER; reclassify to פיננסים or a new category; do not place in ACTIVE |
| FFWM | First Western Financial | N/A | 0 | No data — possibly delisted or symbol changed. First Western Financial (FFWM) may have been acquired or delisted. | Keep in MASTER; mark TEMPORARILY_INELIGIBLE; verify |
| CADE | Cadence Bank | N/A | 0 | No data from yfinance despite being a known mid-size bank. Ticker may be on OTC or there may be a data feed issue. | Keep in MASTER; mark TEMPORARILY_INELIGIBLE; verify |
| MOFG | MidWestOne Financial | N/A | 0 | No data — possible data feed issue or delistment. | Keep in MASTER; mark TEMPORARILY_INELIGIBLE; verify |

**Correction mappings that require your confirmation before applying:**

| Original ticker | Possible correct ticker | Exchange | Confirmation needed |
|---|---|---|---|
| KAP | KAP.L | London Stock Exchange | Confirm you want to track via LSE data |
| YCA | YCA.L | London AIM | Confirm you want to track via LSE data |
| DYL | DYL.AX | ASX (Australia) | Confirm you want to track via ASX data |
| FCU | FCU.TO or FCUUF | TSX / US OTC | Confirm preferred listing |
| PDN | PDN.AX or PALAF | ASX / US OTC | Confirm preferred listing |
| BOE | Confirm intended company | Unknown | Do not substitute automatically |

**Note:** yfinance can fetch data for some international tickers using exchange suffixes (e.g. KAP.L, DYL.AX). This would require storing the yfinance ticker separately from the display symbol. Do not implement this until you confirm which symbols you actually want to track internationally.

---

## Bank stock handling

The current watchlist has 149 bank-related symbols in the "פיננסים" category.

**Findings:**
- 80% of them would be excluded by a $50M/day dollar volume filter
- 57% would be excluded by a $20M/day filter
- Only about 30 banks have dollar volume above $50M/day
- The community and regional bank segment is not well-suited to the current 9-gate swing-trade strategy because: (a) many trade below 300K shares/day, making execution difficult; (b) their price movement is often driven by rate decisions and macro news rather than technical momentum; (c) yfinance data for small bank stocks can be unreliable

**Recommended handling:**
- Do not delete any bank stocks from MASTER
- Apply eligibility filters (D5 at 250K, D6 at $10M) — this automatically places ~66 banks in TEMPORARILY_INELIGIBLE
- Of the remaining ~83 banks that pass liquidity, recommend a maximum of 5 bank stocks in ACTIVE at any one time (sector cap)
- The largest banks (JPM, GS) should be in MONITOR or CONTEXT depending on whether you want alerts on them
- The KRE-style community bank universe is better suited to a dedicated mean-reversion or earnings-based strategy than the current swing-trade momentum approach

---

## 20 Decisions — summary table

| # | Decision | My recommendation | Alternatives | Change after deploy? |
|---|---|---|---|---|
| D1 | Max ACTIVE list size | **30** | 20 (faster scan), 35 (more coverage) | Yes, no migration |
| D2 | Active scan frequency | **15 min** (yfinance is ~15-min delayed anyway) | 10 min (no benefit), 5 min (wastes API) | Yes, no migration |
| D3 | Monitor scan frequency | **60 min** | 30 min (higher cost), 2 hr (may miss setups) | Yes, no migration |
| D4 | Minimum price (E4) | **$3.00** | $1 (allows penny stocks), $5 (excludes some small-caps) | Yes, no migration |
| D5 | Min avg share volume (E5) | **250,000/day** | 100K (too permissive), 500K (too aggressive) | Yes, no migration |
| D6 | Min avg dollar volume (E6) | **$10M/day** | $5M (too permissive), $20M (may exclude valid small-caps) | Yes, no migration |
| D7 | Promotion threshold | **Score ≥ 70, sustained 2 consecutive days** | 65 (more symbols promoted), 75 (very selective) | Yes, no migration |
| D8 | Demotion threshold | **Score < 45, sustained 3 consecutive days** | < 40/2 days (thrashing risk), < 50/3 days (too aggressive) | Yes, no migration |
| D9 | Min dwell time in ACTIVE | **5 trading days** | 3 days (less protection), 10 days (too slow) | Yes, no migration |
| D10 | Earnings behavior | **Warn in message; do not auto-suppress** (no earnings data source yet) | Auto-suppress (requires earnings API) | Yes, no migration |
| D11 | VIX regime behavior | **Warn in /status if VIX > 25; do not suppress alerts** | Auto-suppress if VIX > 25 (misses setups in recoveries) | Yes, no migration |
| D12 | ETF alert behavior | **No BUY alerts for ETFs; show in /analyze and /watchlist only** | Treat as stocks (wrong strategy fit) | Yes, no migration |
| D13 | Crypto alert behavior | **Exclude from 9-gate alerts; include in /analyze only** | Same as stocks (market hours mismatch) | Yes, no migration |
| D14 | Intraday provisional alerts | **Disallow until data-delay disclosure is implemented** | Allow with "PROVISIONAL" tag | Yes, no migration |
| D15 | Daily summary time | **23:30 IL (30 min after US market close)** | 16:30 IL (too close to open), 08:00 IL (next morning) | Yes, no migration |
| D16 | Duplicate symbols | **Resolve QQQ/NUKZ to ETF category; add `symbol_tags` text column for multi-category display (DDOG, NEE, UUUU)** | Full many-to-many table (heavier migration) | Schema change needed for symbol_tags |
| D17 | Bank stock handling | **Apply D5/D6 automatically; cap Active at 5 bank stocks; keep all in Master** | Delete all banks (too aggressive) | Yes, no migration |
| D18 | Possibly invalid symbols | **Auto-flag via eligibility rules; manual review list produced above; no automatic substitution** | Manual deletion | No migration needed |
| D19 | watchlist_seed.yaml vs WATCHLIST in config.py | **Move to watchlist_seed.yaml** (decouples data from code, prevents recurrence of today's AUTHORIZED_CHAT_IDS accident) | Keep in config.py (simpler but fragile) | No migration; requires startup code change |
| D20 | Eligibility evaluation frequency | **Daily, after market close** | Weekly (too slow to adapt), real-time (too expensive) | Yes, no migration |

---

## Proposed symbol classification

### How to read this table

- **ACTIVE**: Recommend scanning every 15 minutes and allowing BUY alerts
- **MONITOR**: Scan every 60 minutes; no alerts; eligible for promotion
- **CONTEXT**: Fetch price only; no alerts; used for regime awareness
- **MASTER_ONLY**: Keep in database but do not scan regularly
- **INELIGIBLE**: No data or data problems; skip until resolved

Symbols not flagged below: classified as MONITOR by default pending your review.

### ETF_INDEX_CONTEXT list (never generate BUY alerts)

| Symbol | Name | Notes |
|---|---|---|
| ^GSPC | S&P 500 Index | Market regime indicator |
| ^IXIC | Nasdaq Composite | Market regime indicator |
| ^DJI | Dow Jones | Market regime indicator |
| ^RUT | Russell 2000 | Small-cap risk indicator |
| ^VIX | CBOE Volatility Index | Fear indicator — already used in /status |
| SPY | SPDR S&P 500 ETF | Regime tracking |
| VOO | Vanguard S&P 500 ETF | Regime tracking |
| QQQ | Invesco Nasdaq 100 ETF | Regime tracking (canonical; remove from indices) |
| VGT | Vanguard IT ETF | Sector context |
| XLK | SPDR Technology ETF | Sector context |
| SOXX | iShares Semiconductor ETF | Sector context |
| CIBR | First Trust Cybersecurity ETF | Sector context |
| ARKK | ARK Innovation ETF | Growth sentiment |
| SCHG | Schwab Large-Cap Growth ETF | Growth context |
| UFO | Procure Space ETF | Very low AUM (~$26M); borderline |
| NUKZ | Range Nuclear ETF | Nuclear sector context (canonical; remove from nuclear) |
| URA | Global X Uranium ETF | Uranium sector context |
| URNM | Sprott Uranium Miners ETF | Uranium sector context |
| NLR | VanEck Uranium+Nuclear ETF | Nuclear sector context |
| REMX | VanEck Rare Earth ETF | Rare earth sector context |
| COPX | Global X Copper Miners ETF | Copper sector context |
| CPER | US Copper Index Fund | Copper context |
| SLX | VanEck Steel ETF | Steel context (misplaced in nuclear category) |
| BTC-USD | Bitcoin | Crypto context — no market hours |
| ETH-USD | Ethereum | Crypto context — no market hours |

### Proposed ACTIVE list (20–30 symbols for frequent scanning)

These represent the highest-quality, most liquid candidates that best fit the current 9-gate swing-trade strategy. Selection is based on: liquidity (>$50M/day), sector diversity, and known strategy compatibility.

| Symbol | Name | Sector | Dollar Vol |
|---|---|---|---|
| NVDA | NVIDIA | AI/Semis | Very high |
| AMD | Advanced Micro Devices | AI/Semis | Very high |
| AVGO | Broadcom | AI/Semis | Very high |
| ASML | ASML Holding | Semis | High |
| TSM | Taiwan Semiconductor | Semis | High |
| AMAT | Applied Materials | Semis | High |
| KLAC | KLA Corporation | Semis | High |
| LRCX | Lam Research | Semis | High |
| MU | Micron Technology | Semis | High |
| GOOGL | Alphabet | Mega-cap Tech | Very high |
| MSFT | Microsoft | Mega-cap Tech | Very high |
| META | Meta Platforms | Mega-cap Tech | Very high |
| AMZN | Amazon | Mega-cap Tech | Very high |
| AAPL | Apple | Mega-cap Tech | Very high |
| PLTR | Palantir | AI/Defense Software | High |
| CRWD | CrowdStrike | Cybersecurity | High |
| PANW | Palo Alto Networks | Cybersecurity | High |
| NET | Cloudflare | Cloud/CDN | High |
| SNOW | Snowflake | Cloud Data | High |
| DDOG | Datadog | Cloud Observability | High |
| CEG | Constellation Energy | Nuclear | High |
| VST | Vistra | Energy | High |
| GEV | GE Vernova | Energy Infrastructure | High |
| RKLB | Rocket Lab | Space | High |
| AXON | Axon Enterprise | Defense/Tech | High |
| RTX | Raytheon Technologies | Defense | High |
| VRT | Vertiv | Data Center | High |
| EQIX | Equinix | Data Center REIT | High |
| CBRS | Cerebras Systems | AI Hardware | Very high |
| TSLA | Tesla | EV/AI | Very high |

**Total: 30 symbols.** This fits D1. Adjust based on your conviction in each name.

Note: TSLA and CBRS are high-volatility names. If the scan produces too many alerts from them, consider moving to MONITOR.

### TEMPORARILY_INELIGIBLE (data problems or below eligibility floor)

| Symbol | Reason | Next step |
|---|---|---|
| CEZ | No reliable US volume; Czech Stock Exchange primary listing | Verify if you want to track; if yes, confirm yfinance ticker suffix |
| KAP | No US data; LSE primary listing (KAP.L) | Confirm preferred ticker |
| YCA | No US data; AIM (London) listing (YCA.L) | Confirm preferred ticker |
| DYL | No US data; ASX primary listing (DYL.AX) | Confirm preferred ticker |
| FCU | No US data; TSX primary listing (FCU.TO) | Confirm preferred ticker |
| PDN | Near-zero US volume (25K/day); ASX primary | Confirm preferred ticker |
| BOE | No confirmed entity match; low liquidity ($1.5M/day) | Confirm intended company and ticker |
| AREC | Price below $3.00 floor (currently $2.14) | Re-evaluate when price > $3 |
| FFWM | No data — possible delistment | Verify company status |
| CADE | No data — possible data feed issue | Re-check next trading day |
| MOFG | No data — possible data feed issue | Re-check next trading day |

### Symbols requiring category correction (not deletion)

| Symbol | Current category | Issue | Proposed category |
|---|---|---|---|
| CCC | ענן ותוכנה (Cloud/Software) | Claros Mortgage Trust is a commercial mortgage REIT | פיננסים (Financials) or a new REITs category |
| SLX | גרעין (Nuclear) | VanEck Steel ETF has no connection to nuclear energy | ETFs or חומרי גלם (Materials) |
| NUKZ | גרעין (Nuclear) + ETFs | Duplicate; it is an ETF | ETFs only |
| QQQ | מדדים (Indices) + ETFs | Duplicate; it is an ETF, not an index | ETFs only |

### Symbols to verify (do not modify yet, flagged for your review)

| Symbol | Flag | Reason |
|---|---|---|
| ONDS | VERIFY | Very high volume ($643M/day) for a micro-cap drone company; check for reverse split or data anomaly |
| CRML | VERIFY | High volume for small critical metals company; confirm business matches thesis |
| USAR | VERIFY | Reasonable data but OTC market; confirm exchange stability |
| AUR | VERIFY | Aurora Innovation (autonomous vehicles); SPAC background, high dilution risk |
| NNE | VERIFY | Nano Nuclear Energy — early stage with no revenue |
| BB | VERIFY | BlackBerry pivoted to cybersecurity; verify if strategy still fits |

---

## Items still requiring your approval

Before any automation (Active/Monitor tier management) is built, confirm:

1. **D1** — Accept 30 as the maximum ACTIVE list size, or choose a different number
2. **D5** — Accept 250,000 shares/day, or choose a different threshold
3. **D6** — Accept $10M/day, or choose a different threshold
4. **D16** — Approve the duplicate resolution plan (QQQ/NUKZ to ETFs; symbol_tags column for multi-category display)
5. **D17** — Confirm bank stock handling (max 5 in ACTIVE; D5/D6 filter does the work)
6. **D18** — Confirm which TEMPORARILY_INELIGIBLE symbols you want to verify manually
7. **D19** — Approve moving the watchlist to `watchlist_seed.yaml`
8. **Proposed ACTIVE list** — Review the 30 proposed symbols; add/remove as needed
9. **Category corrections** — Approve or reject the CCC and SLX reclassifications
10. **International tickers** — Decide whether to use exchange-suffix tickers (KAP.L, DYL.AX, etc.) or drop the foreign-primary symbols

---

## Exact revert commands

| Commit | Description | Revert command |
|---|---|---|
| `ef00728` | Restore .env.example placeholders | `git revert ef00728` (**Not recommended** — would restore a file with real credentials) |
| `5f6e699` | Watchlist reseed fix | `git revert 5f6e699` (safe; existing enabled=0 rows would stay disabled; DB state unaffected beyond re-enabling DELETE behavior) |

Note: reverting `ef00728` is listed for completeness only. Do not revert it.

---

## Final status confirmation

| Item | Status |
|---|---|
| Branch | `claude/stocksage-review-20260617-1200` |
| Backup branch | Untouched — confirmed |
| Security commit | `ef00728` |
| Reseed fix commit | `5f6e699` |
| Tests | 94/94 pass |
| Smoke test | Pass |
| Real Telegram messages sent | No |
| Real .env modified | No |
| Real database used in tests | No (all tests use temp files) |
| Secrets displayed anywhere | No |
| Secrets in git history | No (confirmed via `git log -S`) |
| Credential types requiring rotation | **TELEGRAM_BOT_TOKEN** (if shared outside this machine) |
| Runtime /remove survives restart | Yes (soft-delete, enabled=0) |
| Runtime /add survives restart | Yes (upsert, sets enabled=1) |
| Git pull overwrites runtime watchlist | No (SQLite is not git-tracked; seed is INSERT OR IGNORE) |
| GitHub push | Not done |
