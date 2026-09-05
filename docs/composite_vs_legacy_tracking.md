# Composite vs. Legacy — Forward-Return Tracking

Running record of forward returns for signals where the composite engine
(`analyzers/composite.py`) and the legacy engine (`analyzers/technical.py`
`full_analysis()`) disagreed, plus composite-exclusive fires, drawn from the
2026-07-15–23 diagnostic window. The composite engine is NOT wired into
alerting (see `CLAUDE.md`), so none of this affected real trades — it's a
comparison exercise only.

**Append new checks as dated sections below — never overwrite prior
rows.** This file exists because the original diagnostic data lived only in
a chat session and was lost once; the point of this file is to stop that
from happening again.

Composite required-score bar during this window: 70 (bull regime).
Forward return = (latest close / close on signal date − 1) × 100, using
`auto_adjust=True` history (matches `data/fetcher.get_historical`).

## Signal set (fixed — do not edit rows below, only append return checks)

### A. Legacy/composite disagreement pairs (10)

| # | Symbol | Date | Legacy score | Legacy verdict | Composite score | Composite clears 70? |
|---|--------|------|--------------|-----------------|------------------|-----------------------|
| 1 | WAL | 2026-07-22 | 90 | STRONG BUY | 75.0 | yes |
| 2 | WAL | 2026-07-23 | 90 | STRONG BUY | 89.5 | yes |
| 3 | BEP | 2026-07-23 | 90 | STRONG BUY | 84.4 | yes |
| 4 | ETN | 2026-07-23 | 75 | STRONG BUY | 70.5 | yes |
| 5 | MOD | 2026-07-23 | 75 | STRONG BUY | 70.0 | yes (barely) |
| 6 | GS | 2026-07-22 | 85 | STRONG BUY | 63.1 | no |
| 7 | IRM | 2026-07-21 | 75 | STRONG BUY | 69.3 | no (barely) |
| 8 | TER | 2026-07-23 | 70 | BUY | 56.1 | no |
| 9 | ASML | 2026-07-15 | 75 | STRONG BUY | 65.0 | no |
| 10 | ANET | 2026-07-21 | 65 | BUY | 45.4 | no |

### B. Composite-exclusive fires (7) — legacy never came close

Reconstructed by running `composite_score()` on truncated historical price
data (same method `scripts/run_composite_scan.py` uses on live data).
Verified against the recalled IRM 7/21 score (69.3, matched exactly) — trust
level: high. HBNC turned up 3 fire-days here vs. 4 recalled from the original
session; the 4th could not be reconstructed (recall gap or data revision).

| # | Symbol | Date | Composite score |
|---|--------|------|------------------|
| 11 | HBNC | 2026-07-17 | 70.4 |
| 12 | HBNC | 2026-07-20 | 71.0 |
| 13 | HBNC | 2026-07-21 | 70.0 |
| 14 | IRM | 2026-07-17 | 70.4 |
| 15 | IRM | 2026-07-20 | 70.3 |
| 16 | IRM | 2026-07-22 | 70.6 |
| 17 | IRM | 2026-07-23 | 70.9 |

---

## Check log

### 2026-08-07 — first check (11–17 trading days elapsed)

| # | Symbol | Date | Days elapsed | Entry price | Price on 2026-08-07 | Fwd return | Outcome (±5% = resolved) |
|---|--------|------|---------------|--------------|----------------------|------------|----------------------------|
| 1 | WAL | 2026-07-22 | 12 | 83.41 | 81.33 | −2.49% | ambiguous |
| 2 | WAL | 2026-07-23 | 11 | 82.97 | 81.33 | −1.98% | ambiguous |
| 3 | BEP | 2026-07-23 | 11 | 33.07 | 33.20 | +0.39% | ambiguous |
| 4 | ETN | 2026-07-23 | 11 | 415.13 | 448.68 | +8.08% | **UP — resolved** |
| 5 | MOD | 2026-07-23 | 11 | 249.62 | 195.60 | −21.64% | **DOWN — resolved** |
| 6 | GS | 2026-07-22 | 12 | 1098.20 | 1039.61 | −5.34% | **DOWN — resolved** |
| 7 | IRM | 2026-07-21 | 13 | 125.56 | 121.15 | −3.51% | ambiguous |
| 8 | TER | 2026-07-23 | 11 | 373.75 | 379.31 | +1.49% | ambiguous |
| 9 | ASML | 2026-07-15 | 17 | 1812.93 | 1740.99 | −3.97% | ambiguous |
| 10 | ANET | 2026-07-21 | 13 | 174.58 | 188.67 | +8.07% | **UP — resolved** |
| 11 | HBNC | 2026-07-17 | 15 | 20.30 | 20.42 | +0.59% | ambiguous |
| 12 | HBNC | 2026-07-20 | 14 | 20.21 | 20.42 | +1.04% | ambiguous |
| 13 | HBNC | 2026-07-21 | 13 | 20.41 | 20.42 | +0.05% | ambiguous |
| 14 | IRM | 2026-07-17 | 15 | 123.82 | 121.15 | −2.16% | ambiguous |
| 15 | IRM | 2026-07-20 | 14 | 124.23 | 121.15 | −2.48% | ambiguous |
| 16 | IRM | 2026-07-22 | 12 | 124.51 | 121.15 | −2.70% | ambiguous |
| 17 | IRM | 2026-07-23 | 11 | 124.55 | 121.15 | −2.73% | ambiguous |

**Group averages (2026-08-07):**
- Both engines fired (#1–5, n=5): −3.53% (drop MOD outlier → +1.00% on the
  remaining 4)
- Legacy-only, composite missed 70 (#6–10, n=5): −0.65%
- Composite-exclusive (#11–17, n=7): −1.20%

**Read at this check:** too early/noisy to conclude anything. Only 2 of 17
rows both resolved (±5%) *and* discriminate between the engines — ANET
(legacy win, composite missed it) and GS (legacy loss, composite correctly
skipped it) — and they cancel out. MOD's −21.6% and ETN's +8.1% are the
biggest moves but both engines agreed on both, so they don't favor either
engine. Composite-exclusive fires (HBNC/IRM) haven't broken 3% either way.
Re-check in 2–3 weeks (~2026-08-21 to 2026-08-28) once more of these have
had time to resolve, and append — don't overwrite — the results above.

### 2026-09-05 — second check (31–37 trading days elapsed) + layer breakdown + SPY baseline

Forward returns recomputed fresh (latest close 2026-09-05); layer scores
reconstructed via `composite_score()` on truncated history (SPY truncated
to the same date for regime/RS), same method as the original
reconstruction. Ad hoc scripts used (not part of the test suite, not
scheduled): `scripts/diag_composite_volume_near_misses.py`,
`scripts/diag_composite_layer_breakdown.py`.

| # | Symbol | Date | Days elapsed | Entry price | Price on 2026-09-05 | Fwd return | trend | momentum | volume | RS | rs_ratio |
|---|--------|------|---------------|--------------|----------------------|------------|-------|----------|--------|----|----|
| 1 | WAL | 2026-07-22 | 32 | 82.99 | 80.95 | −2.45% | 25.0 | 10.0 | 15.0 | 25.0 | 1.0060 |
| 2 | WAL | 2026-07-23 | 31 | 82.55 | 80.95 | −1.94% | 25.0 | 25.0 | 15.0 | 24.5 | 0.9957 |
| 3 | BEP | 2026-07-23 | 31 | 32.66 | 31.41 | −3.83% | 25.0 | 15.0 | 25.0 | 19.4 | 0.9549 |
| 4 | ETN | 2026-07-23 | 31 | 414.11 | 410.85 | −0.79% | 24.9 | 15.0 | 10.0 | 20.6 | 0.9652 |
| 5 | MOD | 2026-07-23 | 31 | 249.62 | 194.66 | −22.02% | 21.9 | 15.0 | 10.0 | 23.1 | 0.9848 |
| 6 | GS | 2026-07-22 | 32 | 1092.85 | 1038.61 | −4.96% | 18.1 | 20.0 | 0.0 | 25.0 | 1.1338 |
| 7 | IRM | 2026-07-21 | 33 | 125.56 | 116.86 | −6.93% | 19.3 | 15.0 | 10.0 | 25.0 | 1.0281 |
| 8 | TER | 2026-07-23 | 31 | 373.61 | 357.03 | −4.44% | 18.8 | 15.0 | 10.0 | 12.3 | 0.8988 |
| 9 | ASML | 2026-07-15 | 37 | 1812.93 | 1714.88 | −5.41% | 15.0 | 0.0 | 25.0 | 25.0 | 1.1695 |
| 10 | ANET | 2026-07-21 | 33 | 174.58 | 193.78 | +11.00% | 16.0 | 0.0 | 10.0 | 19.4 | 0.9554 |
| 11 | HBNC | 2026-07-17 | 35 | 20.30 | 19.91 | −1.92% | 20.4 | 15.0 | 10.0 | 25.0 | 1.0934 |
| 12 | HBNC | 2026-07-20 | 34 | 20.21 | 19.91 | −1.48% | 21.0 | 15.0 | 10.0 | 25.0 | 1.1095 |
| 13 | HBNC | 2026-07-21 | 33 | 20.41 | 19.91 | −2.45% | 20.0 | 15.0 | 10.0 | 25.0 | 1.0913 |
| 14 | IRM | 2026-07-17 | 35 | 123.82 | 116.86 | −5.62% | 20.4 | 15.0 | 10.0 | 25.0 | 0.9996 |
| 15 | IRM | 2026-07-20 | 34 | 124.23 | 116.86 | −5.93% | 20.3 | 15.0 | 10.0 | 25.0 | 1.0199 |
| 16 | IRM | 2026-07-22 | 32 | 124.51 | 116.86 | −6.14% | 20.6 | 15.0 | 10.0 | 25.0 | 1.0294 |
| 17 | IRM | 2026-07-23 | 31 | 124.55 | 116.86 | −6.17% | 20.9 | 15.0 | 10.0 | 25.0 | 1.0748 |

**Group averages (2026-09-05):**
- Legacy/composite disagreement pairs (#1–10, n=10): −4.18% avg, median
  −4.13%, 1 up / 9 down
- Composite-exclusive (#11–17, n=7): −4.24% avg, median −5.62%, 0 up / 7
  down

**SPY baseline over the same signal dates (same window, same days-elapsed,
through 2026-09-05 close of 770.19):**

| Signal date | SPY entry | Days elapsed | SPY fwd return |
|---|---|---|---|
| 2026-07-15 | 754.81 | 37 | +2.04% |
| 2026-07-17 | 743.29 | 35 | +3.62% |
| 2026-07-20 | 742.09 | 34 | +3.79% |
| 2026-07-21 | 748.28 | 33 | +2.93% |
| 2026-07-22 | 747.41 | 32 | +3.05% |
| 2026-07-23 | 738.18 | 31 | +4.34% |

SPY avg across these dates: **+3.29%**. Both signal groups underperformed
SPY by roughly 7.5 percentage points on average — this was a rising market
that these picks missed, not a broad down-stretch that dragged everything
down together.

**Per-layer correlation with forward return (Pearson, n=17):** trend
r=−0.159, momentum r=−0.338, volume r=+0.026, relative-strength r=−0.195.
No layer shows a positive relationship with outcome in this sample.

**IRM's 4 composite-exclusive fires (#14–17):** momentum (15.0) and volume
(10.0) are identical on every date — both come from coarse discrete-bucket
scoring functions (`_macd_points`/`_stoch_points`, `volume_layer_points`),
not continuous magnitude tracking. Relative-strength is pinned at the full
25/25 on all four dates despite `rs_ratio` sitting at 0.9996–1.0748 — right
at the bull-regime bar (`required_rs=1.0`), one date technically below it
before rounding. Root cause: `_rs_points()` was a hard ceiling — any
`rs_ratio >= required_rs` scored the max 25 regardless of margin, so a
razor-thin edge over SPY scored identically to a large one. RS contributed
25/70 (36%) of IRM's clearing score on a statistically negligible edge, and
all four fires lost 5.6–6.2%. HBNC's 3 composite-exclusive fires show the
same RS-ceiling pattern but with a real margin (`rs_ratio` 1.09–1.11) and
still lost — so a genuine RS edge didn't help either, in this small sample.

**Read at this check:** the picture firmed up considerably from the
2026-08-07 read. Both groups are now solidly negative in absolute terms AND
underperformed a rising SPY by ~7.5pp. No individual composite layer shows
a positive correlation with outcome. The RS layer's flat-ceiling design is
a specific, fixable weakness (see code fix logged separately below) —
independent of whether relative-strength as a concept has predictive value
here, which this sample is too small to settle. n=17 remains small; treat
directional, not conclusive.

### 2026-09-05 — RS layer fix + re-run on the same 17 signals

Code changes (`analyzers/composite.py` + `config.py`, still fully
observation-mode — `use_composite_volume`, `check_alerts()`, and the live
alert path untouched):

1. **RS ceiling fixed.** `_rs_points()` used to flatten to the full 25 pts
   at `rs_ratio >= required_rs` — no credit for margin. Full points now
   require clearing `required_rs` by `COMPOSITE_RS_CEILING_MARGIN` (0.15
   default); the 0→25 ramp now stretches from `rs_ratio=0.8` to
   `required_rs + 0.15` instead of stopping at `required_rs`.
2. **Regime-adjusted `required_rs`.** When SPY sits more than
   `COMPOSITE_SPY_STRONG_EXTENSION_PCT` (8.0%, ~75th percentile of SPY's
   trailing-3y pct-above-SMA150) above its own SMA150, `required_rs` scales
   up by `COMPOSITE_RS_REGIME_BUMP_RATE` (0.02) per point past that
   threshold, capped at `COMPOSITE_RS_REGIME_MAX_BUMP` (+0.15). As
   calibrated (off SPY's own 3y history, not tuned to this window): this
   window's SPY extension (4.90–7.67%) and 60-day trailing return
   (3.48–6.56%, ~33rd–60th percentile) were both unremarkable by SPY's own
   standard, so this fix contributes ~0 bump on all 17 signal dates here —
   flagged in advance as expected, confirmed after the fact. All the change
   below comes from fix 1.

Full test suite: 702 tests, all passing after 2 tests updated for the new
exact-value semantics (`test_rs_points_regime_thresholds`,
`test_bull_regime_thresholds`) and 2 tests added
(`test_rs_points_margin_more_credit_than_bare_threshold`,
`test_regime_bump_capped`).

**Re-run on the same 17 signals, old score vs. new score, old
fire/no-fire vs. new (required_score=70, bull regime throughout this
window):**

| Symbol | Date | Group | Old score | New score | Fired (old) | Fired (new) |
|---|---|---|---|---|---|---|
| WAL | 2026-07-22 | disagree | 75.0 | 64.7 | yes | **no** |
| WAL | 2026-07-23 | disagree | 89.5 | 79.0 | yes | yes |
| BEP | 2026-07-23 | disagree | 84.4 | 76.1 | yes | yes |
| ETN | 2026-07-23 | disagree | 70.5 | 61.7 | yes | **no** |
| MOD | 2026-07-23 | disagree | 70.0 | 60.1 | yes | **no** |
| GS | 2026-07-22 | disagree | 63.1 | 61.9 | no | no |
| IRM | 2026-07-21 | disagree | 69.3 | 60.6 | no | no |
| TER | 2026-07-23 | disagree | 56.1 | 50.9 | no | no |
| ASML | 2026-07-15 | disagree | 65.0 | 65.0 | no | no |
| ANET | 2026-07-21 | disagree | 45.4 | 37.1 | no | no |
| HBNC | 2026-07-17 | exclusive | 70.4 | 66.4 | yes | **no** |
| HBNC | 2026-07-20 | exclusive | 71.0 | 68.1 | yes | **no** |
| HBNC | 2026-07-21 | exclusive | 70.0 | 65.8 | yes | **no** |
| IRM | 2026-07-17 | exclusive | 70.4 | 59.7 | yes | **no** |
| IRM | 2026-07-20 | exclusive | 70.3 | 61.0 | yes | **no** |
| IRM | 2026-07-22 | exclusive | 70.6 | 62.0 | yes | **no** |
| IRM | 2026-07-23 | exclusive | 70.9 | 65.5 | yes | **no** |

**12 of 17 previously fired; only 2 still fire after the fix (10 flip from
fire to no-fire).** Every single composite-exclusive fire (all 7 — HBNC
×3, IRM ×4) drops below the bar; those were 0/7 winners averaging −4.24% —
100% of that group's bad calls are now excluded. In the disagreement group,
3 of 5 previously-favored signals (WAL 7/22, ETN, MOD) also drop out; MOD
(the −22.02% outlier) is now excluded. The 2 that still fire — WAL 7/23
(−1.94%) and BEP 7/23 (−3.83%) — remain losers but far more moderate ones
than the excluded set, and neither is anywhere near MOD's outlier.

**Read at this check:** the flat RS ceiling was doing essentially all of
the damage in this sample — replacing it with a graduated one removed
every composite-exclusive fire and the worst disagreement-group outlier,
while leaving only two modest losers. This is a strong result for a single
targeted fix, but n=17 (now effectively n=2 for the "still fires" bucket)
is far too small to call this validated — it shows the fix does what it
was designed to do on the cases that motivated it, not that composite is
now reliable going forward. Regime bump (fix 2) had no effect on this
window by design/calibration; it may matter in a genuinely extended
market, which this window wasn't. Composite remains fully observation-only
— legacy (`full_analysis()`) is still the only engine wired into
`check_alerts()`.

### 2026-0X-XX — next check (append here)

_(pending)_
