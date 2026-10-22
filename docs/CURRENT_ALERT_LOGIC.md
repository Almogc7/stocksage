# Current Alert Logic — Read-Only Audit

Audited at commit `4c50b72` (verified via `git rev-parse HEAD` =
`4c50b729970686ff88165c5d17cc1220d2cffcf6`), 2026-10-21.

This document describes what the code **actually does**, not what any other
document claims. Every statement below carries a `file:line` citation. All
file paths are relative to the repository root. In this worktree, **the
repository root is the `stocksage-webapp/` folder itself** — `agent/core.py`,
`analyzers/technical.py`, `config.py`, `data/fetcher.py`, `db/database.py`
all live directly under it, with no nested `stocksage/` subdirectory inside
it.

---

## 1. Alert pipeline in order

Entry point: `check_alerts(bot, chat_id)` — `agent/core.py:238-366`.

**Setup** (`agent/core.py:246-257`):
- `wl = get_active_watchlist()` (`agent/core.py:246`) → `db/database.py:626-642`:
  `SELECT symbol, category FROM watchlist WHERE wl_state = 'ACTIVE' AND enabled = 1
  ORDER BY category, symbol`, de-duplicated via a `seen` set before grouping by category.
- `all_symbols` flattened from `wl` (`agent/core.py:247`); early return if empty (`agent/core.py:248-249`).
- `prices = get_multiple_prices(all_symbols)` (`agent/core.py:251`) — see §7 for the exact fetch.
- Loop `for symbol, price_data in prices.items():` (`agent/core.py:253`); `continue` if `price_data` is falsy (`agent/core.py:254-255`).

**Gate 1 — price-change threshold** (inline, `agent/core.py:261-266`):
```
if change_pct < ALERT_MIN_PRICE_CHANGE: ... continue
```
`ALERT_MIN_PRICE_CHANGE: float = 0.5` — `config.py:433`. `change_pct = price_data["change_pct"]`
(`agent/core.py:257`), itself computed in `get_multiple_prices()` — see §4(b).

**Gate 2 — in-memory session dedup** (inline, `agent/core.py:269-275`):
`key = _session_key(symbol)` (`agent/core.py:269`, keyed `"{SYMBOL}_{UTC date}"`,
defined `agent/core.py:43-44`); `if key in _alerted_this_session: continue`.
This dict (`agent/core.py:40`) lives only in process memory — it resets on restart,
unlike Gate 3.

**Gate 3 — DB cooldown** (inline gate, delegated query, `agent/core.py:278-280`):
`if _in_cooldown(symbol): continue`. `_in_cooldown()` (`agent/core.py:84-86`) calls
`was_alerted_today(symbol)` — `db/database.py:1444-1453`:
```python
def was_alerted_today(symbol: str) -> bool:
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM alerts WHERE symbol = ?"
            " AND DATE(triggered_at) = DATE('now')"
            " LIMIT 1",
            (symbol.upper(),),
        ).fetchone()
    return row is not None
```
`DATE('now')` is SQLite's own UTC-based current date — once per symbol per UTC day.

**Data fetch + full_analysis() call** (delegated, `agent/core.py:283-291`):
```python
df = get_historical(symbol, period="1y")      # agent/core.py:283
if df is None: continue                        # agent/core.py:284-285
analysis = full_analysis(symbol, df, price_data["price"])   # agent/core.py:287
score, verdict, rsi, triggered = ...            # agent/core.py:288-291 (unpacked from analysis)
```
Everything from here through the score/verdict/triggered_signals values is computed
entirely inside `analyzers/technical.py` — see §2–§3.

**Candle selection** (precedes Gate 7, `agent/core.py:306-312`):
```python
if is_market_open() and len(df) >= 2:
    ref_candle = df.iloc[-2]
else:
    ref_candle = df.iloc[-1]
last_close = float(ref_candle["close"])
last_open  = float(ref_candle["open"])
```
This explicitly steps back to the last *completed* session when the market is open
(comment block at `agent/core.py:293-305` explains the incomplete-bar rationale).

**Gate 5 — score/verdict threshold** (inline comparison, delegated values,
`agent/core.py:317-322`):
```
if score < ALERT_MIN_SCORE or verdict not in ALERT_VERDICTS: ... continue
```
`ALERT_MIN_SCORE: int = 65` — `config.py:424`. `ALERT_VERDICTS: list[str] = ["BUY", "STRONG BUY"]`
— `config.py:430`. See §4(a) for why this is provably redundant at current values.

**Gate 6 — required signals** (inline check, delegated signal set,
`agent/core.py:326-332`):
```python
missing = [sig for sig in _REQUIRED_SIGNALS if sig not in triggered]
if missing: ... continue
```
`_REQUIRED_SIGNALS = ("rsi_healthy_range", "volume_spike")` — `agent/core.py:235`.

**Gate 7 — green candle** (inline, uses the completed candle from above,
`agent/core.py:334-338`):
```python
last_candle_green = last_close > last_open
if ALERT_REQUIRE_GREEN_CANDLE and not last_candle_green: ... continue
```
`ALERT_REQUIRE_GREEN_CANDLE: bool = True` — `config.py:449`.

**Fire path** (all 7 gates passed, `agent/core.py:344-366`):
- Log `[ALERT FIRE]` (`agent/core.py:344-347`).
- `message = _fmt_buy_alert(...)` (`agent/core.py:349`) — see §6.
- `chart_bytes = generate_chart_image(symbol, df, analysis)` (`agent/core.py:352`).
- If chart present: truncate caption to ≤1024 chars, `send_alert_with_chart(...)`
  (`agent/core.py:353-355`); else log `[CHART FAIL]`, `send_alert(...)` text-only
  (`agent/core.py:357-358`).
- Mark session dedup: `_alerted_this_session[key] = HH:MM` (`agent/core.py:361`).
- `log_alert(symbol, "BUY_SIGNAL", message, analysis=analysis, price_at_alert=price_data["price"])`
  (`agent/core.py:364-365`) → `db/database.py:1157-1206` (see §8 for the `current_price` note).

**Completed bars vs. in-progress bar — summary table:**

| Computation | Bar used | Citation |
|---|---|---|
| RSI | `iloc[-1]` (live if market open) | `analyzers/technical.py:109` |
| MACD crossover | `iloc[-1]`/`iloc[-2]` of MACD series (live-anchored) | `analyzers/technical.py:127-130` |
| Bollinger Bands | `iloc[-1]` (live if market open) | `analyzers/technical.py:152-155` |
| Volume spike | `iloc[-1]` (live if market open) | `analyzers/technical.py:54` |
| SMA150/200 rolling windows | include `iloc[-1]` in the window | `analyzers/technical.py:97,31` |
| Pivot points | explicitly `iloc[-2]` (last completed) | `analyzers/technical.py:205` |
| Gate 7 green-candle `ref_candle` | explicitly `iloc[-2]` if market open | `agent/core.py:306-309` |

*"Live if market open" in this table is **inferred from the code and known
`yfinance` library behavior, pending a live check** (see §9) — no live call
was made during this audit to directly observe an in-progress daily bar.
What is independently confirmed from the code is narrower: no function in
this pipeline performs any completeness check before indexing `iloc[-1]`.*

`full_analysis()` itself (`analyzers/technical.py:263-384`) has **no** market-open
awareness anywhere in its own body — only the two exceptions above (pivot points,
and the separate Gate 7 logic in `agent/core.py`) ever step back to `iloc[-2]`.

---

## 2. Every indicator

All in `analyzers/technical.py` unless noted.

- **RSI**: `ta.momentum.rsi(df["close"], window=period).iloc[-1]` — line 109,
  default `period=14` (signature line 107). Interpretation bands (not the window)
  are config-driven — see §3.
- **MACD**: `ta.trend.MACD(close)` (library defaults, not overridden) — lines 122-125,
  then `.macd()`, `.macd_signal()`, `.macd_diff()`. Bullish-crossover-in-last-3-candles
  helper `_macd_bullish_last3()` — lines 34-46 (hardcoded `range(3)` lookback, line 39).
- **SMA150 / SMA200**: plain pandas `.rolling(window=150).mean()` / `.rolling(window=200).mean()`
  — lines 97 and 31 respectively. **Simple**, not exponential (comment block lines 23-26
  documents this as intentional, matching the TradingView Pine Script source of truth).
- **Bollinger Bands**: `ta.volatility.BollingerBands(close, window=period)`, default
  `period=20` (signature line 147) — lines 150-155.
- **ATR**: `ta.volatility.average_true_range(high, low, close, window=period).iloc[-1]`,
  default `period=14` (signature line 178) — lines 180-182.
- **Volume spike**: hand-rolled, not a `ta` call — lines 49-55: `avg_vol =
  df["volume"].iloc[-window-1:-1].mean()` (20-day trailing average excluding today,
  `window=20` default, line 49) vs `curr_vol = df["volume"].iloc[-1]`; spike =
  `curr_vol > 1.5 * avg_vol` (`multiplier=1.5` default, line 55). An opt-in composite
  variant, `_volume_spike_composite()` (lines 58-64), exists but is unused by any live
  caller (`use_composite_volume` defaults to `False`, signature lines 263-264).
- **StochRSI** (computed, feeds scoring but not any gate): `ta.momentum.StochRSIIndicator(df["close"])`
  (default window) — line 70, `.stochrsi_k()`/`.stochrsi_d()`.
- **VWAP** (computed, feeds scoring but not any gate): `ta.volume.VolumeWeightedAveragePrice(...,
  window=window)`, default `window=20` (signature line 80) — lines 85-87.

See §3 and §4b for exactly which of these feed the score/gates and which don't.

---

## 3. How the 0–100 score is built

All within `analyzers/technical.py:316-372` (`score` starts at 0, line 324).

**Veto gates** (hard disqualifiers — force `score=0`, `verdict="NEUTRAL"`,
`triggered_signals=[]`, before any scoring runs):
```python
if not sma["above_sma150"]: return _base_result(0, "NEUTRAL", [])   # line 316-317
if rsi < RSI_VETO_MIN:       return _base_result(0, "NEUTRAL", [])   # line 318-319
if rsi > RSI_VETO_MAX:       return _base_result(0, "NEUTRAL", [])   # line 320-321
```
`RSI_VETO_MIN: int = 35` — `config.py:441`. `RSI_VETO_MAX: int = 75` — `config.py:442`.

**Scoring** (reached only if all three vetoes are survived):
| Points | Condition | Citation |
|---|---|---|
| +20 | price above SMA150 (always true here — veto already required it; kept "for score transparency," per comment) | lines 327-329 |
| +15 | SMA150 > SMA200 | lines 331-334 |
| +20 | MACD bullish crossover within last 3 candles | lines 336-339 |
| +15 | RSI in healthy band (`RSI_HEALTHY_MIN=45`–`RSI_HEALTHY_MAX=65`, `config.py:445-446`) | lines 343-345 |
| +5  | RSI in the "fringe" zone (outside healthy band but inside veto bounds — the `else` of the above) | lines 346-350 |
| +15 | Volume spike | lines 352-360 |
| +10 | Stochastic RSI bullish cross | lines 362-365 |
| +5  | Price above rolling VWAP | lines 367-370 |

Clamp: `score = max(0, min(100, score))` — line 372. Max possible sum
(20+15+20+15+15+10+5) = 100, consistent with the clamp.

**Verdict cutoffs** (lines 375-382):
```python
if score >= 75:    verdict = "STRONG BUY"
elif score >= 55:  verdict = "BUY"
elif score >= 35:  verdict = "WATCH"
else:              verdict = "NEUTRAL"
```
Confirmed: no other verdict string is ever produced by `full_analysis()`. Searching the
repo for `"WEAK BUY"` / `"AVOID"` / `"NO_BUY"` finds only stale, unreachable display-formatter
entries: `dashboard.py:148` (`"WEAK BUY": "🟠"`), `dashboard.py:150` (`"AVOID": "🔴"`),
`bot/telegram_bot.py:357` (`"WEAK BUY"`), `bot/telegram_bot.py:359` (`"AVOID"`). `"NO_BUY"`
does not appear as a verdict string anywhere.

**`triggered_signals`** — a plain `list[str]` (line 325), stored in the result dict at
line 305. Possible entries and their exact conditions: `"price_above_sma150"` (always,
post-veto, line 329), `"sma150_above_sma200"` (line 332-334), `"macd_bullish_crossover"`
(line 337-339), `"rsi_healthy_range"` (line 343-345), `"rsi_acceptable_zone"` (line 346-350),
`"volume_spike"` (line 354-360), `"stoch_rsi_bullish_cross"` (line 363-365), `"above_vwap"`
(line 368-370). On veto failure this list is forced to `[]` (lines 317/319/321) — no
signals are ever reported for a vetoed/NEUTRAL result.

---

## 4. Specific questions

### (a) Is the verdict gate redundant given `ALERT_MIN_SCORE`?

**Yes, unconditionally, at current configured values — not just "mostly."**

Both the verdict (§3) and `ALERT_MIN_SCORE` key off the *same* `score` variable, and
the verdict thresholds are monotonic in score:

- `score ≥ 75` → `STRONG BUY`
- `55 ≤ score < 75` → `BUY`
- `35 ≤ score < 55` → `WATCH`
- `score < 35` → `NEUTRAL`

Gate 5 requires `score ≥ ALERT_MIN_SCORE=65` (`config.py:424`) **and** `verdict in
ALERT_VERDICTS=["BUY","STRONG BUY"]` (`config.py:430`). Any `score ≥ 65` necessarily
lands in the `BUY` band (65–74) or `STRONG BUY` band (≥75) — both of which are already
members of `ALERT_VERDICTS`. To get `WATCH` or `NEUTRAL` the score must be `< 55`,
which already fails the score half of Gate 5 on its own. There is no score value for
which the score check passes and the verdict check fails, or vice versa, given these
constants. **The verdict-membership half of Gate 5 can never actually reject anything
the score half didn't already reject — it is dead weight at today's threshold values**
(it would only start doing independent work if `ALERT_MIN_SCORE` were lowered below 55,
or `ALERT_VERDICTS` were narrowed to exclude `"BUY"`).

### (b) How is price-change percent computed, and against what reference price?

From `get_multiple_prices()` (the function `check_alerts()` actually calls, not
`get_current_price()`) — `data/fetcher.py:83-97`:
```python
today = df.iloc[-1]             # line 83 — most recent row of a 2-day bulk download
prev  = df.iloc[-2]             # line 84
price = _f(today["Close"])      # line 89
prev_close = _f(prev["Close"])  # line 90
change_pct = ((price - prev_close) / prev_close * 100) if prev_close else 0.0   # line 91
```
So: `(today's daily-bar close − previous daily-bar close) / previous close × 100`, from
a bulk `yf.download(upper, period="2d", interval="1d", ..., group_by="ticker")`
(`data/fetcher.py:65`). This is **not** the session's opening price, and **not**
`fast_info.previous_close` (that alternate formula exists only in the unused
`get_current_price()`, lines 15-17 — a function `check_alerts()` never calls). During
market hours, `today["Close"]` is **inferred to be** today's partial/in-progress daily
bar close — this is known `yfinance` behavior but was not independently verified by a
live call in this audit (see §9) — so `change_pct` is a live, still-moving intraday
quantity, not a final day-over-day change.

### (c) How are stop and take-profit computed, and what R:R results?

Computed inside `full_analysis()`, not inline in `agent/core.py` —
`analyzers/technical.py:287-288`:
```python
stop_loss   = round(current_price - 1.5 * atr["atr"], 4)   # line 287
take_profit = round(current_price + 3.0 * atr["atr"], 4)   # line 288
```
`atr["atr"]` comes from `calc_atr()` (`ta.volatility.average_true_range(..., window=14)`,
lines 180-182). This gives a **fixed 1:2 risk/reward ratio regardless of score or
verdict** (1.5× ATR risk vs 3.0× ATR reward), consistent with the hardcoded
`"risk_reward": "1:2"` field (line 308) and the Telegram template's literal
`"⚖️ Risk/Reward: 1:2"` (`agent/core.py:114`). Note `calc_atr()` separately computes
and returns an **unused-by-the-alert** alternate set of stop/TP fields —
`stop_loss_1x`, `stop_loss_15x`, `take_profit_2x` (lines 197-199) — which get merged
into the result dict alongside the real `stop_loss`/`take_profit` fields (no key
collision, different names) but are not what the alert message or `alert_signals` DB
row actually uses.

### (d) Which signals are REQUIRED for an alert?

Only two, checked by exact membership in `triggered_signals`
(`_REQUIRED_SIGNALS = ("rsi_healthy_range", "volume_spike")`, `agent/core.py:235`,
enforced at Gate 6, `agent/core.py:326-332`). SMA150-vs-SMA200, MACD bullish crossover,
Stochastic RSI, and VWAP all contribute to the numeric score but are **not** individually
required — a symbol can alert without any of them firing, as long as their combined
point contribution (together with the two required signals and the always-true
SMA150 flag) clears `ALERT_MIN_SCORE=65`. See §4b for exactly which combinations do.

---

## 4b. What counts as passing

Every alerting symbol has a **fixed 50-point floor**, because three components are
either always true post-veto or explicitly required by Gate 6:
- `price_above_sma150`: +20, always true once the veto gate is survived (`technical.py:328-329`)
- `rsi_healthy_range`: +15, required by Gate 6 (`technical.py:343-345`)
- `volume_spike`: +15, required by Gate 6 (`technical.py:354-360`)
- **Floor = 50.**

The gap to `ALERT_MIN_SCORE=65` is **15 points**, which must come from the three
remaining, non-required, score-only components:

| Combination of non-required signals | Extra points | Total score | Clears 65? |
|---|---|---|---|
| none | 0 | 50 | No |
| `above_vwap` only (+5) | 5 | 55 | No |
| `stoch_rsi_bullish_cross` only (+10) | 10 | 60 | No |
| `stoch_rsi_bullish_cross` + `above_vwap` (+15) | 15 | 65 | **Yes** (exactly at floor) |
| `sma150_above_sma200` only (+15) | 15 | 65 | **Yes** (exactly at floor) |
| `macd_bullish_crossover` only (+20) | 20 | 70 | **Yes** |
| any combination summing to ≥15 of the three | ≥15 | ≥65 | **Yes** |

**Minimum extra points needed beyond the required signals: 15**, achievable by
`sma150_above_sma200` alone, `macd_bullish_crossover` alone, or
`stoch_rsi_bullish_cross` + `above_vwap` together. No other single or paired
combination of the three non-required signals reaches 15 except those listed.

**Indicators with no effect on the score or any gate:**
- **Bollinger Bands** — `calc_bollinger()` is computed and merged into the output
  dict (`**bb` at `analyzers/technical.py:297`) for display/formatting purposes only.
  It is never referenced anywhere in the scoring block (lines 316-372) or in any
  alert-path gate.
- **ATR's `volatility` label** (`"low"/"medium"/"high"`, `analyzers/technical.py:186-191`,
  196) — display-only. (ATR's *numeric* value is used, but only for stop/TP sizing —
  §4(c) — never for scoring.)
- **Pivot points** (`calc_pivot_points()`, lines 203-226) and **swing levels**
  (`calc_swing_levels()`, lines 229-258) — merged into the output dict, not referenced
  in scoring or any gate at all.

---

## 4c. Intraday behavior

The window 16:30–23:00 Israel time maps to US market hours 9:30am–4:00pm ET under
standard (non-DST-mismatched) alignment (IL = ET+7h). CLAUDE.md already flags that
this mapping breaks during US/IL DST-transition weeks — `is_market_open()` itself
(`data/fetcher.py:142-148`) does its own independent ET-based check (strict
`9:30:00`–`16:00:00` window, weekday only, no holiday calendar) and does not depend
on the IL-time constants used for the morning scan.

- **Which bar is `iloc[-1]`**: in `get_historical()`'s daily-interval frame — what
  `full_analysis()` actually receives (`agent/core.py:283`, `period="1y"`) — during
  this window `df.iloc[-1]` is **inferred from the code and known `yfinance` library
  behavior, pending a live check**, to be today's incomplete/in-progress daily bar:
  yfinance is known to post a partial row for the current session before market
  close, but this audit made no live call to directly confirm it. What IS confirmed
  directly from the code: nothing in `get_historical()` (`data/fetcher.py:40-56`) or
  in `full_analysis()`'s indicator helpers (RSI/MACD/Bollinger/volume-spike, all
  `.iloc[-1]`-anchored, see §1's table) checks for or corrects bar-completeness at all
  — so if the inferred behavior is accurate, nothing here would catch it.
- **Volume-spike comparison**: `curr_vol = df["volume"].iloc[-1]` — today's *partial,
  still-accumulating* volume (`analyzers/technical.py:54`) — is compared against
  `avg_vol = df["volume"].iloc[-window-1:-1].mean()`, the prior 20 **complete** trading
  sessions (`analyzers/technical.py:49,53`). Mechanically, this biases the check toward
  false negatives early in the session (a partial day's volume is naturally lower than
  a full-day historical average) and toward true positives later as volume accumulates
  — the same symbol, same price, can flip from "no spike" to "spike" purely as the
  clock advances through the session.
- **Can MACD crossover / RSI change within one session for the same symbol?** Yes.
  `check_alerts()` re-fetches and re-runs `full_analysis()` on every
  `CHECK_INTERVAL_MINUTES=15` tick (`config.py:510`), each time against the
  live-updating `iloc[-1]` bar. A symbol can fail Gate 6 (required signals) on one
  15-minute cycle and pass it on a later cycle the same day purely from the
  in-progress bar's close/volume moving, with no new calendar day involved. Gates 2
  and 3 (session dedup, DB cooldown) only block a *second* alert after one has already
  fired that day — they do nothing to prevent this pre-alert oscillation.

---

## 5. The morning scan

Entry point: `run_morning_scan(bot, chat_id)` — `agent/core.py:195-227`.

**Trigger** (scheduler-side, `_is_morning_scan_time()`, `agent/core.py:435-442`):
```python
def _is_morning_scan_time(last_scan_date) -> bool:
    now = datetime.now(_IL_TZ)
    if now.weekday() >= 5:            # Sat=5, Sun=6
        return False
    if last_scan_date == now.date():  # already fired today
        return False
    return now.hour == MARKET_OPEN_HOUR_IL and now.minute >= MARKET_OPEN_MIN_IL
```
`MARKET_OPEN_HOUR_IL: int = 16` — `config.py:506`. `MARKET_OPEN_MIN_IL: int = 35` —
`config.py:507`. Invoked from the scheduler's `loop()` (`agent/core.py:456-458`).

**Filters** (`agent/core.py:197-221`):
- `wl = get_active_watchlist()`, flattened to `eligible` (lines 197, 201).
- Per symbol: fetch `df = get_historical(symbol, period="1y")`, skip if `None`
  (lines 207-209); fetch `price_data = get_current_price(symbol)`, skip if falsy
  (lines 210-212); `analysis = full_analysis(...)` (line 213).
- Score filter: `if analysis["score"] >= SCAN_MIN_SCORE: results.append(analysis)`
  (lines 214-215). `SCAN_MIN_SCORE: int = 50` — `config.py:500`.
- Per-symbol exceptions caught and skipped with a `[SCAN SKIP]` log (lines 216-218).
- Sort descending by score (line 220); truncate to `SCAN_TOP_N` (line 221).
  `SCAN_TOP_N: int = 5` — `config.py:503`.
- **Dead code note**: `_SCAN_SKIP_CATEGORIES = {"מדדים", "ETFs"}` is defined
  (`agent/core.py:192`) but never referenced anywhere else — no category filter
  actually runs here (ETFs/indices are already excluded from `ACTIVE` membership
  by the watchlist lifecycle, per the comment at lines 199-200).

Output fields — see §6.

---

## 6. Message contents, field by field

### BUY_SIGNAL alert — `_fmt_buy_alert()` (`agent/core.py:94-117`), called at line 349:
1. Header: `🚨 התראה — {symbol} {verdict} [{score}/100]` (line 107)
2. Separator (19× `━`, line 91/108)
3. Price line: `💰 מחיר: ${price:,.2f} ({sign}{change_pct:.1f}% היום)` (line 109) —
   `price` is the raw `price` argument passed at the call site (the live fetcher
   price, `price_data["price"]`), not `analysis["current_price"]` (see §8).
4. Score/RSI line: `📊 ציון: {score}/100 | RSI: {rsi:.1f} ✅` (line 110)
5. Trend/volume line: `📈 מעל SMA150 ✅ | נפח גבוה {vol_tag}` (line 111), `vol_tag`
   = `"✅"` if `"volume_spike" in triggered` else `"❌"` (line 104)
6. Signals line: translated labels from `triggered_signals` via `_SIGNAL_LABELS`
   (lines 100-102, 137-146), emitted line 112
7. Stop/TP line: `🛑 Stop: ${analysis['stop_loss']:,.2f} | 🎯 TP: ${analysis['take_profit']:,.2f}` (line 113)
8. Fixed R:R literal: `⚖️ Risk/Reward: 1:2` (line 114)
9. Separator repeat (line 115)
10. Footer CTA: `💡 /analyze {symbol} לניתוח מלא` (line 116)

Delivery: photo caption via `send_alert_with_chart()` (lines 59-79, called at line
355) truncated to 1021 chars + `"..."` if over 1024 (Telegram caption limit, line
354); else plain text via `send_alert()` (lines 49-56, called at line 358).

### Morning scan message — `_fmt_morning_scan()` (`agent/core.py:152-176`):
1. Title line (line 158, `_SCAN_STRINGS[lang]["title"]`)
2. Separator (line 159)
3. Time + static "Market Open" label: `🕑 {HH:MM} | {market_open label}` (line 160)
   — the label is a static string, **not** re-verified against `is_market_open()`
   inside this formatter.
4. Blank line (line 162)
5. Per result (ordered by score desc, truncated to `SCAN_TOP_N`):
   - Medal/rank line: `{medal} {symbol} — {verdict} [{score}/100]` (line 169),
     medal from `_MEDALS = ["🥇","🥈","🥉"]` (line 148) else `"{i+1}."`
   - Signals line: translated `triggered_signals`, or `"—"` if none (lines 166-167, 170)
   - Stop/TP line: `🛑 Stop: ${r['stop_loss']:,.2f} | 🎯 TP: ${r['take_profit']:,.2f}` (line 171)
   - Blank line (line 172)
6. Trailing separator (line 174)
7. Footer CTA: `💡 /analyze SYMBOL {full_analysis label}` (line 175)

If `results` is empty, a static no-results string is sent instead (lines 180-181,
122-135).

---

## 7. Runtime environment

**Dependency manifest**: `requirements.txt` lists 16 bare package names with **no
version pins at all**:
```
anthropic, yfinance, pandas, numpy, ta, python-telegram-bot, python-dotenv,
sqlalchemy, requests, textblob, newsapi-python, aiohttp, schedule, streamlit,
plotly, kaleido
```
No `pyproject.toml`, `setup.py`, `poetry.lock`, or `Pipfile.lock` exists in the
repo. Any claimed production version numbers (e.g. for `ta`, `pandas`, `numpy`,
`yfinance`, `python-telegram-bot`) are **not verifiable from this repository** —
they can only reflect the actually-installed environment on the deployed server
(e.g. `pip freeze` there), not anything checked in here.

**Every yfinance call used by the live bot** — all in `data/fetcher.py`, each a
single `try/except Exception` with **no retry/backoff logic anywhere in the file**:

| Function | Call | Params | Used by alert path? | Error handling |
|---|---|---|---|---|
| `get_current_price()` (lines 10-37) | `yf.Ticker(symbol).fast_info` (12-13) | snapshot, no period/interval | **Not by `check_alerts()`, but yes by the morning scan** — `run_morning_scan()` calls it per-symbol at `agent/core.py:210` (§5); also used by other bot handlers not traced here | `print()` warning (line 36), returns `None` |
| `get_historical()` (lines 40-56) | `yf.download(symbol, period=period, interval=interval, progress=False, auto_adjust=True)` (46) | default `period="6mo", interval="1d"`, but **`check_alerts()` calls with `period="1y"` explicitly** (`agent/core.py:283`) | **Yes** — the frame `full_analysis()` computes RSI/MACD/Bollinger/volume-spike against | empty-frame check (47) + `print()` + `except` (54-56), returns `None` |
| `get_multiple_prices()` (lines 59-112) | `yf.download(upper, period="2d", interval="1d", progress=False, auto_adjust=True, group_by="ticker")` (65) | bulk, all watchlist symbols at once | **Yes** — the call `check_alerts()` uses for price/`change_pct` (`agent/core.py:251`) | bulk-exception returns all-`None` dict (66-68); per-symbol parse exception sets only that symbol to `None` and continues (108-110) |
| `get_52week_high_low()` (lines 115-139) | `yf.download(symbol, period="1y", interval="1d", progress=False, auto_adjust=True)` (117) | single symbol | **No** — not called anywhere in the `check_alerts()`/morning-scan path | `print()` + `except` (137-139), returns `None` |

Both of the two functions actually used by the live alert path
(`get_historical`, `get_multiple_prices`) are, **inferred from the code and known
`yfinance` library behavior (pending a live check, see §9)**, capable of returning
today's incomplete daily bar as their last row while the market is open, since
neither does any completeness filtering. What IS directly confirmed from the code:
the absence of any such check in either function body, and the dedicated workaround
that exists only in `agent/core.py` (Gate 7's `ref_candle` selection, §1) rather than
in `fetcher.py` itself.

`is_market_open()` (`data/fetcher.py:142-148`) is not a yfinance call but is the
sole gate anywhere in `agent/core.py` that reacts to bar-completeness (used only
for Gate 7's candle selection, `agent/core.py:306`): a strict ET `9:30:00`–`16:00:00`
window, weekday check only, **no US holiday calendar**.

All error logging in `data/fetcher.py` goes through bare `print()` (lines 36, 48,
55, 67, 109, 119, 138), not the `stocksage.*` logger namespace used elsewhere
(e.g. `agent/core.py`'s scheduler jobs).

---

## 8. Discrepancies between CLAUDE.md and the code

- **Test file count**: CLAUDE.md states "28 unittest-based test files"; the actual
  `tests/` directory (excluding `__init__.py`/`fixtures.py`) contains **39** test
  files. Stale figure.
- **`current_price` overwrite bug**: CLAUDE.md's description is **confirmed accurate**.
  `_base_result()` (`analyzers/technical.py:290-313`) sets `"current_price": current_price`
  first (line 293), then spreads `**bb` afterward (line 297); `calc_bollinger()`
  itself returns a `"current_price"` key (line 173, `round(price, 4)` where
  `price = float(close.iloc[-1])` — the last close, not the passed-in live price).
  In a Python dict literal, a later key wins, so `bb["current_price"]` silently
  overwrites the original value. The workaround is correctly reflected in
  `agent/core.py:362-365` (passing `price_at_alert=price_data["price"]` explicitly
  rather than `analysis["current_price"]`) and is unit-tested
  (`tests/test_alert_signals.py`, `test_log_alert_uses_live_price_not_analysis_current_price`).
- **Everything else checked** — RSI single-definition / no independent alert-loop
  RSI threshold, cooldown semantics, morning-scan constants, incomplete-bar
  handling description, `ALERT_MIN_SCORE`/`ALERT_VERDICTS`/`ALERT_MIN_PRICE_CHANGE`/
  `ALERT_REQUIRE_GREEN_CANDLE`/`CHECK_INTERVAL_MINUTES` values, "only RSI bands are
  config-driven" claim (confirmed: MACD/Bollinger/ATR/volume-spike parameters are
  all hardcoded Python defaults in `analyzers/technical.py`, e.g. `period: int = 20`
  for Bollinger at line 147) — all **confirmed accurate**, no further discrepancies found.
- **Dependency versions** — not a contradiction, but flagged: CLAUDE.md's specific
  version claims (`ta 0.11.0`, `pandas 3.0.3`, `numpy 2.5.1`, `yfinance 1.5.1`,
  `python-telegram-bot 22.8`) are **unverifiable from the repository**, since
  `requirements.txt` pins nothing (§7).

---

## 9. Behaviors I could not determine from the code

- **Whether yfinance's daily-interval download actually includes an in-progress bar
  for the current session while the US market is open.** Every claim in this document
  that depends on this (§1's completed/live-bar table, §4(b), §4c, §7) is labeled
  **"inferred from the code and known `yfinance` library behavior, pending a live
  check"** rather than confirmed — this audit made no live network call to observe it
  directly. What IS confirmed directly from the code, independent of this assumption,
  is narrower: no function in the alert path performs any bar-completeness check
  before indexing `iloc[-1]`, so *if* the inferred behavior is accurate, nothing here
  would catch it.
- The actual installed dependency versions on the deployed server (no pins in the
  repo; this is server-only knowledge, not something a read-only code audit can confirm).
- Whether the morning-scan message's static "Market Open" label (§6) is ever
  observably wrong in a live run — it's structurally capable of drifting from
  `is_market_open()`'s actual answer since it's never cross-checked, but confirming
  a real-world occurrence would require running the bot, which is out of scope here.
- Anything that would require live network access, a running bot process, or
  database state to observe (explicitly excluded by the read-only constraint of
  this audit).

---

## 10. Which unit tests cover which behavior

| Behavior | Covered by |
|---|---|
| RSI healthy range / veto logic | `tests/test_rsi_label.py` (`TestRsiLabel`: `test_rsi_45_ideal_lower_boundary`, `test_rsi_65_ideal_upper_boundary`, `test_rsi_34_below_minimum_veto`, `test_rsi_76_above_maximum_veto`, `test_fringe_rsi_score_is_five_points_not_fifteen`) |
| Volume spike detection | `tests/test_volume.py` (`TestVolumeSpikeHelper`: threshold-exact, missing-column, zero-volume, insufficient-history cases) |
| Green candle gate / completed-bar candle selection | `tests/test_alert_gates.py` (`test_skips_on_red_candle_when_market_closed`, `test_uses_previous_candle_when_market_open`); `tests/test_incomplete_candle.py` (`TestIncompleteCandleSelection`, `TestProductionCodeUsesCorrectIndex`) |
| Alert cooldown / `was_alerted_today()` | `tests/test_sqlite_cooldown.py` (`TestWasAlertedToday`: `test_alert_now_blocks`, `test_alert_yesterday_does_not_block`, `test_case_insensitive_symbol_lookup`, `test_log_alert_immediately_blocks`); `tests/test_alert_gates.py:test_skips_when_already_alerted_today` |
| Price-change percent gate | `tests/test_alert_gates.py:test_skips_on_insufficient_price_change_without_analyzing` |
| "No independent RSI threshold in the alert loop" | `tests/test_alert_gates.py:test_no_independent_rsi_threshold_in_loop` |
| `log_alert()` / `alert_signals` writes, incl. the `current_price` workaround | `tests/test_alert_signals.py` (`test_log_alert_returns_id_and_writes_signals_row`, `test_log_alert_uses_live_price_not_analysis_current_price`, `test_log_alert_without_analysis_is_backward_compatible`, `test_signals_write_does_not_affect_cooldown`, `test_first_barrier_hit_check_constraint`) |
| **Verdict score cutoffs at the actual 55/75 boundaries** | **No test found.** `test_rsi_label.py` exercises `full_analysis()` but only ever reaches score 0, 25, or 35 — the BUY (≥55) and STRONG BUY (≥75) boundaries are never actually asserted against real scoring logic; other tests referencing "BUY"/"STRONG BUY" pass the verdict in as a hardcoded mock value. This is directly load-bearing, since `ALERT_MIN_SCORE=65` sits strictly between the two untested boundaries. |
| **Stop-loss/take-profit ATR formula** (`1.5×`/`3.0×` ATR) | **No test found.** Tests only consume pre-set `stop_loss`/`take_profit` fixture values; no test asserts the multiplier math itself. |
| **Morning scan filtering** (`SCAN_MIN_SCORE`, `SCAN_TOP_N`, end-to-end) | **No test found.** No test file references `run_morning_scan`, `_fmt_morning_scan`, `SCAN_MIN_SCORE`, or `SCAN_TOP_N`. |
| **The `asyncio.run()` sync→async bridge** in `start_agent()` | **No test found** for the bridge wrapper itself (fresh `Bot` per tick inside `asyncio.run()`, exception containment). `test_alert_gates.py` drives the async `check_alerts()` coroutine directly via its own event-loop helper, which tests gate logic but not the bridge pattern. |
