"""The stats book: per (setup, side, regime) out-of-sample evidence that the live engine reads.

Nothing here is guessed: every number comes from walk-forward OOS instances (§5). A bucket
qualifies for the live board only if it passes the configured gate.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from dalal.clock import IST
from dalal.config import BoardCfg, ScoringCfg
from dalal.regime.classifier import REGIMES
from dalal.scoring.calibration import Calibrator
from dalal.scoring.stats import breakeven_p, bucket_stats, ev, shrink


@dataclass
class BookEntry:
    setup: str
    side: str
    regime: str                 # regime name or "*" (pooled across regimes)
    n: int
    wins: int
    hit: float
    ci_lo: float
    ci_hi: float
    win_r: float
    loss_r: float
    expectancy: float
    breakeven: float
    profit_factor: float
    p_raw: float                # shrunk bucket rate
    p_cal: float                # after isotonic calibration
    ci_lo_cal: float
    ci_hi_cal: float
    cons_ev: float              # EV at ci_lo_cal (ranking key, §5)
    stability: float            # share of WF folds with positive OOS expectancy (setup-side level)
    n_folds: int
    dsr: float
    qualified: bool
    reasons: list[str] = field(default_factory=list)
    live_n: int = 0
    live_hit: float | None = None
    demoted: bool = False

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for k, v in d.items():
            if isinstance(v, float) and not np.isfinite(v):
                d[k] = None
        return d


@dataclass
class StatsBook:
    provenance: str
    created: str
    config_hash: str
    data_range: tuple[str, str]
    model_version: str
    entries: dict[str, BookEntry]
    calibrator: Calibrator
    calibration_report: dict[str, Any] = field(default_factory=dict)
    gate: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def key(setup: str, side: str, regime: str) -> str:
        return f"{setup}|{side}|{regime}"

    def lookup(self, setup: str, side: str, regime: str, min_n: int) -> BookEntry | None:
        """Regime bucket if it has ≥ min_n OOS instances, else the pooled setup-side entry."""
        b = self.entries.get(self.key(setup, side, regime))
        if b is not None and b.n >= min_n:
            return b
        return self.entries.get(self.key(setup, side, "*"))

    def qualified_setups(self) -> list[str]:
        return sorted({e.setup for e in self.entries.values() if e.qualified})

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "provenance": self.provenance, "created": self.created, "config_hash": self.config_hash,
            "data_range": list(self.data_range), "model_version": self.model_version, "gate": self.gate,
            "calibrator": self.calibrator.to_dict(), "calibration_report": self.calibration_report,
            "entries": {k: e.as_dict() for k, e in self.entries.items()},
        }
        path.write_text(json.dumps(payload, indent=1, default=str))

    @classmethod
    def load(cls, path: Path) -> StatsBook:
        d = json.loads(path.read_text())
        ents = {}
        for k, e in d["entries"].items():
            e = {kk: (float("nan") if vv is None and kk not in ("live_hit",) else vv) for kk, vv in e.items()}
            ents[k] = BookEntry(**e)
        return cls(d["provenance"], d["created"], d["config_hash"], tuple(d["data_range"]), d["model_version"],
                   ents, Calibrator.from_dict(d["calibrator"]), d.get("calibration_report", {}), d.get("gate", {}))


def fold_stability(oos: pd.DataFrame, min_fold_trades: int) -> tuple[float, int]:
    vals = []
    for _, g in oos.groupby("fold"):
        if len(g) >= min_fold_trades:
            vals.append(g["r_net"].mean() > 0)
    return (float(np.mean(vals)) if vals else float("nan"), len(vals))


def build_statsbook(wf: pd.DataFrame, scoring: ScoringCfg, board: BoardCfg, provenance: str, config_hash: str,
                    data_range: tuple[str, str], dsr_by_setup_side: dict[tuple[str, str], float],
                    calibration_report: dict[str, Any] | None = None) -> StatsBook:
    oos = wf[wf["fold"] >= 0]
    cal = Calibrator.fit(oos["p_oos"].to_numpy(), oos["win"].to_numpy())
    gate = board.gate
    entries: dict[str, BookEntry] = {}
    for (setup, side), g in oos.groupby(["setup", "side"]):
        stab, nf = fold_stability(g, gate.min_fold_trades)
        pool_p = g["win"].mean()
        dsr = dsr_by_setup_side.get((setup, side), float("nan"))
        groups: list[tuple[str, pd.DataFrame]] = [("*", g)]
        groups += [(REGIMES[int(r)] if r >= 0 else "UNKNOWN", gg) for r, gg in g.groupby("regime")]
        for regime, gg in groups:
            b = bucket_stats(gg["win"].to_numpy(), gg["r_net"].to_numpy(), scoring.ci_z)
            p_raw = shrink(b.wins, b.n, pool_p, scoring.prior_strength) if regime != "*" else b.hit
            p_cal = float(cal(p_raw)) if cal.x else p_raw
            shift = p_cal - b.hit
            lo, hi = float(np.clip(b.ci_lo + shift, 0, 1)), float(np.clip(b.ci_hi + shift, 0, 1))
            w = b.win_r if np.isfinite(b.win_r) else 0.0
            lr = b.loss_r if np.isfinite(b.loss_r) else 0.0
            cons = ev(lo, w, lr)
            be = breakeven_p(w, lr)
            reasons = []
            if b.n < gate.min_n:
                reasons.append(f"N {b.n} < {gate.min_n}")
            if not (np.isfinite(be) and lo > be):
                reasons.append(f"CI low {lo:.2f} ≤ breakeven {be:.2f}")
            if not cons > 0:
                reasons.append(f"conservative EV {cons:+.3f}R ≤ 0")
            if not (np.isfinite(stab) and stab >= gate.min_wf_stability):
                reasons.append(f"WF stability {stab:.0%} < {gate.min_wf_stability:.0%}" if np.isfinite(stab)
                               else "WF stability unknown")
            entries[StatsBook.key(setup, side, regime)] = BookEntry(
                setup=setup, side=side, regime=regime, n=b.n, wins=b.wins, hit=b.hit, ci_lo=b.ci_lo, ci_hi=b.ci_hi,
                win_r=b.win_r, loss_r=b.loss_r, expectancy=b.expectancy, breakeven=be, profit_factor=b.profit_factor,
                p_raw=float(p_raw), p_cal=p_cal, ci_lo_cal=lo, ci_hi_cal=hi, cons_ev=cons, stability=stab,
                n_folds=nf, dsr=dsr, qualified=not reasons, reasons=reasons)
    rep = {k: v for k, v in (calibration_report or {}).items() if k not in ("p_cal", "index")}
    return StatsBook(provenance, datetime.now(tz=IST).isoformat(), config_hash, data_range,
                     f"rulebook-v1-{config_hash}", entries, cal, rep,
                     {"min_n": gate.min_n, "min_wf_stability": gate.min_wf_stability,
                      "min_fold_trades": gate.min_fold_trades})
