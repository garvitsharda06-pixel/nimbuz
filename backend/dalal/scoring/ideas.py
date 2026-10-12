"""The Idea: one trade card on the board, and one row in the journal (full snapshot)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

ACTIVE = {"WATCHING", "TRIGGERED"}
TERMINAL = {"T1_HIT", "T2_HIT", "STOPPED", "EXPIRED", "INVALIDATED", "CLOSED", "DISPLACED"}


@dataclass
class Idea:
    id: str
    day: str
    signal_ts: str                 # IST ISO timestamp of the 5-min close that produced it
    symbol: str
    key: str
    sector: str
    setup: str
    setup_label: str
    side: str                      # long | short
    regime: str
    grade: str                     # A+ | A | B
    entry: float                   # stop-entry trigger
    entry_zone: tuple[float, float]
    stop: float
    t1: float
    t2: float
    rr1: float
    rr2: float
    p: float                       # calibrated P(T1 before stop before 15:15)
    ci_lo: float
    ci_hi: float
    n: int                         # OOS instances behind the estimate
    expectancy: float              # realised OOS expectancy (R, after costs)
    cons_ev: float                 # EV at CI lower bound (ranking key)
    breakeven: float
    win_r: float
    loss_r: float
    qty: int
    risk_inr: float
    notional: float
    valid_until: str               # trigger must happen before this (IST ISO)
    expires_at: str                # 15:10 cut-off
    thesis: list[str]
    invalidation: str
    beta: float
    model_version: str
    config_hash: str
    provenance: str
    mode: str                      # LIVE | REPLAY
    status: str = "WATCHING"
    status_ts: str = ""
    entry_px: float | None = None
    exit_px: float | None = None
    exit_reason: str = ""
    r_realised: float | None = None
    last_price: float | None = None
    gate_bypassed: bool = False
    gate_reasons: list[str] = field(default_factory=list)
    live_hit: float | None = None
    live_n: int = 0
    stats_level: str = ""          # which book entry: regime bucket or pooled "*"
    snapshot: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    t1_done: bool = False
    paper: bool = False

    @property
    def sign(self) -> float:
        return 1.0 if self.side == "long" else -1.0

    @property
    def active(self) -> bool:
        return self.status in ACTIVE

    @property
    def symbol_side_setup(self) -> tuple[str, str, str]:
        return (self.symbol, self.side, self.setup)

    def distance_to_entry_pct(self) -> float | None:
        if self.last_price is None:
            return None
        return (self.entry - self.last_price) / self.last_price * 100 * self.sign

    def card(self) -> dict[str, Any]:
        d = asdict(self)
        d["distance_to_entry_pct"] = self.distance_to_entry_pct()
        for k, v in list(d.items()):
            if isinstance(v, float) and not np.isfinite(v):
                d[k] = None
        return d


def grade_for(cons_ev: float, a_plus: float, a: float) -> str:
    if cons_ev >= a_plus:
        return "A+"
    if cons_ev >= a:
        return "A"
    return "B"
