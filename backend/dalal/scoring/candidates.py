"""Turn a raw setup signal into a sized, scored candidate Idea, or reject it with a reason."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import numpy as np

from dalal.clock import IST, parse_hhmm
from dalal.config import Settings
from dalal.risk.sizing import size_position
from dalal.scoring.explain import thesis
from dalal.scoring.ideas import Idea, grade_for
from dalal.scoring.statsbook import StatsBook


@dataclass
class RawSignal:
    key: str
    symbol: str
    sector: str
    setup: str
    setup_label: str
    side: str
    day: str
    t: int
    signal_ts: datetime
    trigger: float
    stop: float
    t1: float
    t2: float
    regime: str
    invalidation: str
    snapshot: dict[str, Any]
    beta: float
    lot: int = 1


def gate_bypass_allowed(settings: Settings, provenance: str) -> bool:
    """The demo bypass is honoured ONLY on synthetic data (DECISIONS D-002)."""
    return bool(settings.board.demo_bypass_gates) and provenance == "synthetic"


def make_candidate(sig: RawSignal, book: StatsBook | None, settings: Settings, provenance: str, mode: str,
                   n_sectors: int, meta: Any | None = None) -> tuple[Idea | None, str]:
    bypass = gate_bypass_allowed(settings, provenance)
    entry = book.lookup(sig.setup, sig.side, sig.regime, settings.board.gate.min_n) if book else None
    if entry is None:
        return None, "no out-of-sample statistics for this setup"
    if book is not None and book.provenance != provenance:
        return None, f"stats book provenance {book.provenance} ≠ data {provenance}"
    if not entry.qualified and not bypass:
        return None, "; ".join(entry.reasons) or "not qualified"
    if entry.demoted and not bypass:
        return None, "setup demoted: live results diverge from backtest"
    lot = sig.lot if (sig.side == "short" and settings.risk.short_instrument == "futures") else 1
    size = size_position(sig.trigger, sig.stop, settings.risk, lot)
    if size.qty <= 0:
        return None, "position size rounds to zero"
    risk_ps = abs(sig.trigger - sig.stop)
    rr1 = abs(sig.t1 - sig.trigger) / risk_ps
    rr2 = abs(sig.t2 - sig.trigger) / risk_ps
    valid = sig.signal_ts + timedelta(minutes=settings.backtest.validity_minutes)
    exp = datetime.combine(sig.signal_ts.date(), parse_hhmm(settings.session.idea_expiry), tzinfo=IST)
    valid = min(valid, exp)
    chase = 0.25 * risk_ps * (1 if sig.side == "long" else -1)
    zone = (sig.trigger, round(sig.trigger + chase, 2)) if sig.side == "long" else (round(sig.trigger + chase, 2), sig.trigger)
    g = settings.board.grades
    cons = entry.cons_ev if np.isfinite(entry.cons_ev) else float("nan")
    idea = Idea(
        id=f"{sig.day}-{sig.symbol}-{sig.setup}-{sig.side}-{sig.t}",
        day=sig.day, signal_ts=sig.signal_ts.isoformat(), symbol=sig.symbol, key=sig.key, sector=sig.sector,
        setup=sig.setup, setup_label=sig.setup_label, side=sig.side, regime=sig.regime,
        grade=grade_for(cons, g.a_plus, g.a) if np.isfinite(cons) else "B",
        entry=round(sig.trigger, 2), entry_zone=zone, stop=round(sig.stop, 2), t1=round(sig.t1, 2), t2=round(sig.t2, 2),
        rr1=round(rr1, 2), rr2=round(rr2, 2), p=entry.p_cal, ci_lo=entry.ci_lo_cal, ci_hi=entry.ci_hi_cal, n=entry.n,
        expectancy=entry.expectancy, cons_ev=cons, breakeven=entry.breakeven, win_r=entry.win_r, loss_r=entry.loss_r,
        qty=size.qty, risk_inr=size.risk_inr, notional=size.notional, valid_until=valid.isoformat(),
        expires_at=exp.isoformat(), thesis=thesis(sig.setup, sig.side, sig.snapshot, sig.regime, n_sectors),
        invalidation=sig.invalidation, beta=sig.beta, model_version=book.model_version if book else "",
        config_hash=settings.config_hash(), provenance=provenance, mode=mode, status_ts=sig.signal_ts.isoformat(),
        gate_bypassed=bypass and not entry.qualified, gate_reasons=list(entry.reasons),
        live_hit=entry.live_hit, live_n=entry.live_n, stats_level=entry.regime,
        snapshot={k: (v if not (isinstance(v, float) and not np.isfinite(v)) else None) for k, v in sig.snapshot.items()},
    )
    if meta is not None:
        why = apply_meta(idea, sig, entry, meta, settings, bypass, n_sectors)
        if why:
            return None, why
    return idea, ""


def apply_meta(idea: Idea, sig: RawSignal, entry: Any, meta: Any, settings: Settings, bypass: bool,
               n_sectors: int) -> str:
    """Per-instance probability from the shipped meta-model; CI width from the bucket's evidence;
    thesis drivers from exact TreeSHAP contributions. Returns a rejection reason or ''."""
    import pandas as pd

    from dalal.regime.classifier import REGIMES
    from dalal.scoring.metamodel import shap_bullets
    from dalal.scoring.stats import ev

    row = {**sig.snapshot, "t": sig.t, "rr1": idea.rr1, "setup": sig.setup, "side": sig.side,
           "regime": REGIMES.index(sig.regime) if sig.regime in REGIMES else -1}
    p, drivers = meta.predict(pd.DataFrame([row]))
    half_lo = max(0.0, entry.hit - entry.ci_lo) if np.isfinite(entry.ci_lo) else 0.05
    half_hi = max(0.0, entry.ci_hi - entry.hit) if np.isfinite(entry.ci_hi) else 0.05
    pm = float(p[0])
    idea.p, idea.ci_lo, idea.ci_hi = pm, max(0.0, pm - half_lo), min(1.0, pm + half_hi)
    w = entry.win_r if np.isfinite(entry.win_r) else 0.0
    lr = entry.loss_r if np.isfinite(entry.loss_r) else 0.0
    idea.cons_ev = ev(idea.ci_lo, w, lr)
    g = settings.board.grades
    idea.grade = grade_for(idea.cons_ev, g.a_plus, g.a)
    bullets = shap_bullets(drivers[0])
    if bullets:
        idea.thesis = [idea.thesis[0], *bullets][:3]
        if len(idea.thesis) < 3:
            idea.thesis = thesis(sig.setup, sig.side, sig.snapshot, sig.regime, n_sectors, idea.thesis[1:])
    idea.snapshot["p_model"] = "lightgbm-meta"
    idea.snapshot["p_rule"] = entry.p_cal
    if not idea.cons_ev > 0 and not bypass:
        return f"meta-model conservative EV {idea.cons_ev:+.3f}R ≤ 0"
    return ""
