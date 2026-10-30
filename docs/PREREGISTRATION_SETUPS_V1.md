# Pre-registration: swing setups, V3-primary (version 1)

STATUS: DRAFT until frozen. Frozen means this file and docs/universe_v1.txt are committed to git in the commit immediately after FREEZE_COMMIT (the commit containing the analysis code named in section 9), before the outcomes run is started. Nothing below may be changed after the outcomes run has produced results.

- FREEZE_COMMIT (the commit this file's rules refer to): `39e3edba542d5c2b61ba5d1eabf0b501d05d73c2`

## 1. Hypothesis (one sentence)

In US-listed stocks from the owner's watchlist, buying a confirmed breakout above the last swing high, after a pullback of at least one ATR inside an uptrend and with a structural stop and a 3R target, earns a positive net R per trade that is also higher than the R of matched control trades entered on the same days.

This is the only confirmatory hypothesis. Everything else in section 8 is descriptive.

## 2. Why this version exists (evidence, all outcome-blind)

- V1 (target must be the nearest confirmed resistance, at least 3R away) and V2 (same, 120-bar lookback) produced zero setups on a 40-stock random sample over 3 years (about 30,040 symbol-nights each). 2,073 nights passed every structural check and died at the target step (V1: 848 NO_RESISTANCE, 1,225 RR_BELOW_3).
- V3 changes only the target step: when no confirmed swing high exists above the trigger within the lookback, the target is trigger + 3 x risk. When a confirmed swing high exists, V3 behaves exactly like V1.
- Full-universe counts-only run (371 stocks, 2021-10-25 to 2026-10-20): V3 produced 1,402 setups (lower bound) to 2,469 (upper bound) from 285 stocks across 6 calendar years.
- Consequence to state plainly: for setups whose target is measured (MEASURED_3R), the 3:1 reward-to-risk holds by construction. The hypothesis is therefore about the expectancy of structural-stop plus 3R-target entries, not about a discovered resistance level.
- Prior: the owner's earlier backtest of the composite scoring engine (run full_2y_run1, 5,044 signals, 2024-05-21 to 2026-09-21) found a win rate of 52.9% and near-zero correlation between its layer scores and outcomes, and did not meet its own pre-committed threshold; earlier pattern research is recorded as having found no edge. There is no positive prior for an edge here.

## 3. Data and universe

- Universe: the 367 symbols in `docs/universe_v1.txt` (sha256 of the file with carriage returns removed, `tr -d '\r' < docs/universe_v1.txt | sha256sum`: `5af1ce933cf5152e517094758393909130274dafb0bd8971cdb045e8d89364be`): wl_state ACTIVE or MONITOR in the bot database snapshot of 2026-10-24, excluding ETF, index and crypto rows, and without the four symbols that could not be downloaded (QRVO, EA, NFBK, WBS). This is today's watchlist (see limitations).
- Scan dates: every trading session in SPY's index from 2021-10-25 to 2026-08-14. Price data is requested through 2026-10-23 (the scan end plus 70 calendar days; the end is exclusive in yfinance, so the last bar is 2026-10-22), which leaves every setup room for its full 5-session window plus the 30-session hold.
- Source: yfinance 1.5.1, daily bars, auto_adjust=True. Prices were downloaded once and copied to a frozen cache; the outcomes run and the analysis read only that frozen copy (`--cache-dir <frozen copy> --cache-only`). Library versions equal requirements.txt (pandas 3.0.3, numpy 2.5.1, ta 0.11.0).
- Data fingerprint: sha256 of the sorted data manifest (one line per symbol, `symbol|rows|first_date|last_date|sha256`, 367 lines) = `ac3e1e4f1cab1030a7ed090b049ee041561259b7d2c7b7e3c8c9db977db99900`. After the outcomes run, the same fingerprint computed from that run's own manifest must equal this value; otherwise the run is void.
- Confirmatory command: `scripts/replay_setups.py --symbols-file docs/universe_v1.txt --start 2021-10-25 --end 2026-08-14 --output <new file outside the repo> --run-outcomes --variants V3 --cache-dir <frozen copy> --cache-only --workers 8`.

## 4. Frozen rules

The rules are the code at FREEZE_COMMIT (setups/definition.py, setups/outcomes.py, setups/params.py, setups/evaluate.py, setups/trend_filter.py, setups/data_validation.py). Variant: V3 (`V3_PARAMS`). The numeric settings, all set by reasoning before any outcome was seen (23 in total):

| Setting | Value |
|---|---|
| ATR period | 14 |
| Trend filter | close > SMA150, SMA150 > SMA200, SMA150 above its value 20 sessions ago |
| SMA windows | 150, 200 |
| Regime | SPY close > SPY SMA150, otherwise no setup |
| Swing highs | 5-bar fractal (2 bars each side), lookback 50 bars |
| Minimum history | 220 bars with a finite close |
| Liquidity (20-session window) | average volume >= 250,000; average dollar volume >= 10,000,000; price >= 3.0 |
| Pullback depth | SH minus lowest low after SH >= 1.0 ATR |
| Pullback low age | at least 2 and at most 15 sessions |
| Stop | pullback low minus 0.5 ATR |
| Stop cap | risk <= min(12% of trigger, 5 ATR), otherwise rejected |
| Target | nearest confirmed swing high above trigger; if none, trigger + 3 x risk |
| Minimum reward:risk | 3.0 (float tolerance 1e-9) |
| Extension veto | close - SMA150 > 6 ATR rejects |
| Validity window | 5 sessions after the scan date |
| Timeout | 30 sessions after the fill session |
| Slippage | 10 bps each way, no commission |

One open setup per symbol; the same (symbol, SH date, SL date) identity is never counted twice.

## 5. Trade simulation (frozen in setups/outcomes.py)

A buy stop at the trigger fills on the first session in the window whose high reaches it, at max(trigger, open). Reward:risk is recomputed at the fill price and the trade is skipped when below 3.0 (CHASE_RR_BELOW_3). A fill session that also trades through the stop is a filled trade stopped out the same day and is included. After the fill: stop is checked before target on the same bar; a session opening at or below the stop exits at the open; a target exit is at the target (gaps above are not credited); timeout exits at the close of the 30th session after the fill. R = (net exit - net entry) / (fill - stop), with slippage applied to the prices only.

Primary trades are those that end as stop, target, timeout or same-day stop. Not trades (counted and reported): invalidated before fill, expired unfilled, chase rejected. Excluded (counted and reported, with their share): DATA_GAP and OPEN_AT_END.

## 6. Control (mechanics-matched)

For each primary trade with fill date d, stop distance s and target distance t (both in units of the setup's ATR at the scan date):
- Peers: every other universe stock that on the session before d passed data validation and the trend and liquidity filters.
- Each peer is entered at the open of d with stop = open - s x ATR(peer) and target = open + t x ATR(peer), the same 30-session timeout, the same exit rules and the same costs.
- The control value for the trade is the equal-weighted mean of the peers' net R. A trade with fewer than 20 peers is excluded and counted.

## 7. Primary measure, inference and decision rule

- Primary quantity: the mean over primary trades of (setup net R minus control value).
- Interval: 95% percentile interval from a month-cluster bootstrap (resample calendar months of the fill date with replacement), 10,000 draws, seed 20261029.
- Periods: calendar years of the fill date. 2021 is partial (from 2021-10-25), 2026 is partial.

**PASS requires all of:**
1. Sample: at least 100 primary trades, 30 distinct symbols, 24 distinct fill months and 3 calendar years.
2. Mean net R of the setups is greater than 0.
3. The lower bound of the interval for the mean paired difference is greater than 0.
4. The mean paired difference is positive in at least 4 of the periods that have at least 30 trades (fewer than 4 such periods makes the result INCONCLUSIVE).
5. No single symbol supplies more than 5% of primary trades and no single month more than 15%.

**INCONCLUSIVE:** condition 1, 4 (too few periods) or 5 fails. **FAIL:** conditions 1 and 5 hold and any of 2, 3 or 4 fails.

## 8. Non-confirmatory analyses (reported, never used to select anything)

- The same analysis with slippage 20 bps each way (always computed in the same report, recomputed from stored prices).
- Results by target_source (RESISTANCE and MEASURED_3R), by year, by regime day, and by symbol and month concentration.
- Win rate, average win and loss in R, MAE and MFE, share of timeouts, and the shares of chase-rejected, expired, invalidated, excluded and no-peer trades.
- V1 and V2: zero setups in the feasibility sample; no outcome run.

## 9. Analysis code

`scripts/analyze_replay.py` implements sections 6 and 7. It is written and tested on synthetic data before the freeze, committed at or before FREEZE_COMMIT, and must not change afterwards. A bug found after results are seen is reported, the analysis is re-run unchanged with only the bug fixed, and both outputs are published.

## 10. Known limitations

- Survivorship and hindsight: the universe is today's watchlist, chosen by the owner, which likely favors winners. The paired difference mostly cancels this because the control comes from the same universe; condition 2 (absolute net R) does not.
- The universe is about 40% financials (mostly banks), which move together; trades are not independent. The month-cluster interval addresses part of this.
- Daily bars only; the entry is assumed to fill at the trigger or the open, and a same-session sequence of high and low is unknown (the conservative reading is used).
- Adjusted prices: levels are in adjusted units; a dividend inside a window shifts them slightly.
- The "no resistance" test only looks 50 bars back; a higher high further back may exist.
- Four delisted or acquired symbols are missing, which adds a little survivorship.

## 11. What happens after the result

- PASS: build the nightly scan and the trigger job and run them in shadow mode (logging, no alerts) for at least 8 weeks. A separate forward rule is written before shadow mode starts. Going live is a separate decision.
- FAIL or INCONCLUSIVE: nothing is wired into the bot. The result is recorded in docs/SETUP_SPEC.md. No rule is tuned on this data. Any new rule is a new version with its own pre-registration and is judged only on data that did not exist at the time of writing.

## 12. Integrity

1. Freeze order. FREEZE_COMMIT is the commit that contains the frozen analysis code. The very next commit adds only this file and docs/universe_v1.txt. Before the run, `git diff --name-only <FREEZE_COMMIT> HEAD` must list exactly those two files, and both commits are pushed to GitHub before the run starts, as a timestamped record.
2. Where it runs. The confirmatory outcomes run and the analysis are executed once, from a dedicated clean worktree checked out at that second commit, in the pinned environment (library versions equal requirements.txt). Nothing edits files in that worktree while they run.
3. The outcomes run uses the command in section 3: `--run-outcomes --variants V3 --cache-dir <frozen copy> --cache-only`. The results file and every report are written outside the repo.
4. After the run, the data fingerprint computed from the run's own manifest must equal the value in section 3; otherwise the run is void.
5. The analysis is `scripts/analyze_replay.py --results-db <results file> --cache-dir <frozen copy> --output <report outside the repo>`. Only a report labelled CONFIRMATORY counts as the confirmatory result. If the label is EXPLORATORY only because of an environment condition (dirty tree, library versions), the analysis may be re-run unchanged after fixing that condition; the results file is never regenerated. Any other reason for EXPLORATORY means the run does not count, and the reasons are recorded.
6. Failures. A crash may be resumed with `--resume-run-id`. If a run fails before any result has been looked at, it may be restarted from scratch with identical inputs. Any rerun with different inputs after looking at results is exploratory and cannot replace the confirmatory result.
7. The report is saved unmodified, and its verdict is recorded in docs/SETUP_SPEC.md whatever it is.


## 13. Clarifications added before the freeze

These resolve ambiguities found while implementing sections 6 and 7. They are part of the pre-registration.

1. Distances. For each primary trade, s = (trigger - stop) / ATR and t = (target - trigger) / ATR, using the setup's own trigger, stop and target and its ATR at the scan date.
2. Peer data. A peer's eligibility and its ATR use only bars up to and including the session before d. The peer's entry and exit use bars from d onward.
3. Peer counting. A peer counts toward the 20-peer minimum only if its simulated trade produced an R. Peers that end as data gap or open at end are excluded and counted.
4. Excluded trades. A primary trade with fewer than 20 usable peers is removed from every quantity in section 7 (all five conditions) and reported separately. For transparency the report also shows the mean net R of all primary trades, including the excluded ones.
5. Authoritative status. The analysis does not trust the status stored in the results file. It re-runs the frozen simulator on the cached prices for every accepted setup and compares the result with the stored values. Any mismatch makes the whole run EXPLORATORY.
6. Labels. The report says CONFIRMATORY only when all of these hold: variant V3; slippage 10 bps; bootstrap seed 20261029 with 10,000 draws; this file is committed in git HEAD; the working tree is clean; library versions equal requirements.txt; and the cached prices match the data manifest of the outcomes run. Otherwise it says EXPLORATORY.
7. Secondary slippage. The 20 bps analysis recomputes R from the stored fill and exit prices; it does not re-run the simulation.
8. Run header. CONFIRMATORY additionally requires that the results file's run header says mode run_outcomes, variants V3 only, slippage 10 bps (from its recorded outcome parameters), and a recorded pre-registration hash equal to the sha256 of this file as committed in git HEAD.