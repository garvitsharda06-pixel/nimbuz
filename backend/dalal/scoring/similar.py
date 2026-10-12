"""The 5 most similar past OOS instances of the same setup/side (drawer, §11)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

SIM_FEATURES = ["rvol_cum", "rs_mkt_z", "dist_vwap_atr", "gap_atr", "atr5_pct", "breadth", "nifty_ret", "t", "rr1",
                "sector_rank"]


class SimilarIndex:
    def __init__(self, path: Path):
        self.path = path
        self._df: pd.DataFrame | None = None
        self._mtime = 0.0

    def df(self) -> pd.DataFrame:
        if not self.path.exists():
            return pd.DataFrame()
        mt = self.path.stat().st_mtime
        if self._df is None or mt != self._mtime:
            cols = ["day", "signal_time", "symbol", "setup", "side", "regime_name", "win", "r_net", "exit_reason",
                    "fold", *SIM_FEATURES]
            df = pd.read_parquet(self.path)
            self._df = df[[c for c in cols if c in df.columns]]
            self._df = self._df[self._df["fold"] >= 0]
            self._mtime = mt
        return self._df

    def nearest(self, setup: str, side: str, snap: dict[str, Any], t: int, rr1: float, k: int = 5,
                before_day: str | None = None) -> list[dict[str, Any]]:
        """Nearest OOS instances, strictly BEFORE `before_day` (no look-ahead when replaying history)."""
        df = self.df()
        if df.empty:
            return []
        g = df[(df["setup"] == setup) & (df["side"] == side)]
        if before_day is not None:
            g = g[g["day"].astype(str) < before_day]
        if g.empty:
            return []
        q = {**snap, "t": t, "rr1": rr1}
        feats = [f for f in SIM_FEATURES if f in g.columns and isinstance(q.get(f), (int, float)) and q.get(f) is not None
                 and np.isfinite(q[f])]
        if not feats:
            return []
        X = g[feats].to_numpy(float)
        mu, sd = np.nanmean(X, axis=0), np.nanstd(X, axis=0)
        sd[sd == 0] = 1
        Z = (X - mu) / sd
        zq = (np.array([q[f] for f in feats], float) - mu) / sd
        dist = np.sqrt(np.nansum((Z - zq) ** 2, axis=1))
        idx = np.argsort(dist)[:k]
        out = []
        for i in idx:
            r = g.iloc[i]
            out.append({"day": str(r["day"]), "time": r["signal_time"], "symbol": r["symbol"], "regime": r["regime_name"],
                        "win": bool(r["win"]), "r_net": round(float(r["r_net"]), 3), "exit": r["exit_reason"],
                        "distance": round(float(dist[i]), 2)})
        return out
