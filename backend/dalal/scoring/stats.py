"""Probability and expectancy arithmetic (§5)."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion."""
    if n <= 0:
        return (float("nan"), float("nan"))
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (max(0.0, centre - half), min(1.0, centre + half))


def ev(p: float, win_r: float, loss_r: float) -> float:
    """Expectancy in R: p·W̄ + (1−p)·L̄  (L̄ is the mean net R of non-wins, usually < 0)."""
    return p * win_r + (1 - p) * loss_r


def breakeven_p(win_r: float, loss_r: float) -> float:
    """Win rate at which EV = 0."""
    if not np.isfinite(win_r) or not np.isfinite(loss_r) or win_r - loss_r <= 0:
        return float("nan")
    return -loss_r / (win_r - loss_r) if loss_r < 0 else 0.0


def shrink(k: int, n: int, prior_p: float, strength: float) -> float:
    """Beta-binomial shrinkage of a bucket hit rate toward a prior mean."""
    return (k + strength * prior_p) / (n + strength)


@dataclass
class BucketStats:
    n: int
    wins: int
    hit: float
    ci_lo: float
    ci_hi: float
    win_r: float          # mean net R | win
    loss_r: float         # mean net R | not win
    expectancy: float     # realised mean net R
    cons_ev: float        # EV at the Wilson lower bound
    breakeven: float
    profit_factor: float

    def as_dict(self) -> dict[str, float]:
        return {k: (round(v, 6) if isinstance(v, float) else v) for k, v in self.__dict__.items()}


def bucket_stats(win: np.ndarray, r_net: np.ndarray, z: float = 1.96) -> BucketStats:
    win = np.asarray(win, dtype=bool)
    r = np.asarray(r_net, dtype=float)
    n = len(r)
    if n == 0:
        nan = float("nan")
        return BucketStats(0, 0, nan, nan, nan, nan, nan, nan, nan, nan, nan)
    k = int(win.sum())
    lo, hi = wilson(k, n, z)
    w = float(r[win].mean()) if k else float("nan")
    lr = float(r[~win].mean()) if k < n else float("nan")
    gp, gl = float(r[r > 0].sum()), float(-r[r < 0].sum())
    pf = gp / gl if gl > 0 else float("inf")
    w_ = w if np.isfinite(w) else 0.0
    l_ = lr if np.isfinite(lr) else 0.0
    return BucketStats(n, k, k / n, lo, hi, w, lr, float(r.mean()), ev(lo, w_, l_), breakeven_p(w_, l_), pf)


def brier(p: np.ndarray, y: np.ndarray) -> float:
    p, y = np.asarray(p, float), np.asarray(y, float)
    return float(np.mean((p - y) ** 2)) if len(p) else float("nan")


def reliability(p: np.ndarray, y: np.ndarray, bins: int = 10) -> list[dict[str, float]]:
    """Reliability diagram rows: mean predicted vs observed frequency per bin (+ Wilson CI)."""
    p, y = np.asarray(p, float), np.asarray(y, float)
    edges = np.linspace(0, 1, bins + 1)
    out = []
    for i in range(bins):
        m = (p >= edges[i]) & ((p < edges[i + 1]) if i < bins - 1 else (p <= edges[i + 1]))
        n = int(m.sum())
        if n == 0:
            continue
        k = int(y[m].sum())
        lo, hi = wilson(k, n)
        out.append({"bin_lo": float(edges[i]), "bin_hi": float(edges[i + 1]), "n": n, "pred": float(p[m].mean()),
                    "obs": k / n, "obs_lo": lo, "obs_hi": hi})
    return out


def max_drawdown(r: np.ndarray) -> float:
    if len(r) == 0:
        return 0.0
    eq = np.cumsum(r)
    peak = np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:]
    return float(np.max(peak - eq))
