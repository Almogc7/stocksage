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

### 2026-0X-XX — next check (append here)

_(pending — see note above)_
