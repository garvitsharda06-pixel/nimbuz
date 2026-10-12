"""Idea status machine (Watching → Triggered → T1 hit → T2 hit / Stopped / Expired / Closed).

Uses exactly the backtester's rules (backtest/simulate.py) so live/replay outcomes match the
research labels. Bars (1-minute) drive transitions in REPLAY; in LIVE, ticks also drive them
via `on_price` (a tick is a degenerate bar with o = h = l = c).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from dalal.backtest.costs import CostModel
from dalal.scoring.ideas import Idea


@dataclass
class Transition:
    idea_id: str
    old: str
    new: str
    ts: str
    price: float | None
    note: str = ""


class Tracker:
    def __init__(self, costs_long: CostModel, costs_short: CostModel, tick: float = 0.05, partial: float = 0.5):
        self.costs = {"long": costs_long, "short": costs_short}
        self.tick = tick
        self.partial = partial
        self._exits: dict[str, list[tuple[float, int]]] = {}
        self._entry_minute: dict[str, int] = {}

    # ------------------------------------------------------------------
    def on_bar(self, idea: Idea, minute: int, ts: datetime, o: float, h: float, lo: float, c: float, slip: float,
               valid_until_min: int, time_exit_min: int, last_bar: bool = False) -> list[Transition]:
        out: list[Transition] = []
        long = idea.side == "long"
        sg = 1.0 if long else -1.0
        tick = self.tick
        iso = ts.isoformat()

        def move(new: str, px: float | None, note: str = "") -> None:
            out.append(Transition(idea.id, idea.status, new, iso, px, note))
            idea.status = new
            idea.status_ts = iso
            idea.events.append({"ts": iso, "status": new, "price": px, "note": note})

        if idea.status == "WATCHING":
            if minute > valid_until_min:
                move("EXPIRED", None, "trigger not reached in the validity window")
                return out
            hit_t = h >= idea.entry if long else lo <= idea.entry
            hit_s = lo <= idea.stop if long else h >= idea.stop
            if hit_s and not hit_t:
                move("INVALIDATED", idea.stop, "stop traded before trigger")
                return out
            if not hit_t:
                return out
            raw = max(idea.entry, o) if long else min(idea.entry, o)
            idea.entry_px = raw + sg * slip
            self._entry_minute[idea.id] = minute
            self._exits[idea.id] = []
            move("TRIGGERED", idea.entry_px)
            # same entry minute: stop first (conservative), T1 only if the minute CLOSES beyond it
            if hit_s:
                self._exit(idea, idea.stop - sg * slip, 1.0)
                move("STOPPED", idea.stop, "stopped in the entry minute")
                return out
            if (c >= idea.t1 + tick) if long else (c <= idea.t1 - tick):
                self._exit(idea, idea.t1, self.partial)
                idea.t1_done = True
                move("T1_HIT", idea.t1)
                if (c >= idea.t2 + tick) if long else (c <= idea.t2 - tick):
                    self._exit(idea, idea.t2, 1.0)
                    move("T2_HIT", idea.t2)
            return out

        if idea.status in ("TRIGGERED", "T1_HIT"):
            entry = idea.entry_px if idea.entry_px is not None else idea.entry
            stop_lvl = entry if idea.t1_done else idea.stop
            if minute >= time_exit_min:
                px = o - sg * slip
                self._exit(idea, px, 1.0)
                move("CLOSED", px, "15:15 time exit")
                return out
            stop_hit = lo <= stop_lvl if long else h >= stop_lvl
            if stop_hit:
                px = (min(stop_lvl, o) if long else max(stop_lvl, o)) - sg * slip
                self._exit(idea, px, 1.0)
                move("CLOSED" if idea.t1_done else "STOPPED", px, "breakeven stop" if idea.t1_done else "")
                return out
            if not idea.t1_done and ((h >= idea.t1 + tick) if long else (lo <= idea.t1 - tick)):
                self._exit(idea, idea.t1, self.partial)
                idea.t1_done = True
                move("T1_HIT", idea.t1)
                if (c >= idea.t2 + tick) if long else (c <= idea.t2 - tick):
                    self._exit(idea, idea.t2, 1.0)
                    move("T2_HIT", idea.t2)
                return out
            if idea.t1_done and ((h >= idea.t2 + tick) if long else (lo <= idea.t2 - tick)):
                self._exit(idea, idea.t2, 1.0)
                move("T2_HIT", idea.t2)
        return out

    def on_price(self, idea: Idea, minute: int, ts: datetime, px: float, slip: float, valid_until_min: int,
                 time_exit_min: int) -> list[Transition]:
        return self.on_bar(idea, minute, ts, px, px, px, px, slip, valid_until_min, time_exit_min)

    # ------------------------------------------------------------------
    def _exit(self, idea: Idea, px: float, frac_of_remaining: float) -> None:
        legs = self._exits.setdefault(idea.id, [])
        done = sum(q for _, q in legs)
        rem = idea.qty - done
        q = rem if frac_of_remaining >= 1.0 else int(idea.qty * frac_of_remaining)
        if q > 0:
            legs.append((px, q))
        if sum(q for _, q in legs) >= idea.qty:
            self._finalise(idea)

    def _finalise(self, idea: Idea) -> None:
        legs = self._exits.get(idea.id, [])
        entry = idea.entry_px if idea.entry_px is not None else idea.entry
        sg = idea.sign
        gross = sum((px - entry) * sg * q for px, q in legs)
        cost = self.costs[idea.side].trade(idea.side, entry, idea.qty, [(px, float(q)) for px, q in legs])
        risk = abs(entry - idea.stop) * idea.qty
        idea.exit_px = legs[-1][0] if legs else None
        idea.r_realised = (gross - cost) / risk if risk > 0 else None
        idea.exit_reason = idea.status

    def won(self, idea: Idea) -> bool:
        return idea.t1_done
