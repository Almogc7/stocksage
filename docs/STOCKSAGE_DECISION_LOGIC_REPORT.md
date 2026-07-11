# StockSage Decision Logic Report

**Date:** 2026-06-20
**Scope:** read-only code/DB analysis. No code was changed, no apply mode was run, no DB writes were made.

## 1. Executive Summary

StockSage is a **personal swing-trading screening and alerting tool**, not an
automated trading system and not a guaranteed BUY/LONG signal generator. It
does two genuinely separate jobs that are easy to conflate:

1. **Watchlist curation** — a relevance-scoring engine (`analyzers/eligibility.py`
   + `services/watchlist_evaluator.py`) decides which symbols are *worth
   scanning at all* (ACTIVE tier, capped at 30) versus just tracked
   (MONITOR) versus context-only (ETFs/indices) versus temporarily broken
   data (TEMPORARILY_INELIGIBLE). This relevance score is about **liquidity,
   data quality, and general technical health** — it is not a buy signal.
2. **Live alert generation** — a completely separate 9-gate check
   (`agent/core.py:check_alerts`) runs every 15 minutes during US market
   hours over the *ACTIVE tier only*, using a different scoring function
   (`analyzers/technical.py:full_analysis`, the "opportunity score") to
   decide whether to actually send a Telegram alert right now.

Both layers are purely **technical/price-and-volume based**. There is no
fundamentals data, no earnings calendar, no news/sentiment analysis, and no
market-regime (VIX) filter anywhere in the codebase — these are stub files
with zero lines of code. The system also has no backtest, no position
sizing, and no stop-loss/take-profit *execution* (it only *prints* a
suggested stop/target alongside the alert text).

**Bottom line up front:** this is a reasonable rule-based screening tool for
narrowing 400 symbols down to a short list worth your personal attention. It
is not, and should not be treated as, an automated BUY/LONG decision engine.

## 2. What the Project Does

StockSage runs two concurrent processes from `main.py`:

- **A background agent thread** (`agent/core.py`) that, every 15 minutes
  while the US market is open (9:30–16:00 ET, computed from Israel time in
  `config.py`), re-fetches prices for every ACTIVE-tier symbol and runs a
  9-gate check (§7) to decide whether to fire a Telegram BUY alert. Once
  per trading day (16:35 Israel time = market open) it also runs a
  "morning scan" across the same ACTIVE tier and sends the top 5 scoring
  symbols regardless of whether they pass the full alert gate set.
- **A Telegram bot** (`bot/telegram_bot.py`, blocking, main thread) that
  serves ~22 commands: portfolio/trade journal commands (`/trade`,
  `/trades`, `/summary`), analysis commands (`/analyze`, `/scan`), watchlist
  management (`/add`, `/remove`, `/watchlist*`), and the newer watchlist
  lifecycle commands (`/refresh_watchlist`, `/watchlist_refresh_status`,
  `/watchlist_changes`).

**While the US market is open:** the background thread polls prices every
15 minutes and can fire alerts; `/scan`, `/analyze`, and the Telegram
commands all work normally and reflect live(ish) data.

**While the market is closed:** the background thread's `run_checks()` exits
immediately (`is_market_open()` returns `False`) — no alerts are evaluated
or sent. Telegram commands still work (they fetch fresh data from yfinance
regardless of market hours, except live alerts are gated off).

**Automatic vs. manual:** the only thing that runs automatically and
unattended is the 15-minute alert check and the once-daily morning scan —
both read-only with respect to the watchlist (they never change ACTIVE/
MONITOR/etc. state). Everything related to the *watchlist lifecycle itself*
(dry-run evaluation, apply, rollback, scheduled evaluation) is manual-only
right now: nothing calls `run_scheduled_evaluation()` automatically, and
Telegram/scheduled apply are both disabled by default
(`TELEGRAM_ALLOW_WATCHLIST_APPLY=false`, `WATCHLIST_SCHEDULE_APPLY=false`).

## 3. Data Sources

All market data comes from **yfinance** (`data/fetcher.py`,
`data/market_data_validator.py`) — an unofficial, free Yahoo Finance wrapper
with no SLA. Specifically:

- **Live/snapshot price** — `yf.Ticker(symbol).fast_info` (current price,
  previous close, day high/low/open, 3-month average volume).
- **Historical daily candles** — `yf.download(symbol, period=..., interval="1d")`,
  adjusted close (`auto_adjust=True`) — used for every indicator
  calculation. Live alert checks use `period="1y"`; the watchlist
  evaluator's data-validation layer defaults to `period="6mo"`.
- **Batch price snapshots** — `yf.download([...], period="2d", group_by="ticker")`
  for the 15-minute alert loop (one batched call for the whole ACTIVE tier
  instead of one call per symbol).
- **52-week high/low** — `data/fetcher.py:get_52week_high_low()` exists and
  computes it correctly, but **it is never called from anywhere in the
  live code path** — it's dead code as far as decision-making goes.

**Explicitly NOT used anywhere in the codebase:**
- **Fundamentals** (P/E, earnings, revenue, balance sheet) — not fetched, not scored.
- **Earnings calendar / earnings-date risk** — not tracked.
- **News or sentiment** — `analyzers/sentiment.py` and `data/news_fetcher.py`
  exist as empty placeholder files (0 lines of code). `NEWS_API_KEY` is
  read from `.env` but never used by anything.
- **VIX / market-regime filter** — `^VIX` is listed as a watchlist symbol
  for display purposes only; nothing reads its value to suppress or adjust
  alerts.
- **Order book / Level 2 data, options data, short interest** — not used.

## 4. Watchlist States

| State | Meaning | Scanned for live alerts? |
|---|---|---|
| `ACTIVE` | Currently in the "interesting enough to watch closely" tier. Capped at 30 symbols, max 8 from the bank/financial category. | **Yes** — only `ACTIVE` symbols are ever passed to `check_alerts()`/the morning scan. |
| `MONITOR` | Tracked, has a category, but not currently scanned for alerts. The vast majority of the watchlist lives here (355 of ~399 symbols right now). | No. |
| `ETF_INDEX_CONTEXT` | ETFs, indices, and crypto (`SPY`, `^GSPC`, `BTC-USD`, etc.). Permanent — never promoted to ACTIVE by the relevance engine regardless of score. | No. |
| `TEMPORARILY_INELIGIBLE` | A specific, recorded data problem (foreign-exchange-only listing, price below the $3 floor, stale/missing data). Has an `exclusion_reason` and (once a scheduled retry mechanism is wired up) a `reeval_date`. | No. |
| `USER_REMOVED` | Soft-deleted via `/remove`. Immutable — the relevance engine and the startup classifier both refuse to touch it; only an explicit `/add` brings it back (to `MONITOR`, never directly to ACTIVE). | No. |

**Is `config.py` the source of truth?** No — it's a **one-time seed list
only**. `config.py`'s `WATCHLIST` dict (399 unique symbols across 13
Hebrew-named categories) is inserted via `populate_from_config()` using
`INSERT OR IGNORE`, so it never overwrites an existing row. Once a symbol's
row exists in SQLite, its tier/score/counters are managed entirely by the
database — editing `config.py` again will not move an existing symbol
between tiers. The only thing config seeding can do to an *existing* row is
nothing (already present, ignored).

**How SQLite stores runtime state:** a single `watchlist` table holds, per
symbol: `wl_state`, `security_type`, `relevance_score`, `last_evaluated`,
`last_promoted`/`last_demoted`, `exclusion_reason`, `reeval_date`,
`consec_promote_count`/`consec_demote_count`, `dwell_days`, plus a
`wl_classified` flag. That flag is what stops the one-time startup
classifier (`run_initial_classification()`, called from `run_bot()` and
`main.py`) from re-classifying a symbol more than once across restarts — it
only acts on rows that have never been classified before.

## 5. Relevance Score

This is the score computed by `analyzers/eligibility.py:compute_relevance_score()`
and used by `services/watchlist_evaluator.py` to decide tier promotion/
demotion. It answers **"is this symbol worth tracking closely?"** — it is
**not** the BUY/LONG signal (that's §7).

Score range: **0–100 integer**. Six weighted components:

| Component | Weight | What it checks | Causes a LOW score | Causes a HIGH score |
|---|---|---|---|---|
| **Data quality** | 25% | Is there a non-zero price? A non-zero volume? Is the latest bar fresh (≤3 days old by default)? Each of the three sub-checks is worth ~0.35/0.35/0.30 of this component's 0.0-1.0 scale. | Missing/zero price or volume; stale data. | Real, fresh price+volume. |
| **Liquidity** | 25% | Average daily share volume vs. `ELIGIBILITY_MIN_AVG_VOLUME` (250,000 shares) and average dollar volume vs. `ELIGIBILITY_MIN_DOLLAR_VOL` ($10M), each capped at 1.0, averaged together. | Thin/illiquid stock. | High-volume, large-cap-ish stock. |
| **Trend** | 20% | +0.5 if price > EMA150, +0.5 if EMA150 > EMA200 (long-term uptrend confirmed). | Price below its 150-day average, or a death-cross-like EMA150<EMA200 state. | Sustained uptrend on both timeframes. |
| **Momentum** | 15% | RSI zone (1.0 if 45-65 "healthy", 0.5 if 35-44/66-75 "fringe", 0 otherwise) weighted 60%, plus MACD crossover state (1.0 bullish / 0.5 none / 0.0 bearish) weighted 40%. | Overbought/oversold RSI, bearish MACD cross. | Mid-range RSI with a recent bullish MACD cross. |
| **Setup proximity** | 10% | Bollinger Band position: 1.0 "middle", 0.8 "near lower band" (potential bounce setup), 0.4 "near upper", 0.2 "above upper" or "below lower". | Price stretched far above/below its bands. | Price sitting near the middle or just above the lower band. |
| **Volatility suitability** | 5% | ATR% as a fraction of price: 1.0 if between 1.5%-8% (the "tradeable" range), linearly penalized below 1.5% (too quiet) or above 8% up to 15% (too wild). | Either dead-flat or extremely choppy. | Moderate, tradeable daily range. |

**Important implementation detail:** the trend/momentum/proximity/volatility
components all reuse the raw indicator *outputs* of
`analyzers/technical.py:full_analysis()` (EMA150/200, RSI, MACD crossover,
Bollinger position, ATR%) — but **not** that function's own buy_score or
veto logic. Even when `full_analysis()` internally vetoes its own opportunity
score to 0 (e.g. RSI > 75), the raw indicator values it returns are still
real and still feed the relevance score normally. The two scores are
computed from the same indicators but are mathematically independent of
each other.

**Missing data never helps:** every component explicitly returns 0 for
missing/`None` inputs (`analysis is None`, `price_data is None`, etc.) —
there is no code path where absent data produces a non-zero contribution.

**Thresholds and rules** (all in `config.py`, all overridable via env vars):
- `PROMOTION_THRESHOLD = 60`, `PROMOTION_CONSEC_REQUIRED = 2` — a MONITOR
  symbol needs score ≥ 60 on **two consecutive evaluations** before it can
  promote.
- `DEMOTION_THRESHOLD = 45`, `DEMOTION_CONSEC_REQUIRED = 2` — an ACTIVE
  symbol needs score < 45 on two consecutive evaluations to demote.
- `DWELL_MIN_DAYS = 5` — an ACTIVE symbol cannot be demoted before it has
  been ACTIVE for at least 5 days, regardless of score.
- `ACTIVE_MAX_SIZE = 30` — hard cap on the ACTIVE tier.
- `ACTIVE_BANK_MAX = 8` — at most 8 of the 30 ACTIVE slots may come from the
  "פיננסים" (financial/bank) category.
- `REPLACEMENT_MARGIN = 5` — when ACTIVE is full, a new candidate must beat
  the **lowest-scoring current ACTIVE symbol** by at least 5 points to
  replace it (deterministic: lowest score, then alphabetical, is evicted).
- **Hysteresis counters** (`consec_promote_count`/`consec_demote_count`)
  persist in SQLite between evaluation runs — but **only apply mode writes
  them**. A dry-run computes what the counters *would* become but never
  saves it.

## 6. ACTIVE Promotion Logic

To become ACTIVE, a MONITOR symbol must, across **two separate evaluation
runs**:
1. Not be `USER_REMOVED` or an ETF/index/crypto (those are permanently
   excluded from ACTIVE consideration).
2. Have real, non-zero price data, and price ≥ $3 (the hard "penny stock"
   floor — anything below this is force-routed to TEMPORARILY_INELIGIBLE
   instead, regardless of how high its other components score).
3. Score ≥ 60 on **both** of the two evaluations (the second pass is what
   actually flips the state — the first pass only increments a counter).
4. Fit under the 30-symbol cap, and if the symbol is in the bank category,
   also fit under the 8-symbol bank sub-cap.
5. If ACTIVE is already full, beat the current lowest ACTIVE score by ≥ 5
   points — the evicted symbol drops back to MONITOR in the same run.

**A genuinely strong-scoring symbol can still stay in MONITOR** if: it just
qualified for the first time (needs one more passing evaluation), ACTIVE is
full and it doesn't beat the lowest score by the 5-point margin, or it's a
bank-category stock and the bank sub-cap is already at 8 even though
overall ACTIVE has room.

**Provider degradation protection:** if ≥40% of evaluated symbols in one run
return a transient yfinance failure (rate-limited, connection error,
provider error — not a data-quality problem like stale data), the entire
run is flagged `provider_degraded` and every *score-driven* ACTIVE→MONITOR
demotion that run is reverted (those symbols stay ACTIVE with a note
explaining why). This specifically prevents "yfinance had a bad five
minutes" from being mistaken for "every ACTIVE stock suddenly got worse."

## 7. Live Alert / LONG-BUY Signal Logic

This is the part that actually decides whether you get pinged on Telegram
right now. It runs only over the **ACTIVE tier** (`agent/core.py:check_alerts`),
every 15 minutes during market hours, and is a **9-gate all-or-nothing**
check — every gate must pass:

1. **Price change ≥ +0.5%** intraday (today vs. previous close).
2. Not already alerted this process session (in-memory dedup).
3. Not in cooldown — no alert sent for this symbol in the last 2 hours (SQLite-persisted, survives restarts).
4. Historical data fetch succeeds (1-year daily candles).
5. **Price above EMA150.**
6. **RSI between 45 and 68** (a *different* RSI window than the relevance
   score's 45-65 "ideal"/35-75 "acceptable" — see §11, this inconsistency
   is a real, if minor, code smell).
7. **Volume spike** — current volume > 1.5× the 20-day average.
8. **Composite opportunity score ≥ 65 AND verdict is "BUY" or "STRONG BUY"**
   (from `full_analysis()` — see below).
9. **Last completed candle is green** (close > open) — using `df.iloc[-2]`
   while the market is open (to avoid judging an still-forming candle) or
   `df.iloc[-1]` once the market is closed.

**The opportunity score** (`analyzers/technical.py:full_analysis()`,
0-100, separate from the relevance score) first applies hard veto gates
(price below EMA150 → 0; RSI < 35 or > 75 → 0), then adds: +20 price above
EMA150, +15 EMA150>EMA200, +20 MACD bullish cross in the last 3 candles,
+15/+5 RSI zone, +15 volume spike, +10 Stochastic RSI bullish cross, +5
price above rolling VWAP. ≥75 = "STRONG BUY", ≥55 = "BUY", ≥35 = "WATCH",
else "NEUTRAL".

**Yes, RSI, moving averages (EMA150/200), and volume are all used.** MACD,
Bollinger Bands, ATR, pivot points, and swing highs/lows are computed and
shown in `/analyze` output but the live alert gate itself only directly
checks RSI/EMA/volume/green-candle/score-and-verdict — MACD is folded into
the *score*, not a separate gate. **52-week high/low proximity is not used**
anywhere in the alert decision (the function exists but is never called).
Candles used for the trend/RSI/MACD calculation are completed daily candles;
the *price-change-percent* gate (#1) uses the current intraday snapshot
price, so the alert decision mixes a live intraday number with daily-candle
indicators — this is intentional (swing-trade entries react to today's
move) but worth knowing.

**The critical distinction, stated plainly:**
- **ACTIVE** = "this symbol is in the smaller pool the system actually
  watches" — a relevance/liquidity/data-quality judgment, recomputed at
  most twice before it matters.
- **ALERT fired** = "all 9 technical gates lined up for this symbol, right
  now" — a real-time condition, can be true or false on any given 15-minute
  check independent of how it got to ACTIVE.
- **"BUY"/"STRONG BUY"** = a **label produced by a fixed arithmetic rule**
  (a weighted sum of six binary/graded technical conditions crossing a
  number). It is not a probability estimate, not validated against
  historical outcomes (no backtest exists in this codebase), and not aware
  of anything outside price/volume/momentum (earnings, news, market
  regime). Treat it as "this passed my technical screening rules," not as
  "this will go up."

## 8. Telegram Alert Flow

- **Trigger:** all 9 gates above pass, inside the 15-minute background loop, only while `is_market_open()` is true.
- **Cooldown:** `ALERT_COOLDOWN_HOURS = 2` — a symbol that already fired won't fire again for 2 hours, checked via SQLite (`was_alerted_recently`), persists across restarts.
- **Duplicate suppression:** an additional in-process, in-memory dict (`_alerted_this_session`) catches same-cycle duplicates instantly, before even touching the database — closes a tiny race window where the DB write from gate-pass #1 hasn't committed before the same symbol is re-checked.
- **Alert content:** symbol, verdict + score, price + intraday % change, RSI, EMA/volume confirmation icons, the list of triggered scoring signals, a suggested stop-loss (price − 1.5×ATR) and take-profit (price + 3×ATR, fixed "1:2" risk/reward label), and a chart image if generation succeeds (falls back to text-only).
- **Market hours only:** yes — `run_checks()` exits immediately if the market is closed; no after-hours or pre-market alerts.
- **ACTIVE vs. MONITOR:** alerts are **only ever evaluated for ACTIVE-tier symbols**. A MONITOR symbol, no matter how good it looks, will never trigger a Telegram alert until/unless it's promoted to ACTIVE.

## 9. Dry-Run vs Apply

- **`/refresh_watchlist`** (no args, or `dry_run`) runs the full relevance
  evaluation over every ACTIVE+MONITOR+newly-seeded+retry-due-ineligible
  symbol, computes what *would* change, and reports a summary. **It writes
  exactly one bookkeeping row** to `evaluation_runs` (so you have a record
  it happened) and **nothing else** — no `relevance_score`, no state
  transition, no counter change is saved.
- **Why dry-run by default:** this is the safest possible default for a
  rule-based system whose thresholds haven't been backtested — you can see
  exactly what it *would* do before anything happens for real.
- **What apply mode would do:** the identical computation, but every
  evaluated symbol's resulting score, hysteresis counters, `wl_state`
  transition (if any), `last_promoted`/`last_demoted` timestamp, and
  `exclusion_reason`/`reeval_date` (for newly-ineligible symbols) get
  written to the `watchlist` table in one atomic transaction, plus an audit
  row per changed symbol (so a specific run can be rolled back later by
  run ID via `scripts/rollback_evaluation_run.py`).
- **Why Telegram apply is disabled by default:** `TELEGRAM_ALLOW_WATCHLIST_APPLY=false`
  means `/refresh_watchlist apply` is refused outright with a fixed safe
  message; even if you turn that on, the command additionally requires the
  literal word `confirm`. Two independent gates exist specifically so a
  mistyped or copy-pasted command can't silently change your watchlist.
- **What's not changed in dry-run, ever:** `wl_state`, `relevance_score`,
  `consec_promote_count`/`consec_demote_count`, `dwell_days`,
  `last_promoted`/`last_demoted`, `exclusion_reason`, `reeval_date` — none
  of these columns are touched.

## 10. Example Symbols

Pulled read-only from the live production DB (`db/stocksage.db`) at report
time. **All of these have `relevance_score = NULL`** — because **apply mode
has never been run on this database**; every score ever computed (during
the manual dry-runs you've seen in Telegram) was reported in the chat
message and then discarded, never persisted. This is itself a useful
finding: right now there is no way to look up "what was NVDA's last
computed score?" from the database — you'd have to re-run a dry-run and
read the live Telegram/CLI output.

| Symbol | State | Score (DB) | Why | What would change this |
|---|---|---|---|---|
| **NVDA** | MONITOR | `NULL` | It's in the hardcoded 30-symbol startup seed list, but it was already classified to MONITOR in an earlier session *before* this database's most recent 399-symbol reseed — the `wl_classified` flag protects already-classified rows from being re-touched, so being on the seed list didn't promote it. | A real apply-mode evaluation scoring it ≥60 on two consecutive runs (very plausible given NVDA's typical liquidity/trend profile) would promote it normally, same as any other MONITOR candidate. |
| **AMAT** | **ACTIVE** | `NULL` | This one *is* freshly seeded and in the hardcoded `INITIAL_ACTIVE_SET` — the one-time startup classifier (not the relevance engine) put it straight into ACTIVE on first classification. It has never been scored by the real evaluator. | An apply-mode evaluation could demote it if its score falls under 45 for two consecutive runs (and it's been ACTIVE ≥5 days) — but until that first apply run happens, nothing will move it. |
| **SPY** | ETF_INDEX_CONTEXT | `NULL` | Hardcoded as a known ETF in `classify_security_type()`. This is permanent — no score, however high, moves an ETF/index/crypto symbol into ACTIVE. | Nothing — by design, this never changes via the relevance engine. |
| **DYL** | TEMPORARILY_INELIGIBLE | `NULL` | Hardcoded reason: "No US data — ASX primary listing (DYL.AX)" — a foreign-exchange-primary-listing problem that yfinance's US ticker can't resolve, not a quality judgment. | This specific reason is a hardcoded, manually-curated fact, not something the live data validator currently re-checks automatically — it would need a code/data change (a `.AX`-suffixed symbol mapping) to ever recover. |
| **AREC** | TEMPORARILY_INELIGIBLE | `NULL` | Hardcoded reason: "Price below minimum ($3.00 floor)" — same hard floor enforced live in `determine_state_change()`, so even a future live re-evaluation would re-confirm this unless the price genuinely rises above $3. | Price needs to close above $3.00; then a live re-evaluation (once retry scheduling is wired to actually re-check it) would route it to MONITOR for recovery, never straight to ACTIVE. |

**Live alert condition right now for any of these?** Cannot be determined
from the database alone — the 9-gate alert check (§7) is not persisted
anywhere; it only exists transiently inside each 15-minute loop iteration
while the market is open. None of the symbols above are even eligible to be
alert-checked except AMAT (the only one of these five that's ACTIVE).

## 11. Limitations and Risks

- **yfinance reliability** — unofficial API, no SLA, subject to rate
  limiting and occasional outright failures. The system handles this
  explicitly (14 distinct provider-status categories, bounded retries,
  outage detection) but cannot eliminate the underlying risk.
- **No fundamentals** — no P/E, growth, debt, or balance-sheet data anywhere.
- **No earnings calendar** — a stock could gap violently the morning after
  earnings and the system has no idea an earnings date is approaching.
- **No news/sentiment** — `analyzers/sentiment.py`/`data/news_fetcher.py`
  are empty stubs.
- **No market-regime/VIX filter** — alerts fire the same way in a calm
  market or a violent selloff; nothing suppresses signals during high
  systemic risk.
- **No backtest** — every threshold (RSI 45-68, score≥65, volume 1.5×,
  promotion≥60, etc.) is a reasoned default, not validated against
  historical price outcomes.
- **No risk/reward sizing logic** — the alert prints a stop/target based on
  a fixed ATR multiple and a hardcoded "1:2" label; it does not calculate
  position size, account risk percentage, or portfolio-level exposure.
- **No stop-loss/take-profit execution** — these are display-only numbers
  in the alert text; nothing places, tracks, or manages actual orders.
- **Symbol/suffix issues are real and currently handled by a hardcoded
  list, not a general solution** — `CEZ`, `KAP`, `YCA`, `DYL`, `FCU`, `PDN`,
  `BOE`, `AREC`, `FFWM`, `CADE`, `MOFG` are all manually marked
  TEMPORARILY_INELIGIBLE with a hand-written reason in
  `db/database.py`. (`HTBK` was named in your question but does not
  currently appear in this hardcoded list or in the live DB's
  TEMPORARILY_INELIGIBLE set — if you've seen it fail, it likely failed the
  *live* market-data validator at runtime, e.g. insufficient history or a
  genuinely unresolvable ticker, rather than being one of the
  pre-classified exceptions.) Any new foreign-listed or delisted symbol
  added later would need the same manual treatment until a more general
  fix exists.
- **Two different RSI windows in two different layers** — the relevance
  score treats 45-65 as "ideal" RSI (35-75 as the outer veto bound in
  `full_analysis`), while the live alert gate independently enforces
  45-68. Not a bug exactly, but an inconsistency that makes the system
  harder to reason about as a whole.
- **False positives are likely** — a purely rule-based, six-indicator
  screen on daily/intraday price action will flag setups that don't follow
  through; this is normal for *any* technical screener, but is worth
  saying explicitly since no historical hit-rate has ever been measured here.
- **False negatives are equally likely** — a genuinely good setup that
  fails any single one of the 9 gates (e.g. volume spike didn't quite hit
  1.5×, or the last candle happened to close red) gets silently skipped
  with no alert at all.
- **Hysteresis warm-up** — the very first apply-mode run on this database
  will promote nothing (every symbol starts at `consec_promote_count = 0`,
  and 2 consecutive qualifying passes are required) — already observed and
  documented during rollout, not a bug, but worth remembering before
  judging "is it working."
- **Should this be investment advice?** No. It is a **screening/alerting
  tool** that narrows attention using fixed technical rules. Every signal
  it produces should be independently verified before any money moves.

## 12. Is This a BUY Recommendation System?

**No — it is a rule-based screening and alerting engine that uses BUY-style
labels.** The verdict strings ("BUY", "STRONG BUY") are the output of a
fixed weighted-sum formula over six technical conditions crossing a
threshold — not a calibrated probability, not backtested, and blind to
anything outside price/volume/momentum. It correctly distinguishes "worth
watching" (ACTIVE) from "conditions currently lined up" (alert fired), but
neither of those is a substitute for your own judgment, fundamentals check,
or risk management before entering a real position.

## 13. Recommended Manual Confirmation Checklist

Before acting on any alert from this system:

1. **Check the actual chart yourself** — confirm the green-candle/volume-spike claim visually; data lag or a bad fetch is always possible.
2. **Check for upcoming earnings** — the system has no idea if earnings are tomorrow.
3. **Check recent news** — no sentiment/news layer exists here at all.
4. **Check the broader market/sector context** — no VIX or regime filter exists; a "BUY" during a market-wide selloff is still flagged the same way.
5. **Verify liquidity yourself for anything unfamiliar** — the relevance score's liquidity check is a coarse 250k-share/$10M-dollar-volume floor, not a deep order-book check.
6. **Decide your own position size and stop/target** — the printed stop/target is a fixed ATR multiple, not personalized to your account risk.
7. **Confirm the symbol itself is what you think it is** — a handful of tickers in this watchlist are known to be ambiguous/foreign-listed (§11); don't assume every symbol resolves cleanly.

## 14. Recommended Next Improvements

(Not implemented now — listed as future direction only, per the scope of this report.)

- Reconcile the two RSI windows (relevance score vs. live alert gate) into one documented rule.
- Add an earnings-date check that at minimum suppresses or flags alerts within N days of a known earnings date.
- Add a simple market-regime filter (e.g. VIX level or SPY trend) to scale down alert sensitivity in stressed markets.
- Build a backtesting harness against historical data to measure actual hit-rate/expectancy of the current gate set before trusting it more.
- Add basic fundamentals screening (at minimum: avoid micro-caps/penny stocks more robustly than a flat $3 floor).
- Persist relevance scores from dry-runs too (even as a separate, clearly-labeled "last computed" field) so `/watchlist_status` can show a recent score without requiring an apply run.
- Generalize the foreign-listing/suffix problem instead of a hardcoded exception list.
- Only after the above: consider enabling scheduled dry-run evaluation (not apply) to build a track record before ever considering apply automation.

## 15. Appendix: Relevant Files and Functions

| File | Role |
|---|---|
| `analyzers/technical.py` | Opportunity score / verdict (`full_analysis`) — the live-alert scoring engine. |
| `analyzers/eligibility.py` | Relevance score, security-type classification, promotion/demotion decision (`compute_relevance_score`, `determine_state_change`, `evaluate_symbol_eligibility`). |
| `agent/core.py` | 15-minute background loop, the 9-gate `check_alerts()`, morning scan, alert formatting/sending. |
| `services/watchlist_evaluator.py` | Orchestrates a full evaluation pass (dry-run and apply), cap/bank/replacement-margin enforcement, provider-degradation handling. |
| `services/watchlist_scheduler.py` | Market-calendar/timing logic for *when* an evaluation should run (not yet wired to anything automatic). |
| `data/fetcher.py` | Live price/historical fetch helpers used by the alert path and `/analyze`. |
| `data/market_data_validator.py` | Batched/cached/retried yfinance access with explicit failure classification, used by the watchlist evaluator. |
| `db/database.py` | All persistence: watchlist table, state transitions, evaluation-run/audit tables, hardcoded startup seed/ineligible lists. |
| `bot/telegram_bot.py` | All Telegram commands, including `/refresh_watchlist`/`/watchlist_refresh_status`/`/watchlist_changes`. |
| `config.py` | Every threshold referenced in this report. |
| `analyzers/sentiment.py`, `data/news_fetcher.py`, `analyzers/price_alerts.py`, `agent/decision_engine.py`, `agent/watchlist.py` | Empty placeholder files — confirmed 0 lines of code, nothing implemented. |
