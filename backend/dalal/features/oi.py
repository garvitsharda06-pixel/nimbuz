"""Futures positioning (§8.2): price change × open-interest change → build-up classification.

Computed at each 5-minute close from near-month futures 1-minute bars, causally: the futures
close and the latest OI print at bar t vs the previous session's final values.
"""

from __future__ import annotations

import warnings

import numpy as np

from dalal.features.panel import N1

NEUTRAL, LONG_BUILDUP, SHORT_BUILDUP, SHORT_COVERING, LONG_UNWINDING = 0, 1, 2, 3, 4
OI_TEXT = {NEUTRAL: "—", LONG_BUILDUP: "Long build-up", SHORT_BUILDUP: "Short build-up",
           SHORT_COVERING: "Short covering", LONG_UNWINDING: "Long unwinding"}


def _ffill_rows(a: np.ndarray) -> np.ndarray:
    fin = np.isfinite(a)
    idx = np.where(fin, np.arange(a.shape[1])[None, :], 0)
    np.maximum.accumulate(idx, axis=1, out=idx)
    out = a[np.arange(a.shape[0])[:, None], idx]
    out[~np.logical_or.accumulate(fin, axis=1)] = np.nan
    return out


def oi_frame(c1: np.ndarray, oi1: np.ndarray, min_oi_chg: float = 0.01, min_px_chg: float = 0.001) -> dict[str, np.ndarray]:
    """(D, 375) futures close and OI → (D, 75) fut_ret, oi_chg, oi_state. Row d uses row d-1 as
    the previous session (rows must be consecutive sessions of one contract series)."""
    with np.errstate(invalid="ignore", divide="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        c = _ffill_rows(np.asarray(c1, float))
        oi = _ffill_rows(np.asarray(oi1, float))
        at = np.arange(4, N1, 5)
        prev_c = np.full(c.shape[0], np.nan)
        prev_oi = np.full(c.shape[0], np.nan)
        prev_c[1:] = c[:-1, -1]
        prev_oi[1:] = oi[:-1, -1]
        fut_ret = c[:, at] / prev_c[:, None] - 1.0
        oi_chg = oi[:, at] / prev_oi[:, None] - 1.0
        visible = np.isfinite(np.asarray(c1, float)[:, at])
        fut_ret = np.where(visible, fut_ret, np.nan)
        oi_chg = np.where(visible, oi_chg, np.nan)
        up, dn = fut_ret > min_px_chg, fut_ret < -min_px_chg
        oi_up, oi_dn = oi_chg > min_oi_chg, oi_chg < -min_oi_chg
        state = np.select([up & oi_up, dn & oi_up, up & oi_dn, dn & oi_dn],
                          [LONG_BUILDUP, SHORT_BUILDUP, SHORT_COVERING, LONG_UNWINDING], NEUTRAL)
    return {"fut_ret": fut_ret, "oi_chg": oi_chg, "oi_state": state.astype(int)}
