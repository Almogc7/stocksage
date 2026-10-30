# Swing Setup Spec — Design Document (v1, docs only)

This document specifies a new, separate, advisory-only swing-trading setup
system: long-only, held 2-6 weeks (10-30 trading sessions). It is docs-only —
no code was written or changed, no network or market data was accessed, and
no database was touched to produce it. Every numeric default below is marked
**"reasoned default"** with a one-line rationale; none was chosen by
observing data, per the constraint this document was built under. All file
paths are relative to the repo root (`stocksage-webapp/`).

This is a **new, fifth, parallel observation-mode stack** — same posture as
the existing composite engine (`CLAUDE.md`'s "Parallel scoring engine"
section): additive, logging-first, not wired into any existing alerting path
until the evaluation plan (§13) says otherwise.

---

## 1. Scope and non-goals

**In scope:**
- Long-only equities, EOD setup identification on completed daily bars.
- A separate intraday job that only checks whether price has crossed an
  already-identified setup's trigger — it never creates new setups.
- Advisory Telegram messaging (bilingual).
- Additive logging of every candidate considered (fired or not), plus
  outcome tracking by first-barrier-hit as R-multiples.

**Non-goals (explicit):**
- No execution or order placement of any kind.
- No short setups, no options.
- No intraday setup *creation* — only trigger-checking intraday.
- No position management/trailing beyond what's needed to compute and log
  the outcome (this is not `analyzers/position_management.py`'s job, nor
  does this spec extend that module).
- No changes to the existing Stack A alert pipeline (`agent/core.py`) or the
  composite engine (`analyzers/composite.py`) **for the new setup system's
  own feature work**: this system reuses pure helper functions from
  `analyzers/technical.py` and config constants from `config.py`, but never
  calls into or mutates Stack A's or the composite engine's own state
  (in-memory dedup, DB cooldown tables, scheduler loop). **This non-goal
  governs new feature work, not bug fixes.** It does not forbid fixing an
  existing defect in Stack A — PR 0a (§14) is exactly such a fix, justified
  by a gap `docs/CURRENT_ALERT_LOGIC.md` §1 already documents (the alert
  loop has no per-symbol error handling of its own), not by anything this
  new setup system needs for itself.

---

## 2. Universe

**Live scan universe — recommended default: all enabled stock symbols, same
as the replay universe (§13), pending owner confirmation (§15).** Rationale:
(1) **one bulk download a night** — scanning the full enabled-stock universe
costs one additional batch call, not one per symbol, since `get_multiple_prices()`
and `get_historical()`-style bulk fetches already scale to a symbol list, not
a single symbol; (2) **replay equals live** — if the live scan used a
narrower ACTIVE-tier universe while the replay (§13) used the full universe,
the evaluation would be validating a strategy on a different population than
the one it actually runs against, undermining the entire evaluation plan's
purpose. The existing liquidity floors (§4) remain the real gating mechanism
on this broader universe, not tier membership — a symbol still needs to
clear `ELIGIBILITY_MIN_AVG_VOLUME`/`ELIGIBILITY_MIN_DOLLAR_VOL`/`ELIGIBILITY_MIN_PRICE`
regardless of its watchlist tier. Each generated setup/message additionally
**shows an ACTIVE-tier flag** (whether the symbol happens to currently be
ACTIVE on the existing watchlist) purely as informational context, not as a
filter. This is presented as the recommended default, not yet settled —
confirm with the owner before implementation (§15).

**"All enabled stock symbols" — precise definition (not just "whatever
`get_watchlist()` returns"):**

```sql
wl_state IN ('ACTIVE', 'MONITOR')
AND category NOT IN ('מדדים', 'ETFs')
AND enabled = 1
```

- **`wl_state`**: restricting to `ACTIVE`/`MONITOR` directly excludes
  `ETF_INDEX_CONTEXT` (the lifecycle's own dedicated state for index/ETF
  symbols), `TEMPORARILY_INELIGIBLE`, and `USER_REMOVED` rows — this is
  **narrower** than the existing `get_watchlist()` (`db/database.py:611-623`:
  `SELECT symbol, category FROM watchlist WHERE enabled = 1 ORDER BY category, symbol`),
  which filters only on `enabled = 1` and does **not** filter or even select
  `wl_state` at all. `get_watchlist()` cannot be reused as-is for this
  definition — it would need a **new**, additive query (modeled on
  `get_active_watchlist()`'s existing shape, `db/database.py:626-642`:
  `SELECT symbol, category FROM watchlist WHERE wl_state = 'ACTIVE' AND enabled = 1`,
  simply widened to `wl_state IN ('ACTIVE','MONITOR')`), not a modification of
  either existing function.
- **Category exclusion, cited exactly**: `config.py:60` (`"מדדים"`, the
  Hebrew-named indices category in the `WATCHLIST` seed dict) and
  `config.py:74` (`"ETFs"`) are the two category keys to exclude.
- **A stronger, code-enforced alternative worth combining with the category
  check**: `services/watchlist_evaluator.py:100` defines
  `_NON_STOCK_TYPES = frozenset({"etf", "index", "crypto"})`, checked against
  each row's `security_type` (populated by `classify_security_type()`,
  `analyzers/eligibility.py:30-38`, which returns `'index'`/`'etf'`/`'crypto'`/`'stock'`
  per-symbol, not per-category). This is more precise than category-string
  matching — a symbol could in principle sit in a differently-named or
  mixed category while still being classified `'etf'`/`'index'` by
  `security_type` — so the recommended default **excludes a row if either**
  its category is in `{"מדדים", "ETFs"}` **or** its `security_type` is in
  `_NON_STOCK_TYPES`, logging every excluded row with an explicit reason
  (`EXCLUDED_WL_STATE`, `EXCLUDED_CATEGORY`, or `EXCLUDED_SECURITY_TYPE`, §12).
  **Implementation note (PR A, "English category names" — code only, no
  `WATCHLIST` key renamed yet):** `setups/universe.py`'s exclusion set is
  now held in its canonical English form (`{"Indices", "ETFs"}`), and the
  comparison is made via `config.canonical_category(category)` rather
  than a raw equality check — so a row stored under either `"מדדים"` or
  `"Indices"` is excluded identically. The citation above is unaffected;
  the Hebrew string is still a real `WATCHLIST` key today, just no longer
  the literal the code compares against directly.
- **Removes the dependency on the 02:30 IL watchlist evaluation finishing**
  (§3): since this universe is read directly from the `watchlist` table's
  current `wl_state`/`category`/`security_type` values at scan time, rather
  than from any derived or cached membership list, the setup scan no longer
  needs to wait for that separate nightly job to complete first — it simply
  reads whatever the table says at the moment it runs. (The *other* reason
  for staggering the setup scan's own timing — avoiding resource contention
  with `log_composite_signals.py`'s 01:30 IL job, §3 — still applies
  independently of this.)

**Reasoned default — stocks only**: restrict to stock categories, excluding
ETF/index categories as defined precisely above, mirroring the existing
`/pin` command's "stocks only" rule (per `CLAUDE.md`'s pin description).
Rationale: swing structural stops/targets on indices/ETFs behave differently
(generally lower volatility, different resistance dynamics), and the
liquidity floors reused in §4 (`ELIGIBILITY_MIN_AVG_VOLUME`,
`ELIGIBILITY_MIN_DOLLAR_VOL`) were tuned for single stocks, not funds.

**Replay universe matches the recommended live universe above exactly**: the
historical replay (§13) uses the same `wl_state`/category/`security_type`
definition. Rationale: the ACTIVE tier is itself the output of the
watchlist lifecycle's own promotion/demotion logic
(`services/watchlist_evaluator.py`); restricting the replay sample to
symbols that were historically ACTIVE would bake that separate system's own
selection bias into this strategy's evaluation and shrink the sample size
unnecessarily — see §13a for the survivorship caveat this still carries even
with the broader universe.

---

## 3. Scan timing, data validation, holiday handling

**Timing — anchored to a fixed Israel-time slot, not US market close
directly, and staggered away from other nightly jobs.** ET and Israel local
time do not shift DST on the same calendar dates, which is exactly the
mismatch `CLAUDE.md` already flags for the existing 16:35 IL morning-scan
trigger. **Reasoned default**: run the EOD setup scan at a **fixed, late-night
Israel-time slot — but not the exact 01:30 IL slot `scripts/log_composite_signals.py`
already uses, and not concurrent with it.** **Propose 03:00 IL, and the reason is the 01:30 IL composite job, not the
02:30 IL watchlist evaluation — §3 and §15 agree on this.** The sole
contention this slot is chosen to avoid is with `scripts/log_composite_signals.py`'s
01:30 IL run: a 90-minute gap, versus running at the identical minute, which
risks competing for the same yfinance rate limits, DB locks, or interleaved
log output. 03:00 IL is **not** staggered *relative to* the existing 02:30 IL
nightly watchlist evaluation (the Windows Scheduled Task "StockSage Watchlist
Evaluation," per project memory) — it only happens to land after it. (Running
after 02:30 IL is also **not** required by any dependency on that job's
*output* — §2's universe definition reads `wl_state`/category/`security_type`
directly from the `watchlist` table at scan time, so it needs no prior job to
have finished first.) **Before fixing this slot, check the typical duration
of the 02:30 IL watchlist evaluation in its own log**
(`logs/watchlist_evaluation_task.log`, per project memory) — if that job
regularly runs long enough to approach or pass 03:00, the two jobs could
still collide even though neither depends on the other's output, which would
reintroduce exactly the resource contention this slot exists to avoid, just
against a different job. This check, and whatever it implies for the actual
slot, is an open decision (§15, item 8). The underlying
reason a fixed IL-clock time is used at all — several hours after any
plausible US-market close, in any DST alignment — is unchanged from the
original rationale: it's the one pattern in this repo already proven to
avoid the ET/IL mismatch, so this spec reuses that pattern's *shape* (a fixed
IL time) while deliberately choosing its own, later slot.

**If the last bar is not final** (fails §3's exact-equality last-bar-date
check below): **retry once**, after a short delay (e.g. 15-30 minutes,
sized to a typical provider-propagation lag, not derived from observed
data), then **skip the scan entirely for that night** and log reason code
`DATA_NOT_FINAL` — this prevents silently scanning on stale or incomplete
data due to a provider lag, rather than either blocking indefinitely or
proceeding on bad data.

**Idempotency — act only once per newly completed US session.** Before
running any other check in this section, the scan compares the **expected
last trading session's date** (per the holiday-aware calendar below) against
a **persisted "last scanned session date"** value stored in the DB (a new,
additive table or row, following §12's additive-only posture). If the
expected session date is **not newer** than the stored value — e.g. the
03:00 IL job happens to fire twice before a new US session has closed, or a
retry runs after a prior success — the scan **exits silently**: no candidate
rows are logged (§12), and **no Telegram message is sent at all, including
the zero-setups message** (§13b). This is deliberately different from
`DATA_NOT_FINAL` above (which logs the attempt and is itself a visible signal
that something needs attention) — an idempotency skip is not an error, it is
nothing happening, so nothing should be written or sent. The stored value is
updated only **after** a scan completes and actually processes a newer
session (never before), so a crash mid-scan is retried on the next tick
rather than silently marked done.

**Holiday handling — reuse, do not reinvent.** `services/watchlist_scheduler.py:131-157`
(`us_market_holidays()`, `is_us_market_holiday()`, `is_us_market_day()`) is
the **only** module in the repo with real (self-built) NYSE holiday
awareness — a hand-rolled Gregorian-Easter-based calculation of the 10
standard NYSE holidays plus Sat/Sun "observed" shifting, plus an operator
override list, `WATCHLIST_EXTRA_HOLIDAY_DATES` (`config.py`, imported at
`watchlist_scheduler.py:70`), for ad-hoc closures the algorithm can't know
about. This spec reuses that module's functions directly. Explicitly contrast
with `data/fetcher.py:is_market_open()` (`:142-148`) and
`agent/core.py:_is_morning_scan_time()` (`:435-442`) — both weekday-only,
**no** holiday awareness — this spec must NOT copy either of those two.

**Data validation**, each requirement mapped to existing code:

- **"Last bar date equals the last trading session"** — partially reusable,
  a gap exists. `data/market_data_validator.py:326-332,342` computes
  `gap_days` via `_most_recent_expected_trading_day()` (`:197-203`, itself
  only Mon-Fri, no holiday calendar) and flags `STALE_DATA` if
  `gap_days > ELIGIBILITY_STALE_DAYS`. This *tolerates* a gap — it is not an
  exact equality check. **This spec requires exact equality** instead: the
  last fetched bar's date must equal the expected last trading day as
  computed via the holiday-aware calendar from `watchlist_scheduler.py`
  (not the validator's own Mon-Fri-only expected-day math) — a 2-6 week
  structural setup must not be built on a bar that's stale by even one
  session.
- **"No NaN"** — reuse `data/market_data_validator.py:282-293` (close NaN
  handling: `valid_closes = closes.dropna()`, flags `MISSING_OHLCV` if
  empty) and `:298-307` (volume NaN/inf coercion via
  `pd.to_numeric(..., errors="coerce")` + inf-to-NA replacement).
- **"Volume > 0"** — a gap exists: the validator only checks *average*
  volume over the lookback window (`avg_volume == 0` →
  `ProviderStatus.ZERO_VOLUME`, `:343-345`), never the last bar specifically.
  This spec adds an **explicit last-bar `volume > 0` check**, new, not
  reused from anywhere in the repo.

**Still open for the next step (swing-point detection and ATR, §5/§7):**
this section's "no NaN"/"volume > 0" checks above are scoped to the last
bar and the liquidity-window close/volume only (the foundation step
implemented first); the next step must additionally validate **no NaN in
`open`/`high`/`low` over the full lookback window `calc_swing_levels()`/
`find_confirmed_swing_highs()`/`calc_atr()` actually read** (up to
`lookback=50`/120 bars back, not just the last bar or the liquidity
window) — a NaN anywhere in that wider window can silently corrupt a
confirmed swing point or an ATR value without tripping any check this
foundation step runs.

Reference `analyzers/eligibility.py:compute_data_quality()` (`:42-68`) as a
second, independent precedent for weighting these same three checks —
useful as a pattern shape, but not reused directly, since it returns a 0-1
weighted score rather than a hard pass/fail; this spec needs a hard gate,
not a score.

**Bulk download failures.** The universe (§2) is fetched in **chunked bulk
calls — roughly 50 symbols per call** — not one request per symbol and not
one request for the entire universe at once, mirroring the batching already
used by `get_multiple_prices()`-style calls elsewhere in the repo. A symbol
whose chunk call fails outright for it specifically (network error, provider
error, an empty/missing response for that symbol within an otherwise-
successful chunk) is logged with reason code `DATA_FETCH_FAILED` (§12) and
excluded from that night's candidate set — a per-symbol outcome, not by
itself a reason to abort the scan. **If more than 10% of the universe fails
to download** in a given night's run, the failure is treated as systemic
rather than per-symbol: the whole night is marked `DATA_NOT_FINAL` (reusing
the retry-once-then-skip handling above), rather than proceeding on a
universe that's missing more than a tenth of its members — which could
itself bias which setups get a chance to be found that night.

**Defect found and fixed 2026-10-26, outcome-blind (no return/R-multiple
was ever computed with the bad data) — padded rows inflated history
counts.** A multi-ticker yfinance download pads a late-listed symbol with
all-NaN rows for every calendar date before it actually existed, covering
the *whole* requested range — confirmed directly against a cached frame:
GEV had 1,027 total rows, 384 of them all-NaN (first valid bar
2024-03-27); TLN had 179; NVDA had 0. `setups/data_validation.py`'s
`INSUFFICIENT_HISTORY` check compared `len(df)` against `MIN_HISTORY_BARS`
— raw row count, padding included — so a symbol with genuinely few real
bars could still count as having "enough history" and reach the trend
filter. Separately, `setups/trend_filter.py`'s own SMA150/SMA200
comparisons used a bare `<=`/`>` against values that can be `NaN` (not
`None`) when the trailing lookback window itself still spans padding —
and a `NaN` comparison in Python is always `False`, which silently fell
through as a **pass** rather than a rejection. **Fix:** (1) padding rows
are dropped wherever a frame enters the harness — a fresh multi-ticker
download, and a frame read back from the on-disk replay cache (the cache
file's own bytes are left untouched; cleaning happens on every load, so
an older cache file written before this fix still comes back clean); (2)
`INSUFFICIENT_HISTORY` now counts bars with a **finite close**, not raw
row count; (3) `trend_filter.py` gained explicit `isnan` checks on
today's SMA150 and on SMA200 (the SMA150 value `SLOPE_LOOKBACK_SESSIONS`
ago already had its own `pd.isna()` guard, predating this fix), so a
`NaN` comparison can never fall through as a pass. This was
found while running the outcome-blind feasibility count itself (§13) —
before promoting V3, the same counts-only pass that produced the
848/1,225 `NO_RESISTANCE`/`RR_BELOW_3` evidence — not from any
outcome-level result, consistent with this section's own "outcome-blind
until frozen" posture.

---

## 4. Trend and eligibility filter on completed bars

**Daily trend filter** — reuse `check_sma150()` (`analyzers/technical.py:95-104`)
and `_sma200()` (`:28-31`) directly. **Reasoned default**: require
`above_sma150` AND `sma150 > sma200` — the same pair `full_analysis()`
already uses as its veto + bonus (`technical.py:316-317,331-334`).
Rationale: reuses the system's single existing SMA-based trend definition
(decision D1: simple, not exponential, moving averages) rather than
inventing a second one, keeping this spec consistent with the rest of the
codebase.

**Rising-SMA150 slope confirmation — required.** In addition to the above,
also require `sma150_today > sma150_20_sessions_ago`. **Reasoned default:
20-session lookback** — roughly one trading month, a round number giving the
slope check enough separation to distinguish a genuinely rising average from
noise around a flat one, implemented as simply reading the same rolling-mean
series `check_sma150()` already computes at an earlier index — no new
indicator.

**Liquidity reuse** — `ELIGIBILITY_MIN_AVG_VOLUME=250000`,
`ELIGIBILITY_MIN_DOLLAR_VOL=10000000`, `ELIGIBILITY_MIN_PRICE=3.0`
(`config.py:536,539,542`). **Reasoned default**: reuse as-is — these are
existing, already-reasoned constants; a 2-6 week swing hold doesn't need
different liquidity floors than the existing eligibility system.

**Weekly alignment — deferred to v2.** No weekly-resample logic exists
anywhere in the repo (`analyzers/`, `scanners/` searched: zero matches for
`resample`/`W-FRI`/weekly computation). Rationale for deferring: daily
SMA150/200 already encodes an intermediate-to-long-term trend spanning
roughly 7-10 months of daily bars, which already overlaps the horizon a
weekly trend filter would add for a 2-6 week hold; adding a second
resampling pipeline before v1 ships increases scope without a demonstrated
need.

---

## 5. Setup definition(s)

**Recommended default: Pullback-to-support-in-an-uptrend breakout**, specified
below as a numbered list of deterministic conditions with parameters — a
reader should be able to code this without guessing. Every number is marked
**reasoned default**.

**Alternatives considered (not the default):**
- Pure breakout-from-consolidation (tighter range compression as the setup,
  no explicit pullback requirement).
- Flag/pennant continuation off `calc_swing_levels()`'s fractal highs/lows.

**Reasoned default rationale** for the pullback-to-support choice: better
matches a 2-6 week hold horizon (gives room for both the pullback and the
subsequent move) versus a pure breakout, which is more day/swing-trade-
immediate and historically carries tighter, noisier stops.

**`calc_swing_levels()`'s real return shape — read directly from the
source, quoted exactly (`analyzers/technical.py:229-258`):**

```python
def calc_swing_levels(df: pd.DataFrame, lookback: int = 50) -> dict:
    window = df.iloc[-lookback:].reset_index(drop=True)   # :231 — index is DROPPED
    ...
    for i in range(2, n - 2):                              # :239 — i is a positional
        if highs[i] > highs[i-1] and ... :                 #        index into `window`,
            swing_highs.append(round(highs[i], 4))          # :241 — only the PRICE is
        ...                                                 #        kept; `i` is discarded
    return {
        "swing_highs": sorted(set(swing_highs)),            # :254
        "swing_lows": sorted(set(swing_lows)),               # :255
        "nearest_resistance": nearest_resistance,            # :256 (float or None)
        "nearest_support": nearest_support,                  # :257 (float or None)
    }
```

**This return shape is PRICE LEVELS ONLY — no bar dates or indices of any
kind.** Three compounding facts, each confirmed directly from the quoted
lines above:
1. `window = df.iloc[-lookback:].reset_index(drop=True)` (`:231`) discards
   the original DataFrame's date index before the fractal loop even runs.
2. The loop (`:239-243`) only ever appends `highs[i]`/`lows[i]` — the raw
   price float — to the output lists; the positional index `i` itself is
   never captured or returned.
3. `sorted(set(swing_highs))` (`:254`, similarly `:255`) both re-sorts by
   price (destroying whatever chronological order the loop produced) **and**
   deduplicates by price value — two chronologically distinct swing points
   that happen to share an identical rounded price would silently collapse
   into one list entry.

**Consequence: `nearest_resistance`/`nearest_support` (`:256-257`) are a
*price-proximity* concept — the nearest confirmed level above/below the
*current* close — not a *recency* concept.** §5's precise setup definition
needs "the most recent confirmed swing high **by bar date**," which this
function's return shape cannot supply. **A new, separately tested helper is
required** — e.g. `find_confirmed_swing_highs(df, lookback=50)` — that
applies the **identical** 5-bar fractal rule (`i-2,i-1,i,i+1,i+2` comparison,
same `lookback` default) but returns **`(bar_date, price)` pairs** for each
confirmed swing **high**, preserving both identity and chronological order.
This is new work, not a modification of `calc_swing_levels()` (which stays
untouched and can still be used wherever only the price-proximity values are
needed, e.g. parts of §8's target logic below). **This helper is needed for
`SH` only.** The earlier draft also needed it for swing lows (to find `SL`);
that is no longer the case — see the revised `SL` definition below, which
replaces "most recent confirmed swing low" with a plain running-minimum low
that needs no fractal confirmation at all.

**Reusable helpers:**
- `calc_swing_levels(df, lookback=50)` (`analyzers/technical.py:229-258`) —
  still used for its `swing_highs`/`swing_lows` price-only lists (§8's target
  search doesn't need dates, only "is there a confirmed price level above
  X"). Default `lookback=50` (≈10 weeks of daily bars) is kept as-is —
  **reasoned default**: long enough to find real swing structure relevant to
  a multi-week hold, short enough to exclude stale ancient levels; recommend
  no change from the existing default for v1, to minimize new parameters
  (see §13 for V2's 120-bar variant, which tests this choice rather than
  assuming it).
- The **new** `find_confirmed_swing_highs()` helper described above — used
  wherever this spec needs "the most recent confirmed swing high **by
  date**" (§5 items 2/6). Swing **lows** are no longer identified through
  this helper or any fractal confirmation — `SL` (§5 item 2, §7) is a plain
  running-minimum low over the bars after `SH`, needing no confirmation of
  its own.
- `calc_atr(df)` (`:178-200`) for the stop/structural buffer (§7). Its
  existing `stop_loss_1x/15x`/`take_profit_2x` fields (`:197-199`) are **not**
  reused directly — different multipliers are needed for a 2-6 week hold
  than for the existing day/swing alert's 1.5x/3.0x — only the underlying
  `atr` value itself is reused.

**Pivot points are not used anywhere in this setup definition (not v1, no
secondary reference either).** `calc_pivot_points()`'s `iloc[-2]`
(`analyzers/technical.py:205`) exists specifically to step back off an
*unfinished live* last bar during intraday use. In an EOD scan, the fetched
frame's last row is already the completed session — so calling the function
unmodified would wrongly use the session *before* the just-completed one as
its pivot basis, off by one bar for this context. Rather than introduce an
EOD variant (e.g. calling it on `df.iloc[:-1]`) without a test proving it
correct, v1 drops pivot points entirely — every resistance/trigger reference
below comes solely from `calc_swing_levels()`. No part of this spec treats a
pivot level as a secondary fallback.

**Confirmation-lag requirement — applies to `calc_swing_levels()` and to the
new `find_confirmed_swing_highs()` helper, since both apply the same rule to
swing highs. It does NOT apply to `SL`**, which per the revised definition
below is a plain running-minimum low with no fractal confirmation to wait
on — `SL`'s own "at least 2 completed sessions old" requirement (§5 item 2)
is a separate, unrelated rule (a staleness guard against anchoring to a low
that could still be undercut by the very next bar), not this confirmation
lag; the two should not be conflated even though both happen to use the
number 2. The 5-bar fractal check (`analyzers/technical.py:239-243`)
requires 2 bars on *each side* of candidate index `i` (`i-2,i-1,i,i+1,i+2`)
within the lookback window.
Mechanically, this already means **the last 2 bars of any window can never
qualify as a swing point** — there is no `i+1`/`i+2` data after them yet
inside that window. This is a real, existing property of the function, not
something new to add. Two requirements follow:
1. A swing **high** is **only "confirmed"** once 2 further completed
   sessions exist after it — document this as an inherent 2-session
   confirmation lag.
2. **For both production and any historical replay, the dataframe passed to
   `calc_swing_levels()` must never extend past the evaluation date** — i.e.,
   truncate `df` to data available *as of* the scan date before calling it.
   This is the actual lookahead-bias guard: the function's own math only
   guarantees "no future peeking" relative to whatever `df` it's handed, so
   a replay harness that accidentally hands it bars from after the simulated
   "today" would silently manufacture confirmed swings that weren't
   knowable at the time. This truncation is a **hard requirement** on both
   the production scan and the historical-replay harness (§13), and generalizes
   to every indicator this spec uses, not just swing levels (see §13a).

### Precise, numbered setup definition

Evaluated fresh at every EOD scan, for every symbol that passed §3's data
validation and §4's trend/eligibility filter:

1. **Trend filter** (restated from §4, must all hold on the scan date's
   completed bar): `above_sma150` is true, `sma150 > sma200`, and
   `sma150_today > sma150_20_sessions_ago` (rising).

2. **Confirmed swing structure**: using the **new** `find_confirmed_swing_highs(df, lookback=50)`
   helper (above — not `calc_swing_levels()`, which cannot supply dates),
   truncated to data through the scan date (§5's confirmation-lag rule),
   identify:
   - `SH` = the most recent **confirmed** swing high **by bar date** — i.e.,
     the confirmed swing-high point with the latest `bar_date`. This is a
     *recency* concept, explicitly **not** `calc_swing_levels()`'s
     `nearest_resistance` (a *price-proximity* concept — nearest to the
     current close, which could easily be an older point than the most
     recent one).
   - `SL` = **the lowest LOW of every bar strictly after `SH`'s bar, through
     and including the scan date** — a plain running minimum over that
     window, **regardless of whether that bar's low is itself a confirmed
     5-bar fractal swing point.** This replaces the earlier "most recent
     confirmed swing low" definition: a literal lowest-low needs no fractal
     confirmation of its own, so `SL` is always identifiable the moment a
     pullback exists, rather than waiting on a confirmation lag that could
     leave a real pullback low undetected. **`SL`'s bar must be at least 2
     completed sessions old as of the scan date** (i.e., not one of the 2
     most recent bars) — a staleness guard against anchoring to a low that
     is still fresh enough to be undercut by the very next session, which
     would silently move `SL` (and therefore the stop and the pullback
     depth) from one scan to the next on a still-forming low. This 2-session
     rule is independent of, and not to be conflated with, the unrelated
     5-bar-fractal confirmation lag that still applies to `SH` (above).
     **Both the pullback-depth check (item 3) and the stop (§7) use this
     identical `SL` value** — there is no separate recomputation for either.

     **Implemented exactly as: the search is never restricted to a
     2-sessions-old-or-older subset.** `SL` is first found as the **global
     minimum** low over *every* bar strictly after `SH` through the scan
     date (ties broken by taking the **latest** occurrence, not the
     earliest — a decision this document had left open). *Only then* is
     that global minimum's own bar checked for the 2-session staleness
     rule above. **If the true global minimum sits in the last 2 bars
     (not yet confirmed-old), the setup is rejected outright — reason code
     `PULLBACK_LOW_UNCONFIRMED` — never re-anchored to an older, higher
     low that happens to be 2+ sessions old.** An earlier implementation
     draft instead restricted the search itself to the 2-sessions-old-or-
     older subset, which would have silently substituted that older,
     non-minimal low as `SL` whenever the true low was still too recent —
     this is the corrected, spec-faithful behavior.
   If either `SH` or `SL` cannot be identified (e.g., too few confirmed swing
   highs in the lookback window, or no bar exists after `SH` old enough to
   qualify as `SL`), no setup is produced for that symbol — logged with
   reason code `NO_SWING_STRUCTURE`.

3. **Pullback depth — reasoned default: `(SH − SL) ≥ 1.0 × ATR(14)`.**
   Rationale: a floor only, ensuring `SH` and `SL` represent a genuine
   retracement rather than sideways noise (near-zero `SH − SL`). Deliberately
   **no separate upper bound here** — an overly deep pullback is already
   excluded downstream by §7's stop-distance cap, so adding a second ceiling
   here would be a redundant parameter.

4. **Pullback recency — reasoned default: `SL`'s bar date is within the last
   15 trading sessions of the scan date.** Rationale: roughly 3 trading
   weeks — shorter than the 20-session rising-SMA150 lookback (item 1), so
   the identified pullback is a genuinely current development relative to
   the broader trend confirmation, not stale old structure the price has
   since moved well past.

5. **Price position at the scan date — deterministic, no new parameter:**
   `SL < close_at_scan_date < SH`. The close must have recovered off the
   pullback low but must **not** have already closed back above the swing
   high as of this EOD scan (if it has, the breakout already happened same-day
   and this is not a *pending* setup to be tracked — out of scope for setup
   identification, to avoid double-counting an already-fired move).

6. **Trigger level — deterministic, no new parameter:** `trigger_price = SH`
   exactly — i.e. the price of the most-recent-by-date confirmed swing high
   identified in step 2 via `find_confirmed_swing_highs()`, **not**
   `calc_swing_levels()`'s `nearest_resistance` value (a different,
   price-proximity concept, see above — using it here would silently pick
   whichever confirmed high is nearest in *price* to the current close,
   which need not be the most recent pullback's high at all). **Touch, not
   break, decision**: the trigger counts as crossed on `HIGH ≥ SH` (§6) — a
   pure touch of the exact level, no added percentage/ATR buffer above `SH` —
   since the fill model's HIGH-based crossing definition (§6) already handles
   the mechanics of an actual cross, and a separate confirmation buffer here
   would be a redundant, unneeded parameter.

7. **Stop anchor — deterministic, no new parameter beyond §7's buffer:**
   `stop = SL − 0.5 × ATR(14)` (§7's existing buffer rule), where `SL` is
   **exactly the pullback low identified in step 2** — not a separately
   recomputed "nearest support," which could differ from `SL` if an older,
   lower swing low exists further back in the lookback window. The stop is
   always anchored to the specific pullback this setup is built on.

8. **Validity window — reasoned default: 5 trading sessions** from the scan
   date (unchanged from the prior draft). Rationale: a setup's structural
   premise decays as new bars print; by one week either the breakout has
   happened or the chart has likely changed enough to need re-evaluation at
   the next EOD scan.

9. **Staleness — identity-based, not "is this still the most recent point."**
   A pending setup is cancelled and logged with reason code `STALE_STRUCTURE`
   **only if**: the *specific* `SH` point this setup was built on is no
   longer a confirmed swing high in the (re-truncated, updated) lookback
   window — e.g., it has rolled out of the `lookback=50`/120-bar window
   entirely, or re-applying `find_confirmed_swing_highs()`'s fractal rule to
   updated data no longer confirms it as a swing point for some reason — **or**
   the specific `SL` bar this setup was built on has itself rolled out of the
   lookback window (its own, simpler staleness condition, since `SL` per §5
   item 2 is a plain running-minimum low with no fractal confirmation to
   lose) — **or** either point has been invalidated per §6's pre-fill
   invalidation rule. **A newly formed, different (e.g. lower) low appearing
   after `SL` must NOT, by itself, cancel the setup** — the check is "is the original `(SH, SL)`
   pair still identifiable and valid," never "is `(SH, SL)` still the *most
   recent* confirmed pair as of this later scan." Without this identity
   restriction, nearly every subsequent EOD scan would stale out a pending
   setup the moment *any* new swing point prints, which would defeat the
   5-session validity window's entire purpose (item 8). This check is in
   addition to, not instead of, that window's plain expiry.

---

## 6. Trigger rule, validity window, fill model

**Trigger price**: the EOD scan's identified swing-high resistance/breakout
level (§5).

**Validity window — reasoned default: 5 trading sessions** (one calendar
week). Rationale: a setup's structural premise (the specific pullback/support
location) decays as new bars print; by one week either the breakout has
happened or the chart has likely changed enough to need re-evaluation at the
next EOD scan rather than carry a stale trigger forward.

**No intraday volume condition at trigger time.** The v1 trigger is a
**pure price cross** — no volume gate is evaluated intraday. Rationale,
directly reusing a finding from `docs/CURRENT_ALERT_LOGIC.md` §4c: that
audit already showed Stack A's own intraday volume-spike check is biased by
comparing a *partial, still-accumulating* session volume against a *full*-
session historical average, producing false negatives early in the day and
false positives later purely as the clock advances — the same mechanical
problem would apply here. Breakout-day volume vs. the 20-day average is
instead logged as an **informational-only field**, filled in retroactively
by the **next day's EOD scan** once the breakout session's volume is final —
never used to gate the trigger itself.

**Fill model.** A buy-stop **fills at `max(trigger, session_open)`** on the
first session within the validity window whose **HIGH ≥ trigger** — i.e., if
the session gaps open above the trigger, the fill is the (worse) open price;
if the session opens below and crosses intraday, the fill is exactly the
trigger price. At that fill:

```
recomputed_RR = (target − fill_price) / (fill_price − stop)
skip (reject, logged reason CHASE_RR_BELOW_3) if recomputed_RR < 3.0
```

using the *same* stop and target already fixed at setup-definition time —
this directly enforces the spec's binding 3:1 constraint at the real fill
price rather than a fixed gap-percent proxy. The stop is then monitored from
the fill session onward; a same-day stop-vs-target tie (both touched on one
bar) uses the same conservative convention `compute_outcome()` already
applies — **stop checked first** (`scripts/populate_outcomes.py:86-96`).

**Fields `get_multiple_prices()` (`data/fetcher.py:59-112`) actually returns
— quoted exactly, `data/fetcher.py:99-107`:**

```python
results[symbol] = {
    "symbol": symbol,
    "price": round(price, 4),                                   # :101
    "change_pct": round(change_pct, 2),                         # :102
    "volume": int(today["Volume"]) if not pd.isna(...) else 0,  # :103
    "high": round(_f(today["High"]), 4),                        # :104
    "low": round(_f(today["Low"]), 4),                          # :105
    "open": round(_f(today["Open"]), 4),                        # :106
}
```

`"open"` and `"high"` are both present (confirmed) — the fill model's
`session_open` and the crossing check's running `high` both come from this
single existing call, no new fetch needed for those two fields.
**`"today"` here is `today = df.iloc[-1]` (`data/fetcher.py:83`), a row from
a `yf.download(upper, period="2d", interval="1d", ..., group_by="ticker")`
DataFrame (`data/fetcher.py:65`) — that DataFrame carries a DatetimeIndex,
but `get_multiple_prices()`'s returned dict above has no date/timestamp key
at all.** The bar's date is silently discarded on the way into the returned
dict, same structural gap as §5's `calc_swing_levels()` finding.

**Consequence: a new fetch helper is required — `get_multiple_prices()`
itself (`data/fetcher.py`) is not modified**, matching this spec's existing
posture of adding new modules rather than touching frozen production code
(§12's treatment of `scripts/populate_outcomes.py` is the precedent). A new
helper — e.g. `get_multiple_prices_with_date()` — returns everything
`get_multiple_prices()` does, plus the bar's date (`today.name`, the
DatetimeIndex label of that row), so the trigger job can verify it below.

**Bar-date verification, before testing `HIGH ≥ trigger`.** Before evaluating
the crossing condition for a symbol on any poll, the trigger job verifies
that the fetched bar's date **is the current US trading session** — using
the same holiday-aware calendar already reused elsewhere in this spec
(`services/watchlist_scheduler.py:131-157`'s `is_us_market_day()`/
`is_us_market_holiday()`, §3) to compute what "today's session" should be,
and comparing it against the new helper's returned bar date. This guards
against acting on a stale/cached bar (e.g. a provider lag silently returning
yesterday's session) as if it were live intraday data. If the bar date does
not match the expected current US session, the poll is skipped for that
symbol on that cycle (no fill decision is made from stale data).

**Polling cadence — US trading hours, not fixed Israel hours.** The trigger
job polls every N minutes (reusing the existing `CHECK_INTERVAL_MINUTES=15`
cadence, `config.py:510`, as a reasoned default, since no tighter interval is
needed for a 2-6 week structural setup) **only on US trading days, between
09:30 and 16:00 `America/New_York`** (via Python's `zoneinfo`), reusing
`is_us_market_day()` (`services/watchlist_scheduler.py:156-157`) for the
trading-day check. This is deliberately **not** gated by any fixed
Israel-local-time window — unlike the once-nightly EOD scan (§3), which only
needs to run "well after any plausible close" and so can safely use a fixed
IL slot, the trigger job's entire purpose is to observe the live US session,
so it must track the US market clock directly to avoid exactly the kind of
ET/IL DST-mismatch bug this spec has already flagged twice (§3, and
`CLAUDE.md`'s own existing 16:35 IL morning-scan issue).

**Defining "crossing": use the session's HIGH**, not a point-in-time price
snapshot, so a brief intraday spike through the trigger between two polls is
never missed. This applies identically in both places "crossing" is
evaluated: live, via the intraday trigger job reading the `"high"` field
above (which tracks the day's running high as the session progresses, since
`today["High"]` for an in-progress bar updates as the session's own high
updates); and in historical replay, via the completed day's recorded High
column. Using the same HIGH-based definition in both places is what keeps
the live system and the replay harness measuring the identical event.

**Handling a late-detected cross** (the cross happened earlier in the day
than the poll that notices it): because "crossing" is defined via the
session's running HIGH rather than the latest tick, a poll that runs well
after the actual intraday spike still correctly sees `high ≥ trigger` for
that session — the running High field does not reset or get missed between
polls, it only grows monotonically within a session. The trigger job will
therefore never silently miss a cross or misattribute it to the wrong
session; the only consequence of late detection is that the alert/log
timestamp lags the actual crossing moment by up to one poll interval within
the same session. The fill price computed from `max(trigger, session_open)`
is unaffected by *when* within the session the cross is detected, since both
`trigger` and `session_open` are fixed values for that session, not
poll-time-dependent.

**Pre-fill invalidation (reason code `INVALIDATED_BEFORE_FILL`) — applies
only to sessions strictly before the fill session.** A pending, not-yet-filled
setup is cancelled if, on any session *before* the session whose HIGH first
reaches the trigger: that session's **LOW is at or below the stop level**,
OR that session's **CLOSE is below the pullback low (`SL`)**. Either
condition means the structural premise has already broken down before the
planned entry ever happened — there is nothing left to fill into. **This
rule never looks at the fill session itself** — the fill session's own
stop-vs-trigger ambiguity is handled separately, below.

**Fill-day stop-out (event code `FILLED_STOPPED_SAME_DAY`) — the fill
session's own LOW also breaches the stop.** If the session whose HIGH first
reaches the trigger (`HIGH ≥ trigger`) *also* has its **LOW at or below the
stop** on that same bar, daily bars cannot reveal which happened first
intraday (the breakout above trigger, or the drop to stop). An earlier draft
of this spec treated this case as `INVALIDATED_BEFORE_FILL` (never filled).
**This is revised: the trade is treated as FILLED and stopped out the same
day, not excluded.** Fill price = `max(trigger, session_open)` (§6's
ordinary fill model, unchanged); exit price = `min(stop, session_open)` —
the symmetric worse-case convention, so a session that gapped below the stop
exits at that gap-down open rather than being credited the nominal stop
level (mirroring §12's general gap-through-stop exit-price rule). Realized R
is therefore approximately **-1** before costs (exactly -1 only when
`session_open` sits between `stop` and `trigger`, in which case fill =
trigger and exit = stop exactly; worse than -1 when the session gapped below
the stop). This event is logged under its own code, `FILLED_STOPPED_SAME_DAY`
— distinct from `INVALIDATED_BEFORE_FILL` (never filled) and from an
ordinary post-fill stop-out — and **is included in every outcome sample**
(§12's outcome tables, §13's replay statistics) exactly like any other
filled-and-resolved trade. **Rationale for including it, not excluding it:
excluding same-day stop-outs would silently drop exactly the trades where the
breakout failed immediately, biasing every aggregate outcome statistic (mean
R, hit rate) upward relative to what a real trader following this system
would have experienced.** This supersedes the earlier same-day tie-break
convention that assumed the stop alone and logged `INVALIDATED_BEFORE_FILL`
for this case; the stop-checked-first convention reused elsewhere in this
spec from `compute_outcome()` (`scripts/populate_outcomes.py:86-96`) is
otherwise unchanged for every other same-day tie (e.g. stop-vs-target ties
after the fill, per the Fill model paragraph above).

**Trigger-job infrastructure.** The intraday trigger check is its **own
supervised task**, not folded into `agent/core.py`'s existing
`schedule`/`asyncio.run()` loop — it must not share that loop's thread, its
in-memory `_alerted_this_session` dedup, or its DB cooldown table
(`alerts`/`was_alerted_today()`), since those are Stack A's own state. It
needs its own scheduling/supervision entry (mirroring the pattern of
separate Windows Scheduled Tasks already used for `scripts/populate_outcomes.py`
etc., each with its own log file) and its own try/except + logger discipline
(matching the pattern already proven in `agent/core.py`'s
`job()`/`morning_job()`, cited in §1 of `docs/CURRENT_ALERT_LOGIC.md`), as
an independent script, not shared code.

**Dedup: DB-backed, not in-memory**, once per setup per day. **v1 allows at
most one open setup per symbol**, so the dedupe key simplifies to
`(symbol, date)` rather than needing a separate `setup_id` dimension.

**Price/high fetch**: the **new** `get_multiple_prices_with_date()` helper
above — modeled on, and batched the same way as, `get_multiple_prices()`
(`data/fetcher.py:59-112`, the same bulk/batch call `check_alerts()` already
uses) — for polling all symbols with an open, not-yet-triggered setup in one
call.

**Reconciliation at the next EOD scan.** Each EOD scan (§3) re-evaluates
**every** currently-pending setup against that day's just-completed session
OHLC, using the **same** fill-evaluation function the historical replay
harness uses (§13) — not a second, separately-written check. If this
reconciliation finds that a fill *should* have occurred (the completed
session's `HIGH ≥ trigger`) but the live intraday trigger job never recorded
one — e.g. a missed poll, a job outage, a transient fetch failure — this is
logged with reason/event code `MISSED_LIVE`, and the setup is **still
treated as filled** for outcome-tracking purposes (§12), using the same fill
model (§6) applied to the completed session's OHLC. This keeps the live
system's outcome data consistent with what the replay would have computed
for the same historical session, rather than silently losing a setup's
outcome because the live poller happened to miss it.

---

## 7. Stop rule

**Structural stop**: the pullback low `SL` identified in §5's setup
definition (step 2/7) — per the revised definition there, the plain lowest
LOW of the bars after `SH`, at least 2 completed sessions old, **not** a
confirmed fractal swing low and **not** a separately recomputed "nearest
support" either, either of which could point to a different, older level —
minus an ATR buffer.

**Reasoned default — buffer = 0.5× ATR(14)** beyond the swing low.
Rationale: gives room below the structural low to avoid noise-driven
stop-outs on a level that's "support" rather than an exact floor, while
staying smaller than the existing system's own 1x/1.5x ATR day-alert stops,
since a structural level is already doing most of the risk-definition work
here. ATR period 14 is reused as-is from `calc_atr()`'s existing default
(`analyzers/technical.py:178`) for consistency with the rest of the
codebase.

**Maximum stop distance — proposed, but left open for the owner (§15), not
settled**:

```
max_stop_distance = min(0.12 * entry_price, 5.0 * atr)
```

**Worked synthetic example** (illustrative numbers only, not observed from
any symbol): take `ATR(14) = $2.00`, `entry/trigger = $100`. A structural
swing low for a typical multi-week pullback might sit at `$93`, giving a
stop (per the buffer rule above) of `$93 − 0.5×$2 = $92` — a stop distance of
`$8`, i.e. **4× ATR**. Under a tighter `min(10%, 3×ATR)` cap, `3×ATR = $6`
would bind and this entirely ordinary 4-ATR pullback would be **rejected** —
too strict for a 2-6 week hold horizon. Under the proposed
`min(12%, 5×ATR)` cap, `5×ATR = $10` binds and the `$8` stop **clears** the
cap. This is why the wider cap is proposed as the reasoned default, even
though it remains open for the owner to confirm or adjust.

**Interaction with the 3:1 gate (§8)**: a wider surviving stop directly
raises the bar for the target — at `$8` risk, the target must sit at least
`entry + 3×$8 = $124`, i.e. ≥24% above entry, before the setup can qualify.
This is what naturally filters out wide-stop setups that don't have
resistance far enough away, working *together with* (not instead of) the
`NO_RESISTANCE` reject rule (§8).

**When the cap is exceeded: reject the setup outright — never tighten the
stop to fit the cap.** Rationale: artificially moving the stop up to meet the
cap would place it inside normal price noise/structure rather than beyond a
real structural level, which defeats the entire premise of a "structural"
stop — a fabricated non-structural stop is worse than no setup at all.
Rejecting on an over-cap stop is a strictly separate, *prior* gate relative
to the 3:1 R:R check — it never silently shrinks the stop distance to
rescue an otherwise-promising R:R; a setup that only clears 3:1 by first
shrinking its stop below the real structural level would have been invalid
R:R to begin with. Wide-range/choppy names are excluded from v1 whenever
their genuine structural stop is too far away, by design, regardless of how
attractive the target looks. (Reason code: `STOP_CAP_EXCEEDED`, §12.)

---

## 8. Target and R:R rule

**Target — deterministic, must be distinct from the trigger level.** Per
§5's precise setup definition, the trigger price is itself `SH` (the
confirmed swing high being broken out above) — so the target **cannot** also
be "nearest resistance above entry" using the same value, which would
collapse reward to zero by definition. **The target is the smallest value
strictly greater than `SH`, among the same confirmed-swing-high list
`find_confirmed_swing_highs()` already produced to identify `SH` itself**
— i.e., the next confirmed swing-high resistance level *beyond* the
breakout point. **Implemented as a deliberate deviation from this
section's originally-literal text**, which named `calc_swing_levels()`'s
`swing_highs` list instead: both lists are built from the identical
5-bar fractal rule over the identical window, so the *set* of prices is
the same either way (per §5's parity note) — sourcing the target from
`find_confirmed_swing_highs()` instead means `define_setup()` has exactly
one fractal-detection dependency, not two, for what is conceptually one
underlying computation. `calc_swing_levels()` is no longer a
`define_setup()` dependency at all; it remains in use only by
`find_confirmed_swing_highs()`'s own parity test. (Pivot points are not
used anywhere, per §5.)

**3:1 gate**: reject the setup outright if
`(target − entry) / (entry − stop) < 3`, where `entry = trigger_price = SH`
at setup-definition time (re-evaluated at the observed fill price per §6).

**"No overhead resistance" — v1 default is to reject (Option B).**

- **Option B (v1 default)**: when `calc_swing_levels()`'s `swing_highs` list
  contains no confirmed level above `SH`, **reject the setup**, logged with
  an explicit reason code `NO_RESISTANCE` (§12). Rationale: v1 should only
  ever score a setup against a resistance level actually observed in price
  structure — accepting "no resistance found" as sufficient is a materially
  different, untested claim.

- **Option A (deferred — a separate, later, pre-registered variant, not
  v1)**: use a measured target of 3× the defined risk
  (`entry + 3*(entry-stop)`) directly when no resistance is found.
  **Important caveat**: under Option A, the 3:1 R:R gate becomes **true by
  construction** — the target is defined as exactly 3× the risk, so the
  gate can never actually reject anything in this branch; it stops being a
  market-structure-validated reward check and becomes a restatement of the
  sizing formula. This is precisely why it must not be the default: it would
  silently convert "minimum 3:1 reward based on real resistance" into
  "minimum 3:1 reward by definition" for every no-resistance case — a
  different and weaker claim than the rest of the spec makes for setups that
  do have a resistance level. If the owner later wants Option A, it must be
  pre-registered as its own named variant (§13, "V3") before any replay run
  uses it, precisely so this construction-vs-observation distinction can't
  be blurred after the fact.

- Option C (widen the lookback window before falling back to B) remains
  available as a secondary mention, not a default.

### Target arithmetic and expected frequency

Working through the numbers from §5/§7's own reasoned defaults, not
observed from any symbol:

```
risk  = entry − stop = SH − (SL − 0.5×ATR) = (SH − SL) + 0.5×ATR
      ≥ 1.0×ATR + 0.5×ATR            (§5 item 3's pullback-depth floor)
      = 1.5×ATR                       (minimum possible risk)

3:1 gate requires: target ≥ entry + 3×risk = SH + 3×risk
                          ≥ SH + 3×1.5×ATR
                          = SH + 4.5×ATR
```

**So the 3:1 gate needs a confirmed swing high at least 4.5× ATR above `SH`
itself** (more, if the actual pullback is deeper than the 1.0×ATR floor,
since risk scales directly with `SH − SL`). **In an uptrend, the most recent
swing high is usually the highest point reached so far** — by definition, an
uptrend makes higher highs over time, so there is frequently **no older
confirmed swing high priced even above `SH`** within the lookback window at
all. **Expected consequence: V1 will mostly log `NO_RESISTANCE`** (§8's
Option B) rather than producing a qualifying setup — this is a direct,
foreseeable arithmetic consequence of the spec's own reasoned defaults, not
a hypothetical edge case.

**Sensitivity variant V4 (added to the three in §13): target = the highest
high of the last 252 bars (≈1 trading year), if that level sits above `SH`.**
Like V2 and V3, V4 is a sensitivity analysis only, not a competitor for
"best result" — it exists to test whether this specific arithmetic bottleneck
(the 4.5×ATR floor combined with uptrends rarely having resistance above
their own most recent high) is what's suppressing V1's fire count, and is
subject to the same promotion restriction as V2/V3 (§13): it cannot become
primary on the strength of the same replay data that produced it.

**Outcome-blind feasibility check — before the pre-registration is frozen
(§13), not after.** `COUNT` (never compute returns or R-multiples for) the
number of fired setups per calendar year, for V1, V2, V3, and V4, across the
replay universe (§2). Parameters may be adjusted **only in response to these
counts** — e.g. widening the pullback-depth floor or the swing lookback if a
variant's count is implausibly low — and **only before the freeze**. Every
such count-driven adjustment, and the counts that motivated it, must be
recorded in the pre-registration file itself, so the full history of
parameter changes remains visible and auditable rather than silently tuned
away. This check never looks at returns, R-multiples, or any outcome field —
only event frequency — which is what keeps it from being a backdoor into the
exact kind of result-driven tuning pre-registration exists to prevent.

---

## 9. Vetoes

**Extension veto — expressed in ATR terms, not percent:**

```
hard veto if (price − sma150) > 6 × ATR(14)
```

Citing `analyzers/composite.py:_extension_points()` (`:182-190`) as the
existing non-vetoing taper precedent this improves on — that function is
percent-based (`COMPOSITE_EXTENSION_MAX_PCT=10.0`, `config.py:493`) and never
fully zeroes a trade on extension alone. This spec instead uses a hard,
ATR-denominated cutoff, because it scales with each symbol's own volatility
— a high-ATR% name and a low-ATR% name extended by the "same percent" are
not equally extended in risk terms. `N = 6` is used as the reasoned default.
**Implemented against the unrounded SMA150** (`df["close"].rolling(
window=150).mean().iloc[-1]`), deliberately **not** `check_sma150()`'s
display-rounded field — the same fix already applied to `trend_filter.py`
(§4), for the same reason: a true SMA150/price pair separated by under
~0.00005 could otherwise have the comparison's outcome flipped purely by
the rounding. A `NaN` SMA150 (fewer than 150 bars) is checked **before**
the comparison and rejected with `INSUFFICIENT_HISTORY` (§3's existing
code) — `NaN > x` is always `False` in Python/pandas, so without this
explicit guard a short-history frame would silently *pass* the veto
instead of being correctly rejected for lacking the history to evaluate
it at all. A further guard, run **before any swing/stop logic** (not
specific to this veto): over the swing-detection lookback window, every
`high`/`low`/`close` must be finite and `high >= low` on every bar, or the
candidate is rejected with `MISSING_OHLCV` — otherwise a stray `NaN`
deep in that window would silently fail every fractal comparison it's
part of (again, `NaN > x == False`) rather than being rejected outright.

**Liquidity veto**: reuse §4's floors as a hard gate (not a taper) — already
a reasoned default by inheritance from the existing eligibility constants.

**Market regime veto — a true hard veto, logged on every candidate.** No new
long setups while SPY is below its own SMA150. This reuses the *pattern* of
`compute_market_context()`'s BULL/BEAR classification shape
(`analyzers/composite.py:90-153,123-128,142`: `close = spy["close"].iloc[-1]`
vs `sma150 = spy["close"].rolling(window=150).mean().iloc[-1]`), but **not**
its current behavior or the function itself — `compute_market_context()`
has an optional live-`yfinance`-fetch fallback when handed no `spy_df`
(`:99-103`) that this pure-function package must never trigger, and
returns RS/score fields this spec has no use for; in the composite engine,
regime is explicitly documented as never a veto, only a threshold modifier
(module docstring, `composite.py:17-18`). This spec deliberately diverges
from that precedent: a 2-6 week long-only swing system is more
regime-sensitive than a daily alert system. **Implemented: missing, too
short (fewer than 150 bars), or `NaN` SPY close/SMA150 all resolve to
`regime = "UNKNOWN"`, itself treated as a veto** — the same fail-safe
direction `compute_market_context()`'s own docstring states ("a data
outage must not loosen the bar"), applied here as a hard reject rather
than a fallback threshold. **The regime value is always computed and
attached first, independent of every other check; the veto itself,
however, is evaluated last** in the overall precedence order (structural
checks → stop/target/risk checks → extension veto → regime veto) — so a
candidate that fails an earlier, more specific check reports *that*
reason, with the regime value still present on the result, matching this
section's own "recorded on every candidate... including ones that failed
for other reasons first" requirement literally. The regime value
(BULL/BEAR/UNKNOWN) and whether it vetoed is recorded
on **every** logged candidate row (§12), including ones that failed for
other reasons first, so the replay/evaluation harness (§13) can later
separate "rejected for regime" from "rejected for setup quality."

**Earnings: not a veto in v1.** No earnings-calendar/date veto is applied.
Instead, a purely informational line is proposed: static inspection of the
locally-installed `yfinance` package (no network call, no live data fetch —
only checking the library's own class attributes) confirms
`yfinance.Ticker` exposes `calendar` and `earnings_dates` attributes, so a
next-earnings-date lookup is technically available as a data source. The
proposed informational message line is `"Next earnings: <date> / unknown"`
(unknown if the lookup fails or returns nothing), with **no effect on
scoring, gating, or sizing**. **Known limitation to state explicitly: a gap
through the stop driven by an earnings surprise is not covered by the
structural/ATR-based stop** — the stop assumes orderly intraday price
action, and an overnight earnings gap can execute well beyond it, so
realized loss on such an event can exceed the defined R. This line is
advisory context only, never a risk control. Field reliability/format should
be confirmed with a live test before the messaging PR implements it (§15).

---

## 10. Sizing

```
position_size = floor(RISK_PER_TRADE_USD / (entry - stop))
```

exactly as specified.

**Shown at two different prices, explicitly labeled as such.** The EOD
setup-list message (§11) shows share count computed with `entry = trigger_price`
(`SH`, §5) — this is a planning estimate, since the actual fill may differ.
The trigger alert (§11), sent once a fill actually occurs, **recomputes**
share count with `entry = fill_price` (§6's fill model) and the message
states explicitly that this is a recomputation (e.g. "Shares recalculated at
fill price $X — originally estimated N shares at trigger $Y"), so the reader
is never left comparing two numbers without knowing one superseded the
other.

`RISK_PER_TRADE_USD` is read via `os.getenv("RISK_PER_TRADE_USD")` from the
**server's existing, already-untracked `.env`** — the same file and
convention already holding `TELEGRAM_TOKEN`/`TELEGRAM_CHAT_ID`
(`CLAUDE.md`'s Environment section) — never committed, and **never written
to any log line or DB row**, mirroring the existing "never print/log/commit
credentials" rule even though this value isn't itself a credential, since
it's still personal risk-capital information.

**When unset**: the message must explicitly instruct how to set it (e.g.
"Set RISK_PER_TRADE_USD in your environment to size this trade — no position
size shown.") rather than silently defaulting to a guessed dollar figure.
This is a direct requirement, not a reasoned default — picking a dollar
default would itself be a parameter chosen without reference to any
particular owner's risk tolerance, and more importantly, sizing risk capital
is a decision that must stay in the owner's hands, not be guessed by this
system.

**When `position_size()` returns `0`** (a valid, positive risk budget
that still can't afford even one share at the setup's own risk distance —
`floor(RISK_PER_TRADE_USD / (entry - stop))` rounding down to zero,
distinct from the unset-env-var case above and from `None`'s "invalid
input" cases): the message must say so explicitly (e.g. "Risk budget is
below one share's risk at this entry/stop — no position shown.") rather
than silently showing "0 shares" with no explanation, or omitting the
message's sizing line entirely as if it had simply failed.

---

## 11. Message formats, bilingual parity, delivery

Reuse the existing bilingual mechanism exactly: `get_language(chat_id)`
(`db/database.py:1468-1474`, default `"he"`) and a `_SETUP_STRINGS`-style
dict mirroring `_SCAN_STRINGS`'s shape (`agent/core.py:122-135`) — **not**
`_fmt_buy_alert()`'s pattern, which is flagged as a bug/gap in
`docs/CURRENT_ALERT_LOGIC.md`: it's hardcoded Hebrew-only with no `lang`
parameter at all (`agent/core.py:94-117`, confirmed no `get_language()` call
anywhere in `check_alerts()`). Both of this spec's message formatters take
an explicit `lang` parameter from the start, achieving real bilingual parity
where the existing buy-alert path does not.

**EOD setup-list message fields**: symbol, setup type, entry trigger price
(`SH`), stop, target, computed R:R, **share count at the trigger price**
(§10, explicitly a planning estimate), validity window end date, qualifying
reasons ("why it qualified" — which filters it passed), regime at scan time,
ACTIVE-tier flag (§2), next-earnings informational line (§9).

**Trigger-alert message fields**: symbol, fill price (per §6's fill model,
not just the trigger level), entry/stop/target/recomputed-R:R, **recomputed
share count at the fill price**, explicitly labeled as a recomputation of the
EOD list's estimate (§10), an explicit "what invalidates it" line (e.g.
"closes back below the pullback low" or "stop hit intraday") — since this is
advisory-only and the human needs the invalidation condition spelled out,
not just the entry trigger — and the breakout-day-volume-vs-20-day-average
field once the next EOD scan has filled it in (§6).

**Delivery — the EOD list and the trigger alert are deliberately different.**
The EOD list: no Telegram notification at the 03:00 IL scan time itself (§3)
— sending a push at that hour would be unwelcome. **Reasoned default**: send
the EOD list immediately after computation, silently (Telegram
`disable_notification=True`). Rationale: keeps a single scheduled job (no
second send-time job to add/maintain) while ensuring the list is already
present in chat history well before the next trading session, without an
overnight buzz. **The trigger alert uses normal (non-silent) Telegram
notifications** — it represents an actionable, time-sensitive event (an
entry just filled, during US market hours) the user should be audibly/
visually notified about, unlike the EOD list's purely informational,
no-immediate-action content. Only the EOD list (and the zero-setups message,
§13b) is silent.

---

## 12. Logging

New, **additive-only** tables — never modify the existing
`alerts`/`alert_signals`/`composite_signals` tables. Follow the proven
`evaluation_runs`/`evaluation_run_changes` shape (`db/database.py:185-256`):
a header/run table plus a detail table, append-only, with rollback-safe
status fields rather than deletes (citing the conflict-checked rollback
pattern in `scripts/rollback_evaluation_run.py`, which refuses a rollback
when current state has diverged from the recorded snapshot, rather than
overwriting blindly).

**Log every candidate considered, not just fired setups, each with a reason
code.** This does not exist anywhere in the repo today — confirmed: no
persisted reason-code log for alert or composite-signal candidates; the
closest precedents are `scanner_results`'s `(passed, reason, details_json)`
shape (`db/database.py:301-314`) and the `evaluation_run_changes` audit
shape. This spec's logging is new work, reusing only the *shape* of those
precedents. Non-exhaustive reason-code set: `NO_SWING_STRUCTURE` (§5, no
confirmed `SH`/`SL` pair found), `PULLBACK_LOW_UNCONFIRMED` (§5 item 2 —
new, implemented alongside `find_confirmed_swing_highs()`: the true
global-minimum pullback low sits in the last 2 bars, not yet "confirmed"
2-sessions-old; the setup is rejected outright rather than re-anchored to
an older, higher, non-minimal low), `PULLBACK_TOO_SHALLOW` (§5 item 3 —
new, `SH − SL < 1.0×ATR`), `PULLBACK_TOO_OLD` (§5 item 4 — new, `SL`'s bar
is more than 15 sessions before the scan date), `PRICE_OUTSIDE_RANGE` (§5
item 5 — new, covers both directions, `close` already `≥ SH` or still
`≤ SL`, under one code — in practice only the `≥ SH` direction is
reachable as a *distinct* rejection: `low ≤ close` always holds, so any
bar whose close reaches `≤ SL` has itself just become the new global
minimum, which is always the most recent bar, which always fires
`PULLBACK_LOW_UNCONFIRMED` first), `NO_RESISTANCE` (§8), `RR_BELOW_3` (§8
— new, the setup-**definition**-time 3:1 gate; deliberately distinct from
`CHASE_RR_BELOW_3`, §6's *post-fill* recompute, a different pipeline
stage), `STOP_CAP_EXCEEDED` (§7), `EXTENSION_VETO`, `REGIME_VETO` (§9),
`LIQUIDITY_VETO`, `TREND_FILTER_FAILED`, `STALE_STRUCTURE` (§5 item 9, identity-based — the
original `SH`/`SL` point itself is no longer confirmed or valid, not merely
superseded), `INVALIDATED_BEFORE_FILL` (§6, pre-fill stop/close breach),
`CHASE_RR_BELOW_3` (post-cross recompute failure, §6), `DATA_NOT_FINAL` (§3,
last bar not yet settled after one retry, or more than 10% of the universe
failed to download), `DATA_FETCH_FAILED` (§3, a single symbol's bulk-download
chunk failed outright; excluded from that night's candidates, not by itself a
reason to abort the scan unless the 10%-universe-wide threshold is crossed,
in which case the night is `DATA_NOT_FINAL` instead), `EXCLUDED_WL_STATE` /
`EXCLUDED_CATEGORY` / `EXCLUDED_SECURITY_TYPE` (§2, universe-definition
exclusions, logged even though these are pre-candidate filters, for full
auditability of the universe itself), `MISSED_LIVE` (§6, an event code, not
a rejection — the EOD reconciliation found a fill the live trigger job
missed; the setup is still treated as filled), `FILLED_STOPPED_SAME_DAY` (§6,
an event code, not a rejection — the fill session's LOW also breached the
stop; the setup is treated as filled and immediately stopped out, included
in every outcome sample). The setup-definition step (§5-9) also reuses two
existing codes beyond their original §3 scope, rather than minting new
ones for the same underlying "couldn't get usable data to work with"
family: `MISSING_OHLCV` for a missing required OHLCV column on either
input frame, a non-finite (`NaN`/`inf`) or inverted (`high < low`) bar
inside the swing-detection lookback window, and a non-finite or
non-positive ATR; `INSUFFICIENT_HISTORY` when the unrounded SMA150 the
extension veto needs is `NaN` (fewer than 150 bars). Every candidate row also
carries the regime value (BULL/BEAR) regardless of which reason, if any,
rejected it first — per §9's "logged on every candidate" requirement.

**Costs — a fixed, pre-registered assumption, applied uniformly in both
replay and live outcome logging.** **Reasoned default: 10 basis points of
slippage on both the entry fill and the exit (stop/target/timeout), plus
zero commission** (consistent with typical modern no-commission retail
brokerage execution; the owner's actual broker fee schedule, if different,
should replace this before going live). Rationale: an idealized fill price
with no friction assumption would overstate every R-multiple in both the
replay and the live outcome logs; a fixed, symmetric, round-number
assumption keeps the comparison between V1/V2/V3/V4 and the baseline (§13)
apples-to-apples without claiming a precision this spec has no basis to
assert. This assumption is applied identically everywhere R-multiples are
computed — the new outcome module (above) and the baseline's own R-multiples
(§13) — and is frozen in the pre-registration file alongside everything
else.

**Outcome module — a new, separate module; `scripts/populate_outcomes.py` is
not touched.** `HORIZON_DAYS=10` and `_CLOSE_OFFSETS` (`scripts/populate_outcomes.py:52-53`)
stay exactly as they are — this spec's 30-session timeout and target are
implemented in a **new module**, not by parameterizing the existing one.
That new module must carry **parity tests against `compute_outcome()`** for
the parts the two share: the same-day stop-before-target tie-break
(`:86-96`), MAE measured through the exit bar, and T+0 exclusion — proving
no silent algorithmic drift between the two implementations — while
differing only in horizon length (30 vs 10 sessions) and target definition.

**Exit price mechanics — same rule for the stop and the target barrier.**
If the session that triggers a **stop** exit **opens at or below the stop
price**, the exit price is **that session's open**, not the stop level
itself — a gap through the stop is worse than the nominal stop, and
crediting the nominal stop would overstate the realized R (this is the same
rule applied to the fill-day case above, §6's `FILLED_STOPPED_SAME_DAY`). If
the session that triggers a **target** exit **opens above the target**, the
exit price is **the target itself**, not the open — a gap above the target
is upside this system did not need to wait for and is not credited, keeping
the target a disciplined take-profit rather than a moving one. (The
asymmetry is intentional: downside gaps are realized in full; upside gaps
beyond target are capped at target.) The costs assumption below (10 bps
slippage) applies on top of whichever exit price results from this rule,
exactly as it applies to every other entry/exit in this spec. **The outcome
module's parity tests above must include a gap-through-stop case** — a
synthetic bar whose open is below the stop — verifying the module's exit
price equals that bar's open, not the stop constant, before any non-gap
case is trusted.

`first_barrier_hit` enum for this spec's outcome table: `stop` / `target` /
`timeout_30` — mirroring the existing `alert_outcomes.first_barrier_hit`
CHECK-constraint convention (`db/database.py:350-364`), renamed from an
earlier draft's `target_3r` because **`target` is the actual resistance
level from §8, which can realize well more than 3R** — 3:1 was only ever the
*minimum* gate at setup-definition time (§8), not a cap on the realized
result, so labeling the barrier `target_3r` would misleadingly suggest every
target hit is worth exactly 3R.

**Recorded at every outcome resolution** (stop, target, or 30-session
timeout): **R at exit** (realized R-multiple, `(exit_price − entry) / (entry − stop)`,
which can exceed 3 at `target`), **MAE** (max adverse excursion through the
exit bar, same convention as `compute_outcome()`), and **MFE** (max favorable
excursion through the exit bar — the best unrealized gain the position saw
before exiting, parallel to MAE but on the upside) — new field, not present
in the existing `alert_outcomes`/`composite_signal_outcomes` tables, added
here because a 10-30 session hold can meaningfully run up before pulling
back to its eventual exit, which MAE alone can't capture.

**Suppression rule — settled, extends the one-open-setup-per-symbol rule
(§6) through the position-tracking phase.** A new setup is **not** generated
for a symbol at any EOD scan while a previous setup for that symbol is
either (a) still pending — valid and unfilled, within its validity window —
or (b) filled and its resulting position is still being tracked for outcome
(i.e., within its 30-session timeout window and no barrier has resolved it
yet). A symbol can have at most one setup or tracked position live,
end-to-end, at any time; the next new setup for that symbol can only be
generated once the prior one has fully resolved (stop, target, timeout, or a
pre-fill/post-fill invalidation reason code).

**Scope note (outcome module PR): the replay harness writes to its own
results file, not to the bot's live DB.** This section's additive-only DB
logging tables (header/detail, following the
`evaluation_runs`/`evaluation_run_changes` shape) are deferred to the
"wire it up" rollout step (§14, step 4+). The new outcome module
(`setups/outcomes.py`) only computes an in-memory result and returns it —
it opens no DB connection, defines no schema, and writes nowhere.

**Scope note: a target touched on the fill session itself is never
credited.** The outcome module's result carries a diagnostic-only
`target_touched_on_fill_session` flag (true when that session's high
reached `target` and its low did not breach `stop`), but the trade is
still not exited there — this is a conservative, stated rule, not an
oversight.

---

## 13. Evaluation plan outline

**Prior evidence — no positive prior for an edge.** Per the owner's own
account of earlier, external research (a `trend_pullback`/long-type
strategy, evaluated with a held-out test against same-date peers) —
referenced here as something the owner reported, not independently
verifiable from this repository, since those reports live outside it — that
earlier work found **no evidence** of an edge over same-date peers. This
spec should **not** be read as resting on an assumed edge for pullback-breakout
setups in general. **The actual value of v1 is plan quality** — a
precise, falsifiable, pre-registered, outcome-blind-until-frozen evaluation
design — not a presumption that this particular setup definition will prove
to have positive expectancy. The baseline comparison below exists precisely
to test that question honestly, not to confirm it.

**Historical replay**: run the exact same code over historical data,
read-only, writing to its own observation tables — same posture as
`scripts/log_composite_signals.py`'s "silent, observation-mode only"
pattern. **Replay universe = the precise definition in §2** (`wl_state IN
('ACTIVE','MONITOR')`, ETF/index categories and `security_type`s excluded),
to avoid baking the watchlist lifecycle's own selection bias into the
sample.

**Forward shadow period**: run live in parallel, logging only, no alerting,
for a stated period before any live advisory messages are sent — mirrors the
composite engine's current "additive, NOT yet alerting" status.

**Expected setup frequency.** This combination of filters (confirmed uptrend
+ rising SMA150 + pullback + liquidity + extension veto + regime veto + a
3:1-gated, resistance-observed target) **may yield very few historical
setups** — see §8's "Target arithmetic and expected frequency" for the exact
mechanism: the 3:1 gate's ≥4.5×ATR-above-`SH` requirement, combined with
uptrends typically having no older swing high above their own most recent
one, means V1 is expected to mostly log `NO_RESISTANCE`.

**SETTLED 2026-10-25 — V3 promoted to primary, outcome-blind.** The
outcome-blind feasibility count this section's own rule already licenses
("adjusting parameters only in response to these counts, only before the
freeze") ran against 40 random symbols × 3 calendar years (~30,040
symbol-nights) and gave **V1 and V2 zero fired setups**: 2,073 nights
cleared every structural check (§3-7, §9) and died at the target step
alone — 848 `NO_RESISTANCE`, 1,225 `RR_BELOW_3`. This decision was made
**before any outcome or return was computed** — it rests only on counts,
never on which variant "performed better," so it is not the kind of
after-the-fact cherry-picking the paragraph above (and §14's milestone
gating) exists to prevent; it is that same rule's explicit carve-out. V1
and V2 are now sensitivity variants; V4 remains sensitivity-only and
unimplemented.

- **V1** (sensitivity) — the original hypothesis: target-selection
  Option B, reject-`NO_RESISTANCE` (§8). Everything else in this document
  (§1-12) describes V1's mechanics exactly; V3 differs from it only at the
  target step.
- **V2** (sensitivity) — identical to V1 except `calc_swing_levels` lookback
  widened to 120 bars (vs. the 50-bar default, §5).
- **V3 (PRIMARY)** — identical to V1 except the target step (§8): if no
  confirmed swing high above the trigger exists within the swing lookback,
  `target = trigger + min_rr × risk` (a measured-3R target) instead of
  rejecting `NO_RESISTANCE`. If a confirmed swing high above the trigger
  *does* exist, nothing changes — the nearest such high is still the
  target, still gated by `RR_BELOW_3` if it's closer than `min_rr × risk`.
  **The 3:1 R:R is true by construction for every measured-3R setup** — the
  hypothesis actually under test becomes "does a structural-stop +
  exactly-3R-target entry have positive expectancy," not "was a discovered
  resistance level a genuine edge." **The primary analysis pools both
  target sources** (`RESISTANCE` and `MEASURED_3R`) into one V3 sample; a
  report stratified by `target_source` is secondary/diagnostic only, never
  a second primary hypothesis. **Caveat:** "no resistance" means none
  confirmed within `swing_lookback_bars` (50 for V1/V3, 120 for V2) — a
  genuine higher high further back is undetected by this check, not ruled
  out. A fixed `RR_EPSILON = 1e-9` tolerance on the R:R gate's comparison
  (both at setup-definition time, §8, and the outcome module's
  `CHASE_RR_BELOW_3` recompute, §12) is a documented implementation detail
  guarding against float drift around the exact `min_rr` boundary a
  measured-3R target sits on — not a loosening of the 3:1 threshold
  itself.
- **V4** (sensitivity, not yet implemented) — identical to V1 except the
  target is the highest high of the last 252 bars, if above `SH` (§8) — a
  direct test of whether a further-out, non-swing-level resistance
  reference changes the `NO_RESISTANCE` dominance V1/V2 showed above.

**No variant can be promoted to primary on the strength of the same replay
data that produced it.** If any of V2/V3/V4 looks more favorable than V1 in
the historical replay, that is read as "V1's result is/isn't sensitive to
this choice" — not as grounds to switch the production default. Promoting a
variant to primary requires evaluating it against **fresh data** it was not
already checked against (e.g. a later forward-shadow period, or a held-out
historical slice never used while comparing V1/V2/V3/V4) — otherwise this is
exactly the kind of after-the-fact cherry-picking pre-registration exists to
prevent. **Before freezing the pre-registration**, run the outcome-blind
feasibility check described in §8 — `COUNT` (never compute returns for)
fired setups per calendar year for V1-V4, adjusting parameters only in
response to these counts, only before the freeze, with the full history of
any such adjustment recorded in the pre-registration file.

**Minimum sample size — three concrete dimensions, not a single count.** A
variant's replay result is treated as **inconclusive** unless it produces,
over the replay period:
- at least **100 distinct fired setups**,
- across at least **30 distinct symbols**, and
- spanning at least **3 calendar years** of replay data.
(Reasoned default: a single dimension — fire count alone — can be satisfied
by a handful of symbols or a short, unusually active stretch; requiring
breadth across symbols and across years guards against a result that is
really just "this one stock had a good run" or "this one year was
unusually favorable.")

**Baseline — a mechanics-matched control, averaged over all eligible peers on
the fill date, giving exactly one paired difference per setup.** For each V1
(or V2/V3/V4) setup that actually **fills** on a given date, the control is
**not** a single peer, nor a set of individually-compared peers — it is the
**mean realized R-multiple across every symbol that passed §4's trend and
liquidity filters on that same fill date** (not the full §5 setup definition
— just trend + liquidity), each hypothetically **entered at that date's
session open**, with a stop **and** a target **both sized to the same
ATR-multiples as the actual setup being compared** — i.e., if that day's real
setup had risk `= k_stop × ATR` and target `= k_target × ATR` (its own
realized values, **not a fixed 3R** — a setup's real resistance-based target,
§8, typically exceeds the 3:1 minimum), every eligible peer's hypothetical
control trade uses those same `k_stop`/`k_target` multiples, scaled to each
peer's own ATR — and the same 30-session timeout and stop-first tie-break
(§12) as every other outcome computation in this spec. This yields exactly
**one control value per fill date**: the mean R across that date's eligible
peers. **The comparison unit is therefore one paired difference per filled
setup** — `(setup's realized R) − (that date's mean peer-control R)` — not
two separately-compared distributions. **The month-cluster bootstrap (below)
resamples by calendar month over these paired differences**, each setup
contributing exactly one difference, clustered by the setup's own fill
month — not over the setups' and controls' raw R values taken separately.
Independently of that paired-difference bootstrap, **hit rate and mean R are
also reported separately for setups and for controls** (unpaired, purely
descriptive), so a reader can see the two groups' raw performance before
looking at the paired difference the bootstrap actually tests. This replaces
both the earlier "same-date peers" framing and the fixed-3R-target version of
the mechanics-matched control.

**Metric and interval (to be frozen in the pre-registration file, outlined
here):** primary metric is **mean R-multiple per setup** (realized R at
exit, §12, net of the costs assumption, §12), with a **month-cluster
bootstrap confidence interval** — resampling by calendar month rather than
by individual setup, since setups with overlapping hold periods (up to 30
sessions each) are not independent draws and a naive per-setup bootstrap
would understate the true uncertainty. **The paired differences described
above (setup R minus that fill date's mean peer-control R) are what the
bootstrap resamples**; each variant's and the baseline's raw R distributions
(hit rate, mean R) are additionally reported separately, unpaired, as
descriptive context (§13's baseline paragraph). **The pre-registration file
will contain the exact pass
rule** for declaring a variant distinguishable from baseline — including a
**majority-of-years condition** (e.g., outperforming the baseline in a
majority of the replay's calendar years, not merely in aggregate) — before
any run.

**The exact decision rule — V1's full parameter set, the V2/V3/V4
sensitivity definitions, the 100/30/3 thresholds, the costs assumption
(§12), the baseline's pass rule (including the majority-of-years condition),
the metric, and the bootstrap procedure — will be pre-registered in a
separate frozen file before any historical replay or forward-shadow run
begins** — so the evaluation can't be tuned after seeing results. This file
does not exist yet; creating it is out of scope for this document.

**Harness location and code reuse**: lives in `scripts/` (alongside
`scripts/populate_outcomes.py`, `scripts/run_strong_trend_scan.py`, etc.),
and **imports the same pure functions used in production** — `calc_swing_levels`,
`calc_atr`, the new setup-scoring function, the new outcome module (§12) —
rather than reimplementing them for backtesting. Any of the webapp's
bootstrap/setup code the harness needs (DB connection helpers, config
loading) is **copied in with its own tests**, not imported live from
production bootstrap paths — this keeps the harness decoupled from
incidental changes to the live app's startup sequence while still
guaranteeing the actual decision logic under test is identical to what runs
live.

---

## 13a. Survivorship and look-ahead

**Survivorship bias — stated, not solved.** The replay universe (§2/§13) is
built from **today's** `wl_state`/category/`security_type` snapshot of the
`watchlist` table, queried once and then walked backward in time — not a
historical, point-in-time list of what was enabled on each individual past
date. Any symbol that was delisted, merged,
or otherwise removed from the watchlist before today would be **absent**
from this replay even if it would have produced genuine historical setups.
This is textbook survivorship bias. It is not solved by this spec — it is
disclosed here as a known limitation of the replay's sample, to be weighed
when interpreting results, not corrected for.

**Data availability is evaluated as of each simulated scan date**, not once
at the start of the replay. For every historical date the replay harness
simulates, §3's data-validation checks (last-bar-date equality, no NaN,
volume > 0) are re-run against the data available as of *that* date — a
symbol that would have failed validation on a given historical date is
excluded from that date's candidate set, exactly as it would be live.

**Every indicator this spec uses computes only on data up to the scan
date — this is a hard rule, not limited to `calc_swing_levels()`.** §5
already establishes this truncation requirement for swing-level
confirmation; it generalizes to the trend filter's SMA150/SMA200 and the
20-session rising-SMA150 check (§4), ATR (§7), and the extension/regime
vetoes (§9) — every one of these, in both production and replay, must be
computed from a dataframe truncated to the scan date, never from a
dataframe that happens to extend further because it was convenient to fetch
once and reuse.

---

## 13b. Expected behavior

**Most days will produce zero setups.** This follows directly from §13's
"expected setup frequency" note — the combined filter stack (confirmed
uptrend, rising SMA150, a qualifying pullback, liquidity, extension veto,
regime veto, a 3:1-gated, resistance-observed target) is intentionally
restrictive, and zero qualifying setups on a given scan date is the normal,
expected outcome, not a sign of malfunction.

**On a zero-setup day, the bot still sends a message**: a one-line, silent
(`disable_notification=True`, §11) "no setups today" message. Rationale:
**so silence is never ambiguous.** Without this, a day with no Telegram
message would be indistinguishable from the scan job having failed to run
at all (crashed, skipped via `DATA_NOT_FINAL`, scheduler not firing) — an
explicit zero-result message lets the reader immediately tell "ran
successfully, found nothing" apart from "didn't run."

---

## 14. Rollout

1. **PR 0 — split into three independent PRs**, each separately mergeable:
   - **PR 0a — characterization tests on Stack A, PLUS one deliberate behavior
     change: a per-symbol try/except inside `check_alerts()`. MERGED — DONE
     (742 tests passing).** Add tests
     locking down *current* Stack A behavior before anything new touches
     shared helper functions — verdict-cutoff boundaries at score 55/75, the
     stop/TP ATR formula (1.5x/3.0x), and morning-scan filtering end-to-end
     (all three currently untested per `docs/CURRENT_ALERT_LOGIC.md` §10).
     **Then fix one existing defect, in the same PR**: wrap the per-symbol
     body of `check_alerts()`'s loop (`agent/core.py:253` onward) in its own
     `try/except`, so that one symbol raising an exception logs
     `[ALERT SKIP] <symbol>: <error>` via the `stocksage.*` logger (not
     `print()`) and the rest of the cycle continues for every other symbol —
     mirroring the per-symbol try/except `run_morning_scan()` already has
     (`agent/core.py:206-218`, `docs/CURRENT_ALERT_LOGIC.md` §5). **This is a
     bug fix, not new feature work**: `docs/CURRENT_ALERT_LOGIC.md` §1
     already documents that the alert loop currently has **no** per-symbol
     error handling of its own (an uncaught exception for one symbol would
     propagate out and abort the whole cycle, unlike the morning scan) — §1's
     non-goal governs feature work the new setup system does for itself, not
     fixes to a pre-existing gap in Stack A that this audit already
     identified. A regression test proves the new try/except: one symbol's
     `full_analysis()` (or fetch) raising still lets every other symbol in
     the same cycle alert normally.
   - **PR 0b — pin `requirements.txt` to the production-installed versions.
     MERGED — DONE.** No code change: closes the gap flagged in
     `docs/CURRENT_ALERT_LOGIC.md` §7/§8, where `ta`, `pandas`, `numpy`,
     `yfinance`, `python-telegram-bot` currently have zero version pins.
   - **PR 0c — the isolation test for the NEW code, independent of 0a/0b.
     Remaining — not yet started.** The new system's own modules (trigger job, outcome module, logging)
     must never import from or call into `agent/core.py`'s internals
     (`_alerted_this_session`, `was_alerted_today()`, the
     `schedule`/`asyncio.run()` loop) or share its dedup/cooldown state.
     Enforced by a structural test on the new code (e.g. asserting the new
     modules have no import path into `agent.core`'s private state) —
     this constrains only the *new* modules; it says nothing about, and does
     not conflict with, PR 0a's fix inside `check_alerts()` itself.
2. Data validation + trend/eligibility filter (§3-4).
3. Setup definition + trigger/stop/target math (§5-8).
4. Logging tables — candidates (with reason codes) + outcomes (§12).
5. Replay harness (§13), gated through three sequential milestones, each a
   hard gate before the next:
   a. **Outcome-blind feasibility count** (§8/§13) — `COUNT` fired setups
      per calendar year for V1-V4, no returns or R-multiples computed yet.
   b. **Freeze the pre-registration file and commit it to git** (§13) — the
      exact decision rule, V1-V4 definitions, the 100/30/3 thresholds, the
      costs assumption, the baseline's pass rule, the metric, and the
      bootstrap procedure, locked in version control before any outcome is
      looked at.
   c. **Only then**: the first full replay run that actually computes
      returns/R-multiples.
6. **Only then**: messaging (bilingual, both formats, §11) and
   forward-shadow wiring.

Each PR merged **outside US market hours** (not between 9:30-16:00 ET),
matching the spirit of the existing watchlist-apply guard's concurrency
caution, applied here to deployment timing instead of DB writes.

**Scope note (replay harness PR): split between a pure core and I/O, per
§13's own code-reuse rule.** `setups/replay.py` is the pure core (no
network, no file I/O, no DB) — given `{symbol: DataFrame}`, SPY's frame, a
list of scan dates, and `SetupParams`, it runs `evaluate_candidate()` then
`define_setup()` (and, when asked, `simulate_outcome()`) per symbol per
date and returns candidate rows; it also owns the one-open-setup-per-symbol
suppression and a permanent per-symbol dedupe on `(symbol, sh_date,
sl_date)` identity. `scripts/replay_setups.py` is the I/O half: a
read-only DB (or `--symbols-file`) universe, chunked yfinance downloads
with retries and an on-disk per-symbol cache (for reproducibility despite
`auto_adjust=True` retroactively rebasing historical prices on later
ex-dividend events), and the results sqlite file (never the bot's own DB).
`--counts-only` **is** milestone 5a above (outcome-blind, no gates).
`--run-outcomes` implements milestone 5c's mechanism but stays refused
until a human completes milestone 5b — `docs/PREREGISTRATION_SETUPS_V1.md`
does not exist yet, and creating it is still out of scope for this PR.

**Scope note (replay harness speed PR): wall-clock-only, no rule/parameter/
output change.** Two independent, stackable throughput fixes, both
verified against the pre-change implementation as the equivalence oracle
rather than trusted by inspection alone. (1) `--workers N`
(`scripts/replay_setups.py`, default 1 = the original sequential
behavior): a process pool where each worker loads its own symbol/SPY
frames straight from the on-disk cache (never downloads, never writes to
the results DB) and runs the same `process_symbol` code; the main process
writes every symbol's rows in one transaction, in sorted-symbol order,
regardless of the order workers finish in, preserving the existing
checkpoint/resume semantics. `replay_runs.workers` records the pool width
used but is deliberately excluded from `resume_run()`'s compatibility
check — it is a performance knob, not a semantics knob. (2)
`run_replay_core_counts_only_both_bounds()` (`setups/replay.py`): counts-
only mode used to call `evaluate_candidate()`/`define_setup()` once per
bound (UPPER, LOWER) per scan_date; since both functions are pure given
`(df_full, scan_date, spy_df, params)` and never read the suppression
state that differs between bounds, this now runs that expensive pair once
per scan_date and replays the cheap suppression/dedupe bookkeeping twice.
`run_replay_core()` itself is left unchanged and serves as the permanent
regression oracle the two-bound-equivalence tests compare against.
`scripts/compare_replay_runs.py` is a new read-only tool (not part of the
replay harness itself) for diffing two results DBs' `replay_candidates`
tables column-by-column, used as the hand-run acceptance check that a
speed change produced byte-identical output against the baseline on the
same cache.

**Scope note (control builder + statistics PR): milestone 5c tooling,
built and tested on synthetic data only.** `setups/analysis.py` (pure)
implements `docs/PREREGISTRATION_SETUPS_V1.md` sections 6-8 and the
section-13 clarifications added before freeze: a mechanics-matched peer
control per primary trade (synthetic `SetupDefinition` sized to the real
trade's own ATR-multiples, reusing `simulate_outcome()` unmodified), the
month-cluster bootstrap, and `decide()`'s five-condition PASS/FAIL/
INCONCLUSIVE rule. `scripts/analyze_replay.py` (I/O) is the confirmatory
pipeline: reads an existing `--run-outcomes` results DB read-only, never
trusts its stored outcome columns (§13.5 — `OutcomeResult.status` is
never actually persisted there; every accepted row's authoritative
outcome is recomputed against the same price cache and compared against
the stored fields), and labels its own report CONFIRMATORY only when
every §13.6 condition holds (frozen variant/slippage/seed/draws, the
pre-registration file committed in git HEAD, a clean working tree,
matching library versions, cache-vs-manifest sha256 match, zero
stored-vs-recomputed mismatches) — otherwise EXPLORATORY, naming every
failed condition. The `--peer-counts-only` diagnostic (§7's ambiguity
over "fill-eve" peer counts when no outcome has been computed) is its own
script, `scripts/peer_counts.py`, kept structurally separate so it can
never influence the confirmatory label. Per the ordering agreed for this
PR: this code (and its tests, synthetic data only) is committed BEFORE
`docs/PREREGISTRATION_SETUPS_V1.md` is frozen and committed, with that
file's `FREEZE_COMMIT` field naming this commit — the §14 gate applies to
running `--run-outcomes` and to opening real outcome results, not to
writing and testing code against synthetic data.

**Scope note (pre-freeze hardening): real process pool + scale rehearsal,
still synthetic data only.** Two checks added before freeze, neither found
a bug: (1) a test running `scripts/analyze_replay.py`'s full pipeline
under a REAL `ProcessPoolExecutor` (`--workers 2`, Windows spawn — not the
fake in-process pool the rest of the `--workers` test suite uses) on the
small synthetic end-to-end fixture, asserting the report is byte-identical
to `--workers 1`; proves `_pass1_worker`/`_pass2_worker`/`_pass3_worker`
are genuinely picklable top-level functions and that spawn re-importing
the module changes nothing. (2) A one-off, throwaway, uncommitted
rehearsal script (run outside the repo, deleted afterward) built a
~120-symbol synthetic universe over ~1500 sessions and ran the real
`--run-outcomes --variants V3 --workers 8` replay followed by
`scripts/analyze_replay.py --workers 8` with the frozen parameters, through
a disposable temp git repo with every CONFIRMATORY gate satisfied.
Results: replay stage ~340s, analysis stage ~410s (~750s total on this
machine, with other work running concurrently — expect less in isolation);
4,680 accepted setups, 1,223 primary trades with usable peer controls,
~110 eligible peers per trade on average (this synthetic universe's
symbols mostly trend together, so eligibility is high); peak main-process
working set ~266MB (workers' own memory isn't included); label
**CONFIRMATORY with an empty `failed_conditions` list** (asserted); the
confirmatory `decide()` verdict itself came out FAIL on this synthetic,
no-real-edge universe, which is an expected and acceptable outcome at
rehearsal scale — the rehearsal checks the PIPELINE runs correctly and
reports CONFIRMATORY correctly at this scale, not that a manufactured edge
should be found.

**Scope note: `OutcomeResult` gains `resolved_date`.** The outcome module
(§12) now also returns the date as of which a setup/position is
definitively closed — `exit_date` for a resolved trade, `fill_date` for
`CHASE_RR_BELOW_3`, the breaching session for `INVALIDATED_BEFORE_FILL`,
`validity_end` for `EXPIRED_UNFILLED`, and `None` for `DATA_GAP`/
`OPEN_AT_END` (genuinely unresolved — the replay harness itself falls back
to the latest date the position could *possibly* still be open: the
setup's own `validity_end` if never filled, or `fill_date` plus
`timeout_sessions` expected sessions if it was). This is what lets the
replay harness's suppression logic know exactly when a symbol is free for
a new setup, without re-deriving per-status logic of its own.

---

## 15. Open decisions for the owner

1. **SETTLED 2026-10-22 — Live scan universe** (§2): `wl_state IN
   ('ACTIVE','MONITOR')` + category/`security_type` exclusions, exactly as
   specified, confirmed by the owner.
2. **SETTLED 2026-10-22 — Stop cap** (§7): `min(12% of entry, 5×ATR)`,
   confirmed as proposed.
3. **SETTLED 2026-10-22 — Validity window**: 5 trading sessions, confirmed
   as-is (§6).
4. **SETTLED 2026-10-22 — Evaluation thresholds** (§13): **100 setups / 30
   distinct symbols / 3 calendar years**, the month-cluster bootstrap metric
   procedure, and the mechanics-matched baseline's majority-of-years pass
   rule, confirmed as proposed. (Still to be copied verbatim into the
   pre-registration file itself before any replay run, per §13/§14.)
5. **SETTLED 2026-10-22 — Costs assumption** (§12): 10 bps slippage each
   way, zero commission, confirmed as proposed.
6. Earnings informational line (§9): the data source's *existence*
   (`yfinance.Ticker.calendar`/`.earnings_dates`) is now a confirmed fact,
   not an open question (see below) — but its field reliability/format in a
   live call is still unverified and should be confirmed before the
   messaging PR implements it.
7. **`find_confirmed_swing_highs()` (§5) is now IMPLEMENTED** (rollout
   step 3, `setups/swings.py`) — no longer described by required behavior
   only. `get_multiple_prices_with_date()` (§6) remains unimplemented and
   out of scope (§14, rollout steps 4+); their actual implementation and
   tests were out of scope for this document only before this step.
8. **Scan timing slot** (§3/§15 agreement): 03:00 IL is proposed specifically
   to avoid contention with the 01:30 IL composite job
   (`scripts/log_composite_signals.py`), not because of any dependency on the
   02:30 IL watchlist evaluation's output. Before fixing 03:00 as the actual
   slot, check that job's typical run duration in
   `logs/watchlist_evaluation_task.log` to confirm it doesn't itself run long
   enough to still collide with 03:00.

**Three items to verify before coding (not yet confirmed from the repository
alone):**

- **Where `security_type` actually lives.** It is **both** a stored column
  (`db/database.py:144`, `watchlist.security_type TEXT DEFAULT 'stock'`,
  added in the v2 migration documented at `db/database.py:77`) **and** a
  value recomputed per-row on demand via `classify_security_type()`
  (`analyzers/eligibility.py:30-38`) whenever the stored value is missing —
  the existing fallback pattern is `row["security_type"] or
  classify_security_type(sym)` (`services/watchlist_evaluator.py:1041`).
  §2's universe query must decide, and state explicitly, whether it trusts
  the stored column as-is or re-derives it per row the same way
  `watchlist_evaluator.py` does — a stale stored value (e.g. a symbol
  reclassified after the column was last written) would otherwise silently
  diverge between this spec's universe and the existing evaluator's.
- **`auto_adjust=True` and ex-dividend price shifts.** Every `yfinance` call
  this spec relies on indirectly (`data/fetcher.py:46,65,117`,
  `data/market_data_validator.py:410,465`) fetches with `auto_adjust=True`,
  meaning stored price levels (and therefore `SH`, `SL`, stop, target, and
  ATR) are in **adjusted-price units**. If an ex-dividend date falls inside a
  pending setup's validity window (§6) or its 30-session outcome-tracking
  window (§12), previously-computed levels for that symbol shift slightly
  (adjusted prices are retroactively rebased on each new ex-div event).
  **Decision: accept this, log it, treat as low priority** — the shift is
  typically a few cents to low single-digit percent for ordinary dividend
  payers and does not invalidate the setup, but it should be logged
  (symbol + ex-div date + affected setup) rather than silently ignored, so a
  future investigation into an unexpected outcome can rule it out or in.
- **The exact helper names**, restated here for a single point of reference:
  `find_confirmed_swing_highs(df, lookback=50)` (§5, swing-high detection
  only — see above), `get_multiple_prices_with_date(symbols)` (§6, live
  fetch plus bar date), and the new outcome module's entry point (§12,
  name not yet chosen — parity-tested against `compute_outcome()`,
  `scripts/populate_outcomes.py:86-96`). None of the three has an
  implementation yet; this document specifies required behavior only.

**Already settled** (listed here for completeness, not open): weekly
alignment deferred to v2; market regime is a hard veto, logged on every
candidate; extension veto = 6×ATR above SMA150; rising-SMA150 (vs. 20
sessions ago) is required; pivot points are excluded from v1 entirely (no
secondary reference either); at most one open setup *or tracked position*
per symbol (§12); `RISK_PER_TRADE_USD` is read from the existing untracked
`.env` and never logged; the EOD list is delivered silently
(`disable_notification=True`) right after the 03:00 IL computation (staggered
after the 01:30 IL `log_composite_signals.py` job purely to avoid resource
contention, with no data dependency on the 02:30 IL watchlist evaluation,
§2/§3); the **trigger alert uses normal, non-silent notifications** (§11);
the outcome logic lives in a new, separate module (not editing
`populate_outcomes.py`), parity-tested against `compute_outcome()`; **V3 is
the primary pre-registered hypothesis** (promoted from V1 on 2026-10-25,
outcome-blind, per the feasibility-count evidence in §13 — V1/V2 are now
sensitivity analyses, V4 remains sensitivity-only and unimplemented); the
trigger is a **touch**, not a confirmed break — `HIGH ≥ SH`,
no added buffer (§5 item 6); the baseline is a **mechanics-matched control**,
not same-date peers (§13); PR 0c's isolation requirement is enforced as a
test on the new code, constraining only the new modules — it is independent
of, and does not exclude, PR 0a's own deliberate, justified fix inside
`check_alerts()` (the per-symbol try/except, §1/§14). **Settled by the owner
on 2026-10-22** (items 1-5 above): the live scan universe definition (§2);
the `min(12% of entry, 5×ATR)` stop cap (§7); the 5-trading-session validity
window (§6); the 100 setups / 30 symbols / 3 calendar years evaluation
thresholds with the month-cluster bootstrap and majority-of-years baseline
pass rule (§13); and the 10-bps-each-way/zero-commission costs assumption
(§12).

**Confirmed facts (no longer open), with citations:**
- `get_watchlist()` is defined at `db/database.py:611-623`; `get_active_watchlist()`
  at `db/database.py:626-642` — both re-read directly against the current
  code, not cited secondhand. Neither can supply the `wl_state`-filtered,
  two-state universe §2 now requires; a new query is needed (§2).
- `calc_swing_levels()`'s exact return shape (`analyzers/technical.py:229-258`)
  is price-levels-only, with no bar dates or indices — confirmed by direct
  reading of the function body, quoted in full in §5.
- `get_multiple_prices()`'s exact return shape (`data/fetcher.py:59-112`,
  fields quoted in §6) has no bar-date field — confirmed by direct reading.
- The locally installed `yfinance` package exposes `Ticker.calendar` and
  `Ticker.earnings_dates` as class attributes — confirmed by static, offline
  inspection of the installed package (no network call made). What remains
  genuinely unknown is listed in the section below, not here.

---

## Behaviors / facts this document could not independently verify

- Whether `yfinance.Ticker.calendar`/`.earnings_dates` return reliable,
  consistently-formatted data in practice — only their existence as class
  attributes was confirmed (via static, offline inspection of the locally
  installed package; no live call was made).
- Any production-vs-development difference in installed `yfinance` version:
  the locally installed copy in this working tree reports `1.2.0`, which
  does not match `CLAUDE.md`'s claimed production version `1.5.1` — this is
  consistent with `docs/CURRENT_ALERT_LOGIC.md`'s existing finding that
  dependency versions are unpinned in `requirements.txt` and therefore
  unverifiable from the repository alone; PR 0b (§14) is where this gets
  resolved for the production server specifically.
