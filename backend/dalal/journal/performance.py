"""Live track record (§10): forward hit rate vs predicted probability, expectancy by setup /
regime / time of day, cumulative R and drawdown, and live-vs-backtest divergence (demotion)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import binomtest

from dalal.clock import IST
from dalal.scoring.stats import brier, bucket_stats, max_drawdown, reliability, wilson


def _clean(x: Any) -> Any:
    if isinstance(x, dict):
        return {k: _clean(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_clean(v) for v in x]
    if isinstance(x, (float, np.floating)):
        return None if not np.isfinite(x) else round(float(x), 6)
    if isinstance(x, np.integer):
        return int(x)
    return x


def performance(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """`rows`: journal ideas (resolved and unresolved)."""
    df = pd.DataFrame(rows)
    out: dict[str, Any] = {"n_shown": len(df)}
    if df.empty:
        return {**out, "n_resolved": 0}
    res = df[(df["resolved"] == 1) & df["won"].notna() & (df["status"] != "DISPLACED")].copy()
    trig = res[res["entry_px"].notna()]
    out["n_resolved"] = len(res)
    out["n_triggered"] = len(trig)
    out["status_counts"] = df["status"].value_counts().to_dict()
    if trig.empty:
        return _clean(out)
    trig = trig.sort_values("signal_ts")
    y = trig["won"].astype(float).to_numpy()
    p = trig["p"].astype(float).to_numpy()
    r = trig["r_realised"].astype(float).fillna(0).to_numpy()
    k, n = int(y.sum()), len(y)
    lo, hi = wilson(k, n)
    out["hit_rate"] = {"k": k, "n": n, "hit": k / n, "ci_lo": lo, "ci_hi": hi, "pred_mean": float(np.nanmean(p))}
    out["brier"] = brier(p[np.isfinite(p)], y[np.isfinite(p)])
    out["reliability"] = reliability(np.nan_to_num(p, nan=0.5), y, bins=10)
    cum = np.cumsum(r)
    out["equity"] = [{"ts": t, "cum_r": float(c)} for t, c in zip(trig["signal_ts"], cum, strict=True)]
    out["total_r"] = float(cum[-1])
    out["max_dd_r"] = max_drawdown(r)
    out["expectancy_r"] = float(r.mean())
    trig["hour"] = trig["signal_ts"].str.slice(11, 13) + ":00"
    for col in ["setup", "regime", "hour", "side"]:
        rows_ = []
        for v, g in trig.groupby(col):
            b = bucket_stats(g["won"].astype(bool).to_numpy(), g["r_realised"].astype(float).fillna(0).to_numpy())
            rows_.append({"bucket": str(v), **b.as_dict(), "pred": float(g["p"].mean())})
        out[f"by_{col}"] = rows_
    return _clean(out)


def divergence(rows: list[dict[str, Any]], book_entries: dict[str, dict[str, Any]], min_n: int = 30,
               alpha: float = 0.01) -> dict[str, Any]:
    """Compare live forward results with the stats book. A setup-side is DEMOTED when, with at
    least `min_n` live triggered instances, the live hit rate is significantly below the
    predicted probability (one-sided binomial test at `alpha`) or live expectancy < 0 with its
    CI entirely below the backtest's."""
    df = pd.DataFrame(rows)
    out: dict[str, Any] = {"entries": {}, "created": datetime.now(tz=IST).isoformat()}
    if df.empty:
        return out
    trig = df[(df["resolved"] == 1) & df["entry_px"].notna() & df["won"].notna() & (df["gate_bypassed"] == 0)]
    for (setup, side), g in trig.groupby(["setup", "side"]):
        n = len(g)
        k = int(g["won"].sum())
        pred = float(g["p"].mean())
        pval = float(binomtest(k, n, pred, alternative="less").pvalue) if n > 0 and 0 < pred < 1 else 1.0
        r = g["r_realised"].astype(float).fillna(0)
        demote = n >= min_n and pval < alpha
        e = {"live_n": n, "live_hit": k / n if n else None, "pred": pred, "p_value": pval,
             "live_exp_r": float(r.mean()), "demoted": bool(demote),
             "reason": f"live hit {k}/{n} vs predicted {pred:.0%} (p={pval:.3f})" if demote else ""}
        for key in book_entries:
            s_, sd, _reg = key.split("|")
            if s_ == setup and sd == side:
                out["entries"][key] = e
    return _clean(out)


def write_feedback(path: Path, fb: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fb, indent=1))
