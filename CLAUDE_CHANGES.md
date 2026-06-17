# CLAUDE_CHANGES.md

All modifications made by Claude on branch `claude/stocksage-review-20260617-1200`.

---

## Entry 1 — Review documentation created

| Field | Value |
|---|---|
| **Date and time** | 2026-06-17 |
| **Commit hash** | `8b5305b` |
| **Files changed** | `STOCKSAGE_REVIEW.md` (new), `CLAUDE_CHANGES.md` (new) |
| **Reason** | Comprehensive project review per user instructions |
| **Previous behavior** | No review documentation existed |
| **New behavior** | `STOCKSAGE_REVIEW.md` contains full 18-section review; `CLAUDE_CHANGES.md` tracks all modifications |
| **Tests executed** | `python test_fetch.py` (integration smoke test) |
| **Test results** | PASSED — all sections completed successfully, NVDA analysis score=50 matches manual calculation |
| **Known limitations** | Test requires live internet access and Yahoo Finance availability |
| **Revert command** | `git checkout main -- STOCKSAGE_REVIEW.md CLAUDE_CHANGES.md && git rm STOCKSAGE_REVIEW.md CLAUDE_CHANGES.md` |
| **Affects stock rankings** | No — documentation only |
| **Affects historical comparability** | No |

---

## Pending changes (awaiting approval)

The following fixes have been proposed in `STOCKSAGE_REVIEW.md` Section 16 and are **not yet applied**.

| # | Fix | File | Severity |
|---|---|---|---|
| 1 | RSI fringe-zone label: rename `rsi_healthy_range` → `rsi_acceptable_zone` in fringe path | `analyzers/technical.py`, `agent/core.py` | Medium — affects `triggered_signals` output |
| 2 | `get_muted_symbols` UTC bug: add `'utc'` modifier | `db/database.py` | Medium — affects cooldown window on non-UTC machines |
| 3 | Chart RSI formula: replace rolling mean with `ta.momentum.rsi()` | `analyzers/chart_generator.py` | Medium — affects visual RSI in alert charts |
| 4 | Incomplete candle green-check: use `df.iloc[-2]` instead of `df.iloc[-1]` | `agent/core.py` | High — removes look-ahead bias in Gate 9 |
| 5 | Telegram auth check: add `AUTHORIZED_CHAT_IDS` allowlist | `bot/telegram_bot.py`, `config.py` | High — security fix |

**Awaiting explicit approval before implementing any of the above.**
