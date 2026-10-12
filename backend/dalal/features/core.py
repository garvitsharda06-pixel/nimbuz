"""Causal features on day panels.

Rules (enforced by tests/test_lookahead.py):
- Daily context for day d uses only days < d (fixed-length windows via sliding_window_view,
  so values do not depend on how much history is loaded).
- Intraday feature at bar t uses only bars ≤ t of day d and (for warm-ups) day d-1.
"""

from __future__ import annotations

import warnings
from typing import Any

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from dalal.features.panel import N1, N5, SymbolPanel

Features = dict[str, Any]

W_ATR, W_SIG, W_ADV, W_SLOT, W_BETA = 14, 20, 20, 20, 40
HISTORY_DAYS_NEEDED = 45      # live panels load this many prior days (≥ largest window)


def _quiet() -> warnings.catch_warnings:
    w = warnings.catch_warnings()
    w.__enter__()
    warnings.simplefilter("ignore", RuntimeWarning)
    return w


def prev_window(x: np.ndarray, w: int) -> np.ndarray:
    """(D, w) windows where row d = x[d-w : d] (strictly before d), NaN-padded at the start."""
    pad = np.full((w, *x.shape[1:]), np.nan)
    xp = np.concatenate([pad, x], axis=0)[:-1] if len(x) else pad[:0]
    if len(x) == 0:
        return np.zeros((0, w, *x.shape[1:]))
    v = sliding_window_view(xp, w, axis=0)          # (D, *rest, w)
    return v


def prev_stat(x: np.ndarray, w: int, fn: str, min_n: int | None = None) -> np.ndarray:
    v = prev_window(x, w)
    min_n = w // 2 if min_n is None else min_n
    with np.errstate(invalid="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        n = np.sum(~np.isnan(v), axis=-1)
        if fn == "mean":
            out = np.nanmean(v, axis=-1)
        elif fn == "median":
            out = np.nanmedian(v, axis=-1)
        elif fn == "std":
            out = np.nanstd(v, axis=-1, ddof=1)
        else:
            raise ValueError(fn)
    return np.where(n >= min_n, out, np.nan)


def shift1(x: np.ndarray) -> np.ndarray:
    out = np.full_like(x, np.nan, dtype=float)
    out[1:] = x[:-1]
    return out


def ema_rows(x: np.ndarray, alpha: float) -> np.ndarray:
    """EMA along axis 1 for each row independently; NaN inputs carry the previous value.
    Seeds with the first non-NaN value of the row."""
    out = np.full_like(x, np.nan, dtype=float)
    prev = np.full(x.shape[0], np.nan)
    for j in range(x.shape[1]):
        xj = x[:, j]
        nxt = np.where(np.isnan(prev), xj, alpha * xj + (1 - alpha) * prev)
        nxt = np.where(np.isnan(xj), prev, nxt)
        out[:, j] = nxt
        prev = nxt
    return out


def with_prev_day(x: np.ndarray) -> np.ndarray:
    """(D, n) → (D, 2n): [day d-1, day d] side by side (warm-up window for EMAs/ATRs)."""
    return np.concatenate([shift_rows(x), x], axis=1)


def shift_rows(x: np.ndarray) -> np.ndarray:
    out = np.full_like(x, np.nan, dtype=float)
    out[1:] = x[:-1]
    return out


def daily_context(p: SymbolPanel, index_dc: np.ndarray | None = None) -> Features:
    """Per-day scalars known BEFORE the session of day d."""
    w = _quiet()
    try:
        pdc, pdh, pdl = shift1(p.dc), shift1(p.dh), shift1(p.dl)
        pdo = shift1(p.do)
        ppdc = shift1(pdc)
        tr = np.fmax(p.dh - p.dl, np.fmax(np.abs(p.dh - shift1(p.dc)), np.abs(p.dl - shift1(p.dc))))
        atr14 = prev_stat(tr, W_ATR, "mean")
        dret = p.dc / shift1(p.dc) - 1.0
        sigma_d = prev_stat(dret, W_SIG, "std")
        adv20 = prev_stat(p.dv, W_ADV, "mean")
        med_tv20 = prev_stat(p.dtv, W_ADV, "median")
        slot_v5 = prev_stat(p.v5, W_SLOT, "mean")               # (D, 75)
        rv_slot = prev_stat(realized_vol(p), W_SLOT, "mean")    # (D, 75)
        beta = np.full(p.D, np.nan)
        ret20 = prev_window(dret, W_SIG)                        # (D, 20) for correlation
        if index_dc is not None:
            iret = index_dc / shift1(index_dc) - 1.0
            a = prev_window(dret, W_BETA)
            b = prev_window(iret, W_BETA)
            ok = ~(np.isnan(a) | np.isnan(b))
            n = ok.sum(axis=1)
            am = np.where(ok, a, 0).sum(1) / np.maximum(n, 1)
            bm = np.where(ok, b, 0).sum(1) / np.maximum(n, 1)
            cov = np.where(ok, (a - am[:, None]) * (b - bm[:, None]), 0).sum(1) / np.maximum(n - 1, 1)
            var = np.where(ok, (b - bm[:, None]) ** 2, 0).sum(1) / np.maximum(n - 1, 1)
            beta = np.where((n >= W_BETA // 2) & (var > 0), cov / np.where(var > 0, var, 1), np.nan)
    finally:
        w.__exit__(None, None, None)
    return {"pdc": pdc, "pdh": pdh, "pdl": pdl, "pdo": pdo, "ppdc": ppdc, "atr14": atr14, "sigma_d": sigma_d,
            "adv20": adv20, "med_tv20": med_tv20, "slot_v5": slot_v5, "rv_slot": rv_slot, "beta": beta, "ret20": ret20}


def slice_ctx(ctx: Features, rows: slice) -> Features:
    """Rows of a daily context (used by live to pair a 2-day panel with the full-history context)."""
    return {k: v[rows] for k, v in ctx.items()}


def realized_vol(p: SymbolPanel) -> np.ndarray:
    """(D, 75) cumulative realised vol of 1-minute log returns at each 5-min close."""
    with np.errstate(invalid="ignore", divide="ignore"):
        lr = np.diff(np.log(p.c1), axis=1, prepend=np.nan)
    lr[:, 0] = 0.0                                               # first minute: no intraday return yet
    return np.sqrt(np.nancumsum(lr**2, axis=1))[:, np.arange(4, N1, 5)]


def intraday(p: SymbolPanel, ctx: Features, is_index: bool = False) -> Features:
    """(D, 75) intraday features at each 5-min bar close."""
    w = _quiet()
    try:
        D = p.D
        o5, h5, l5, c5, v5 = p.o5, p.h5, p.l5, p.c5, p.v5
        # ---- VWAP and σ bands from 1-minute bars (TWAP for indices, which have no volume) ----
        tp1 = (p.h1 + p.l1 + p.c1) / 3.0
        v1 = np.nan_to_num(p.v1, nan=0.0)
        if is_index or np.nansum(v1) == 0:
            v1 = np.where(np.isnan(tp1), 0.0, 1.0)
        tp1z = np.nan_to_num(tp1)
        cv = np.cumsum(v1, axis=1)
        cpv = np.cumsum(v1 * tp1z, axis=1)
        cpv2 = np.cumsum(v1 * tp1z**2, axis=1)
        at = np.arange(4, N1, 5)                                 # last minute of each 5-min bar
        with np.errstate(divide="ignore", invalid="ignore"):
            vwap = (cpv / cv)[:, at]
            var = (cpv2 / cv)[:, at] - vwap**2
        vwap_sd = np.sqrt(np.clip(var, 0, None))
        visible = ~np.isnan(c5)
        vwap = np.where(visible, vwap, np.nan)
        vwap_sd = np.where(visible, vwap_sd, np.nan)
        # ---- warm-up frames [prev day | today] ----
        c2, h2, l2 = with_prev_day(c5), with_prev_day(h5), with_prev_day(l5)
        ema20 = ema_rows(c2, 2 / 21)[:, N5:]
        ema9 = ema_rows(c2, 2 / 10)[:, N5:]
        ema20_2 = ema_rows(c2, 2 / 21)
        ema_slope = ema20 - ema20_2[:, N5 - 3: 2 * N5 - 3]
        prev_c2 = np.concatenate([np.full((D, 1), np.nan), c2[:, :-1]], axis=1)
        tr5 = np.fmax(h2 - l2, np.fmax(np.abs(h2 - prev_c2), np.abs(l2 - prev_c2)))
        atr5 = ema_rows(tr5, 1 / 14)[:, N5:]
        swing_lo3 = np.fmin(np.fmin(l2[:, N5 - 2: 2 * N5 - 2], l2[:, N5 - 1: 2 * N5 - 1]), l5)
        swing_hi3 = np.fmax(np.fmax(h2[:, N5 - 2: 2 * N5 - 2], h2[:, N5 - 1: 2 * N5 - 1]), h5)
        # ---- 15-minute trend filter (last COMPLETED 15-min bar at each 5-min close) ----
        c15 = c2[:, 2::3]                                        # (D, 50): prev day 25 + today 25
        e15 = ema_rows(c15, 2 / 10)
        s15 = np.sign(c15 - e15) * (np.sign(e15 - np.concatenate([np.full((D, 1), np.nan), e15[:, :-1]], axis=1)) ==
                                    np.sign(c15 - e15))
        t_idx = np.arange(N5)
        k15 = 25 + (t_idx + 1) // 3 - 1                          # index into the 50-wide frame
        trend15 = s15[:, k15]
        # ---- opening range (first 15 minutes = bars 0..2), known from bar 2 onwards ----
        or_h = np.nanmax(h5[:, :3], axis=1)
        or_l = np.nanmin(l5[:, :3], axis=1)
        or_known = t_idx >= 2
        or_h_t = np.where(or_known[None, :], or_h[:, None], np.nan)
        or_l_t = np.where(or_known[None, :], or_l[:, None], np.nan)
        # ---- running day extremes ----
        dhi = np.fmax.accumulate(np.where(np.isnan(h5), -np.inf, h5), axis=1)
        dlo = np.fmin.accumulate(np.where(np.isnan(l5), np.inf, l5), axis=1)
        dhi = np.where(visible, dhi, np.nan)
        dlo = np.where(visible, dlo, np.nan)
        prior_hi = np.concatenate([np.full((D, 1), np.nan), dhi[:, :-1]], axis=1)
        prior_lo = np.concatenate([np.full((D, 1), np.nan), dlo[:, :-1]], axis=1)
        # ---- volume ----
        slot = ctx["slot_v5"]
        cum_v = np.nancumsum(v5, axis=1)
        cum_slot = np.nancumsum(slot, axis=1)
        rvol_cum = np.where(visible, cum_v / cum_slot, np.nan)
        rvol_bar = np.where(visible, v5 / slot, np.nan)
        prev_v = np.concatenate([np.full((D, 1), np.nan), v5[:, :-1]], axis=1)
        vol_rising = v5 > prev_v
        # ---- returns, gaps ----
        pdc = ctx["pdc"][:, None]
        day_open = o5[:, :1]
        ret_pdc = c5 / pdc - 1.0
        ret_open = c5 / day_open - 1.0
        gap = (o5[:, 0] / ctx["pdc"] - 1.0)
        gap_atr = (o5[:, 0] - ctx["pdc"]) / ctx["atr14"]
        rv = np.where(visible, realized_vol(p), np.nan)
        rv_ratio = rv / ctx["rv_slot"]
    finally:
        w.__exit__(None, None, None)
    return {
        "o": o5, "h": h5, "l": l5, "c": c5, "v": v5, "visible": visible,
        "vwap": vwap, "vwap_sd": vwap_sd, "ema20": ema20, "ema9": ema9, "ema_slope": ema_slope,
        "atr5": atr5, "swing_lo3": swing_lo3, "swing_hi3": swing_hi3, "trend15": trend15,
        "or_h": or_h_t, "or_l": or_l_t, "day_hi": dhi, "day_lo": dlo, "prior_hi": prior_hi, "prior_lo": prior_lo,
        "rvol_cum": rvol_cum, "rvol_bar": rvol_bar, "vol_rising": vol_rising,
        "ret_pdc": ret_pdc, "ret_open": ret_open, "gap": gap, "gap_atr": gap_atr,
        "rv": rv, "rv_ratio": rv_ratio,
    }


def relative_strength(F: Features, ctx: Features, idx_ret_pdc: np.ndarray, sec_ret_pdc: np.ndarray | None) -> None:
    """RS vs Nifty (beta-adjusted) and vs sector, in σ units of the stock's daily vol scaled
    to elapsed session time. Adds 'rs_mkt_z' and 'rs_sec_z' to F (in place)."""
    w = _quiet()
    try:
        frac = np.sqrt((np.arange(N5) + 1) / N5)[None, :]
        sig = ctx["sigma_d"][:, None] * frac
        beta = np.where(np.isnan(ctx["beta"]), 1.0, ctx["beta"])[:, None]
        F["rs_mkt_z"] = (F["ret_pdc"] - beta * idx_ret_pdc) / sig
        F["rs_sec_z"] = (F["ret_pdc"] - sec_ret_pdc) / sig if sec_ret_pdc is not None else np.full_like(F["c"], np.nan)
    finally:
        w.__exit__(None, None, None)


def oi_buildup(price_chg: float, oi_chg: float, thresh: float = 0.0) -> str:
    """Classic futures positioning read (§8.2): price × OI change."""
    if np.isnan(price_chg) or np.isnan(oi_chg) or abs(oi_chg) <= thresh:
        return "—"
    if price_chg > 0 and oi_chg > 0:
        return "Long build-up"
    if price_chg < 0 and oi_chg > 0:
        return "Short build-up"
    if price_chg > 0 and oi_chg < 0:
        return "Short covering"
    return "Long unwinding"
