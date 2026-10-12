"""Day panels: one symbol's history as (days × minutes) arrays.

Every research and live computation runs on these arrays, so the backtester and the
live engine share exactly the same code path (DECISIONS D-005). Live builds a short
panel (≈25 prior days + today so far, future minutes NaN); research builds the full
history. All features are causal and use fixed-length windows, so the values for a
given (day, bar) are identical in both cases.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from dalal.clock import IST

N1 = 375      # 1-minute bars per regular session (09:15 → 15:30)
N5 = 75       # 5-minute bars per session
OPEN_MIN = 9 * 60 + 15


@dataclass
class SymbolPanel:
    key: str
    days: np.ndarray          # (D,) python dates (object array)
    o1: np.ndarray            # (D, 375) 1-minute OHLCV
    h1: np.ndarray
    l1: np.ndarray
    c1: np.ndarray
    v1: np.ndarray
    oi1: np.ndarray
    o5: np.ndarray            # (D, 75) 5-minute OHLCV (complete buckets only; forming bucket NaN)
    h5: np.ndarray
    l5: np.ndarray
    c5: np.ndarray
    v5: np.ndarray
    # daily (complete days only; today's row is NaN until the session ends)
    do: np.ndarray
    dh: np.ndarray
    dl: np.ndarray
    dc: np.ndarray
    dv: np.ndarray
    dtv: np.ndarray           # traded value ₹ = Σ v × (h+l+c)/3 over 1-minute bars

    @property
    def D(self) -> int:
        return len(self.days)

    def day_index(self) -> dict[date, int]:
        return {d: i for i, d in enumerate(self.days)}


def _empty(D: int, n: int) -> np.ndarray:
    return np.full((D, n), np.nan)


def panel_from_1m(key: str, df: pd.DataFrame, partial_last_day_minutes: int | None = None) -> SymbolPanel:
    """Build a panel from 1m bars. If `partial_last_day_minutes` is given, the last day is
    treated as in-progress: only its first that-many minutes are visible, later ones NaN,
    and its daily row is NaN (not yet known)."""
    if df.empty:
        z = np.zeros((0, N1))
        return panel_from_arrays(key, np.array([], dtype=object), z, z, z, z, z, z)
    t = df["ts"].dt.tz_convert(IST)
    day = t.dt.date.to_numpy()
    minute = ((t.dt.hour * 60 + t.dt.minute) - OPEN_MIN).to_numpy()
    days = np.array(sorted(set(day)), dtype=object)
    di = {d: i for i, d in enumerate(days)}
    rows = np.array([di[d] for d in day])
    ok = (minute >= 0) & (minute < N1)
    D = len(days)
    arrs = {}
    for name in ["open", "high", "low", "close", "volume", "oi"]:
        a = _empty(D, N1)
        a[rows[ok], minute[ok]] = df[name].to_numpy(dtype=float)[ok]
        arrs[name] = a
    return panel_from_arrays(key, days, arrs["open"], arrs["high"], arrs["low"], arrs["close"], arrs["volume"],
                             arrs["oi"], partial_last_day_minutes)


def panel_from_arrays(key: str, days: np.ndarray, o1: np.ndarray, h1: np.ndarray, l1: np.ndarray, c1: np.ndarray,
                      v1: np.ndarray, oi1: np.ndarray, partial_last_day_minutes: int | None = None,
                      visible: np.ndarray | None = None) -> SymbolPanel:
    """Panel from (D, 375) minute arrays (copied). Used directly by the live engine.

    `visible` (optional, per row): number of visible minutes in each row, for stacked
    multi-symbol panels where several rows are "today" (in-progress sessions)."""
    o1, h1, l1, c1, v1, oi1 = (np.array(a, dtype=float, copy=True) for a in (o1, h1, l1, c1, v1, oi1))
    D = len(days)
    if D == 0:
        z5 = np.zeros((0, N5))
        z = np.zeros(0)
        return SymbolPanel(key, days, o1, h1, l1, c1, v1, oi1, z5, z5, z5, z5, z5, z, z, z, z, z, z)
    if visible is None:
        visible = np.full(D, N1)
        if partial_last_day_minutes is not None:
            visible[-1] = partial_last_day_minutes
    cut = np.arange(N1)[None, :] >= visible[:, None]
    for a in (o1, h1, l1, c1, v1, oi1):
        a[cut] = np.nan
    # fill missing minutes inside the visible part of each day: carry close, zero volume
    _fill_missing(o1, h1, l1, c1, v1, visible)
    o5, h5, l5, c5, v5 = agg5(o1, h1, l1, c1, v1)
    tp = (h1 + l1 + c1) / 3.0
    with np.errstate(invalid="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        complete = ~np.isnan(c1[:, -1])
        do = np.where(complete, o1[:, 0], np.nan)
        dh = np.where(complete, np.nanmax(h1, axis=1), np.nan)
        dl = np.where(complete, np.nanmin(l1, axis=1), np.nan)
        dc = np.where(complete, c1[:, -1], np.nan)
        dv = np.where(complete, np.nansum(v1, axis=1), np.nan)
        dtv = np.where(complete, np.nansum(v1 * tp, axis=1), np.nan)
    return SymbolPanel(key, days, o1, h1, l1, c1, v1, oi1, o5, h5, l5, c5, v5, do, dh, dl, dc, dv, dtv)


def append_empty_day(p: SymbolPanel, day: date) -> SymbolPanel:
    """Add an all-NaN row for `day` (a session that has not produced any bars yet)."""
    def r1(a: np.ndarray, n: int) -> np.ndarray:
        return np.concatenate([a, np.full((1, n), np.nan)], axis=0)

    def r0(a: np.ndarray) -> np.ndarray:
        return np.concatenate([a, [np.nan]])

    return SymbolPanel(p.key, np.array([*p.days, day], dtype=object), r1(p.o1, N1), r1(p.h1, N1), r1(p.l1, N1),
                       r1(p.c1, N1), r1(p.v1, N1), r1(p.oi1, N1), r1(p.o5, N5), r1(p.h5, N5), r1(p.l5, N5),
                       r1(p.c5, N5), r1(p.v5, N5), r0(p.do), r0(p.dh), r0(p.dl), r0(p.dc), r0(p.dv), r0(p.dtv))


def _fill_missing(o1, h1, l1, c1, v1, visible: np.ndarray) -> None:
    rows = np.where(np.isnan(c1) & (np.arange(N1)[None, :] < visible[:, None]))[0]
    for d in np.unique(rows):
        lim = int(visible[d])
        if lim <= 0:
            continue
        row = c1[d, :lim]
        miss = np.isnan(row)
        if not miss.any():
            continue
        if miss.all():
            continue                     # whole day missing (halt / no data): leave NaN
        # forward-fill close; leading gaps back-filled from first available open
        idx = np.where(~miss, np.arange(lim), 0)
        np.maximum.accumulate(idx, out=idx)
        first = int(np.argmax(~miss))
        filled = row[idx]
        filled[:first] = o1[d, first]
        c1[d, :lim] = filled
        for a in (o1, h1, l1):
            a[d, :lim] = np.where(np.isnan(a[d, :lim]), filled, a[d, :lim])
        v1[d, :lim] = np.where(np.isnan(v1[d, :lim]), 0.0, v1[d, :lim])


def agg5(o1, h1, l1, c1, v1):
    D = o1.shape[0]
    r = lambda a: a.reshape(D, N5, 5)  # noqa: E731
    o5 = r(o1)[:, :, 0]
    c5 = r(c1)[:, :, 4]                    # NaN if the bucket's last minute isn't there yet → forming bucket hidden
    with np.errstate(invalid="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        h5 = np.nanmax(r(h1), axis=2)
        l5 = np.nanmin(r(l1), axis=2)
        v5 = np.nansum(r(v1), axis=2)
    incomplete = np.isnan(c5)
    for a in (o5, h5, l5, v5):
        a[incomplete] = np.nan
    return o5.copy(), h5, l5, c5.copy(), v5


def minute_of(ts: pd.Timestamp) -> int:
    t = ts.tz_convert(IST)
    return t.hour * 60 + t.minute - OPEN_MIN


def bar5_close_minute(t: int) -> int:
    """Minute index (from 09:15) at which 5-min bar t closes (i.e. first minute after it)."""
    return 5 * (t + 1)


def bar5_close_hhmm(t: int) -> str:
    m = OPEN_MIN + bar5_close_minute(t)
    return f"{m // 60:02d}:{m % 60:02d}"


def hhmm_to_bar5_last(hhmm: str) -> int:
    """Last 5-min bar index whose close time is ≤ hh:mm."""
    h, m = map(int, hhmm.split(":"))
    return (h * 60 + m - OPEN_MIN) // 5 - 1


def hhmm_to_minute(hhmm: str) -> int:
    h, m = map(int, hhmm.split(":"))
    return h * 60 + m - OPEN_MIN
