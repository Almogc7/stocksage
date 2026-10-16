"""
Derive data-driven composite-engine layer weights from backtest results and
compare them, honestly, against the current fixed 25/25/25/25 split.

Pure analysis. Reads ONLY composite_backtest_signals (schema v11, written by
scripts/backtest_composite.py); does not touch analyzers/composite.py or any
live/observation-mode table. Nothing here changes scoring behavior anywhere
— it prints a report so a human can decide whether new weights are even
worth trying.

Method:
  1. Chronological train/test split (70/30 by signal_date, never randomized
     — randomizing would let same-day/overlapping-window signals leak
     across the split and inflate the "generalization" result).
  2. OLS: r_multiple ~ trend_pts + momentum_pts + volume_pts + rs_pts on the
     TRAINING set only. Coefficients (clipped at 0, renormalized to sum 100)
     are the candidate new weights. Reported with standard errors, t-stats,
     and p-values (normal approximation to the t-distribution — no scipy/
     statsmodels dependency in this project; at n in the thousands the
     approximation is standard practice, not a corner cut).
  3. A second OLS on the RAW underlying indicator values (rsi, macd_hist,
     pct_from_sma150, rel_vol, obv_slope, rs_ratio) instead of the already
     0-25-bucketed layer scores, to check whether the bucketing (the ramps
     in analyzers/composite.py's _rsi_points/_relvol_points/etc.) throws
     away predictive information. These raw values are NOT stored in
     composite_backtest_signals (only the bucketed layer points and
     rs_ratio are) — this script recomputes them by refetching each
     symbol's price history ONCE and re-deriving the indicators at each
     already-known signal_date via the exact same functions/parameters
     analyzers/composite.py uses (ta.momentum.rsi, ta.trend.MACD,
     volume_layer_points()) — never reimplemented, so there is no drift
     risk between what was scored historically and what is measured here.
  4. Both the CURRENT fixed weights and the NEW candidate weights are
     applied to the held-out TEST set, and win rate / mean r_multiple are
     reported for each — this is the honest generalization check.

IMPORTANT METHODOLOGICAL LIMIT (read before trusting the test-set numbers):
  composite_backtest_signals only contains signals that ALREADY cleared the
  fixed-weight flag_buy gate — days that failed under the current weights
  were never scored/stored. Re-weighting can therefore only RE-RANK/RE-
  FILTER this already-selected population; it cannot surface candidates the
  current weights excluded but a different weighting might have caught.
  This is a real, structural limitation of doing this analysis from
  observation-mode signal logs rather than a full re-scored backtest, not a
  bug in this script.

Usage (from repo root):
    python scripts/derive_composite_weights.py --run-id full_2y_run1
    python scripts/derive_composite_weights.py                      # latest run
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd
import ta

from analyzers.composite import volume_layer_points
from data.market_data_service import MarketDataService
from db.database import get_backtest_signals, get_latest_backtest_run_id
from scripts.backtest_composite import WARMUP_YEARS, _period_for_years, _trim_to_years

TRAIN_FRACTION = 0.70
LAYER_COLS = ["trend_pts", "momentum_pts", "volume_pts", "rs_pts"]
RAW_COLS = ["rsi", "macd_hist", "pct_from_sma150", "rel_vol", "obv_slope", "rs_ratio"]


# ── OLS with standard errors / t-stats / p-values (no scipy/statsmodels) ────

def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def ols_with_stats(X: np.ndarray, y: np.ndarray, names: list[str]) -> dict:
    n = X.shape[0]
    Xd = np.column_stack([np.ones(n), X])
    XtX_inv = np.linalg.pinv(Xd.T @ Xd)
    beta = XtX_inv @ Xd.T @ y
    y_hat = Xd @ beta
    resid = y - y_hat
    rss = float(resid @ resid)
    tss = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - rss / tss if tss > 0 else float("nan")
    dof = n - Xd.shape[1]
    sigma2 = rss / dof if dof > 0 else float("nan")
    se = np.sqrt(np.clip(np.diag(XtX_inv), 0, None) * sigma2)
    t_stats = np.divide(beta, se, out=np.full_like(beta, np.nan), where=se > 0)
    p_values = [2.0 * (1.0 - _norm_cdf(abs(t))) if t == t else float("nan") for t in t_stats]
    adj_r2 = 1.0 - (1.0 - r2) * (n - 1) / dof if dof > 0 and r2 == r2 else float("nan")
    return {
        "names": ["intercept"] + names, "beta": beta, "se": se,
        "t": t_stats, "p": p_values, "r2": r2, "adj_r2": adj_r2,
        "n": n, "dof": dof,
    }


def print_ols(label: str, fit: dict) -> None:
    print(f"\n-- {label} (n={fit['n']}, R²={fit['r2']:.4f}, adj R²={fit['adj_r2']:.4f}) --")
    for name, b, se, t, p in zip(fit["names"], fit["beta"], fit["se"], fit["t"], fit["p"]):
        flag = "***" if p == p and p < 0.001 else "**" if p == p and p < 0.01 else "*" if p == p and p < 0.05 else ""
        print(f"  {name:16s} coef={b:+.4f}  se={se:.4f}  t={t:+.2f}  p={p:.4f} {flag}")
    print("  (*** p<0.001, ** p<0.01, * p<0.05)")


# ── Raw indicator recomputation (reuses analyzers.composite functions) ─────

def fetch_raw_indicators(df: pd.DataFrame, symbols: list[str]) -> pd.DataFrame:
    """For each (symbol, signal_date) pair, refetch history once per symbol
    and recompute the raw underlying indicator values at that historical
    date using the SAME functions analyzers/composite.py uses internally."""
    service = MarketDataService()
    fetch_years = 3.0  # covers the 2y backtest window + SMA200 warmup with margin
    period = _period_for_years(fetch_years)

    out_rows: list[dict] = []
    by_symbol = df.groupby("symbol")
    total = len(by_symbol)
    for i, (symbol, rows) in enumerate(by_symbol, 1):
        if i % 50 == 0 or i == total:
            print(f"  [raw indicators] {i}/{total} symbols...")
        result = service.fetch_history(symbol, period=period)
        if not result.ok:
            continue
        hist = _trim_to_years(result.df.sort_index(), fetch_years)
        for _, row in rows.iterrows():
            sig_date = pd.Timestamp(row["signal_date"]).date()
            prior = hist.index[pd.to_datetime(hist.index).date <= sig_date]
            if len(prior) < 150:
                continue
            completed = hist.loc[:prior[-1]]
            closes = completed["close"]
            last_close = float(closes.iloc[-1])
            sma150 = float(closes.rolling(window=150).mean().iloc[-1])
            if sma150 != sma150:
                continue
            rsi = float(ta.momentum.rsi(closes, window=14).iloc[-1])
            hist_diff = ta.trend.MACD(closes).macd_diff()
            macd_hist = float(hist_diff.iloc[-1]) if hist_diff.notna().any() else None
            vol_layer = volume_layer_points(completed, market_open=False)
            out_rows.append({
                "id": row["id"],
                "rsi": rsi,
                "macd_hist": macd_hist,
                "pct_from_sma150": (last_close - sma150) / sma150 * 100.0,
                "rel_vol": vol_layer["rel_vol"],
                "obv_slope": vol_layer["obv_slope"],
            })

    raw_df = pd.DataFrame(out_rows)
    return df.merge(raw_df, on="id", how="left") if not raw_df.empty else df


# ── Weight derivation / test-set evaluation ─────────────────────────────────

def candidate_weights(fit: dict) -> dict[str, float]:
    """Layer coefficients (excluding intercept), clipped at 0 and normalized
    to sum to 100 — a negative coefficient means that layer, on this data,
    does not deserve a positive scoring weight at all."""
    coefs = {name: max(0.0, b) for name, b in zip(fit["names"][1:], fit["beta"][1:])}
    total = sum(coefs.values())
    if total <= 0:
        return {name: 0.0 for name in coefs}
    return {name: round(c / total * 100.0, 2) for name, c in coefs.items()}


def evaluate_weights(test: pd.DataFrame, weights: dict[str, float] | None) -> dict:
    """weights=None means the CURRENT fixed weights, i.e. the test set as
    logged (already flag_buy=True under 25/25/25/25 — see the module
    docstring's methodological limit)."""
    if weights is None:
        subset = test
    else:
        new_total = sum((test[layer] / 25.0) * weights[layer] for layer in LAYER_COLS)
        subset = test[new_total >= test["required_score"]]

    n = len(subset)
    if n == 0:
        return {"n": 0, "win_rate": float("nan"), "mean_r": float("nan"), "coverage": 0.0}
    return {
        "n": n,
        "win_rate": float((subset["r_multiple"] > 0).mean() * 100),
        "mean_r": float(subset["r_multiple"].mean()),
        "coverage": n / len(test) * 100,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run-id", help="Backtest run to analyze (default: latest)")
    parser.add_argument("--skip-raw-indicators", action="store_true",
                         help="Skip the raw-indicator refetch/regression (faster, skips requirement 3)")
    args = parser.parse_args()

    run_id = args.run_id or get_latest_backtest_run_id()
    if not run_id:
        print("No backtest runs found.")
        return 1

    rows = get_backtest_signals(run_id)
    df = pd.DataFrame(rows)
    df = df[df["r_multiple"].notna()].copy()
    df["signal_date"] = pd.to_datetime(df["signal_date"])
    df = df.sort_values("signal_date").reset_index(drop=True)
    n = len(df)
    print(f"Run {run_id}: {n} resolved signals, {df['symbol'].nunique()} symbols, "
          f"{df['signal_date'].min().date()} .. {df['signal_date'].max().date()}")

    # Chronological split at a DATE boundary (not a row-count boundary) so no
    # signal_date is split across train and test.
    unique_dates = np.sort(df["signal_date"].unique())
    cutoff_date = unique_dates[int(len(unique_dates) * TRAIN_FRACTION)]
    train = df[df["signal_date"] <= cutoff_date].copy()
    test = df[df["signal_date"] > cutoff_date].copy()
    print(f"Train: {len(train)} signals (<= {pd.Timestamp(cutoff_date).date()}), "
          f"Test: {len(test)} signals (> {pd.Timestamp(cutoff_date).date()})")

    # ── Requirement 2: bucketed layer-score regression on TRAIN ───────────
    X_train = train[LAYER_COLS].to_numpy(dtype=float)
    y_train = train["r_multiple"].to_numpy(dtype=float)
    layer_fit = ols_with_stats(X_train, y_train, LAYER_COLS)
    print_ols("Bucketed layer-score model (TRAIN)", layer_fit)

    weights = candidate_weights(layer_fit)
    print("\nCandidate data-driven weights (coefficients clipped at 0, normalized to sum 100):")
    for layer, w in weights.items():
        print(f"  {layer:16s} {w:6.2f} / 100  (current fixed: 25.00)")
    if sum(weights.values()) == 0:
        print("  ALL coefficients were <= 0 on the training set — none of the four layers "
              "earned a positive weight; see the p-values above before concluding anything further.")

    # ── Requirement 3: raw-indicator regression, compared by R² ────────────
    if not args.skip_raw_indicators:
        print("\nRecomputing raw indicator values for all resolved signals (refetch once per symbol)...")
        df_raw = fetch_raw_indicators(df, sorted(df["symbol"].unique()))
        missing = df_raw[RAW_COLS].isna().any(axis=1).sum()
        print(f"Raw indicators recovered for {len(df_raw) - missing}/{len(df_raw)} signals "
              f"({missing} dropped: symbol refetch failed or <150 bars available before that date).")
        df_raw = df_raw.dropna(subset=RAW_COLS)

        train_raw = df_raw[df_raw["signal_date"] <= cutoff_date]
        if len(train_raw) > len(RAW_COLS) + 5:
            X_raw = train_raw[RAW_COLS].to_numpy(dtype=float)
            y_raw = train_raw["r_multiple"].to_numpy(dtype=float)
            raw_fit = ols_with_stats(X_raw, y_raw, RAW_COLS)
            print_ols("Raw-indicator model (TRAIN)", raw_fit)
            print(f"\nR² comparison (TRAIN): bucketed layers={layer_fit['r2']:.4f}  "
                  f"raw indicators={raw_fit['r2']:.4f}  "
                  f"({'raw indicators explain more' if raw_fit['r2'] > layer_fit['r2'] else 'bucketed layers explain more (or tie)'})")
        else:
            print("Not enough rows with complete raw indicators to fit the raw-indicator model.")
    else:
        print("\n(Skipped raw-indicator regression: --skip-raw-indicators)")

    # ── Requirement 4: apply both weight schemes to the held-out TEST set ──
    print("\n" + "=" * 70)
    print("HELD-OUT TEST SET EVALUATION")
    print("=" * 70)
    print(
        "NOTE: the test set already only contains signals that cleared the FIXED-weight\n"
        "flag_buy gate — this evaluation can re-rank/re-filter that population, it cannot\n"
        "recover signals the fixed weights excluded. See the module docstring."
    )
    baseline = evaluate_weights(test, None)
    new = evaluate_weights(test, weights)
    print(f"\n{'':20s} {'n':>6s} {'coverage':>10s} {'win_rate':>10s} {'mean_r':>10s}")
    print(f"{'Current (25/25/25/25)':20s} {baseline['n']:6d} {baseline['coverage']:9.1f}% "
          f"{baseline['win_rate']:9.1f}% {baseline['mean_r']:+10.3f}")
    print(f"{'New (data-driven)':20s} {new['n']:6d} {new['coverage']:9.1f}% "
          f"{new['win_rate']:9.1f}% {new['mean_r']:+10.3f}")
    if new["n"] < 30:
        print(f"\nWARNING: only {new['n']} test signals still clear the new weights — "
              "too few to draw a reliable win-rate/mean-r conclusion from.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
