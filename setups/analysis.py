"""Control builder, statistics, and decide() for the setups replay
(docs/PREREGISTRATION_SETUPS_V1.md sections 6, 7, 8, 13). Pure: no
network, no file I/O, no DB -- mirrors setups/replay.py's posture. The
I/O half (scripts/analyze_replay.py, scripts/peer_counts.py) imports this
module and supplies the in-memory frames/rows it operates on.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from analyzers.technical import calc_atr
from setups.definition import SetupDefinition
from setups.evaluate import evaluate_candidate
from setups.outcomes import OutcomeResult, V1_OUTCOME_PARAMS, _r_multiple, simulate_outcome
from setups.params import OutcomeParams
from setups.reason_codes import (
    BARRIER_STOP,
    BARRIER_TARGET,
    BARRIER_TIMEOUT_30,
    CHASE_RR_BELOW_3,
    DATA_GAP,
    EXPIRED_UNFILLED,
    FILLED_STOPPED_SAME_DAY,
    INVALIDATED_BEFORE_FILL,
    OPEN_AT_END,
)
from setups.time_utils import truncate_to_date

# ── Frozen pre-registration values (PREREGISTRATION_SETUPS_V1.md §4/§6/§7/§13.6) ──
FROZEN_VARIANT = "V3"
FROZEN_SLIPPAGE_BPS = 10.0
FROZEN_SEED = 20261029
FROZEN_DRAWS = 10000
MIN_PEERS = 20
MIN_TRADES = 100
MIN_SYMBOLS = 30
MIN_MONTHS = 24
MIN_YEARS = 3
MIN_QUALIFYING_PERIODS = 4
QUALIFYING_PERIOD_MIN_TRADES = 30
MAX_SYMBOL_SHARE = 0.05
MAX_MONTH_SHARE = 0.15

PRIMARY_STATUSES = frozenset({BARRIER_STOP, BARRIER_TARGET, BARRIER_TIMEOUT_30, FILLED_STOPPED_SAME_DAY})
NOT_TRADE_STATUSES = frozenset({INVALIDATED_BEFORE_FILL, EXPIRED_UNFILLED, CHASE_RR_BELOW_3})
EXCLUDED_STATUSES = frozenset({DATA_GAP, OPEN_AT_END})

_FLOAT_TOLERANCE = 1e-9
# §13.5: fields the analysis compares between a stored replay_candidates
# row and a fresh recomputation to detect drift. "status" is deliberately
# absent -- it is never stored anywhere (see classify_outcome()'s own
# docstring) so there is nothing to compare it against.
_COMPARABLE_OUTCOME_FIELDS = (
    "fill_date", "fill_price", "exit_date", "exit_price",
    "first_barrier_hit", "r_multiple", "mae_r", "mfe_r", "sessions_held",
    "resolved_date",
)


def classify_outcome(status: str) -> str:
    """§5's three buckets: "primary" (stop/target/timeout_30/same-day
    stop), "not_trade" (invalidated/expired/chase-rejected), "excluded"
    (DATA_GAP/OPEN_AT_END). Raises on an unrecognized status -- a defensive
    guard against a future reason-code addition silently falling through
    unclassified."""
    if status in PRIMARY_STATUSES:
        return "primary"
    if status in NOT_TRADE_STATUSES:
        return "not_trade"
    if status in EXCLUDED_STATUSES:
        return "excluded"
    raise ValueError(f"unrecognized outcome status {status!r}")


def rebuild_setup_definition(row: dict) -> SetupDefinition:
    """Rebuilds the exact SetupDefinition an accepted replay_candidates
    row was derived from, from its own stored columns."""
    return SetupDefinition(
        sh_date=row["sh_date"], sh_price=row["sh_price"],
        sl_date=row["sl_date"], sl_price=row["sl_price"],
        trigger=row["trigger"], stop=row["stop"], target=row["target"],
        risk=row["risk"], rr=row["rr"], atr=row["atr"],
        regime=row["regime"], validity_end=row["validity_end"],
        scan_date=row["scan_date"], target_source=row.get("target_source") or "RESISTANCE",
    )


def recompute_primary_outcome(
    row: dict, symbol_df_future: pd.DataFrame,
    outcome_params: OutcomeParams = V1_OUTCOME_PARAMS, reference_sessions=None,
) -> OutcomeResult:
    """§13.5: "The analysis does not trust the status stored in the
    results file. It re-runs the frozen simulator on the cached prices
    for every accepted setup" -- replay_candidates never stores
    OutcomeResult.status at all (confirmed against setups/replay.py's own
    run_replay_core: reason_code stays None for every simulated row, and
    resolved_date is backfilled to validity_end for EXPIRED_UNFILLED and
    both never-filled DATA_GAP/OPEN_AT_END fallbacks alike, making them
    indistinguishable from the stored columns). This is the authoritative
    classifier: rebuild the setup, re-run simulate_outcome against the
    symbol's own cached frame, and use ITS status."""
    setup = rebuild_setup_definition(row)
    return simulate_outcome(setup, symbol_df_future, outcome_params, reference_sessions=reference_sessions)


def _values_match(a, b) -> bool:
    if a is None or b is None:
        return a is b
    if isinstance(a, float) or isinstance(b, float):
        try:
            if not (math.isfinite(a) and math.isfinite(b)):
                return a == b
            return abs(a - b) < _FLOAT_TOLERANCE
        except TypeError:
            return a == b
    return a == b


def stored_vs_recomputed_mismatches(stored_row: dict, recomputed: OutcomeResult) -> list[str]:
    """§13.5: the field names where a stored replay_candidates row
    disagrees with a freshly recomputed OutcomeResult. Empty -> no
    mismatch. Float fields use a small tolerance (not specified by the
    pre-registration; the same 1e-9 already established elsewhere in this
    codebase for RR_EPSILON) since both values come from the same pure
    function given the same inputs and should be bit-identical in
    practice -- any real divergence is a genuine bug, not float noise.

    resolved_date is skipped when the RECOMPUTED value is None: for
    DATA_GAP/OPEN_AT_END, simulate_outcome() itself returns
    resolved_date=None (genuinely unresolved), but the replay harness
    (setups/replay.py's run_replay_core) stores a fallback bound
    (_max_possible_resolution_date()) in its place so a later setup
    knows when the symbol is free again -- comparing the fresh None
    against that stored fallback would report a mismatch on every
    single DATA_GAP/OPEN_AT_END row, which isn't a real divergence."""
    mismatches = []
    for name in _COMPARABLE_OUTCOME_FIELDS:
        recomputed_value = getattr(recomputed, name)
        if name == "resolved_date" and recomputed_value is None:
            continue
        stored_value = stored_row.get(name)
        if not _values_match(stored_value, recomputed_value):
            mismatches.append(name)
    return mismatches


def recompute_r_at_slippage(fill_price: float, exit_price: float, stop: float, slippage_bps: float) -> float:
    """§8/§13.7: the secondary-slippage analysis recomputes R from stored
    fill/exit prices -- it does not re-run the simulation. risk is fixed
    at fill (fill_price - stop), matching setups/outcomes.py's own
    _r_multiple contract exactly (reused directly, not duplicated)."""
    risk = fill_price - stop
    slippage = slippage_bps / 10000.0
    return _r_multiple(fill_price, exit_price, risk, slippage)


def session_before(calendar_dates: list, d: date):
    """The entry immediately preceding `d` in an ascending list of trading
    dates (e.g. SPY's own index -- the same real-data-derived calendar
    convention `build_scan_dates`/`build_regime_by_date` already use,
    rather than the holiday-heuristic setups/calendar_utils functions).
    None if `d` is the first date or earlier than every listed date."""
    try:
        pos = calendar_dates.index(d)
    except ValueError:
        earlier = [c for c in calendar_dates if c < d]
        return earlier[-1] if earlier else None
    return calendar_dates[pos - 1] if pos > 0 else None


def peer_atr_and_eligibility(peer_df_full: pd.DataFrame, session_before_d: date) -> tuple:
    """§6/§13.2: "A peer's eligibility and its ATR use only bars up to and
    including the session before d." Returns (eligible, atr_or_none).
    atr is None when eligible but ATR couldn't be computed usably (same
    guard setups/definition.py applies around its own calc_atr() call) --
    the caller treats that the same as an unusable peer (§13.3)."""
    eval_result = evaluate_candidate(peer_df_full, session_before_d, session_before_d)
    if not eval_result.passed:
        return False, None
    truncated = truncate_to_date(peer_df_full, session_before_d)
    try:
        atr = float(calc_atr(truncated)["atr"])
    except (TypeError, ValueError, ZeroDivisionError):
        return True, None
    if not math.isfinite(atr) or atr <= 0:
        return True, None
    return True, atr


def build_synthetic_peer_setup(
    d: date, session_before_d: date, open_d: float, s: float, t: float, atr_peer: float,
) -> SetupDefinition:
    """§6: a peer entered at the open of d, stop/target sized to the real
    trade's own s/t ATR-multiples scaled to the peer's own ATR.
    scan_date=session_before_d + validity_end=d (a one-session validity
    window covering exactly day d) forces a deterministic fill at exactly
    open_d through the existing pure simulate_outcome(): on the only
    checked session (d itself), high_d >= trigger is true because
    high >= open always, so fill_price = max(trigger, open_d) = open_d
    exactly, before the invalidation/chase-reject branches are ever
    reached. sl_price/regime/rr/risk/sh_date/sh_price/sl_date/
    target_source are therefore dead-code inputs for this construction --
    required SetupDefinition fields, but simulate_outcome never reads
    their values given a one-session window -- filled with harmless,
    finite placeholders.
    """
    trigger = open_d
    stop = open_d - s * atr_peer
    target = open_d + t * atr_peer
    risk = trigger - stop
    rr = (target - trigger) / risk if risk > 0 else float("nan")
    return SetupDefinition(
        sh_date=session_before_d, sh_price=trigger, sl_date=session_before_d, sl_price=stop,
        trigger=trigger, stop=stop, target=target, risk=risk, rr=rr, atr=atr_peer,
        regime="BULL", validity_end=d, scan_date=session_before_d, target_source="RESISTANCE",
    )


def simulate_peer_control(
    peer_df_future: pd.DataFrame, synthetic_setup: SetupDefinition,
    outcome_params: OutcomeParams = V1_OUTCOME_PARAMS, reference_sessions=None,
) -> OutcomeResult:
    return simulate_outcome(synthetic_setup, peer_df_future, outcome_params, reference_sessions=reference_sessions)


def _date_of(ts) -> date:
    return ts.date() if hasattr(ts, "date") else ts


def _open_on_date(df_full: pd.DataFrame, d: date):
    dates = [_date_of(ts) for ts in df_full.index]
    try:
        pos = dates.index(d)
    except ValueError:
        return None
    return float(df_full.iloc[pos]["open"])


@dataclass
class PeerDiagnostic:
    symbol: str
    eligible: bool
    atr: float | None = None
    status: str | None = None
    r_multiple: float | None = None
    # Raw prices, retained (not just R) so §8/§13.7's secondary-slippage
    # table can recompute R at an alternate slippage without re-simulating
    # this peer -- fill_price/exit_price are the UNADJUSTED prices
    # simulate_outcome() itself returns; slippage is applied only in the
    # R formula, never to these.
    fill_price: float | None = None
    exit_price: float | None = None
    stop: float | None = None


@dataclass
class ControlResult:
    fill_date: date
    peer_count: int  # eligible peers considered (§13.3: eligible, regardless of usable R)
    usable_peer_count: int  # peers whose simulated trade produced an R
    mean_control_r: float | None
    excluded: bool  # True if usable_peer_count < MIN_PEERS
    peer_diagnostics: list = field(default_factory=list)


def build_control_for_trade(
    d: date, session_before_d: date, s: float, t: float,
    peers: list, eligibility_atr_lookup: dict,
    outcome_params: OutcomeParams = V1_OUTCOME_PARAMS, reference_sessions=None,
) -> ControlResult:
    """peers: list of (peer_symbol, peer_df_full) -- one full frame per
    peer (covering both the eligibility-reference date and day d onward).
    eligibility_atr_lookup: precomputed {(symbol, date): (eligible, atr)}
    from the I/O layer's process pool -- this function does not recompute
    eligibility/ATR itself, only orchestrates simulation + aggregation."""
    diagnostics: list[PeerDiagnostic] = []
    usable_rs: list[float] = []
    eligible_count = 0

    for peer_symbol, peer_df_full in peers:
        eligible, atr_peer = eligibility_atr_lookup.get((peer_symbol, session_before_d), (False, None))
        if not eligible:
            diagnostics.append(PeerDiagnostic(peer_symbol, False))
            continue
        eligible_count += 1
        if atr_peer is None:
            diagnostics.append(PeerDiagnostic(peer_symbol, True, None, None, None))
            continue

        open_d = _open_on_date(peer_df_full, d)
        if open_d is None or not math.isfinite(open_d):
            diagnostics.append(PeerDiagnostic(peer_symbol, True, atr_peer, OPEN_AT_END, None))
            continue

        synthetic = build_synthetic_peer_setup(d, session_before_d, open_d, s, t, atr_peer)
        peer_future = peer_df_full[[_date_of(ts) > session_before_d for ts in peer_df_full.index]]
        result = simulate_peer_control(peer_future, synthetic, outcome_params, reference_sessions)
        if result.r_multiple is None:
            diagnostics.append(PeerDiagnostic(peer_symbol, True, atr_peer, result.status, None))
            continue
        diagnostics.append(PeerDiagnostic(
            peer_symbol, True, atr_peer, result.status, result.r_multiple,
            fill_price=result.fill_price, exit_price=result.exit_price, stop=synthetic.stop,
        ))
        usable_rs.append(result.r_multiple)

    usable_count = len(usable_rs)
    excluded = usable_count < MIN_PEERS
    mean_control_r = (sum(usable_rs) / usable_count) if usable_count > 0 else None
    return ControlResult(
        fill_date=d, peer_count=eligible_count, usable_peer_count=usable_count,
        mean_control_r=mean_control_r, excluded=excluded, peer_diagnostics=diagnostics,
    )


def mean_control_r_at_slippage(control: ControlResult, slippage_bps: float) -> float | None:
    """§8/§13.7: recomputes a trade's control value at an alternate
    slippage from each usable peer's retained fill_price/exit_price/stop
    -- no re-simulation."""
    rs = [
        recompute_r_at_slippage(p.fill_price, p.exit_price, p.stop, slippage_bps)
        for p in control.peer_diagnostics
        if p.r_multiple is not None and p.fill_price is not None and p.exit_price is not None
    ]
    return (sum(rs) / len(rs)) if rs else None


@dataclass
class PairedDifference:
    symbol: str
    fill_date: date
    setup_r: float
    control_r: float

    @property
    def diff(self) -> float:
        return self.setup_r - self.control_r

    @property
    def fill_month(self) -> tuple:
        return (self.fill_date.year, self.fill_date.month)

    @property
    def fill_year(self) -> int:
        return self.fill_date.year


@dataclass
class Sufficiency:
    trade_count: int
    symbol_count: int
    month_count: int
    year_count: int
    meets_minimum: bool


def sample_sufficiency(
    diffs: list, min_trades: int = MIN_TRADES, min_symbols: int = MIN_SYMBOLS,
    min_months: int = MIN_MONTHS, min_years: int = MIN_YEARS,
) -> Sufficiency:
    """§7 condition 1. Thresholds are parameters (frozen defaults) so
    tests can pass relaxed values without touching the frozen constants."""
    trade_count = len(diffs)
    symbol_count = len({d.symbol for d in diffs})
    month_count = len({d.fill_month for d in diffs})
    year_count = len({d.fill_year for d in diffs})
    meets_minimum = (
        trade_count >= min_trades and symbol_count >= min_symbols
        and month_count >= min_months and year_count >= min_years
    )
    return Sufficiency(trade_count, symbol_count, month_count, year_count, meets_minimum)


def all_primary_trades_mean_r(all_primary_rows: list):
    """§13.4: "For transparency the report also shows the mean net R of
    all primary trades, including the excluded ones" -- the UNFILTERED
    mean, computed separately from `diffs` (which is already filtered to
    trades with >= MIN_PEERS usable peers). `all_primary_rows` entries
    need only an 'r_multiple' key."""
    rs = [r["r_multiple"] for r in all_primary_rows if r.get("r_multiple") is not None]
    return (sum(rs) / len(rs)) if rs else None


@dataclass
class PeriodStats:
    year: int
    trade_count: int
    mean_diff: float
    mean_diff_positive: bool
    qualifies: bool


def per_period_table(diffs: list, qualifying_min_trades: int = QUALIFYING_PERIOD_MIN_TRADES) -> dict:
    """§7 condition 4's input: calendar-year buckets of the fill date."""
    by_year: dict = {}
    for d in diffs:
        by_year.setdefault(d.fill_year, []).append(d)
    table = {}
    for year, rows in by_year.items():
        mean_diff = sum(r.diff for r in rows) / len(rows)
        table[year] = PeriodStats(
            year=year, trade_count=len(rows), mean_diff=mean_diff,
            mean_diff_positive=mean_diff > 0, qualifies=len(rows) >= qualifying_min_trades,
        )
    return table


@dataclass
class Concentration:
    symbol_shares: dict
    month_shares: dict
    max_symbol: object
    max_symbol_share: float
    max_month: object
    max_month_share: float


def concentration_shares(diffs: list) -> Concentration:
    """§7 condition 5's input."""
    total = len(diffs)
    if total == 0:
        return Concentration({}, {}, None, 0.0, None, 0.0)
    symbol_counts: dict = {}
    month_counts: dict = {}
    for d in diffs:
        symbol_counts[d.symbol] = symbol_counts.get(d.symbol, 0) + 1
        month_counts[d.fill_month] = month_counts.get(d.fill_month, 0) + 1
    symbol_shares = {s: c / total for s, c in symbol_counts.items()}
    month_shares = {m: c / total for m, c in month_counts.items()}
    max_symbol, max_symbol_share = max(symbol_shares.items(), key=lambda kv: kv[1])
    max_month, max_month_share = max(month_shares.items(), key=lambda kv: kv[1])
    return Concentration(symbol_shares, month_shares, max_symbol, max_symbol_share, max_month, max_month_share)


@dataclass
class BootstrapResult:
    point_estimate: float
    lower: float
    upper: float
    draws: int
    seed: int


def month_cluster_bootstrap(
    diffs: list, draws: int = FROZEN_DRAWS, seed: int = FROZEN_SEED, ci: float = 0.95,
) -> BootstrapResult:
    """§7: "95% percentile interval from a month-cluster bootstrap
    (resample calendar months of the fill date with replacement), 10,000
    draws, seed 20261029." Resamples the CLUSTER UNITS (distinct months),
    the same count as observed, with replacement; each draw's mean is
    trade-weighted (sum of selected months' diff-sums / sum of their
    trade counts), not an average of per-month means -- preserves the
    overall mean estimator. Vectorized via numpy for the 10,000 draws."""
    if not diffs:
        raise ValueError("month_cluster_bootstrap requires at least one paired difference")

    month_sums: dict = {}
    month_counts: dict = {}
    for d in diffs:
        month_sums[d.fill_month] = month_sums.get(d.fill_month, 0.0) + d.diff
        month_counts[d.fill_month] = month_counts.get(d.fill_month, 0) + 1

    months = sorted(month_sums.keys())
    n_months = len(months)
    total_sum = sum(month_sums.values())
    total_count = sum(month_counts.values())
    point_estimate = total_sum / total_count

    rng = np.random.default_rng(seed)
    sums_arr = np.array([month_sums[m] for m in months])
    counts_arr = np.array([month_counts[m] for m in months])
    idx = rng.integers(0, n_months, size=(draws, n_months))
    draw_sums = sums_arr[idx].sum(axis=1)
    draw_counts = counts_arr[idx].sum(axis=1)
    draw_means = draw_sums / draw_counts

    alpha = (1.0 - ci) / 2.0
    lower = float(np.percentile(draw_means, alpha * 100))
    upper = float(np.percentile(draw_means, (1.0 - alpha) * 100))
    return BootstrapResult(point_estimate=point_estimate, lower=lower, upper=upper, draws=draws, seed=seed)


@dataclass
class Decision:
    verdict: str  # "PASS" | "FAIL" | "INCONCLUSIVE"
    reasons: list


def decide(
    diffs: list, bootstrap: BootstrapResult, per_period: dict,
    concentration: Concentration, sufficiency: Sufficiency,
    min_qualifying_periods: int = MIN_QUALIFYING_PERIODS,
    max_symbol_share: float = MAX_SYMBOL_SHARE, max_month_share: float = MAX_MONTH_SHARE,
) -> Decision:
    """§7 EXACTLY (condition 4 bifurcates between INCONCLUSIVE "too few
    qualifying periods" and FAIL "not positive in enough of them" --
    these are different conditions, not one flag). The verdict logic
    itself is unchanged from a strict priority order: INCONCLUSIVE if
    ANY of {insufficient sample (condition 1), too few qualifying
    periods (condition 4's INCONCLUSIVE half), concentration exceeded
    (condition 5)} holds -- but ALL three are checked and ALL applicable
    reasons are reported together (not just the first one found), so a
    caller sees the complete picture of why a run is INCONCLUSIVE in one
    pass rather than having to fix one condition and re-run to discover
    the next.

    1. Any of {condition 1 insufficient, too few qualifying periods,
       condition 5 concentration} -> INCONCLUSIVE, listing every one
       that actually failed.
    2. Otherwise: PASS iff mean net R > 0 (condition 2) AND bootstrap
       lower bound > 0 (condition 3) AND the paired difference is
       positive in >= `min_qualifying_periods` of the qualifying periods
       (condition 4's FAIL half) -- else FAIL, naming every failed one.
    """
    inconclusive_reasons = []
    if not sufficiency.meets_minimum:
        inconclusive_reasons.append("insufficient sample (condition 1)")

    qualifying_periods = [p for p in per_period.values() if p.qualifies]
    if len(qualifying_periods) < min_qualifying_periods:
        inconclusive_reasons.append(f"fewer than {min_qualifying_periods} qualifying periods (condition 4)")

    if concentration.max_symbol_share > max_symbol_share:
        inconclusive_reasons.append(
            f"symbol concentration {concentration.max_symbol_share:.1%} "
            f"> {max_symbol_share:.0%} (condition 5)"
        )
    if concentration.max_month_share > max_month_share:
        inconclusive_reasons.append(
            f"month concentration {concentration.max_month_share:.1%} "
            f"> {max_month_share:.0%} (condition 5)"
        )

    if inconclusive_reasons:
        return Decision("INCONCLUSIVE", inconclusive_reasons)

    fail_reasons = []
    mean_setup_r = sum(d.setup_r for d in diffs) / len(diffs)
    if not (mean_setup_r > 0):
        fail_reasons.append("mean net R of the setups is not greater than 0 (condition 2)")
    if not (bootstrap.lower > 0):
        fail_reasons.append("lower bound of the interval is not greater than 0 (condition 3)")
    positive_qualifying = sum(1 for p in qualifying_periods if p.mean_diff_positive)
    if positive_qualifying < min_qualifying_periods:
        fail_reasons.append(
            f"paired difference positive in only {positive_qualifying} of "
            f"{len(qualifying_periods)} qualifying periods (condition 4)"
        )

    if fail_reasons:
        return Decision("FAIL", fail_reasons)
    return Decision("PASS", [])


@dataclass
class Label:
    verdict: str  # "CONFIRMATORY" | "EXPLORATORY"
    failed_conditions: list


def determine_label(
    mode: str, variants: list, header_slippage_bps: float, seed: int, draws: int,
    prereg_committed: bool, tree_clean: bool, library_versions_ok: bool,
    manifest_matches: bool, recompute_mismatches: list, preregistration_hash_matches: bool,
) -> Label:
    """§13.6 + §13.8: CONFIRMATORY only when every listed condition
    holds, else EXPLORATORY with every failed condition named -- never
    silently downgraded without saying why.

    `header_slippage_bps` is the slippage recorded in the ORIGINAL
    --run-outcomes run's own header (outcome_params_json), never a CLI
    argument to this analysis -- §13.8: "slippage 10 bps (from its
    recorded outcome parameters)". `mode`/`variants` are likewise read
    from that same run header (§13.8: mode run_outcomes, variants
    exactly ["V3"]). `preregistration_hash_matches` compares the run
    header's recorded preregistration_file_hash against the
    pre-registration file's sha256 as committed in git HEAD (§13.8)."""
    failed = []
    if mode != "run_outcomes":
        failed.append(f"results DB mode {mode!r} is not 'run_outcomes'")
    if list(variants) != [FROZEN_VARIANT]:
        failed.append(f"results DB variants {list(variants)!r} is not exactly [{FROZEN_VARIANT!r}]")
    if header_slippage_bps != FROZEN_SLIPPAGE_BPS:
        failed.append(f"run header slippage_bps {header_slippage_bps} != frozen {FROZEN_SLIPPAGE_BPS}")
    if seed != FROZEN_SEED:
        failed.append(f"seed {seed} != frozen {FROZEN_SEED}")
    if draws != FROZEN_DRAWS:
        failed.append(f"draws {draws} != frozen {FROZEN_DRAWS}")
    if not prereg_committed:
        failed.append("pre-registration file is not committed in git HEAD")
    if not tree_clean:
        failed.append("working tree is not clean")
    if not library_versions_ok:
        failed.append("installed library versions do not match requirements.txt")
    if not manifest_matches:
        failed.append("cached price data does not match the run's data manifest")
    if not preregistration_hash_matches:
        failed.append("run header's recorded pre-registration hash does not match the committed file's sha256")
    if recompute_mismatches:
        failed.append(f"{len(recompute_mismatches)} trade(s) have a stored-vs-recomputed mismatch")
    verdict = "CONFIRMATORY" if not failed else "EXPLORATORY"
    return Label(verdict, failed)


# ── §8: non-confirmatory, descriptive only -- never feeds decide() ────────

def by_target_source(rows: list) -> dict:
    return _grouped_r_stats(rows, lambda r: r.get("target_source") or "UNKNOWN")


def by_year(rows: list, date_key: str = "fill_date") -> dict:
    def _year(r):
        d = r.get(date_key)
        return d.year if d is not None else None
    return _grouped_r_stats([r for r in rows if _year(r) is not None], _year)


def by_regime(rows: list) -> dict:
    return _grouped_r_stats(rows, lambda r: r.get("regime") or "UNKNOWN")


def _grouped_r_stats(rows: list, key_fn) -> dict:
    groups: dict = {}
    for r in rows:
        groups.setdefault(key_fn(r), []).append(r)
    out = {}
    for key, group in groups.items():
        rs = [x["r_multiple"] for x in group if x.get("r_multiple") is not None]
        out[key] = {"count": len(group), "mean_r": (sum(rs) / len(rs)) if rs else None}
    return out


def win_rate_and_r_stats(rows: list) -> dict:
    rs = [r["r_multiple"] for r in rows if r.get("r_multiple") is not None]
    if not rs:
        return {
            "count": 0, "win_rate": None, "avg_win_r": None, "avg_loss_r": None,
            "avg_mae_r": None, "avg_mfe_r": None, "timeout_share": None,
        }
    wins = [x for x in rs if x > 0]
    losses = [x for x in rs if x <= 0]
    timeouts = sum(1 for r in rows if r.get("first_barrier_hit") == BARRIER_TIMEOUT_30)
    maes = [r["mae_r"] for r in rows if r.get("mae_r") is not None]
    mfes = [r["mfe_r"] for r in rows if r.get("mfe_r") is not None]
    return {
        "count": len(rs),
        "win_rate": len(wins) / len(rs),
        "avg_win_r": (sum(wins) / len(wins)) if wins else None,
        "avg_loss_r": (sum(losses) / len(losses)) if losses else None,
        "avg_mae_r": (sum(maes) / len(maes)) if maes else None,
        "avg_mfe_r": (sum(mfes) / len(mfes)) if mfes else None,
        "timeout_share": (timeouts / len(rows)) if rows else None,
    }


def rejection_shares(classified_counts: dict, no_peer_count: int) -> dict:
    """classified_counts: {status: count} for every accepted row's
    recomputed status (not_trade + excluded statuses). no_peer_count:
    trades excluded in §6 for < MIN_PEERS usable peers -- tracked
    separately since it's a §7-stage exclusion, not an outcome status."""
    total = sum(classified_counts.values()) + no_peer_count
    if total == 0:
        return {}
    shares = {status: count / total for status, count in classified_counts.items()}
    shares["no_peer"] = no_peer_count / total
    return shares
