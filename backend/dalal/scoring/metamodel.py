"""Step 2 (§8.3): LightGBM meta-labelling model.

Predicts which rule-based setup instances reach T1 before the stop. Trained with purged,
embargoed walk-forward folds (the same folds as the rule book), calibrated with isotonic
regression on a held-out slice of each training window. **Ships only if it beats the rule
book out-of-sample** on the same instances (Brier AND AUC). Explanations are exact TreeSHAP
contributions from LightGBM (`pred_contrib=True`).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from dalal.backtest.walkforward import make_folds
from dalal.pipeline import META_FEATURES
from dalal.scoring.calibration import Calibrator
from dalal.scoring.stats import brier

# features whose sign must be flipped for shorts so "good for the trade" has one direction
ORIENTED = {"ret_pdc", "ret_open", "rs_mkt_z", "rs_sec_z", "trend15", "gap_atr", "nifty_ret", "mkt_score"}
CATS = ["setup", "side", "regime"]
PARAMS: dict[str, Any] = {"objective": "binary", "learning_rate": 0.03, "num_leaves": 15, "min_data_in_leaf": 80,
                          "feature_fraction": 0.8, "bagging_fraction": 0.8, "bagging_freq": 1, "lambda_l2": 5.0,
                          "verbose": -1, "seed": 7, "deterministic": True, "num_threads": 2}
ROUNDS = 300


def design(df: pd.DataFrame, setups: list[str]) -> pd.DataFrame:
    X = pd.DataFrame(index=df.index)
    sg = np.where(df["side"] == "long", 1.0, -1.0)
    for f in META_FEATURES:
        v = pd.to_numeric(df[f], errors="coerce") if f in df.columns else pd.Series(np.nan, index=df.index)
        X[f] = v * sg if f in ORIENTED else v
    X["setup"] = pd.Categorical(df["setup"], categories=setups).codes
    X["side"] = (df["side"] == "long").astype(int)
    X["regime"] = df["regime"].astype(int)
    return X


def _fit(X: pd.DataFrame, y: np.ndarray) -> lgb.Booster:
    ds = lgb.Dataset(X, label=y, categorical_feature=["setup", "regime"], free_raw_data=False)
    return lgb.train(PARAMS, ds, num_boost_round=ROUNDS)


def _fit_calibrated(X: pd.DataFrame, y: np.ndarray, days: np.ndarray) -> tuple[lgb.Booster, Calibrator]:
    """Train on the earlier 80 % of the window (by day), isotonic on the latest 20 %."""
    ud = np.unique(days)
    cut = ud[int(len(ud) * 0.8)] if len(ud) > 5 else ud[-1]
    tr, ho = days < cut, days >= cut
    model = _fit(X[tr], y[tr])
    cal = Calibrator.fit(np.asarray(model.predict(X[ho]), float), y[ho], min_n=100)
    return model, cal


@dataclass
class MetaReport:
    shipped: bool
    reason: str
    n_oos: int
    brier_rule: float
    brier_meta: float
    auc_rule: float
    auc_meta: float
    sel_exp_rule: float          # mean net R of the top-30 % instances ranked by each model
    sel_exp_meta: float
    folds: list[dict[str, Any]] = field(default_factory=list)


def walk_forward_meta(wf: pd.DataFrame, train_months: int, test_months: int, embargo_days: int,
                      min_train: int = 500) -> tuple[pd.Series, MetaReport]:
    """OOS meta predictions for every instance that also has a rule-book OOS prediction."""
    setups = sorted(wf["setup"].unique())
    X = design(wf, setups)
    y = wf["win"].astype(int).to_numpy()
    days = wf["day"].to_numpy()
    p_meta = pd.Series(np.nan, index=wf.index)
    folds_out = []
    for f in make_folds(sorted(set(days)), train_months, test_months, embargo_days):
        tr = days < f.train_end                       # purged + embargoed: nothing within the embargo
        te = (days >= f.test_start) & (days < f.test_end)
        if tr.sum() < min_train or te.sum() == 0:
            continue
        model, cal = _fit_calibrated(X[tr], y[tr], days[tr])
        raw = np.asarray(model.predict(X[te]), float)
        p_meta[te] = cal(raw) if cal.x else raw
        folds_out.append({"fold": f.k, "n_train": int(tr.sum()), "n_test": int(te.sum())})
    rep = compare(wf, p_meta)
    rep.folds = folds_out
    return p_meta, rep


def compare(wf: pd.DataFrame, p_meta: pd.Series) -> MetaReport:
    m = p_meta.notna() & wf["p_cal_oos"].notna()
    if m.sum() < 200:
        return MetaReport(False, f"insufficient OOS overlap ({int(m.sum())} < 200)", int(m.sum()), *(float("nan"),) * 6)
    y = wf.loc[m, "win"].astype(int).to_numpy()
    pr, pm = wf.loc[m, "p_cal_oos"].to_numpy(float), p_meta[m].to_numpy(float)
    r = wf.loc[m, "r_net"].to_numpy(float)
    br, bm = brier(pr, y), brier(pm, y)
    try:
        ar, am = roc_auc_score(y, pr), roc_auc_score(y, pm)
    except ValueError:
        ar = am = float("nan")
    k = max(1, int(0.3 * len(y)))
    se_r = float(r[np.argsort(-pr, kind="stable")[:k]].mean())
    se_m = float(r[np.argsort(-pm, kind="stable")[:k]].mean())
    better = bm < br * 0.99 and am > ar
    reason = (f"ships: OOS Brier {bm:.4f} < rule {br:.4f} and AUC {am:.3f} > {ar:.3f}" if better else
              f"does NOT ship: OOS Brier {bm:.4f} vs rule {br:.4f}, AUC {am:.3f} vs {ar:.3f} "
              "(needs Brier ≥ 1 % better AND higher AUC)")
    return MetaReport(bool(better), reason, int(m.sum()), br, bm, ar, am, se_r, se_m)


class MetaModel:
    """Final model (all data) for live use; only loaded when the OOS comparison shipped it."""

    def __init__(self, booster: lgb.Booster, cal: Calibrator, setups: list[str], report: dict[str, Any]):
        self.booster, self.cal, self.setups, self.report = booster, cal, setups, report

    @classmethod
    def train(cls, wf: pd.DataFrame, report: MetaReport) -> MetaModel:
        setups = sorted(wf["setup"].unique())
        X = design(wf, setups)
        model, cal = _fit_calibrated(X, wf["win"].astype(int).to_numpy(), wf["day"].to_numpy())
        return cls(model, cal, setups, report.__dict__)

    def save(self, d: Path) -> None:
        d.mkdir(parents=True, exist_ok=True)
        self.booster.save_model(str(d / "metamodel.txt"))
        (d / "metamodel.json").write_text(json.dumps({"calibrator": self.cal.to_dict(), "setups": self.setups,
                                                      "report": _clean(self.report), "features": META_FEATURES},
                                                     indent=1))

    @classmethod
    def load(cls, d: Path) -> MetaModel | None:
        if not (d / "metamodel.txt").exists():
            return None
        meta = json.loads((d / "metamodel.json").read_text())
        if not meta["report"].get("shipped"):
            return None
        b = lgb.Booster(model_file=str(d / "metamodel.txt"))
        return cls(b, Calibrator.from_dict(meta["calibrator"]), meta["setups"], meta["report"])

    def predict(self, rows: pd.DataFrame) -> tuple[np.ndarray, list[list[tuple[str, float, float]]]]:
        """Calibrated p and the top-3 SHAP drivers (feature, contribution, raw value) per row."""
        X = design(rows, self.setups)
        raw = np.asarray(self.booster.predict(X), float)
        p = np.asarray(self.cal(raw) if self.cal.x else raw, float)
        contrib = np.asarray(self.booster.predict(X, pred_contrib=True), float)[:, :-1]
        drivers = []
        cols = list(X.columns)
        for i in range(len(X)):
            order = np.argsort(-np.abs(contrib[i]))
            top = [(cols[j], float(contrib[i, j]), float(X.iloc[i, j])) for j in order
                   if cols[j] not in ("setup", "side") and contrib[i, j] > 0][:3]
            drivers.append(top)
        return p, drivers


SHAP_TEXT = {
    "rs_mkt_z": lambda v: f"RS vs Nifty {v:+.1f}σ (in the trade's direction)",
    "rs_sec_z": lambda v: f"RS vs sector {v:+.1f}σ",
    "rvol_cum": lambda v: f"Volume {v:.1f}× normal for this time of day",
    "rvol_bar": lambda v: f"Signal-bar volume {v:.1f}× normal",
    "sector_rank": lambda v: f"Sector rank {v:.0f}",
    "breadth": lambda v: f"Breadth {v:.0%} of universe above VWAP",
    "dist_vwap_atr": lambda v: f"{v:+.1f}× ATR(5m) from VWAP",
    "dist_ema_atr": lambda v: f"{v:+.1f}× ATR(5m) from the 20-EMA",
    "ema_slope_bps": lambda v: f"20-EMA slope {v:+.0f} bps per 15 min",
    "gap_atr": lambda v: f"Gap {v:+.1f}× daily ATR",
    "trend15": lambda v: "15-min trend aligned" if v > 0 else "15-min trend against",
    "rv_ratio": lambda v: f"Intraday volatility {v:.1f}× normal",
    "vix_chg": lambda v: f"India VIX {v * 100:+.1f}% vs prev close",
    "nifty_ret": lambda v: f"Nifty {v * 100:+.2f}% (trade direction)",
    "mkt_score": lambda v: f"Market trend score {v:+.0f}",
    "atr5_pct": lambda v: f"ATR(5m) {v:.2f}% of price",
    "atr14_pct": lambda v: f"Daily ATR {v:.1f}% of price",
    "ret_pdc": lambda v: f"{v * 100:+.2f}% vs prev close (trade direction)",
    "ret_open": lambda v: f"{v * 100:+.2f}% from the open (trade direction)",
    "beta": lambda v: f"Beta {v:.2f}",
    "t": lambda v: "Signal time-of-day effect",
    "rr1": lambda v: f"Reward:risk to T1 {v:.1f}",
    "regime": lambda v: "Regime effect",
}


def shap_bullets(drivers: list[tuple[str, float, float]]) -> list[str]:
    out = []
    for f, _c, v in drivers:
        fn = SHAP_TEXT.get(f)
        if fn is not None and (isinstance(v, float) and math.isfinite(v)):
            out.append(fn(v))
    return out


def _clean(x: Any) -> Any:
    if isinstance(x, dict):
        return {k: _clean(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_clean(v) for v in x]
    if isinstance(x, float) and not math.isfinite(x):
        return None
    if isinstance(x, (np.floating, np.integer, np.bool_)):
        return x.item()
    return x
