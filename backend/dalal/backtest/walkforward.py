"""Walk-forward out-of-sample evaluation (§9) of rule-based setup statistics (§8.3 step 1).

Folds: test windows of `test_months`, expanding training window of at least `train_months`,
with an embargo of `embargo_days` trading days between the end of training and the start of
the test window. For each test instance the predicted probability comes ONLY from training
data: a regime-bucket hit rate shrunk toward the setup-side mean (Beta prior).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd


@dataclass
class Fold:
    k: int
    train_end: date       # exclusive
    test_start: date
    test_end: date        # exclusive


def make_folds(days: list[date], train_months: int, test_months: int, embargo_days: int) -> list[Fold]:
    if not days:
        return []
    days = sorted(days)
    first = pd.Timestamp(days[0])
    start = (first + pd.DateOffset(months=train_months)).date()
    folds, k = [], 0
    while True:
        test_start = start
        test_end = (pd.Timestamp(test_start) + pd.DateOffset(months=test_months)).date()
        if test_start > days[-1]:
            break
        before = [d for d in days if d < test_start]
        train_end = before[-embargo_days] if len(before) > embargo_days else test_start
        folds.append(Fold(k, train_end, test_start, test_end))
        k += 1
        start = test_end
    return folds


def predict_bucket(train: pd.DataFrame, test: pd.DataFrame, prior_strength: float, min_bucket_n: int) -> np.ndarray:
    """p̂ for each test row from (setup, side, regime) buckets estimated on `train`."""
    if train.empty or test.empty:
        return np.full(len(test), np.nan)
    key2, key3 = ["setup", "side"], ["setup", "side", "regime"]
    pool = train.groupby(key2)["win"].agg(pk="sum", pn="count").reset_index()
    buck = train.groupby(key3)["win"].agg(bk="sum", bn="count").reset_index()
    t = test[key3].merge(pool, on=key2, how="left").merge(buck, on=key3, how="left")
    prior = t["pk"] / t["pn"]
    use_b = t["bn"].fillna(0) >= min_bucket_n
    k = np.where(use_b, t["bk"], t["pk"])
    n = np.where(use_b, t["bn"], t["pn"])
    return ((k + prior_strength * prior) / (n + prior_strength)).to_numpy(dtype=float)


def walk_forward(inst: pd.DataFrame, train_months: int, test_months: int, embargo_days: int,
                 prior_strength: float, min_bucket_n: int) -> tuple[pd.DataFrame, list[Fold]]:
    """Returns the instances with columns `fold` (−1 = never out-of-sample) and `p_oos`."""
    df = inst.copy()
    df["fold"] = -1
    df["p_oos"] = np.nan
    if df.empty:
        return df, []
    days = sorted(set(df["day"]))
    folds = make_folds(days, train_months, test_months, embargo_days)
    for f in folds:
        tr = df[df["day"] < f.train_end]
        te_m = (df["day"] >= f.test_start) & (df["day"] < f.test_end)
        if not te_m.any():
            continue
        df.loc[te_m, "fold"] = f.k
        df.loc[te_m, "p_oos"] = predict_bucket(tr, df[te_m], prior_strength, min_bucket_n)
    return df, folds
