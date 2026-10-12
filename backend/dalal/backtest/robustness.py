"""Robustness (§9): Monte Carlo on trade order, deflated Sharpe, breakdowns, sensitivity grids."""

from __future__ import annotations

import itertools
import math
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import kurtosis, norm, skew

from dalal.scoring.stats import bucket_stats, max_drawdown

EULER_GAMMA = 0.5772156649015329


def monte_carlo(r: np.ndarray, n_paths: int = 1000, seed: int = 0) -> dict[str, float]:
    """Shuffle trade order: distribution of max drawdown (R). Bootstrap: distribution of total R."""
    r = np.asarray(r, float)
    if len(r) < 10:
        return {}
    rng = np.random.default_rng(seed)
    dd = np.array([max_drawdown(rng.permutation(r)) for _ in range(n_paths)])
    boot = np.array([rng.choice(r, len(r)).sum() for _ in range(n_paths)])
    return {"dd_p50": float(np.percentile(dd, 50)), "dd_p95": float(np.percentile(dd, 95)),
            "dd_actual": max_drawdown(r), "total_p05": float(np.percentile(boot, 5)),
            "total_p50": float(np.percentile(boot, 50)), "p_total_le_0": float((boot <= 0).mean())}


def deflated_sharpe(r: np.ndarray, n_trials: int, sr_var_trials: float | None = None) -> dict[str, float]:
    """Bailey & López de Prado (2014) deflated Sharpe ratio on per-trade returns.

    n_trials: number of variants tried for this setup (multiple-testing haircut).
    sr_var_trials: variance of the per-trade Sharpe across those variants (if known).
    """
    r = np.asarray(r, float)
    T = len(r)
    if T < 30 or np.std(r) == 0:
        return {"sr": float("nan"), "sr0": float("nan"), "dsr": float("nan"), "n_trials": n_trials}
    sr = float(np.mean(r) / np.std(r, ddof=1))
    g3 = float(skew(r))
    g4 = float(kurtosis(r, fisher=False))
    n = max(1, n_trials)
    if n > 1:
        v = sr_var_trials if sr_var_trials is not None and sr_var_trials > 0 else 1.0 / T
        sr0 = math.sqrt(v) * ((1 - EULER_GAMMA) * norm.ppf(1 - 1 / n) + EULER_GAMMA * norm.ppf(1 - 1 / (n * math.e)))
    else:
        sr0 = 0.0
    den = math.sqrt(max(1e-12, 1 - g3 * sr + (g4 - 1) / 4 * sr * sr))
    dsr = float(norm.cdf((sr - sr0) * math.sqrt(T - 1) / den))
    return {"sr": sr, "sr0": float(sr0), "dsr": dsr, "n_trials": n, "skew": g3, "kurt": g4}


def breakdowns(df: pd.DataFrame) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    if df.empty:
        return out
    d = df.assign(year=pd.to_datetime(df["day"]).dt.year,
                  hour=df["signal_time"].str.slice(0, 2) + ":00")
    for col in ["year", "regime_name", "hour", "side"]:
        rows = []
        for val, g in d.groupby(col):
            b = bucket_stats(g["win"].to_numpy(), g["r_net"].to_numpy())
            rows.append({"bucket": str(val), **b.as_dict()})
        out[col] = rows
    return out


def param_grid(grid: dict[str, list[Any]]) -> list[dict[str, Any]]:
    keys = list(grid)
    return [dict(zip(keys, vals, strict=True)) for vals in itertools.product(*(grid[k] for k in keys))]


# Pre-declared sensitivity grids (2 parameters each → heatmap). Every cell counts as a trial.
SENSITIVITY_GRIDS: dict[str, dict[str, list[Any]]] = {
    "orb": {"rvol_min": [1.0, 1.2, 1.5], "t1_r": [0.75, 1.0, 1.5]},
    "vwap_reclaim": {"rvol_bar_min": [0.8, 1.0, 1.3], "t1_r": [0.75, 1.0, 1.5]},
    "trend_pullback": {"touch_pct": [0.0005, 0.001, 0.002], "t1_r": [0.75, 1.0, 1.5]},
    "pdhl_retest": {"retest_pct": [0.001, 0.0015, 0.003], "t1_r": [0.75, 1.0, 1.5]},
    "gap_go": {"gap_atr_min": [0.3, 0.5, 0.8], "t1_r": [0.75, 1.0, 1.5]},
    "gap_fade": {"gap_atr_min": [0.3, 0.5, 0.8], "t1_r": [0.75, 1.0, 1.5]},
    "rel_strength": {"rs_z": [1.0, 1.5, 2.0], "t1_r": [0.75, 1.0, 1.5]},
    "vwap_mr": {"k_sigma": [1.5, 2.0, 2.5], "min_rr": [0.6, 0.8, 1.0]},
}
