"""Guardrails (§10) and event blackouts. Fail closed: any active guardrail ⇒ no NEW ideas
(existing ideas keep being tracked)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time

from dalal.clock import IST, parse_hhmm
from dalal.config import RiskCfg, SessionCfg, load_yaml


@dataclass
class GuardState:
    allowed: bool
    reasons: list[str] = field(default_factory=list)


@dataclass
class Blackout:
    day: date
    kind: str
    note: str = ""
    symbols: list[str] = field(default_factory=list)   # empty = market-wide


def load_blackouts() -> list[Blackout]:
    raw = load_yaml("calendar.yaml").get("blackouts") or []
    out = []
    for b in raw:
        out.append(Blackout(date.fromisoformat(str(b["date"])), str(b.get("kind", "EVENT")), str(b.get("note", "")),
                            list(b.get("symbols") or [])))
    return out


def check_guardrails(now: datetime, sess: SessionCfg, risk: RiskCfg, *, kill_switch: bool, feed_ok: bool,
                     feed_reason: str, pnl_today_inr: float, open_ideas: int,
                     blackouts: list[Blackout] | None = None) -> GuardState:
    reasons = []
    t = now.astimezone(IST).time()
    if kill_switch:
        reasons.append("Kill switch ON")
    if not feed_ok:
        reasons.append(f"Data feed not healthy: {feed_reason}")
    if t < parse_hhmm(sess.first_signal):
        reasons.append(f"Before {sess.first_signal}")
    if t > parse_hhmm(sess.last_new_idea):
        reasons.append(f"No new ideas after {sess.last_new_idea}")
    limit = -risk.capital * risk.max_daily_loss_pct / 100.0
    if pnl_today_inr <= limit:
        reasons.append(f"Daily loss limit hit (₹{pnl_today_inr:,.0f} ≤ ₹{limit:,.0f})")
    if open_ideas >= risk.max_open_ideas:
        reasons.append(f"Max open ideas ({risk.max_open_ideas}) reached")
    for b in blackouts or []:
        if b.day == now.astimezone(IST).date() and not b.symbols:
            reasons.append(f"Event blackout: {b.kind} {b.note}".strip())
    return GuardState(not reasons, reasons)


def symbol_blackouts(day: date, blackouts: list[Blackout]) -> set[str]:
    return {s for b in blackouts if b.day == day for s in b.symbols}


def session_minute(t: time) -> int:
    return t.hour * 60 + t.minute
