"""Setup library (§8.2). Parameters here are fixed a priori. Sensitivity grids in
backtest/robustness.py count every variant tried (multiple-testing haircut)."""

from __future__ import annotations

from typing import Any, ClassVar

import numpy as np

from dalal.regime.classifier import R_DN, R_RANGE, R_UP
from dalal.setups.base import LONG, SHORT, Setup, SetupContext, floor_stop, prev


class ORB(Setup):
    name: ClassVar[str] = "orb"
    label: ClassVar[str] = "Opening-range breakout"
    regimes: ClassVar[dict[str, set[int]]] = {LONG: {R_UP, R_RANGE}, SHORT: {R_DN, R_RANGE}}
    defaults: ClassVar[dict[str, Any]] = {"rvol_min": 1.2, "last_time": "11:30", "stop_atr": 0.5, "t1_r": 1.0, "t2_r": 2.0}

    def raw(self, s: SetupContext):
        F, tk = s.F, s.tick
        t = np.arange(F["c"].shape[1])[None, :]
        base = (t >= 3) & (F["rvol_cum"] >= self.p("rvol_min"))
        lm = base & (F["c"] > F["or_h"]) & (prev(F["c"]) <= F["or_h"]) & (F["c"] > F["vwap"])
        sm = base & (F["c"] < F["or_l"]) & (prev(F["c"]) >= F["or_l"]) & (F["c"] < F["vwap"])
        lt, st = F["h"] + tk, F["l"] - tk
        ls = floor_stop(F["l"] - tk, lt, F["atr5"], LONG, self.p("stop_atr"))
        ss = floor_stop(F["h"] + tk, st, F["atr5"], SHORT, self.p("stop_atr"))
        return {LONG: (lm, lt, ls, None, None), SHORT: (sm, st, ss, None, None)}

    def invalidation(self, side: str, stop: float, extra: dict[str, float]) -> str:
        lvl = extra.get("or_h" if side == LONG else "or_l")
        back = "below" if side == LONG else "above"
        return f"Stop {stop:.2f}. Thesis fails on a 5-min close back {back} the opening range ({lvl:.2f})." if lvl else \
            f"Stop {stop:.2f}."


class VWAPReclaim(Setup):
    name: ClassVar[str] = "vwap_reclaim"
    label: ClassVar[str] = "VWAP reclaim / rejection"
    regimes: ClassVar[dict[str, set[int]]] = {LONG: {R_UP, R_RANGE}, SHORT: {R_DN, R_RANGE}}
    defaults: ClassVar[dict[str, Any]] = {"rvol_bar_min": 1.0, "stop_atr": 0.5, "t1_r": 1.0, "t2_r": 2.0}

    def raw(self, s: SetupContext):
        F, tk = s.F, s.tick
        t = np.arange(F["c"].shape[1])[None, :]
        base = (t >= 3) & (F["rvol_bar"] >= self.p("rvol_bar_min"))
        lm = base & (prev(F["c"]) < prev(F["vwap"])) & (F["c"] > F["vwap"]) & s.idx_above_vwap & (s.idx_ret > 0)
        sm = base & (prev(F["c"]) > prev(F["vwap"])) & (F["c"] < F["vwap"]) & ~s.idx_above_vwap & (s.idx_ret < 0)
        lt, st = F["h"] + tk, F["l"] - tk
        ls = floor_stop(F["swing_lo3"] - tk, lt, F["atr5"], LONG, self.p("stop_atr"))
        ss = floor_stop(F["swing_hi3"] + tk, st, F["atr5"], SHORT, self.p("stop_atr"))
        return {LONG: (lm, lt, ls, None, None), SHORT: (sm, st, ss, None, None)}

    def invalidation(self, side: str, stop: float, extra: dict[str, float]) -> str:
        v = extra.get("vwap", float("nan"))
        return f"Stop {stop:.2f}. Thesis fails on a 5-min close back {'below' if side == LONG else 'above'} VWAP ({v:.2f})."


class TrendPullback(Setup):
    name: ClassVar[str] = "trend_pullback"
    label: ClassVar[str] = "Trend pullback to 20-EMA/VWAP"
    regimes: ClassVar[dict[str, set[int]]] = {LONG: {R_UP}, SHORT: {R_DN}}
    defaults: ClassVar[dict[str, Any]] = {"touch_pct": 0.001, "stop_atr": 0.5, "t1_r": 1.0, "t2_r": 2.0}

    def raw(self, s: SetupContext):
        F, tk = s.F, s.tick
        t = np.arange(F["c"].shape[1])[None, :]
        tp = self.p("touch_pct")
        upper = np.fmax(F["ema20"], F["vwap"])
        lower = np.fmin(F["ema20"], F["vwap"])
        lm = ((t >= 3) & (F["c"] > F["ema20"]) & (F["ema_slope"] > 0) & (F["trend15"] == 1)
              & (F["l"] <= upper * (1 + tp)) & (F["c"] > F["o"]) & F["vol_rising"])
        sm = ((t >= 3) & (F["c"] < F["ema20"]) & (F["ema_slope"] < 0) & (F["trend15"] == -1)
              & (F["h"] >= lower * (1 - tp)) & (F["c"] < F["o"]) & F["vol_rising"])
        lt, st = F["h"] + tk, F["l"] - tk
        ls = floor_stop(F["swing_lo3"] - tk, lt, F["atr5"], LONG, self.p("stop_atr"))
        ss = floor_stop(F["swing_hi3"] + tk, st, F["atr5"], SHORT, self.p("stop_atr"))
        return {LONG: (lm, lt, ls, None, None), SHORT: (sm, st, ss, None, None)}

    def invalidation(self, side: str, stop: float, extra: dict[str, float]) -> str:
        e = extra.get("ema20", float("nan"))
        return f"Stop {stop:.2f}. Trend thesis fails on a 5-min close {'below' if side == LONG else 'above'} the 20-EMA ({e:.2f})."


class PDHLRetest(Setup):
    name: ClassVar[str] = "pdhl_retest"
    label: ClassVar[str] = "Prev-day high/low break & retest"
    regimes: ClassVar[dict[str, set[int]]] = {LONG: {R_UP, R_RANGE}, SHORT: {R_DN, R_RANGE}}
    defaults: ClassVar[dict[str, Any]] = {"retest_pct": 0.0015, "stop_atr": 0.5, "t1_r": 1.0, "t2_r": 2.0}

    def raw(self, s: SetupContext):
        F, ctx, tk = s.F, s.ctx, s.tick
        pdh, pdl = ctx["pdh"][:, None], ctx["pdl"][:, None]
        t = np.arange(F["c"].shape[1])[None, :]
        broke_up = np.maximum.accumulate(np.nan_to_num(prev(F["c"]) > pdh, nan=0).astype(bool), axis=1)
        broke_dn = np.maximum.accumulate(np.nan_to_num(prev(F["c"]) < pdl, nan=0).astype(bool), axis=1)
        rp = self.p("retest_pct")
        lm = (t >= 2) & broke_up & (F["l"] <= pdh * (1 + rp)) & (F["c"] > pdh) & (F["c"] > F["o"])
        sm = (t >= 2) & broke_dn & (F["h"] >= pdl * (1 - rp)) & (F["c"] < pdl) & (F["c"] < F["o"])
        lt, st = F["h"] + tk, F["l"] - tk
        ls = floor_stop(F["l"] - tk, lt, F["atr5"], LONG, self.p("stop_atr"))
        ss = floor_stop(F["h"] + tk, st, F["atr5"], SHORT, self.p("stop_atr"))
        return {LONG: (lm, lt, ls, None, None), SHORT: (sm, st, ss, None, None)}

    def invalidation(self, side: str, stop: float, extra: dict[str, float]) -> str:
        lvl = extra.get("pdh" if side == LONG else "pdl", float("nan"))
        return f"Stop {stop:.2f}. Retest fails on a 5-min close back {'below PDH' if side == LONG else 'above PDL'} ({lvl:.2f})."


class GapGo(Setup):
    name: ClassVar[str] = "gap_go"
    label: ClassVar[str] = "Gap-and-go"
    defaults: ClassVar[dict[str, Any]] = {"gap_atr_min": 0.5, "rvol_min": 1.5, "last_time": "10:15", "t1_r": 1.0, "t2_r": 2.0}

    def raw(self, s: SetupContext):
        F, tk = s.F, s.tick
        t = np.arange(F["c"].shape[1])[None, :]
        g = F["gap_atr"][:, None]
        day_open = F["o"][:, :1]
        base = (t >= 2) & (F["rvol_cum"] >= self.p("rvol_min")) & (prev(F["c"]) <= F["or_h"]) & (prev(F["c"]) >= F["or_l"])
        lm = base & (g >= self.p("gap_atr_min")) & (F["c"] > day_open) & (F["c"] > F["vwap"]) & (F["c"] > F["or_h"])
        sm = base & (g <= -self.p("gap_atr_min")) & (F["c"] < day_open) & (F["c"] < F["vwap"]) & (F["c"] < F["or_l"])
        lt, st = F["h"] + tk, F["l"] - tk
        ls = floor_stop(F["or_l"] - tk, lt, F["atr5"], LONG)
        ss = floor_stop(F["or_h"] + tk, st, F["atr5"], SHORT)
        return {LONG: (lm, lt, ls, None, None), SHORT: (sm, st, ss, None, None)}


class GapFade(Setup):
    name: ClassVar[str] = "gap_fade"
    label: ClassVar[str] = "Gap-fade"
    defaults: ClassVar[dict[str, Any]] = {"gap_atr_min": 0.5, "last_time": "10:30", "t1_r": 1.0, "t2_r": 2.0}

    def raw(self, s: SetupContext):
        F, tk = s.F, s.tick
        t = np.arange(F["c"].shape[1])[None, :]
        g = F["gap_atr"][:, None]
        day_open = F["o"][:, :1]
        # gap DOWN that fails to follow through → long; gap UP that fails → short
        lm = (t >= 2) & (g <= -self.p("gap_atr_min")) & (F["c"] > day_open) & (F["c"] > F["vwap"]) & (prev(F["c"]) <= prev(F["vwap"]))
        sm = (t >= 2) & (g >= self.p("gap_atr_min")) & (F["c"] < day_open) & (F["c"] < F["vwap"]) & (prev(F["c"]) >= prev(F["vwap"]))
        lt, st = F["h"] + tk, F["l"] - tk
        ls = floor_stop(F["day_lo"] - tk, lt, F["atr5"], LONG)
        ss = floor_stop(F["day_hi"] + tk, st, F["atr5"], SHORT)
        return {LONG: (lm, lt, ls, None, None), SHORT: (sm, st, ss, None, None)}


class RelStrength(Setup):
    name: ClassVar[str] = "rel_strength"
    label: ClassVar[str] = "Relative-strength leader / laggard"
    regimes: ClassVar[dict[str, set[int]]] = {LONG: {R_UP}, SHORT: {R_DN}}
    defaults: ClassVar[dict[str, Any]] = {"rs_z": 1.5, "top_sectors": 3, "first_bar": 8, "last_time": "14:00",
                                          "t1_r": 1.0, "t2_r": 2.0}

    def raw(self, s: SetupContext):
        F, tk = s.F, s.tick
        k = self.p("top_sectors")
        lm = (F["rs_mkt_z"] >= self.p("rs_z")) & (s.sector_rank <= k) & (F["c"] > F["vwap"]) & (F["c"] > F["prior_hi"])
        sm = (F["rs_mkt_z"] <= -self.p("rs_z")) & (s.sector_rank > s.n_sectors - k) & (F["c"] < F["vwap"]) & (F["c"] < F["prior_lo"])
        lt, st = F["h"] + tk, F["l"] - tk
        ls = floor_stop(F["swing_lo3"] - tk, lt, F["atr5"], LONG)
        ss = floor_stop(F["swing_hi3"] + tk, st, F["atr5"], SHORT)
        return {LONG: (lm, lt, ls, None, None), SHORT: (sm, st, ss, None, None)}


class VWAPMeanRevert(Setup):
    name: ClassVar[str] = "vwap_mr"
    label: ClassVar[str] = "VWAP ±2σ mean reversion"
    regimes: ClassVar[dict[str, set[int]]] = {LONG: {R_RANGE}, SHORT: {R_RANGE}}
    defaults: ClassVar[dict[str, Any]] = {"k_sigma": 2.0, "first_bar": 6, "min_rr": 0.8}

    def raw(self, s: SetupContext):
        F, tk = s.F, s.tick
        k = self.p("k_sigma")
        lo_band, hi_band = F["vwap"] - k * F["vwap_sd"], F["vwap"] + k * F["vwap_sd"]
        lm = (prev(F["c"]) < prev(lo_band)) & (F["c"] > prev(F["c"])) & (F["c"] > F["o"])
        sm = (prev(F["c"]) > prev(hi_band)) & (F["c"] < prev(F["c"])) & (F["c"] < F["o"])
        lt, st = F["h"] + tk, F["l"] - tk
        ls = floor_stop(np.fmin(F["l"], F["day_lo"]) - tk, lt, F["atr5"], LONG)
        ss = floor_stop(np.fmax(F["h"], F["day_hi"]) + tk, st, F["atr5"], SHORT)
        lt1, lt2 = F["vwap"] - F["vwap_sd"], F["vwap"]
        st1, st2 = F["vwap"] + F["vwap_sd"], F["vwap"]
        return {LONG: (lm, lt, ls, lt1, lt2), SHORT: (sm, st, ss, st1, st2)}

    def invalidation(self, side: str, stop: float, extra: dict[str, float]) -> str:
        return f"Stop {stop:.2f} beyond the day's extreme. Range thesis fails if the regime turns to trend."


class OIConfirm(Setup):
    """ORB confirmed by futures positioning (long build-up for longs, short build-up for shorts).
    Disabled until enough OI history exists (N ≥ 200 OOS) — see DECISIONS."""

    name: ClassVar[str] = "oi_confirm"
    label: ClassVar[str] = "ORB + F&O positioning"
    regimes: ClassVar[dict[str, set[int]]] = ORB.regimes
    defaults: ClassVar[dict[str, Any]] = ORB.defaults

    def raw(self, s: SetupContext):
        base = ORB(params=self.params).raw(s)
        if s.oi_state is None:
            z = np.zeros_like(s.F["c"], dtype=bool)
            return {side: (z, *rest) for side, (_, *rest) in base.items()}  # type: ignore[misc]
        lm, *lr = base[LONG]
        sm, *sr = base[SHORT]
        return {LONG: (lm & (s.oi_state == 1), *lr), SHORT: (sm & (s.oi_state == 2), *sr)}  # type: ignore[return-value]


ALL_SETUPS: list[type[Setup]] = [ORB, VWAPReclaim, TrendPullback, PDHLRetest, GapGo, GapFade, RelStrength,
                                 VWAPMeanRevert, OIConfirm]
SETUP_BY_NAME = {s.name: s for s in ALL_SETUPS}


def build_setups(settings: Any, names: list[str] | None = None) -> list[Setup]:
    out = []
    for cls in ALL_SETUPS:
        if names is not None and cls.name not in names:
            continue
        if names is None and not settings.setup_enabled(cls.name):
            continue
        out.append(cls(params=settings.setup_params(cls.name), last_new_idea=settings.session.last_new_idea))
    return out
