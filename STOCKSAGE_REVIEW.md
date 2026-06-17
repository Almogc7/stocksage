# StockSage Review

**Review Date:** 2026-06-17  
**Reviewer:** Claude (claude-sonnet-4-6) acting as senior Python engineer, software architect, code reviewer, and quantitative finance specialist  
**Branch:** `claude/stocksage-review-20260617-1200`

---

## 1. Executive Summary

StockSage is a functional Telegram-bot-based stock alert and research assistant. Its core loop — fetch live prices, run technical analysis, and send alerts — works correctly as demonstrated by the passing smoke test (`test_fetch.py`). The architecture is clean and lean for a personal project.

**What it currently does:**
- Monitors a watchlist of ~75 symbols across 13 categories every 15 minutes during US market hours
- Runs a swing-trading signal composite (7 binary conditions, max 100 points)
- Sends Telegram alerts with chart images when all 9 gates pass
- Provides a Streamlit dashboard and Telegram bot commands for manual analysis and trade logging

**Whether it achieves its purpose:**
Partially. It successfully reduces a large watchlist to actionable alerts. However, it only considers technical momentum signals and no fundamental data, has no risk score, and contains several bugs that make individual outputs misleading.

**Reliability of stock results:**
Moderate. The pipeline runs correctly end-to-end, but specific issues reduce trust:
- The RSI fringe zone always mislabels itself as "rsi_healthy_range" in signal output
- The chart uses a different RSI formula than the analysis engine
- No data-staleness checks exist
- Hard-veto gates produce a score of exactly 0, discarding actual signal information

**Strongest parts:**
- Clean module separation (fetcher / technical / bot / db)
- Well-implemented 9-gate alert filter prevents alert spam
- `auto_adjust=True` on yfinance calls handles splits correctly
- In-memory + DB dual cooldown guard is robust
- Chart generation with fallback to text is production-quality

**Most serious problems:**
1. RSI fringe zone populates `triggered_signals` with wrong label (misleading output)
2. Chart RSI uses simple rolling mean; analysis engine uses Wilder smoothing — values diverge
3. `datetime.utcnow()` is deprecated in Python 3.12 (used in `db/database.py`)
4. SQLite UTC inconsistency between `was_alerted_recently` and `get_muted_symbols`
5. No fundamental analysis, no risk score, no confidence score
6. No backtesting system at all
7. No allowlist for who can use the bot (any chat ID can trigger analysis)

**Should you rely on it for research in its current state?**  
Yes, with caution. Use it as a momentum-screen first pass only. Do not rely on the signal labels in the triggered output as they can be misleading (RSI fringe bug). Always confirm signals manually before acting.

---

## 2. Backup and Recovery Status

| Item | Value |
|---|---|
| Original project path | `C:\Users\almog\Desktop\projects\StockSage_V2\stocksage` |
| Git available | Yes (git 2.50.1) |
| Already a Git repository | Yes |
| Original branch | `main` |
| Uncommitted changes existed | Yes — `.claude/settings.local.json` modified; `test_chart.png` and `test_chart.py` untracked |
| Recovery branch | `backup/pre-claude-review-20260617-1200` (created from `main` HEAD `cbb21b9`) |
| Working branch | `claude/stocksage-review-20260617-1200` |
| Original version recoverable | Yes — `backup/pre-claude-review-20260617-1200` is an exact copy of `main` at the time of review |

**Commands to return to original:**
```bash
git checkout main
# Recovery branch remains untouched:
git checkout backup/pre-claude-review-20260617-1200
```

---

## 3. Current Architecture

### Important files

| File | Role |
|---|---|
| `main.py` | Entry point: init DB → start agent thread → start bot (blocking) |
| `config.py` | All constants, watchlist, alert thresholds |
| `agent/core.py` | Background scheduler, alert logic, morning scan, formatters |
| `analyzers/technical.py` | All indicator calculations + scoring engine |
| `analyzers/chart_generator.py` | Plotly PNG chart generation (kaleido) |
| `data/fetcher.py` | yfinance wrappers: price, historical, bulk, 52-week, market-hours |
| `db/database.py` | SQLite CRUD: watchlist, trades, alerts, user_preferences |
| `bot/telegram_bot.py` | All Telegram command handlers + bilingual strings |
| `dashboard.py` | Streamlit web UI (independent process) |
| `test_fetch.py` | Integration smoke test |

### Empty stub files (no implementation)
`agent/decision_engine.py`, `agent/watchlist.py`, `analyzers/price_alerts.py`, `analyzers/sentiment.py`, `bot/formatters.py`, `data/news_fetcher.py`, `db/models.py`

### Execution flow

```
python main.py
    ├── init_db() + populate_from_config()       [SQLite seeded from config.WATCHLIST]
    ├── start_agent(token, chat_id)              [daemon thread]
    │       ├── Runs job() immediately on start
    │       ├── schedule.every(15).minutes → job()
    │       │       └── asyncio.run(_run())
    │       │               └── run_checks(bot, chat_id)
    │       │                       └── is_market_open()?
    │       │                               └── check_alerts(bot, chat_id)
    │       │                                       ├── get_watchlist() → all symbols
    │       │                                       ├── get_multiple_prices(symbols)  [bulk yfinance]
    │       │                                       └── for each symbol:
    │       │                                               Gate 1: price_change ≥ 0.5%
    │       │                                               Gate 2: in-memory dedup
    │       │                                               Gate 3: DB cooldown (2h)
    │       │                                               Gate 4: get_historical(1y)
    │       │                                               Gate 5: above EMA150
    │       │                                               Gate 6: RSI 45–68
    │       │                                               Gate 7: volume spike
    │       │                                               Gate 8: score≥65 + verdict in [BUY, STRONG BUY]
    │       │                                               Gate 9: last candle green
    │       │                                               → send_alert_with_chart()
    │       └── Morning scan at 16:35 IL → run_morning_scan()
    │               └── full_analysis() on all non-index symbols, top 5 by score
    └── run_bot(token)                           [blocking — main thread]
            └── ApplicationBuilder().run_polling()
                    └── CommandHandlers: /analyze /scan /watchlist /add /remove
                                         /trade /trades /summary /alerts /status /test /help /language
```

### Data flow

```
config.py WATCHLIST
    ↓ populate_from_config()
SQLite watchlist table
    ↓ get_watchlist()
Symbol list
    ↓ get_multiple_prices() [bulk yfinance 2d/1d]
Price snapshot (price, change_pct, volume, OHLC)
    ↓ Gate 1-3 filtering
Surviving symbols
    ↓ get_historical(symbol, "1y", "1d") [yfinance auto_adjust=True]
OHLCV DataFrame (251 rows typical)
    ↓ full_analysis(symbol, df, price)
{score, verdict, triggered_signals, ema150, rsi, macd, bb, atr, pivots, swings, stop_loss, take_profit}
    ↓ Gate 5-9 filtering
Alert-worthy symbols
    ↓ generate_chart_image() [plotly+kaleido → PNG bytes]
    ↓ bot.send_photo() or bot.send_message()
Telegram notification
    ↓ log_alert() → SQLite alerts table
```

### External services
- **yfinance** (Yahoo Finance) — price data, historical OHLCV. Free, rate-limited, unofficial API
- **Telegram Bot API** — notification delivery
- **SQLite** — local persistence at `db/stocksage.db`
- **Anthropic API key** loaded but **not used** anywhere in the codebase

---

## 4. Confirmed Bugs

| Severity | File and Function | Problem | Impact | Recommended Fix |
|---|---|---|---|---|
| **Medium** | `analyzers/technical.py:full_analysis` lines 315–317 | RSI fringe zone (35–44 and 66–75) appends `"rsi_healthy_range"` to `triggered_signals` and adds `+5`. The label is identical to the genuine healthy-range label, making it impossible to distinguish an ideal RSI from a borderline one | Alert messages and `/analyze` output show "RSI healthy" for stocks with RSI=40 or RSI=72, which is not healthy | Rename to `"rsi_fringe_zone"` and keep the `+5` score but update `_SIGNAL_LABELS` to reflect `"RSI acceptable"` |
| **Medium** | `db/database.py:get_muted_symbols` line 178 | Missing `'utc'` modifier in SQLite `datetime()` call: `datetime('now', ? || ' hours')` vs `was_alerted_recently` which uses `datetime('now', 'utc', ...)`. On servers where the system clock is not UTC, the cooldown window is calculated against local time | On non-UTC machines, `get_muted_symbols` returns incorrect results while `was_alerted_recently` is correct — they diverge | Change to `datetime('now', 'utc', ? \|\| ' hours')` |
| **Medium** | `data/fetcher.py:get_current_price` line 23 | `info.three_month_average_volume` can return `None` for indices (^VIX, ^GSPC, ^IXIC) and some ETFs. No null-guard. The returned dict contains `None` for `"volume"` | If downstream code tries to do arithmetic on the volume field, it crashes. Currently callers don't use this volume field critically, but it's a latent crash | Add `"volume": info.three_month_average_volume or 0` |
| **Medium** | `analyzers/chart_generator.py` lines 31–34 | RSI computed as simple rolling mean of gains/losses: `gain.rolling(14).mean()`. The `ta` library in `technical.py` uses Wilder's smoothing (EMA with `alpha=1/14`). The two formulas produce meaningfully different values, especially after recent price swings | The RSI shown on the chart does not match the RSI used for scoring/alerting, confusing the user | Use the same `ta.momentum.rsi(df["close"], window=14)` from the `ta` library in `chart_generator.py` |
| **Low** | `db/database.py:log_alert` line 158 | `datetime.utcnow()` is deprecated since Python 3.12 | Deprecation warning noise; will break in a future Python version | Replace with `datetime.now(timezone.utc).isoformat(timespec="seconds")` and add `from datetime import timezone` |
| **Low** | `config.py` lines 40–41 | `QQQ` appears in both `"מדדים"` and `"ETFs"` categories. `INSERT OR IGNORE` keeps only the first insertion's category | `/watchlist` shows QQQ under `"מדדים"` but never under `"ETFs"`; the ETFs category appears incomplete | Remove `QQQ` from one of the two categories |
| **Low** | `data/fetcher.py:get_multiple_prices` line 80 | Inner `_f()` converts NaN to `0.0`. If `prev_close` is NaN (returns 0.0), the `if prev_close` guard fires and sets `change_pct = 0.0`, masking a data-quality problem as a normal zero-change day | A symbol with no previous-day data silently appears as 0.0% change rather than being flagged as bad data | Return `None` for `change_pct` when `prev_close` is NaN and skip it in the alert gate |

---

## 5. Suspected Risks

| Severity | File and Function | Risk | Why It May Fail | How to Validate |
|---|---|---|---|---|
| **High** | `agent/core.py:check_alerts` lines 269–270 | Last candle green/red check reads `df.iloc[-1]` which is today's **incomplete** candle during market hours | A stock that opened up and is now pulling back has a "green candle" temporarily even if the close will be red | Validate by comparing alert candle with end-of-day close; consider using `df.iloc[-2]` (the confirmed last completed candle) |
| **High** | All | No allowlist for Telegram bot users. Any person who finds or guesses the bot token can call `/scan` (triggering full analysis across 75 symbols) or `/trade` (inserting records into the DB) | Bot tokens are sometimes leaked in git history or screenshots | Add a `AUTHORIZED_CHAT_IDS` list in config and check `update.effective_chat.id` at the start of every command handler |
| **Medium** | `data/fetcher.py` | No data freshness check. yfinance sometimes returns cached/delayed data silently, or returns data up to 24h old during market disruptions | Analysis runs on stale data without any warning | Check the last date in `df.index` against today's date; warn or skip if data is >1 trading day old |
| **Medium** | `agent/core.py:start_agent` | `asyncio.run()` is called in a loop every 15 minutes inside a daemon thread. Each call creates and destroys a new event loop. If `Bot.__aexit__` fails (e.g., network timeout), the httpx client session may leak file descriptors | Under sustained network instability, file descriptor exhaustion could crash the process | Add a top-level `try/finally` around `async with Bot(token) as bot` or add `asyncio.run_coroutine_threadsafe` with a shared loop |
| **Medium** | `analyzers/technical.py:check_ema150` | Called with any-length DataFrame. If `len(df) < 150`, `ta.trend.ema_indicator` returns a series where early values are NaN. `iloc[-1]` would still return a valid value for a 150-period EMA on a 100-bar series because `ta` uses EWM which starts from day 1 — but the EMA is unreliable with fewer bars | For new listings or small-cap stocks with limited history, EMA150 is inaccurate | Add `if len(df) < 150: return {"ema150": None, "above_ema150": False, "pct_from_ema": 0}` |
| **Medium** | `bot/telegram_bot.py:cmd_add` | No ticker validation before calling `add_to_watchlist`. Any string (including invalid tickers, SQL meta-characters, emoji) can be inserted | The DB uses parameterized queries (safe from injection), but garbage tickers clutter the watchlist and trigger failed API calls in every subsequent scan | Validate ticker format (alphanumeric + `-` + `.`) and optionally verify it with a quick `get_current_price()` call |
| **Low** | `agent/core.py:check_alerts` | `get_multiple_prices` performs a single bulk yfinance download of all 75 symbols. If Yahoo rate-limits or the request times out, the entire cycle silently returns no prices and no alerts fire | Yahoo Finance has undocumented rate limits; bulk downloads for 75 symbols occasionally fail | Log a warning when `prices` is empty or nearly empty after a bulk fetch |
| **Low** | `analyzers/technical.py:_stoch_rsi_bullish` | Broad `except Exception: return False` swallows all errors silently | A programming error in this function would appear as "Stoch RSI condition not met" rather than an exception | Narrow the exception to `(IndexError, ValueError)` |

---

## 6. Financial Methodology Review

| Severity | Indicator or Rule | Current Behavior | Problem | Recommended Change |
|---|---|---|---|---|
| **High** | RSI signal labels | Fringe RSI (35–44, 66–75) gets `"rsi_healthy_range"` label with +5 score | Misleading output — a stock with RSI=40 or 72 looks like it has a "healthy RSI" signal | Rename fringe condition to `"rsi_acceptable"` or `"rsi_fringe"` |
| **High** | Scoring — no fundamentals | 100% technical signals | For a "research tool," zero fundamental context is a significant gap. A technically strong stock with deteriorating earnings or negative cash flow gets the same score as one with strong fundamentals | Add at minimum: P/E ratio check, revenue trend direction, debt-to-equity flag via yfinance `ticker.info` |
| **High** | Incomplete candle in alert | Gate 9 reads today's open vs. close from the daily historical DataFrame | During market hours, today's candle is in progress. "Green candle" is checked against an intraday snapshot that changes throughout the day, not the final close | Use `df.iloc[-2]` for the green candle check (last completed daily candle), or explicitly skip if the last candle date == today |
| **Medium** | Rolling VWAP (20-period) | `ta.volume.VolumeWeightedAveragePrice` with `window=20` computes a rolling average | True VWAP resets at session open. A rolling 20-bar VWAP on daily data is a weighted moving average of prices, not a traditional intraday VWAP. This is a valid indicator but should be labeled differently | Rename to `above_vwma` (Volume-Weighted Moving Average) in signals and documentation |
| **Medium** | Volume spike | 1.5× 20-day average volume (excluding today) — correct window excludes the current bar | Reasonable, but the `window` slice is `df["volume"].iloc[-window - 1:-1]` (last 21 bars excluding current). This is correct. | No change needed — implementation is correct |
| **Medium** | EMA150 veto gate | Hard veto: price must be above EMA150, otherwise score = 0 | This is a reasonable trend filter for swing trading. However, setting score=0 discards all other valid signals. A stock that just dipped below EMA150 with all other signals firing gets the same output as one in a deep downtrend | Return the computed score with `verdict = "BELOW_TREND"` instead of zeroing it, so the user sees what the score would be |
| **Medium** | Stop-loss formula | `current_price - 1.5 × ATR` | Ignores swing lows, support levels, and recent structure. The calculated swing lows from `calc_swing_levels` are computed but never used in stop-loss placement | Consider using `max(nearest_support, current_price - 1.5×ATR)` for a structure-aware stop |
| **Medium** | No sector/index filter | Morning scan runs on all non-index/non-ETF symbols | During a broad market selloff (high VIX), the system may fire multiple BUY signals that all fail. No market-regime filter exists | Add a VIX filter: if VIX > 25 (or configurable threshold), suppress alerts or add a warning |
| **Low** | MACD lookback | Default MACD (12, 26, 9) on daily data | Standard parameters, correctly implemented via `ta.trend.MACD` | No change needed |
| **Low** | Bollinger Bands | Computed but never used in scoring | BB position is displayed in `/analyze` and dashboard but adds 0 points to the score | Consider adding +5 for `position == "near_lower"` as a mean-reversion signal, or +5 for `position != "above_upper"` as an overbought penalty |

---

## 7. Scoring-System Audit

### Current formula

```
Score = 0  (if below EMA150 → hard veto to score=0, verdict=NEUTRAL)
Score = 0  (if RSI < 35 → hard veto)
Score = 0  (if RSI > 75 → hard veto)

If all veto gates pass:
  +20  price above EMA150               [always fires if we reach here]
  +15  EMA150 > EMA200                  [long-term uptrend]
  +20  MACD bullish crossover (last 3 candles)
  +15  RSI 45–65                        [ideal zone]
   +5  RSI 35–44 or 66–75              [fringe zone — same label bug]
  +15  volume spike (current > 1.5× 20-day avg)
  +10  Stochastic RSI %K crossed above %D from below 0.3
   +5  price above rolling 20-period VWAP

Max achievable: 20+15+20+15+15+10+5 = 100 ✓
```

### Current weights
| Signal | Points | % of Max |
|---|---|---|
| Price above EMA150 | 20 | 20% |
| EMA150 > EMA200 | 15 | 15% |
| MACD bullish crossover | 20 | 20% |
| RSI ideal zone | 15 | 15% |
| RSI fringe zone | 5 | 5% |
| Volume spike | 15 | 15% |
| Stochastic RSI cross | 10 | 10% |
| Above VWAP | 5 | 5% |

### Verdict thresholds
| Score | Verdict |
|---|---|
| 0 (veto) | NEUTRAL |
| 1–34 | NEUTRAL |
| 35–54 | WATCH |
| 55–74 | BUY |
| 75–100 | STRONG BUY |

### Manual sample calculation — NVDA (2026-06-17)

From test output:
- RSI: 47.49 (ideal zone ✓ → +15)
- MACD crossover: "none" (no cross → 0)
- above_ema150: True → +20
- EMA150 (193.81) > EMA200 (189.29) ✓ → +15
- Volume spike: not triggered (0)
- Stoch RSI: not in triggered list (0)
- VWAP: 213.91, price 207.41 < VWAP → 0

**Manual total: 20 + 15 + 0 + 15 + 0 + 0 + 0 = 50**
**System output: 50** ✓ — calculation matches.

### Weaknesses
1. **Score of 0 from veto gates is indistinguishable from a genuine zero-signal stock** — the veto reason is invisible to the user
2. **MACD crossover is a "within 3 candles" signal** — this is a recency-biased spike signal, not a sustained condition; can fire and expire within 48h
3. **No penalty system** — a stock cannot score below 0 regardless of negative signals (bearish MACD divergence, falling volume, etc.)
4. **All signals are binary** — no partial credit for "almost" conditions creates cliff-edge score jumps
5. **No normalization across sectors** — a slow large-cap and a volatile small-cap are scored identically

### Recommended improved model
```
Technical trend score    (EMA alignment, price position)    20 pts
Momentum score           (MACD, Stoch RSI, RSI)             30 pts  
Volume confirmation      (volume spike, VWAP)                20 pts
Risk adjustment          (ATR%, drawdown, VIX regime)       -20 to 0 pts
Data quality score       (freshness, completeness)          confidence 0–1×
```
Present confidence alongside score rather than hiding data-quality issues.

---

## 8. Data-Quality Review

| Area | Current State | Issue | Recommendation |
|---|---|---|---|
| **Freshness** | No check | `get_historical` returns whatever yfinance gives — no validation of last-bar date against today | Validate `df.index[-1].date() >= (today - 1 business day)` before analysis |
| **Adjusted prices** | `auto_adjust=True` on all `yf.download` calls | Correctly handles splits and dividends | No change needed |
| **Missing values** | No NaN checks in `full_analysis()` | If yfinance returns a row with NaN close, `ta` library may produce NaN indicators; `float(series.iloc[-1])` on NaN returns `nan`; comparisons with `nan` silently return False | Add `df.dropna(subset=["close","high","low","open"])` before analysis |
| **API failures** | Exceptions caught and `None` returned | `get_current_price` and `get_historical` return `None` on error — callers check for this | Good practice. However, rate-limit errors are not distinguished from data-not-found errors |
| **Caching** | None | Every 15-minute cycle re-fetches 1 year of history for every symbol that passes Gates 1–3 | Add a local in-memory or file-based cache for historical data with a 6-hour TTL |
| **Timestamps** | Not stored with analysis results | Alert messages have a `triggered_at` timestamp in the DB but the analysis dict has no data-as-of timestamp | Add `data_as_of` key to `full_analysis()` result using `df.index[-1]` |
| **Corporate actions** | Handled via `auto_adjust=True` | Splits/dividends are adjusted. Ticker changes and delistings are not handled — the symbol just starts returning empty data | Implement a "symbol validation" step that flags symbols returning 0 rows for removal from watchlist |
| **Currency** | Not checked | Watchlist includes US-listed symbols only. All are USD. BTC-USD and ETH-USD include "-USD" suffix which yfinance handles | No issue for current watchlist |
| **Duplicate tickers** | QQQ appears in two categories | DB `INSERT OR IGNORE` silently de-duplicates but config is inconsistent | Fix in config.py |
| **Index symbols** | `^GSPC`, `^VIX`, `^DJI`, `^RUT`, `^IXIC` in watchlist | Volume data for indices is unreliable; `three_month_average_volume` may be None | Separate index symbols from equity symbols in scan logic (already partially done via `_SCAN_SKIP_CATEGORIES`) |

---

## 9. Bias and Backtesting Review

### Biases present

| Type | Status | Evidence | Notes |
|---|---|---|---|
| **Look-ahead bias** | Potential | Last candle in daily df is today's in-progress candle during market hours | Gate 9 checks `last_close > last_open` on an incomplete candle |
| **Survivorship bias** | Likely | Watchlist is manually curated — all symbols currently exist and trade | Historical performance of alert signals is never tested against delisted companies |
| **Selection bias** | Confirmed | Watchlist heavily weighted toward recent outperformers (comments like "+249% YTD", "+229%", "🔥") | Back-checking alerts against these symbols will show inflated success rates |
| **Data-snooping bias** | Potential | Thresholds (RSI 45–68, score≥65, volume 1.5×, MACD 3-candle lookback) appear to have been chosen based on intuition/observation | No documented out-of-sample validation period |
| **No backtesting** | Confirmed | Zero backtesting infrastructure exists | There is no `backtester.py`, no trade simulation, no historical signal replay |
| **Signal/execution timing** | Potential | Alerts fire during market hours and the user is expected to act on them — no execution delay is modeled | A 15-minute alert interval means entry price may differ from alert price |
| **Transaction costs ignored** | Confirmed | No slippage, spread, or commission modeling anywhere | Even a simple ATR-based stop system needs to account for bid-ask spread on entry |

### Backtesting plan (not implemented — awaiting approval)

1. Load daily OHLCV for each symbol over a defined historical period (e.g., 3 years)
2. For each day, apply `full_analysis()` using only data up to that day (point-in-time)
3. Record any day where score ≥ 65 and verdict ∈ {BUY, STRONG BUY}
4. Simulate entry at next-day open, exit at 1.5×ATR stop or 3×ATR target (whichever hits first)
5. Track: win rate, avg gain, avg loss, max drawdown, Sharpe, profit factor
6. Compare against buy-and-hold benchmark

**Critical constraint:** Must use `df.iloc[:-1]` (exclude in-progress candle) for every historical day to avoid look-ahead bias.

---

## 10. Risk-Management Review

### Current risk logic
- **ATR-based stop-loss** (`price - 1.5×ATR`) and take-profit (`price + 3×ATR`) — computed but never auto-enforced
- **RSI cap at 75** — prevents buying overbought stocks
- **Volume spike requirement** — Gate 7 prevents alert on low-liquidity moves
- **Cooldown guard (2h)** — prevents alert spam

### Missing risk controls

| Missing Control | Impact | Recommendation |
|---|---|---|
| No VIX regime filter | In high-fear markets (VIX > 25), momentum signals fail frequently | Add configurable `VIX_FEAR_THRESHOLD = 25`; suppress or downgrade alerts when ^VIX is above threshold |
| No position-size / portfolio risk | System alerts on any qualifying stock regardless of existing exposure | Out of scope for current tool but should be documented |
| No earnings-date awareness | Buying before earnings introduces binary event risk | Add `ticker.calendar` check; flag symbols within 5 days of earnings with a ⚠️ warning |
| No minimum volume / liquidity filter | Small-cap or low-volume stocks can have erratic signals | Add `ALERT_MIN_DAILY_VOLUME = 500_000` shares filter |
| No price floor | Penny stocks (< $1) have very different dynamics | Add `ALERT_MIN_PRICE = 5.0` to config and check in Gate 1 |
| No drawdown awareness | A stock in a -40% downtrend from its 52-week high can still score high if it recently bounced | Compute `pct_from_52w_high` and add penalty or warning if below -30% |
| Risk score separate from opportunity score | A risky stock with high momentum can achieve STRONG BUY without any warning | Implement a separate `risk_score` that does NOT contribute to the opportunity score |

---

## 11. Security Review

| Item | Status | Notes |
|---|---|---|
| `.env` in `.gitignore` | ✅ Protected | Telegram token, chat ID, API keys are not committed |
| `db/*.db` in `.gitignore` | ✅ Protected | SQLite database not committed |
| SQL injection | ✅ Safe | All database queries use parameterized statements (`?` placeholders) |
| Command injection | ✅ Safe | No `subprocess`, `os.system`, or `eval` with user input |
| Secrets in source code | ✅ Clean | No hardcoded credentials found in any `.py` file |
| Telegram bot access control | ❌ Missing | **Any Telegram user who knows the bot token can call `/scan`, `/trade`, `/add`, `/remove`**. The bot has no allowlist. `/trade` inserts records into the personal trade journal. `/add` modifies the watchlist. |
| Anthropic API key | ⚠️ Unused risk | Key is loaded in `config.py` and stored in memory but never used. If a future feature uses it carelessly (e.g., passes user input to the API), prompt injection could be a risk. |
| Input validation | ⚠️ Weak | `/add` and `/trade` accept any ticker string without format validation |
| Telegram message length | ⚠️ Unchecked | Alert messages could theoretically exceed Telegram's 4096-char limit if many signals fire; `caption` is truncated to 1024 chars for photos but plain text is not |
| Exception output | ✅ Safe | Exceptions are logged to stdout only, not sent to Telegram users |

**Immediate fix needed:** Add an authorized-user check to all command handlers:

```python
AUTHORIZED_CHAT_IDS = set(os.getenv("AUTHORIZED_CHAT_IDS", "").split(","))

async def _check_auth(update: Update) -> bool:
    if not AUTHORIZED_CHAT_IDS or AUTHORIZED_CHAT_IDS == {""}:
        return True  # backwards-compat: no restriction if not configured
    return str(update.effective_chat.id) in AUTHORIZED_CHAT_IDS
```

---

## 12. Testing Review

### Existing coverage
| Test | File | What it tests | Verdict |
|---|---|---|---|
| `test_fetch.py` | Integration smoke test | `init_db`, `get_historical`, `get_current_price`, `full_analysis` end-to-end for NVDA | Passes ✅ — but requires live internet and Yahoo Finance availability |
| `test_chart.py` | Chart generation | `generate_chart_image` for NVDA | Passes ✅ — creates `test_chart.png` |

### Missing tests (prioritized)

1. **Unit: RSI fringe zone label bug** — assert that `triggered_signals` for RSI=42 contains `"rsi_fringe_zone"` not `"rsi_healthy_range"` (confirms the bug exists and will verify the fix)
2. **Unit: scoring arithmetic** — mock df, assert score=50 for known conditions (deterministic, no network)
3. **Unit: `_volume_spike`** — test with volume exactly at threshold, below, and above
4. **Unit: `_macd_bullish_last3`** — test with crossover on bar -1, -2, -3, and no crossover
5. **Unit: `was_alerted_recently`** — mock `triggered_at` timestamps; assert True/False correctly
6. **Unit: `get_multiple_prices` with single-symbol case** — line 67 has a special case for `len(upper) == 1`; this path needs a test
7. **Unit: `full_analysis` veto gates** — test that below-EMA150 returns score=0 and correct verdict
8. **Unit: `full_analysis` with short history** — test with df of length < 150 (EMA accuracy)
9. **Integration: duplicate ticker** — assert QQQ appears exactly once in watchlist after `populate_from_config`
10. **Security: unauthorized Telegram access** — verify commands from unknown chat IDs are rejected (once auth is added)

### Test creation constraints
- All unit tests must use mocked DataFrames — no live yfinance calls
- Tests must not send real Telegram messages
- Tests must use an in-memory SQLite database, not the production `stocksage.db`

---

## 13. Performance Review

### Current performance profile

| Scenario | Behavior | Risk |
|---|---|---|
| **10 symbols** | ~2–3s per cycle (bulk price fetch + N individual historical fetches) | Fine |
| **75 symbols** (current size) | Gate 1–3 filter most symbols before the expensive Gate 4 fetch. Typically 1–5 symbols reach Gate 4 per cycle | Acceptable |
| **Morning scan** | Fetches 1y history for every non-index symbol (~60 symbols) sequentially | **Slow** — can take 60–120s. yfinance serial calls at 1–2s each = potential timeout |
| **1,000 symbols** | Gate 4 could fetch many histories sequentially; would take 15–30min per cycle | System would not complete within the 15-min interval |

### Bottlenecks

1. **Morning scan is sequential** — 60 `get_historical()` calls done one after another. Each call takes 1–3s on average. Total: 60–180s. During this time, the agent thread is blocked inside `asyncio.run()`, preventing the next scheduled job from starting on time.

2. **No historical data cache** — every alert cycle re-downloads 1 year of history for every symbol that passes Gates 1–3. If NVDA passes Gates 1–3 five times in one morning, it downloads 251 rows of history 5 times.

3. **`get_multiple_prices` is efficient** — single bulk yfinance call for all 75 symbols, correctly handled.

### Recommendations

1. **Cache historical DataFrames** in-process with a `{symbol: (df, fetched_at)}` dict. Evict after 6 hours or at end-of-day.

2. **Parallelize morning scan** using `asyncio.gather()` with a semaphore to limit concurrent requests:
   ```python
   sem = asyncio.Semaphore(5)
   async def fetch_one(symbol):
       async with sem:
           return await asyncio.to_thread(get_historical, symbol, "1y")
   ```

3. **Move morning scan to a separate low-priority background task** so it doesn't block the alert checker.

---

## 14. Recommended Architecture

### Proposed folder structure

```
stocksage/
├── config.py                    # all configuration and watchlist
├── main.py                      # entry point
│
├── data/
│   ├── fetcher.py               # yfinance wrappers (existing, needs freshness check)
│   ├── cache.py                 # NEW: in-process DataFrame cache with TTL
│   └── news_fetcher.py          # stub — future news/sentiment
│
├── analyzers/
│   ├── technical.py             # existing — fix label bug, add data-quality return
│   ├── fundamental.py           # NEW: yfinance ticker.info for P/E, revenue trend
│   ├── risk.py                  # NEW: ATR%, VIX regime, drawdown, earnings-date
│   ├── chart_generator.py       # existing — fix RSI formula
│   └── scoring.py               # NEW: combine technical + risk into final score
│
├── agent/
│   ├── core.py                  # existing — fix incomplete-candle bug
│   ├── gates.py                 # EXTRACT: the 9 gate functions from core.py
│   └── morning_scan.py          # EXTRACT: morning scan logic from core.py
│
├── bot/
│   ├── telegram_bot.py          # existing — add auth check
│   └── formatters.py            # IMPLEMENT: move _fmt_* functions here
│
├── db/
│   ├── database.py              # existing — fix UTC bug, fix deprecated utcnow
│   └── models.py                # stub — future ORM
│
├── tests/
│   ├── test_technical.py        # unit tests for scoring logic
│   ├── test_fetcher.py          # unit tests with mocked data
│   ├── test_database.py         # unit tests with in-memory SQLite
│   └── fixtures/                # sample DataFrames for deterministic tests
│
├── dashboard.py                 # existing
├── test_fetch.py                # existing smoke test
└── requirements.txt
```

### Module responsibilities
- **`data/cache.py`** — thread-safe dict: `{symbol → (df, datetime)}`, `get()` with TTL, `invalidate_all()` at end-of-day
- **`analyzers/fundamental.py`** — `get_fundamental_signals(symbol)` → P/E relative to sector, revenue growth direction, debt-to-equity flag
- **`analyzers/risk.py`** — `calc_risk_score(symbol, df, vix)` → independent risk score 0–100 (higher = riskier)
- **`analyzers/scoring.py`** — combine technical + fundamental + risk into `final_score`, `confidence` (based on data quality)
- **`bot/formatters.py`** — move all `_fmt_*` functions out of `core.py` and `telegram_bot.py`

---

## 15. Prioritized Improvement Plan

### Immediate (fix before next use)

| # | Task | Reason | Benefit | Complexity | Files |
|---|---|---|---|---|---|
| 1 | Fix RSI fringe-zone label | Misleading `triggered_signals` output that reaches the user | Correct signal display | Small | `analyzers/technical.py` |
| 2 | Fix `get_muted_symbols` UTC bug | Incorrect cooldown window on non-UTC machines | Correct alert suppression | Small | `db/database.py` |
| 3 | Fix chart RSI formula | Chart RSI differs from analysis RSI | Trust in chart vs. system consistency | Small | `analyzers/chart_generator.py` |
| 4 | Fix incomplete candle green-check | Look-ahead bias on in-progress daily candle | Correct directional signal | Small | `agent/core.py` |
| 5 | Add Telegram bot auth check | Any user can modify your watchlist and trade log | Security | Small | `bot/telegram_bot.py`, `config.py` |

### High Priority (needed for reliable daily use)

| # | Task | Reason | Benefit | Complexity | Files |
|---|---|---|---|---|---|
| 6 | Add data freshness check | Stale data produces misleading scores | Trust in analysis | Small | `data/fetcher.py`, `analyzers/technical.py` |
| 7 | Fix `datetime.utcnow()` deprecation | Will fail in future Python | Forward compatibility | Small | `db/database.py` |
| 8 | Fix duplicate QQQ in config | Inconsistent watchlist | Clean data | Trivial | `config.py` |
| 9 | Add minimum price/volume filters | Prevents analysis of penny stocks | Alert quality | Small | `config.py`, `agent/core.py` |
| 10 | Add VIX regime check | Prevents BUY alerts in market panic | Fewer false positives | Small | `agent/core.py`, `config.py` |
| 11 | Add earnings-date warning | Binary event risk warning | User awareness | Medium | `data/fetcher.py`, `agent/core.py` |
| 12 | Handle `three_month_average_volume` None | Latent crash for index symbols | Stability | Small | `data/fetcher.py` |
| 13 | Add NaN guard in `full_analysis` | Silent wrong scores from bad data | Correctness | Small | `analyzers/technical.py` |
| 14 | Add unit tests for scoring | Ensure fixes don't regress | Maintainability | Medium | `tests/test_technical.py` |

### Medium Priority (maintainability and architecture)

| # | Task | Reason | Benefit | Complexity | Files |
|---|---|---|---|---|---|
| 15 | In-process historical data cache | Avoids re-fetching 1y of data per cycle | Performance | Medium | `data/cache.py` |
| 16 | Parallelize morning scan | 60 sequential fetches is slow | Speed | Medium | `agent/core.py` |
| 17 | Move formatters to `bot/formatters.py` | `core.py` and `telegram_bot.py` are too large | Maintainability | Small | `bot/formatters.py` |
| 18 | Ticker input validation in `/add` | Garbage tickers in watchlist | Data hygiene | Small | `bot/telegram_bot.py` |
| 19 | Score 0 veto → informative NEUTRAL | Veto gates hide signal information | User understanding | Medium | `analyzers/technical.py` |
| 20 | Validate ticker format and existence | `/add` can add invalid symbols | Watchlist quality | Small | `bot/telegram_bot.py` |

### Optional (future features)

| # | Task | Benefit | Complexity | Score Impact |
|---|---|---|---|---|
| 21 | Add fundamental analysis module | Much richer stock research tool | Large | Yes — stock rankings change |
| 22 | Add risk score (separate from opportunity) | High-risk stocks marked explicitly | Large | Yes |
| 23 | Implement backtesting engine | Validate that signals actually work | Large | No (historical analysis only) |
| 24 | Add sector-relative scoring | Removes sector bias | Medium | Yes |
| 25 | Add `data_as_of` to analysis result | Transparency about data freshness | Small | No |
| 26 | Implement news sentiment | Additional signal dimension | Large | Yes |
| 27 | Port to async scheduler (APScheduler) | Cleaner than `schedule` + `asyncio.run()` loop | Medium | No |

---

## 16. Proposed Code Changes

The following are precise patches for the five Immediate-priority items. **Do not apply until you approve.**

### Fix 1 — RSI fringe-zone label (`analyzers/technical.py` lines 315–317)

```python
# CURRENT (BUGGY):
elif rsi < 45 or rsi > 65:   # fringe zone — veto already blocked < 35 and > 75
    score += 5
    triggered.append("rsi_healthy_range")

# PROPOSED FIX:
elif rsi < 45 or rsi > 65:   # fringe zone — veto already blocked < 35 and > 75
    score += 5
    triggered.append("rsi_acceptable_zone")
```

And in `agent/core.py` `_SIGNAL_LABELS`:
```python
"rsi_acceptable_zone": "RSI acceptable",   # ADD THIS LINE
```

### Fix 2 — `get_muted_symbols` UTC bug (`db/database.py` line 178)

```python
# CURRENT (BUGGY):
"WHERE triggered_at >= datetime('now', ? || ' hours')",

# PROPOSED FIX:
"WHERE triggered_at >= datetime('now', 'utc', ? || ' hours')",
```

### Fix 3 — Chart RSI formula (`analyzers/chart_generator.py` lines 31–34)

```python
# CURRENT (BUGGY — simple rolling mean):
delta = df["close"].diff()
gain  = delta.clip(lower=0).rolling(14).mean()
loss  = (-delta).clip(lower=0).rolling(14).mean()
rsi   = (100 - 100 / (1 + gain / loss)).tail(90)

# PROPOSED FIX — matches ta library Wilder smoothing:
import ta
rsi = ta.momentum.rsi(df["close"], window=14).tail(90)
```

### Fix 4 — Incomplete candle green-check (`agent/core.py` lines 269–270)

```python
# CURRENT (uses today's in-progress candle):
last_close = float(df["close"].iloc[-1])
last_open  = float(df["open"].iloc[-1])

# PROPOSED FIX — use last completed candle:
last_close = float(df["close"].iloc[-2])
last_open  = float(df["open"].iloc[-2])
```

### Fix 5 — Telegram auth check (`bot/telegram_bot.py` + `config.py`)

Add to `config.py`:
```python
# Comma-separated list of authorized Telegram chat IDs.
# Leave empty to allow all (backwards-compatible).
AUTHORIZED_CHAT_IDS: set[str] = set(
    filter(None, os.getenv("AUTHORIZED_CHAT_IDS", "").split(","))
)
```

Add to `bot/telegram_bot.py` (after imports, before handlers):
```python
from config import AUTHORIZED_CHAT_IDS

async def _check_auth(update: Update) -> bool:
    if not AUTHORIZED_CHAT_IDS:
        return True
    return str(update.effective_chat.id) in AUTHORIZED_CHAT_IDS

# Wrap each sensitive handler:
async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_auth(update):
        return
    # ... existing code ...
```

---

## 17. Revert and Recovery Instructions

### View all Claude branch changes
```bash
git log main..claude/stocksage-review-20260617-1200 --oneline
```

### Compare Claude branch with original main
```bash
git diff main claude/stocksage-review-20260617-1200
```

### View full commit history on Claude branch
```bash
git log claude/stocksage-review-20260617-1200
```

### Revert one specific commit (example: revert the review doc commit)
```bash
# Replace <HASH> with the actual commit hash
git revert <HASH>
```

### Revert ALL Claude commits (return working branch to state of main)
```bash
git checkout claude/stocksage-review-20260617-1200
git reset --soft main
git checkout main
```

### Return to original branch
```bash
git checkout main
```

### Verify original version via recovery branch
```bash
git checkout backup/pre-claude-review-20260617-1200
# This branch is identical to main at the time the review started
```

### Keep only selected commits (cherry-pick to main)
```bash
git checkout main
git cherry-pick <HASH-OF-SPECIFIC-FIX-COMMIT>
```

### Delete Claude branch (only after you confirm you no longer need it)
```bash
git branch -d claude/stocksage-review-20260617-1200
# Use -D instead of -d if Git refuses due to unmerged state
```

---

## 18. Final Scores

| Dimension | Score | Explanation |
|---|---|---|
| **Code quality** | 7/10 | Clean, readable Python. Good use of type hints in most places. Some functions are long (core.py). Broad `except Exception` in places. |
| **Architecture** | 6/10 | Clean separation of fetcher / analyzer / bot / db. The core.py is overloaded (scheduling, alert logic, formatting, morning scan). Stub files suggest unfinished design. |
| **Reliability** | 6/10 | The pipeline works end-to-end. Three confirmed bugs affect output correctness. No retry logic for yfinance failures. No graceful degradation under API outages. |
| **Test coverage** | 2/10 | Two integration tests that require live internet. Zero unit tests. Zero mocked tests. Any code change has no safety net. |
| **Security** | 5/10 | Secrets handled correctly (.env, .gitignore). SQL injection protected. Critical missing control: no Telegram bot access restriction. |
| **Data quality** | 5/10 | `auto_adjust=True` is correct. No data freshness validation. No NaN guards in analysis. Duplicate ticker in config. No staleness warning to user. |
| **Financial logic** | 5/10 | RSI, MACD, EMA, volume spike are correctly implemented. Zero fundamental analysis. Look-ahead bias risk on in-progress candle. Fringe RSI mislabeled. No risk score. |
| **Risk management** | 3/10 | ATR-based stop/target is good. No VIX regime filter, no earnings-date check, no liquidity filter, no position sizing, no portfolio-level risk. Risk not separated from opportunity score. |
| **Explainability** | 6/10 | `triggered_signals` list is useful. Stop-loss and take-profit are shown. But the RSI fringe label bug makes signals misleading. No explanation when veto gate fires. Score 0 from veto is uninformative. |
| **Production readiness** | 4/10 | Works for personal use. Not safe for multi-user use (no auth). No retry/backoff on API failures. No monitoring or alerting on system health. No backtesting to validate signals. |
