"""Small-sample-honest reporting: bootstrap (resampled by symbol)
confidence interval of mean excess return, and the inconclusive label.
Pure numpy — no scipy."""
import numpy as np

from . import config


def bootstrap_mean_ci(
    values_by_symbol: dict[str, list[float]],
    n_resamples: int = config.BOOTSTRAP_RESAMPLES,
    confidence_pct: float = config.BOOTSTRAP_CONFIDENCE_PCT,
    seed: int | None = config.BOOTSTRAP_SEED,
) -> tuple[float, float, float]:
    """Resamples SYMBOLS with replacement (not individual detections —
    a symbol contributing many detections isn't treated as that many
    independent draws). Returns (observed_mean, lower, upper)."""
    symbols = list(values_by_symbol.keys())
    all_values = [v for vs in values_by_symbol.values() for v in vs]
    if not all_values:
        return float("nan"), float("nan"), float("nan")
    observed_mean = float(np.mean(all_values))
    if not symbols:
        return observed_mean, float("nan"), float("nan")

    rng = np.random.default_rng(seed)
    n_symbols = len(symbols)
    resample_means: list[float] = []
    for _ in range(n_resamples):
        sampled_symbols = rng.choice(symbols, size=n_symbols, replace=True)
        pooled: list[float] = []
        for s in sampled_symbols:
            pooled.extend(values_by_symbol[s])
        if pooled:
            resample_means.append(float(np.mean(pooled)))

    if not resample_means:
        return observed_mean, float("nan"), float("nan")

    lower_pct = (100 - confidence_pct) / 2
    upper_pct = 100 - lower_pct
    lower = float(np.percentile(resample_means, lower_pct))
    upper = float(np.percentile(resample_means, upper_pct))
    return observed_mean, lower, upper


def summarize_pattern(
    pattern: str,
    direction: str,
    scored: list[dict],
    horizon: int,
    seed: int | None = config.BOOTSTRAP_SEED,
) -> dict:
    """`scored`: list of dicts with at least {"symbol": str,
    "excess_return_by_horizon": {horizon: float | None, ...}}. Returns one
    report row: n, distinct symbols, concentration, hit rate, mean/median
    excess return, bootstrap interval, and the inconclusive label."""
    n = len(scored)
    counts_by_symbol: dict[str, int] = {}
    for s in scored:
        counts_by_symbol[s["symbol"]] = counts_by_symbol.get(s["symbol"], 0) + 1
    distinct_symbols = len(counts_by_symbol)
    max_detections_per_symbol = max(counts_by_symbol.values()) if counts_by_symbol else 0

    values_by_symbol: dict[str, list[float]] = {}
    for s in scored:
        er = s["excess_return_by_horizon"].get(horizon)
        if er is not None:
            values_by_symbol.setdefault(s["symbol"], []).append(er)
    all_values = [v for vs in values_by_symbol.values() for v in vs]

    mean_er, ci_lower, ci_upper = bootstrap_mean_ci(values_by_symbol, seed=seed)
    median_er = float(np.median(all_values)) if all_values else float("nan")
    hit_rate = (sum(1 for v in all_values if v > 0) / len(all_values)) if all_values else float("nan")

    inconclusive = n < config.INCONCLUSIVE_MIN_N or distinct_symbols < config.INCONCLUSIVE_MIN_DISTINCT_SYMBOLS
    concentrated = (
        max_detections_per_symbol / n > config.CONCENTRATION_WARN_FRACTION if n else False
    )

    return {
        "pattern": pattern,
        "direction": direction,
        "horizon": horizon,
        "n": n,
        "distinct_symbols": distinct_symbols,
        "max_detections_per_symbol": max_detections_per_symbol,
        "hit_rate": hit_rate,
        "mean_excess_return": mean_er,
        "median_excess_return": median_er,
        "ci_lower": ci_lower,
        "ci_upper": ci_upper,
        "inconclusive": inconclusive,
        "concentrated": concentrated,
    }


def passes_decision_rule(primary_horizon_row: dict, yearly_rows: list[dict]) -> bool:
    """The pre-registered PASS rule from config.py's docstring, fixed
    before any real backtest runs: ALL of —
      - n >= INCONCLUSIVE_MIN_N
      - distinct_symbols >= INCONCLUSIVE_MIN_DISTINCT_SYMBOLS
      - the bootstrap interval's lower bound is > 0
      - YEAR RULE: counting only "qualifying" years (n >=
        MIN_DETECTIONS_PER_YEAR), there are at least MIN_QUALIFYING_YEARS
        of them, and a strict majority have positive mean excess return
    `primary_horizon_row` must be a summarize_pattern() row computed at
    PRIMARY_HORIZON; `yearly_rows` are that same (pattern, direction)'s
    per-calendar-year rows, also at PRIMARY_HORIZON (each itself a
    summarize_pattern() row, so each has its own "n"). Anything that
    fails this is reported as "NO EVIDENCE", not "doesn't work" — see the
    config.py docstring."""
    if primary_horizon_row["n"] < config.INCONCLUSIVE_MIN_N:
        return False
    if primary_horizon_row["distinct_symbols"] < config.INCONCLUSIVE_MIN_DISTINCT_SYMBOLS:
        return False
    ci_lower = primary_horizon_row["ci_lower"]
    if not (ci_lower == ci_lower and ci_lower > 0):  # NaN-safe (NaN != NaN)
        return False

    qualifying_years = [r for r in yearly_rows if r["n"] >= config.MIN_DETECTIONS_PER_YEAR]
    if len(qualifying_years) < config.MIN_QUALIFYING_YEARS:
        return False
    positive_years = sum(1 for r in qualifying_years if r["mean_excess_return"] > 0)
    return positive_years > len(qualifying_years) / 2


def label_pattern_direction(pass_by_tier: dict[str, bool]) -> str:
    """The TIER RULE from config.py's docstring. `pass_by_tier`: {tier_name:
    passed_decision_rule_in_that_tier} for one (pattern, direction) across
    every tier the backtest scored.
      - PASSes in >=1 non-ACTIVE tier -> "SUPPORTED" (corroborated outside
        the selection-biased ACTIVE sample — whether or not it also
        PASSes in ACTIVE).
      - PASSes in ACTIVE only (no non-ACTIVE PASS) ->
        "PROMISING (ACTIVE only, selection-biased)".
      - PASSes nowhere -> "NO EVIDENCE"."""
    non_active_pass = any(passed for tier, passed in pass_by_tier.items() if tier != "ACTIVE" and passed)
    if non_active_pass:
        return "SUPPORTED"
    if pass_by_tier.get("ACTIVE", False):
        return "PROMISING (ACTIVE only, selection-biased)"
    return "NO EVIDENCE"
