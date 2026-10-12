"""Setup interface (§8.2). Every setup declares its timeframe, allowed regimes, entry trigger,
structural stop, T1/T2, max holding time and invalidation. Signals are evaluated on completed
5-minute candles only, using arrays at bar t (and t-1/t-2): never later bars.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from typing import Any, ClassVar

import numpy as np

from dalal.features.core import Features
from dalal.features.panel import hhmm_to_bar5_last
from dalal.regime.classifier import R_CHOP, R_DN, R_RANGE, R_UP

LONG, SHORT = "long", "short"


@dataclass
class SideSignals:
    mask: np.ndarray        # (D, 75) bool
    trigger: np.ndarray     # (D, 75) stop-entry price
    stop: np.ndarray
    t1: np.ndarray
    t2: np.ndarray


@dataclass
class SetupContext:
    """Everything a setup may read, aligned on the symbol's days (D, 75)."""

    F: Features                       # symbol intraday features
    ctx: Features                     # symbol daily context
    regime: np.ndarray                # (D, 75) int
    idx_ret: np.ndarray               # (D, 75) Nifty return vs prev close
    idx_above_vwap: np.ndarray        # (D, 75) bool
    sector_rank: np.ndarray           # (D, 75) 1 = strongest
    n_sectors: int
    tick: float = 0.05
    oi_state: np.ndarray | None = None   # (D, 75) +1 long build-up … (see oi_confirm)


@dataclass
class Setup:
    name: ClassVar[str] = "base"
    label: ClassVar[str] = "Base"
    timeframe: ClassVar[str] = "5m"
    regimes: ClassVar[dict[str, set[int]]] = {LONG: {R_UP, R_DN, R_RANGE, R_CHOP}, SHORT: {R_UP, R_DN, R_RANGE, R_CHOP}}
    defaults: ClassVar[dict[str, Any]] = {}
    params: dict[str, Any] = field(default_factory=dict)
    first_bar: int = 0
    last_new_idea: str = "14:45"
    max_risk_pct: float = 0.015
    min_risk_pct: float = 0.0015
    tag: str = ""                     # variant id for sensitivity runs ("" = the base setup)

    def __post_init__(self) -> None:
        self.params = {**self.defaults, **self.params}

    @property
    def variant(self) -> str:
        return f"{self.name}@{self.tag}" if self.tag else self.name

    def p(self, k: str) -> Any:
        return self.params[k]

    @property
    def last_bar(self) -> int:
        lb = hhmm_to_bar5_last(self.last_new_idea)
        if "last_time" in self.params:
            lb = min(lb, hhmm_to_bar5_last(self.params["last_time"]))
        return lb

    # ---- to implement ----
    def raw(self, s: SetupContext) -> dict[str, tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray | None, np.ndarray | None]]:
        """side → (mask, trigger, stop, t1 or None, t2 or None) before common filters."""
        raise NotImplementedError

    def invalidation(self, side: str, stop: float, extra: dict[str, float]) -> str:
        return f"Stop {stop:.2f}; void if traded before trigger"

    # ---- common pipeline ----
    def evaluate(self, s: SetupContext) -> dict[str, SideSignals]:
        out: dict[str, SideSignals] = {}
        D = s.F["c"].shape[0]
        t = np.arange(s.F["c"].shape[1])[None, :]
        time_ok = (t >= max(self.first_bar, self.params.get("first_bar", 0))) & (t <= self.last_bar)
        with np.errstate(invalid="ignore"), warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            for side, (mask, trig, stop, t1, t2) in self.raw(s).items():
                sign = 1.0 if side == LONG else -1.0
                trig = _round_tick(trig, s.tick, up=side == LONG)
                stop = _round_tick(stop, s.tick, up=side == SHORT)
                risk = (trig - stop) * sign
                px = s.F["c"]
                lut = np.zeros(8, dtype=bool)
                lut[[r + 1 for r in self.regimes[side]]] = True
                reg_ok = lut[np.clip(s.regime, -1, 6) + 1]
                ok = mask & time_ok & reg_ok & s.F["visible"] & (risk >= np.maximum(self.min_risk_pct * px, 2 * s.tick)) \
                    & (risk <= self.max_risk_pct * px)
                r1, r2 = self.params.get("t1_r", 1.0), self.params.get("t2_r", 2.0)
                T1 = trig + sign * r1 * risk if t1 is None else _round_tick(t1, s.tick, up=side == SHORT)
                T2 = trig + sign * r2 * risk if t2 is None else _round_tick(t2, s.tick, up=side == SHORT)
                if t1 is not None:  # structural targets must still pay ≥ min_rr
                    ok &= ((T1 - trig) * sign) >= self.params.get("min_rr", 0.8) * risk
                    ok &= ((T2 - T1) * sign) >= 0
                ok &= ~np.isnan(trig) & ~np.isnan(stop)
                out[side] = SideSignals(np.asarray(ok, dtype=bool).reshape(D, -1), trig, stop, T1, T2)
        return out


def _round_tick(x: np.ndarray, tick: float, up: bool) -> np.ndarray:
    q = x / tick
    return (np.ceil(q - 1e-9) if up else np.floor(q + 1e-9)) * tick


def prev(a: np.ndarray, k: int = 1) -> np.ndarray:
    out = np.full_like(a, np.nan, dtype=float)
    out[:, k:] = a[:, :-k]
    return out


def floor_stop(stop: np.ndarray, trig: np.ndarray, atr5: np.ndarray, side: str, k: float = 0.5) -> np.ndarray:
    """Structural stop, but never closer than k × ATR(5m) to the trigger (noise floor)."""
    if side == LONG:
        return np.fmin(stop, trig - k * atr5)
    return np.fmax(stop, trig + k * atr5)
