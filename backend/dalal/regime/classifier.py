"""Market regime (§8.1), updated every 5-minute candle, strictly causal.

Inputs at bar t: Nifty vs its session TWAP (index VWAP proxy, indices carry no volume),
15-min trend, 20-EMA alignment, breadth (% of universe above VWAP), India VIX change vs
previous close, and intraday realised volatility vs its 20-day same-time average.

Labels: TREND_UP / TREND_DOWN / RANGE / HIGH_VOL_CHOP.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from datetime import date

import numpy as np

from dalal.features.core import Features

REGIMES = ["TREND_UP", "TREND_DOWN", "RANGE", "HIGH_VOL_CHOP"]
R_UP, R_DN, R_RANGE, R_CHOP = 0, 1, 2, 3


@dataclass
class MarketContext:
    """Market-wide arrays aligned on `days` (D, 75)."""

    days: np.ndarray
    regime: np.ndarray                       # (D, 75) int codes, -1 = unknown
    score: np.ndarray                        # (D, 75) trend score −4…+4
    breadth: np.ndarray                      # (D, 75) fraction of universe above VWAP
    adv_dec: np.ndarray                      # (D, 75) advancers / (adv + dec) vs prev close
    nifty_ret: np.ndarray                    # (D, 75) return vs prev close
    nifty_c: np.ndarray
    nifty_dc: np.ndarray                     # (D,) daily closes (complete days)
    vix: np.ndarray                          # (D, 75)
    vix_chg: np.ndarray                      # (D, 75) vs prev close
    rv_ratio: np.ndarray                     # (D, 75)
    bank_ret: np.ndarray                     # (D, 75)
    sector_ret: dict[str, np.ndarray] = field(default_factory=dict)   # sector -> (D, 75)
    sector_rank: dict[str, np.ndarray] = field(default_factory=dict)  # sector -> (D, 75), 1 = strongest

    def day_index(self) -> dict[date, int]:
        return {d: i for i, d in enumerate(self.days)}

    def align(self, sym_days: np.ndarray) -> np.ndarray:
        di = self.day_index()
        return np.array([di.get(d, -1) for d in sym_days], dtype=int)


def take_rows(arr: np.ndarray, rows: np.ndarray) -> np.ndarray:
    """Rows of a (D, …) market array for a symbol's days; missing days → NaN."""
    out = arr[np.clip(rows, 0, None)].astype(float)
    out[rows < 0] = np.nan
    return out


def classify(nifty: Features, breadth: np.ndarray, vix_chg: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    with np.errstate(invalid="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        c = nifty["c"]
        s1 = np.sign(c - nifty["vwap"])
        s2 = np.nan_to_num(nifty["trend15"])
        s3 = np.where((nifty["ema_slope"] > 0) & (c > nifty["ema20"]), 1,
                      np.where((nifty["ema_slope"] < 0) & (c < nifty["ema20"]), -1, 0))
        s4 = np.where(breadth >= 0.6, 1, np.where(breadth <= 0.4, -1, 0))
        score = s1 + s2 + s3 + s4
        chop = (nifty["rv_ratio"] >= 1.6) | (vix_chg >= 0.07)
        reg = np.where(chop & (np.abs(score) < 4), R_CHOP,
                       np.where(score >= 3, R_UP, np.where(score <= -3, R_DN, R_RANGE)))
        reg = np.where(np.isnan(c), -1, reg)
    return reg.astype(int), score


def sector_ranks(sector_ret: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    if not sector_ret:
        return {}
    names = list(sector_ret)
    stack = np.stack([np.nan_to_num(sector_ret[n], nan=-np.inf) for n in names])   # (S, D, 75)
    order = (-stack).argsort(axis=0).argsort(axis=0) + 1
    return {n: order[i] for i, n in enumerate(names)}
