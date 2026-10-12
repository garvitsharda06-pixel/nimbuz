"""Isotonic calibration with an honest, forward-chained evaluation (§5, §8.3)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression

from dalal.scoring.stats import brier, reliability


@dataclass
class Calibrator:
    """Monotone map p_raw → p_calibrated. Identity until fitted on ≥ min_n points."""

    x: list[float] = field(default_factory=list)
    y: list[float] = field(default_factory=list)
    n_fit: int = 0

    def __call__(self, p: float | np.ndarray) -> Any:
        if not self.x:
            return p
        return np.interp(p, self.x, self.y)

    @classmethod
    def fit(cls, p: np.ndarray, y: np.ndarray, min_n: int = 200) -> Calibrator:
        p, y = np.asarray(p, float), np.asarray(y, float)
        ok = np.isfinite(p)
        p, y = p[ok], y[ok]
        if len(p) < min_n:
            return cls()
        iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip").fit(p, y)
        xs = np.unique(np.round(p, 4))
        return cls(list(map(float, xs)), list(map(float, iso.predict(xs))), len(p))

    def to_dict(self) -> dict[str, Any]:
        return {"x": self.x, "y": self.y, "n_fit": self.n_fit}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Calibrator:
        return cls(list(d.get("x", [])), list(d.get("y", [])), int(d.get("n_fit", 0)))


def forward_calibration(df: pd.DataFrame, min_n: int = 200) -> dict[str, Any]:
    """For each fold k, fit isotonic on folds < k and apply to fold k. Reports Brier for the
    climatological base rate (training hit rate), raw bucket predictions, and calibrated
    predictions, plus reliability rows. Only OOS rows (fold ≥ 0) are used."""
    oos = df[(df["fold"] >= 0) & np.isfinite(df["p_oos"])].sort_values("day")
    if oos.empty:
        return {"n": 0}
    p_cal = np.full(len(oos), np.nan)
    p_base = np.full(len(oos), np.nan)
    folds = sorted(oos["fold"].unique())
    y = oos["win"].to_numpy(float)
    praw = oos["p_oos"].to_numpy(float)
    fold_arr = oos["fold"].to_numpy()
    for k in folds:
        prior = fold_arr < k
        cur = fold_arr == k
        cal = Calibrator.fit(praw[prior], y[prior], min_n)
        p_cal[cur] = cal(praw[cur]) if cal.x else praw[cur]
        p_base[cur] = y[prior].mean() if prior.any() else np.nan
    ok = np.isfinite(p_base)
    return {
        "n": len(oos),
        "brier_base": brier(p_base[ok], y[ok]),
        "brier_raw": brier(praw, y),
        "brier_cal": brier(p_cal, y),
        "reliability_raw": reliability(praw, y),
        "reliability_cal": reliability(p_cal, y),
        "p_cal": p_cal,
        "index": oos.index.to_numpy(),
    }
