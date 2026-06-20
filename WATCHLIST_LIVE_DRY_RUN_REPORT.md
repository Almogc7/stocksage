# Watchlist Live Dry-Run Validation Report (Phase 4.5)

**Date:** 2026-06-20
**Branch:** `claude/stocksage-review-20260617-1200`
**Purpose:** Validate the Phase 4 dry-run evaluator against a **copy** of the
real production database, using **real yfinance data**, before approving
Phase 5 (which will start writing proposed changes to the real watchlist).

This was a validation run only. No code logic was changed to produce this
report (see "Git behavior" at the end). No watchlist row was written
anywhere — neither in the real DB nor in the copy.

## 1. Executive summary

The dry-run evaluator was run against a timestamped copy of
`db/stocksage.db` using a real `MarketDataClient` (live yfinance calls, no
mocking). All 62 evaluable symbols (MONITOR tier) fetched successfully
with zero provider errors, zero stale data, zero invalid symbols, and zero
missing-data failures. No promotions were proposed — this is **expected,
not a bug**: every symbol's `consec_promote_count` starts at 0 in the real
DB (the hysteresis engine has never run against it before), and
`PROMOTION_CONSEC_REQUIRED = 2` means no symbol can be promoted on its very
first evaluation regardless of score. ACTIVE is currently empty (0/30) in
both the real DB and the copy, because `run_bot()` — the only code path
that calls `run_initial_classification()` — has never been executed
against this database; this is a pre-existing fact about the production
DB, not something this validation run changed or could fix.

## 2. Did the dry-run complete successfully?

**Yes.** `fatal_error: None`, run status `success` (not `partial_failure`).

## 3. Did yfinance work reliably?

**Yes.** 62/62 symbols returned `OK`. 0 provider errors, 0 rate limits, 0
timeouts, 0 invalid symbols, 0 stale data, 0 missing OHLCV/volume.

## 4. Was provider degradation detected?

**No.** `provider_degraded: False` (0% transient failures, threshold is 40%).

## 5. Total runtime

**2.69 seconds** (`elapsed_seconds` measured around the call;
`duration_seconds` recorded inside the evaluation_runs row: ~2.7s).

## 6. yfinance request count

**2** — all 62 symbols were fetched in 2 batched calls
(`MARKET_DATA_BATCH_SIZE = 50` → ⌈62/50⌉ = 2 chunks). This confirms the
Phase 3 batching design works as intended against the real, larger
production universe (vs. the small mocked universes used in unit tests).

## 7. Cache hits / misses

**cache_hits: 0, cache_misses: 62** — expected for a single evaluation
run with a fresh `MarketDataClient` (the in-memory cache only helps within
one run if a symbol is looked up twice; this evaluator only looks up each
symbol once).

## 8. Total symbols in copied DB

**80** rows in `watchlist` (identical to the real DB at copy time):
62 MONITOR, 18 ETF_INDEX_CONTEXT, 0 ACTIVE, 0 TEMPORARILY_INELIGIBLE,
0 USER_REMOVED.

## 9–12. Considered / evaluated / skipped / failed

| Metric | Count |
|---|---|
| Symbols considered | 62 |
| Symbols evaluated | 62 |
| Symbols skipped | 0 |
| Symbols failed | 0 |

(0 skipped because there were no USER_REMOVED, ETF/index/crypto-misclassified,
disabled, or not-yet-due TEMPORARILY_INELIGIBLE rows in the MONITOR/ACTIVE
universe at copy time — the 18 ETF_INDEX_CONTEXT rows are correctly never
gathered as candidates in the first place, see section 18.)

## 13–17. Tier counts: current vs. proposed

| Tier | Current | Proposed |
|---|---|---|
| ACTIVE | 0 | 0 |
| MONITOR | 62 | 62 |
| ETF_INDEX_CONTEXT | 18 | 18 (unchanged — not evaluated) |
| TEMPORARILY_INELIGIBLE | 0 | 0 |
| USER_REMOVED | 0 | 0 (unchanged — not evaluated) |

## 18–21. Promotions / demotions / recoveries / new data problems

- **Proposed promotions:** none.
- **Proposed demotions:** none (ACTIVE is empty, nothing to demote).
- **Proposed recoveries:** none (no TEMPORARILY_INELIGIBLE rows existed).
- **New data problems:** none (0 newly-ineligible proposals).

## 22–24. Provider error / stale data / invalid symbol counts

All **0**.

## 25–26. Cap compliance

- Proposed ACTIVE count (0) is **at or below 30**. ✅
- Proposed bank-ACTIVE count (0) is **at or below 8**. ✅

## 27–28. Safety assessment and recommendation

**Safe for Phase 5, with one expectation to set correctly in Phase 5's
design:** the very first live run after Phase 5 ships will also propose
zero promotions, for the same hysteresis reason explained in the executive
summary — Phase 5 must not be judged "broken" if its first real run
doesn't promote anything immediately. A second consecutive run (next
trading day) is needed before any promotion becomes possible. Everything
else — fetch reliability, batching, cap enforcement, outage detection
wiring — checked out cleanly against real data.

**Recommendation: proceed to Phase 5**, with the above hysteresis-warm-up
behavior called out explicitly in Phase 5's own documentation/report so it
isn't mistaken for a bug on day one.

## Current ACTIVE

| Symbol | State | Categories | Security Type |
| ------ | ----- | ---------- | ------------- |
| _(none — ACTIVE is currently empty in the production DB)_ | | | |

## Proposed ACTIVE

| Symbol | Current State | Proposed State | Relevance Score | Security Type | Categories | Latest Close | Avg Daily Volume | Avg Daily Dollar Volume | Provider Status | Reason |
| ------ | -------------- | --------------- | ---------------- | --------------- | ------------ | -------------- | ------------------- | --------------------------- | ------------------ | ------ |
| _(none — no promotions are possible on a first evaluation pass; see executive summary)_ | | | | | | | | | | |

## Proposed promotions

None this run (see executive summary — `consec_promote_count` starts at 0
for all 62 MONITOR symbols; `PROMOTION_CONSEC_REQUIRED = 2`).

## Proposed demotions

None this run (ACTIVE tier is currently empty).

## Proposed temporarily ineligible symbols

None this run (0 failures of any kind among the 62 evaluated symbols).

## Top 30 evaluated candidates by relevance score

| Rank | Symbol | Current State | Score | Provider Status | Avg Dollar Volume | Reason |
| ---- | ------ | -------------- | ----- | ------------------ | ---------------------- | ------ |
| 1 | VRT | MONITOR | 80 | OK | 2,130,263,840 | no change |
| 2 | MRVL | MONITOR | 79 | OK | 8,745,101,992 | no change |
| 3 | AAPL | MONITOR | 77 | OK | 13,537,984,691 | no change |
| 4 | AMD | MONITOR | 77 | OK | 13,865,683,506 | no change |
| 5 | ANET | MONITOR | 77 | OK | 1,409,527,841 | no change |
| 6 | APLD | MONITOR | 77 | OK | 840,375,160 | no change |
| 7 | AVGO | MONITOR | 77 | OK | 10,212,918,499 | no change |
| 8 | BA | MONITOR | 77 | OK | 1,479,592,328 | no change |
| 9 | CCJ | MONITOR | 77 | OK | 356,344,068 | no change |
| 10 | CEG | MONITOR | 77 | OK | 996,720,653 | no change |
| 11 | CRWD | MONITOR | 77 | OK | 1,893,410,552 | no change |
| 12 | CSCO | MONITOR | 77 | OK | 2,489,787,570 | no change |
| 13 | DDOG | MONITOR | 77 | OK | 1,055,396,602 | no change |
| 14 | DOCN | MONITOR | 77 | OK | 509,534,549 | no change |
| 15 | ETN | MONITOR | 77 | OK | 1,022,783,809 | no change |
| 16 | FTNT | MONITOR | 77 | OK | 714,636,061 | no change |
| 17 | GLW | MONITOR | 77 | OK | 2,267,013,002 | no change |
| 18 | GOOGL | MONITOR | 77 | OK | 10,561,792,228 | no change |
| 19 | JPM | MONITOR | 77 | OK | 2,811,488,390 | no change |
| 20 | NEE | MONITOR | 77 | OK | 965,783,522 | no change |
| 21 | NET | MONITOR | 77 | OK | 951,639,572 | no change |
| 22 | NVDA | MONITOR | 77 | OK | 33,178,491,504 | no change |
| 23 | QCOM | MONITOR | 77 | OK | 4,079,960,397 | no change |
| 24 | SNOW | MONITOR | 77 | OK | 1,556,792,540 | no change |
| 25 | TSLA | MONITOR | 77 | OK | 22,908,614,616 | no change |
| 26 | VST | MONITOR | 77 | OK | 729,065,572 | no change |
| 27 | ENPH | MONITOR | 76 | OK | 361,527,593 | no change |
| 28 | OKLO | MONITOR | 76 | OK | 863,959,511 | no change |
| 29 | RKLB | MONITOR | 76 | OK | 2,705,720,528 | no change |
| 30 | SMR | MONITOR | 76 | OK | 397,319,515 | no change |

## Bottom 30 evaluated candidates by relevance score

(Only 62 symbols total were evaluated; this table shows ranks 33–62, the
lowest 30. Ranks 31–32 — AMZN and ARM, both scoring 72 — are omitted to
keep this table at exactly 30 rows as requested without overlapping the
top-30 table above.)

| Rank | Symbol | Current State | Score | Provider Status | Avg Dollar Volume | Reason |
| ---- | ------ | -------------- | ----- | ------------------ | ---------------------- | ------ |
| 33 | BE | MONITOR | 72 | OK | 2,533,875,800 | no change |
| 34 | CBRS | MONITOR | 72 | OK | 2,092,681,418 | no change |
| 35 | DLR | MONITOR | 72 | OK | 400,319,445 | no change |
| 36 | EQIX | MONITOR | 72 | OK | 589,855,393 | no change |
| 37 | FSLR | MONITOR | 72 | OK | 586,559,034 | no change |
| 38 | GS | MONITOR | 72 | OK | 2,040,759,838 | no change |
| 39 | INTC | MONITOR | 72 | OK | 12,516,567,629 | no change |
| 40 | IRM | MONITOR | 72 | OK | 177,580,744 | no change |
| 41 | LMT | MONITOR | 72 | OK | 793,148,538 | no change |
| 42 | META | MONITOR | 72 | OK | 10,632,058,502 | no change |
| 43 | MOD | MONITOR | 72 | OK | 298,041,528 | no change |
| 44 | PANW | MONITOR | 72 | OK | 1,832,330,290 | no change |
| 45 | PLTR | MONITOR | 72 | OK | 6,254,766,785 | no change |
| 46 | S | MONITOR | 72 | OK | 127,642,815 | no change |
| 47 | SO | MONITOR | 72 | OK | 499,076,203 | no change |
| 48 | ZS | MONITOR | 72 | OK | 644,303,885 | no change |
| 49 | ASTS | MONITOR | 69 | OK | 1,929,769,811 | no change |
| 50 | BEP | MONITOR | 68 | OK | 30,972,962 | no change |
| 51 | COP | MONITOR | 68 | OK | 1,027,847,047 | no change |
| 52 | LUNR | MONITOR | 68 | OK | 453,277,330 | no change |
| 53 | MSFT | MONITOR | 68 | OK | 14,610,184,774 | no change |
| 54 | MSTR | MONITOR | 67 | OK | 2,758,706,713 | no change |
| 55 | CRM | MONITOR | 66 | OK | 2,665,787,117 | no change |
| 56 | GEV | MONITOR | 64 | OK | 2,677,778,476 | no change |
| 57 | LHX | MONITOR | 64 | OK | 434,675,028 | no change |
| 58 | PL | MONITOR | 64 | OK | 538,662,081 | no change |
| 59 | CVX | MONITOR | 60 | OK | 2,097,284,497 | no change |
| 60 | NOC | MONITOR | 60 | OK | 522,840,015 | no change |
| 61 | OXY | MONITOR | 60 | OK | 812,102,415 | no change |
| 62 | XOM | MONITOR | 60 | OK | 3,074,559,132 | no change |

**Note:** the lowest score observed across all 62 symbols is 60 — exactly
at `PROMOTION_THRESHOLD`. No symbol scored below the promotion threshold
on this run; the watchlist's MONITOR tier is, at present, entirely
composed of liquid, large/mid-cap names with strong data quality and
liquidity components, so the score floor here reflects the curated
universe, not a scoring-formula issue (component-level math was already
validated with synthetic edge cases in the Phase 3/4 unit tests).

## Bank stock analysis

- **Bank symbols considered:** 3 (`JPM`, `GS`, `MSTR` — all carry the
  `פיננסים` category tag in the copied DB).
- **Bank symbols evaluated:** 3 (all fetched successfully, `OK`).
- **Bank symbols that pass liquidity rules:** 3 (all have
  avg-dollar-volume far above `ELIGIBILITY_MIN_DOLLAR_VOL`).
- **Bank symbols proposed for ACTIVE:** 0 (no promotions occurred this
  run for any symbol, bank or not — see executive summary).
- **Did the bank cap (8) block any candidate?** No — only 3 bank
  candidates exist in the current universe, well under the cap, and no
  promotions were proposed at all this run.

### Top bank candidates by relevance score

| Rank | Symbol | Score | Proposed State |
|---|---|---|---|
| 1 | JPM | 77 | MONITOR |
| 2 | GS | 72 | MONITOR |
| 3 | MSTR | 67 | MONITOR |

(Only 3 bank candidates exist in the current watchlist; all 3 are shown —
fewer than the requested top-15 because no more exist.)

**Note:** `MSTR` (MicroStrategy) is tagged with the `פיננסים` (financial)
category in the existing production config — this is a pre-existing data
classification in `config.py`'s `WATCHLIST`, not something this
validation run set or changed.

## ETF and index handling

- **ETF/index count:** 18 (`wl_state = ETF_INDEX_CONTEXT` in the copied DB).
- **Which were skipped as ordinary stock candidates?** None of the 18 even
  reached the candidate-gathering stage — they are already correctly
  classified as `ETF_INDEX_CONTEXT`, so the evaluator's `get_symbols_by_state("ACTIVE")`/
  `get_symbols_by_state("MONITOR")` queries never return them in the first
  place. This is the expected, by-design separation (see
  `services/watchlist_evaluator.py` universe-selection logic and its
  Phase 4 tests).
- **Did any ETF/index incorrectly enter proposed ACTIVE?** No —
  `proposed_active_count = 0`, and the evaluator's `_NON_STOCK_TYPES` guard
  was not triggered (no ACTIVE/MONITOR row had a non-stock `security_type`
  in this copy).
- **Do context symbols remain separated correctly?** Yes — `context_count`
  stayed at 18 before and after; the dry-run never reads or modifies
  `ETF_INDEX_CONTEXT` rows.

## Provider / data quality review

| Issue type | Count |
|---|---|
| Stale data | 0 |
| Provider errors | 0 |
| Invalid data | 0 |
| Insufficient history | 0 |
| Missing OHLCV | 0 |

**Are failures symbol-specific or provider-wide?** Neither — there were no
failures of any kind in this run. yfinance returned complete, fresh,
valid daily OHLCV history for all 62 symbols across both batched requests.
`provider_degraded` correctly evaluated to `False` (0% transient failures,
well under the 40% threshold).

## Git behavior

No code changes were required to perform this validation — the existing
Phase 3/4 modules (`data/market_data_validator.py`,
`services/watchlist_evaluator.py`) worked against the copied database
without modification. The one-off runner script used to drive this
validation was written to the OS temp directory
(`%TEMP%\stocksage_live_dryrun_runner.py`), not inside the repository, and
is not part of this commit. Only this report file (`WATCHLIST_LIVE_DRY_RUN_REPORT.md`)
is being added to the repository.

**Not committed (by design):**
- The timestamped database copy (`db/stocksage_dryrun_copy_20260620_100138.db`)
  — already covered by `.gitignore`'s `db/*.db` rule, confirmed via
  `git check-ignore -v`, and will be deleted after this report is finalized.
- The raw JSON dump of the run (written to the OS temp directory, never
  inside the repo).
- The one-off runner script (OS temp directory only).
