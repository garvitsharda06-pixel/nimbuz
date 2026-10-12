"""Candle aggregation (1m → N-min, anchored at the session open) and a tick → 1m bar builder."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta

import numpy as np
import pandas as pd

from dalal.clock import IST
from dalal.data.models import Bar, Tick


def session_minute(ts: pd.Series, open_t: time = time(9, 15)) -> np.ndarray:
    """Minutes since session open for each (IST) timestamp."""
    t = ts.dt.tz_convert(IST)
    return ((t.dt.hour - open_t.hour) * 60 + (t.dt.minute - open_t.minute)).to_numpy()


def resample(df1m: pd.DataFrame, minutes: int, open_t: time = time(9, 15), complete_only: bool = True) -> pd.DataFrame:
    """Aggregate 1m bars into `minutes` bars anchored at the session open.

    `ts` of each output bar is its start. With `complete_only`, a trailing bucket that
    does not yet contain its last minute is dropped (never trade on a forming candle).
    """
    if df1m.empty:
        return df1m.iloc[0:0].assign(n=pd.Series(dtype="int64"))
    df = df1m.sort_values("ts")
    day = df["ts"].dt.tz_convert(IST).dt.normalize()
    m = session_minute(df["ts"], open_t)
    bucket = m // minutes
    g = df.assign(_day=day, _b=bucket, _m=m).groupby(["_day", "_b"], sort=True)
    out = g.agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
        oi=("oi", "last"),
        n=("open", "size"),
        last_m=("_m", "max"),
    ).reset_index()
    out["ts"] = out["_day"] + pd.to_timedelta(open_t.hour * 60 + open_t.minute + out["_b"] * minutes, unit="m")
    if complete_only:
        # a bucket is complete when its final minute is present (or a later bucket exists that day)
        last_minute_of_bucket = out["_b"] * minutes + minutes - 1
        later_exists = out.groupby("_day")["_b"].transform("max") > out["_b"]
        out = out[(out["last_m"] >= last_minute_of_bucket) | later_exists]
    return out[["ts", "open", "high", "low", "close", "volume", "oi", "n"]].reset_index(drop=True)


@dataclass
class _Building:
    start: datetime
    open: float
    high: float
    low: float
    close: float
    vol_start: float | None
    vol_last: float | None
    oi: float | None


class BarBuilder:
    """Builds 1-minute bars per instrument from ticks.

    Volume uses the cumulative day volume (vtt) delta, which is robust to dropped ticks.
    `close_until(ts)` closes every bar whose minute ended at or before `ts`.
    """

    def __init__(self) -> None:
        self._cur: dict[str, _Building] = {}
        self._last_vol: dict[str, float] = {}
        self._last_close: dict[str, float] = {}

    def on_tick(self, t: Tick) -> list[Bar]:
        """Feed a tick; returns any bars closed because this tick belongs to a later minute."""
        closed: list[Bar] = []
        start = t.ts.astimezone(IST).replace(second=0, microsecond=0)
        cur = self._cur.get(t.key)
        if cur is not None and start > cur.start:
            closed.append(self._finish(t.key, cur))
            cur = None
        if cur is None:
            vs = self._last_vol.get(t.key, t.volume_cum)
            cur = _Building(start, t.ltp, t.ltp, t.ltp, t.ltp, vs, t.volume_cum, t.oi)
            self._cur[t.key] = cur
        elif start < cur.start:
            return closed  # late tick for an already-closed minute: drop
        cur.high = max(cur.high, t.ltp)
        cur.low = min(cur.low, t.ltp)
        cur.close = t.ltp
        if t.volume_cum is not None:
            cur.vol_last = t.volume_cum
        if t.oi is not None:
            cur.oi = t.oi
        return closed

    def close_until(self, ts: datetime) -> list[Bar]:
        out = []
        for key, cur in list(self._cur.items()):
            if cur.start + timedelta(minutes=1) <= ts:
                out.append(self._finish(key, cur))
        return out

    def _finish(self, key: str, cur: _Building) -> Bar:
        vol = 0.0
        if cur.vol_last is not None and cur.vol_start is not None:
            vol = max(0.0, cur.vol_last - cur.vol_start)
        if cur.vol_last is not None:
            self._last_vol[key] = cur.vol_last
        self._last_close[key] = cur.close
        self._cur.pop(key, None)
        return Bar(key, cur.start, cur.open, cur.high, cur.low, cur.close, vol, cur.oi)
