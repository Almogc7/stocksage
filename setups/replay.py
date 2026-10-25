"""Replay harness pure core (docs/SETUP_SPEC.md §13, rollout step 5).

Given historical frames for a universe of symbols, SPY's own frame, and a
list of scan dates, walks forward calling evaluate_candidate() (§3-4) then
define_setup() (§5-9) per symbol per date, and simulate_outcome() (§6/§12)
when asked, returning one CandidateRow per (symbol, scan_date) pair. Pure
function: no network, no file I/O, no DB -- the I/O (universe fetch,
chunked downloads, caching, results storage) lives in
scripts/replay_setups.py, which imports this module.

Never mutates its `frames`/`spy_df` inputs -- every downstream call
(truncate_to_date, normalize_frame, simulate_outcome's own .copy()) already
returns new frames rather than mutating in place, and this module performs
no operation of its own on the caller's DataFrames.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

import pandas as pd

from setups.calendar_utils import advance_us_trading_days
from setups.definition import Rejection, SetupDefinition, define_setup
from setups.evaluate import evaluate_candidate
from setups.outcomes import simulate_outcome
from setups.params import OutcomeParams, SetupParams, V1_OUTCOME_PARAMS, V1_PARAMS
from setups.time_utils import truncate_to_date

# Local to this module -- not added to setups/reason_codes.py (out of this
# round's allowed files). Both are replay-harness-level statuses, never
# produced by evaluate_candidate()/define_setup()/simulate_outcome() itself.
SUPPRESSED = "SUPPRESSED"
DUPLICATE_SETUP = "DUPLICATE_SETUP"


@dataclass
class CandidateRow:
    symbol: str
    scan_date: date
    regime: str
    accepted: bool
    reason_code: str | None = None
    variant: str = "V1"
    setup_identity: tuple | None = None
    sh_date: date | None = None
    sh_price: float | None = None
    sl_date: date | None = None
    sl_price: float | None = None
    trigger: float | None = None
    stop: float | None = None
    target: float | None = None
    risk: float | None = None
    rr: float | None = None
    atr: float | None = None
    validity_end: date | None = None
    # Outcome fields -- populated only when compute_outcomes=True and the
    # setup was accepted. Stay None in counts-only mode (test-enforced).
    fill_date: date | None = None
    fill_price: float | None = None
    exit_date: date | None = None
    exit_price: float | None = None
    first_barrier_hit: str | None = None
    r_multiple: float | None = None
    mae_r: float | None = None
    mfe_r: float | None = None
    sessions_held: int | None = None
    resolved_date: date | None = None


def _normalize_date(d) -> date:
    return d.date() if hasattr(d, "date") else d


def _compute_regime_for_date(spy_df: pd.DataFrame, scan_date: date) -> str:
    """Duplicates the *pattern* of setups/definition.py's private
    _compute_regime() (close vs rolling-150 SMA on SPY), not that function
    itself -- same "reuse the pattern, not the function" posture
    definition.py already uses for compute_market_context(). Needed so
    EVERY candidate row carries a real regime (§9), including ones
    rejected at the evaluate_candidate() stage, which never computes one
    on its own."""
    if not isinstance(spy_df, pd.DataFrame):
        return "UNKNOWN"
    spy = truncate_to_date(spy_df, scan_date)
    if spy.empty:
        return "UNKNOWN"
    last_date = spy.index[-1]
    last_date = _normalize_date(last_date)
    if last_date != scan_date:
        return "UNKNOWN"
    if len(spy) < 150:
        return "UNKNOWN"
    try:
        close = float(spy["close"].iloc[-1])
        sma150 = float(spy["close"].rolling(window=150).mean().iloc[-1])
    except (KeyError, TypeError, ValueError):
        return "UNKNOWN"
    if math.isnan(close) or math.isnan(sma150):
        return "UNKNOWN"
    return "BULL" if close > sma150 else "BEAR"


def _max_possible_resolution_date(
    setup: SetupDefinition, result, outcome_params: OutcomeParams
) -> date:
    """Fallback for DATA_GAP/OPEN_AT_END, whose own resolved_date is None
    (outcomes.py has no definitive conclusion there -- a data problem, not
    a trading one). Never-filled -> the setup could not possibly still be
    open past its own validity_end. Filled -> it could not possibly still
    be open past timeout_sessions expected sessions from the fill. Tighter
    than "blocked for the rest of the run" -- a single data hole doesn't
    have to cost the rest of the replay for that symbol."""
    if result.fill_date is not None:
        return advance_us_trading_days(result.fill_date, outcome_params.timeout_sessions)
    return setup.validity_end


def build_regime_by_date(spy_df: pd.DataFrame, scan_dates: list[date]) -> dict:
    """Regime depends only on spy_df + scan_date, never on the symbol, the
    variant, or the bound -- computed once per date here (each date sees
    only SPY bars up to that date, via _compute_regime_for_date's own
    truncate_to_date call) so a caller that will invoke run_replay_core
    more than once for the same spy_df/scan_dates (e.g. once per symbol,
    per variant, per bound) can build this a single time and pass it in,
    instead of every call silently recomputing it from scratch."""
    return {scan_date: _compute_regime_for_date(spy_df, scan_date) for scan_date in scan_dates}


def run_replay_core(
    frames: dict[str, pd.DataFrame],
    spy_df: pd.DataFrame,
    scan_dates: list[date],
    params: SetupParams = V1_PARAMS,
    outcome_params: OutcomeParams = V1_OUTCOME_PARAMS,
    compute_outcomes: bool = False,
    counts_only_block_extra_sessions: int = 0,
    variant: str = "V1",
    regime_by_date: dict | None = None,
) -> list[CandidateRow]:
    """Deterministic: symbols processed in sorted order, scan_dates in the
    given (assumed-ascending) order.

    compute_outcomes=False (counts-only, outcome-blind): simulate_outcome()
    is never called; a symbol is blocked from a new setup while
    scan_date < blocked_until, where blocked_until = outcome.validity_end
    (UPPER bound, counts_only_block_extra_sessions=0) or
    advance_us_trading_days(outcome.validity_end,
    counts_only_block_extra_sessions) (LOWER bound, pass
    outcome_params.timeout_sessions to pretend every setup fills and runs
    the full timeout).

    compute_outcomes=True: simulate_outcome() is called for every accepted
    setup; blocked_until = result.resolved_date, falling back to
    _max_possible_resolution_date() when that's None.

    regime_by_date: optional precomputed build_regime_by_date() result.
    None (every direct caller and existing test) builds it internally,
    exactly as before -- unchanged behavior. A caller that invokes this
    function repeatedly for the same spy_df/scan_dates (scripts/
    replay_setups.py, once per symbol x variant x bound) builds it once
    and passes it in here, used as-is.
    """
    if regime_by_date is None:
        regime_by_date = build_regime_by_date(spy_df, scan_dates)

    rows: list[CandidateRow] = []
    for symbol in sorted(frames.keys()):
        df_full = frames[symbol]
        blocked_until: date | None = None
        seen_identities: set[tuple] = set()

        for scan_date in scan_dates:
            regime = regime_by_date[scan_date]

            if blocked_until is not None and scan_date < blocked_until:
                rows.append(CandidateRow(
                    symbol=symbol, scan_date=scan_date, regime=regime,
                    accepted=False, reason_code=SUPPRESSED, variant=variant,
                ))
                continue

            eval_result = evaluate_candidate(df_full, scan_date, scan_date)
            if not eval_result.passed:
                rows.append(CandidateRow(
                    symbol=symbol, scan_date=scan_date, regime=regime,
                    accepted=False, reason_code=eval_result.reason_code,
                    variant=variant,
                ))
                continue

            outcome = define_setup(df_full, scan_date, spy_df, params)
            if isinstance(outcome, Rejection):
                rows.append(CandidateRow(
                    symbol=symbol, scan_date=scan_date, regime=outcome.regime,
                    accepted=False, reason_code=outcome.reason_code,
                    variant=variant,
                ))
                continue

            identity = (symbol, outcome.sh_date, outcome.sl_date)
            if identity in seen_identities:
                rows.append(CandidateRow(
                    symbol=symbol, scan_date=scan_date, regime=outcome.regime,
                    accepted=False, reason_code=DUPLICATE_SETUP, variant=variant,
                    setup_identity=identity,
                ))
                continue
            seen_identities.add(identity)

            row = CandidateRow(
                symbol=symbol, scan_date=scan_date, regime=outcome.regime,
                accepted=True, reason_code=None, variant=variant,
                setup_identity=identity,
                sh_date=outcome.sh_date, sh_price=outcome.sh_price,
                sl_date=outcome.sl_date, sl_price=outcome.sl_price,
                trigger=outcome.trigger, stop=outcome.stop, target=outcome.target,
                risk=outcome.risk, rr=outcome.rr, atr=outcome.atr,
                validity_end=outcome.validity_end,
            )

            if compute_outcomes:
                result = simulate_outcome(
                    outcome, df_full, outcome_params, reference_sessions=spy_df.index,
                )
                resolved = result.resolved_date
                if resolved is None:
                    resolved = _max_possible_resolution_date(outcome, result, outcome_params)
                blocked_until = resolved
                row.fill_date = result.fill_date
                row.fill_price = result.fill_price
                row.exit_date = result.exit_date
                row.exit_price = result.exit_price
                row.first_barrier_hit = result.first_barrier_hit
                row.r_multiple = result.r_multiple
                row.mae_r = result.mae_r
                row.mfe_r = result.mfe_r
                row.sessions_held = result.sessions_held
                row.resolved_date = resolved
            else:
                if counts_only_block_extra_sessions > 0:
                    blocked_until = advance_us_trading_days(
                        outcome.validity_end, counts_only_block_extra_sessions
                    )
                else:
                    blocked_until = outcome.validity_end

            rows.append(row)

    return rows
