"""Trade simulation on 1-minute bars: stop-entry fill, triple-barrier outcome (§5, §9).

Conventions (DECISIONS D-003):
- The signal is known at the close of 5-min bar t; the order is live from the next minute.
- Entry: first minute whose range trades through the trigger; fill = max(trigger, open)
  (+ slippage) for longs. A gap through the trigger fills at the open, which is worse.
- The idea is void if the stop trades before the trigger, or if the validity window ends.
- If one minute bar touches both stop and T1, the STOP is assumed first (conservative).
- T1/T2 are limit exits: they need a trade one tick beyond the level to count as filled.
- 50 % off at T1, then the stop on the rest moves to breakeven; the rest exits at T2,
  breakeven or the 15:15 time exit.
- Label (probability): win = T1 filled before the stop and before 15:15.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from dalal.backtest.costs import CostModel


@dataclass
class Fill:
    triggered: bool
    void_reason: str = ""            # "" | "stop_before_trigger" | "expired"
    entry_min: int = -1
    entry_px: float = float("nan")
    exit_min: int = -1
    exit_reason: str = ""            # T1_T2 | T1_BE | T1_TIME | STOP | TIME
    win: bool = False
    r_gross: float = float("nan")
    r_net: float = float("nan")
    cost_inr: float = float("nan")
    pnl_inr: float = float("nan")
    qty: int = 0
    mae_r: float = float("nan")
    mfe_r: float = float("nan")
    t1_min: int = -1


def slippage(price: float, sigma_1m: float, spread_bps: float, k_vol: float, tick: float) -> float:
    s = price * (spread_bps / 2.0) / 1e4 + k_vol * sigma_1m * price
    return max(tick, s) if np.isfinite(s) else tick


def position_qty(capital: float, risk_pct: float, entry: float, stop: float, lot: int, leverage: float) -> int:
    per = abs(entry - stop)
    if per <= 0 or not np.isfinite(per):
        return 0
    q = int((capital * risk_pct / 100.0) // per)
    q = min(q, int(capital * leverage // entry))
    if lot > 1:
        q = (q // lot) * lot
    return max(q, 0)


def simulate(
    side: str, o: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray,  # noqa: E741 - 1m arrays of the day (375,)
    start_min: int, trigger: float, stop: float, t1: float, t2: float,
    valid_until_min: int, time_exit_min: int, tick: float, slip: float,
    costs: CostModel, capital: float, risk_pct: float, lot: int = 1, leverage: float = 5.0,
    partial: float = 0.5,
) -> Fill:
    long = side == "long"
    sgn = 1.0 if long else -1.0
    n = len(o)
    end_watch = min(valid_until_min, time_exit_min - 1, n - 1)
    if start_min > end_watch:
        return Fill(False, "expired")
    # ---- wait for trigger (or stop-before-trigger) ----
    seg = slice(start_min, end_watch + 1)
    hit_trig = (h[seg] >= trigger) if long else (l[seg] <= trigger)
    hit_stop = (l[seg] <= stop) if long else (h[seg] >= stop)
    it = int(np.argmax(hit_trig)) if hit_trig.any() else -1
    is_ = int(np.argmax(hit_stop)) if hit_stop.any() else -1
    if it < 0:
        return Fill(False, "stop_before_trigger" if is_ >= 0 else "expired")
    if 0 <= is_ < it:
        return Fill(False, "stop_before_trigger")
    j = start_min + it
    raw_fill = max(trigger, o[j]) if long else min(trigger, o[j])
    entry = raw_fill + sgn * slip
    risk = (entry - stop) * sgn
    if risk <= 0:
        return Fill(False, "stop_before_trigger")
    qty = position_qty(capital, risk_pct, entry, stop, lot, leverage)
    if qty <= 0:
        return Fill(False, "size_zero")
    q1 = int(qty * partial) if lot <= 1 else int((qty * partial) // lot * lot)
    q2 = qty - q1

    # ---- manage the position minute by minute (vectorised searches) ----
    last = min(time_exit_min - 1, n - 1)
    path = slice(j, last + 1)
    hh, ll = h[path], l[path]
    stop_hit = (ll <= stop) if long else (hh >= stop)
    t1_hit = (hh >= t1 + tick) if long else (ll <= t1 - tick)
    # entry minute: T1 only counts if that minute also CLOSES beyond T1 (order inside the bar unknown)
    if len(t1_hit):
        t1_hit[0] = (c[j] >= t1 + tick) if long else (c[j] <= t1 - tick)
    i_stop = int(np.argmax(stop_hit)) if stop_hit.any() else 10**9
    i_t1 = int(np.argmax(t1_hit)) if t1_hit.any() else 10**9
    exits: list[tuple[float, float]] = []
    reason = ""
    win = False
    t1_min = -1
    if i_stop <= i_t1 and i_stop < 10**9:              # stop first (ties → stop)
        k = j + i_stop
        px = (min(stop, o[k]) if long else max(stop, o[k])) if k > j else stop
        px -= sgn * slip
        exits = [(px, qty)]
        reason, exit_min = "STOP", k
    elif i_t1 < 10**9:
        win = True
        k1 = j + i_t1
        t1_min = k1
        exits.append((t1, q1))
        be = entry
        rest = slice(k1, last + 1)
        hh2, ll2 = h[rest], l[rest]
        t2_hit = (hh2 >= t2 + tick) if long else (ll2 <= t2 - tick)
        be_hit = (ll2 <= be) if long else (hh2 >= be)
        be_hit[0] = False                               # T1 minute: assume BE not revisited inside it
        t2_hit[0] = (c[k1] >= t2 + tick) if long else (c[k1] <= t2 - tick)
        i_be = int(np.argmax(be_hit)) if be_hit.any() else 10**9
        i_t2 = int(np.argmax(t2_hit)) if t2_hit.any() else 10**9
        if q2 <= 0:
            reason, exit_min = "T1_ONLY", k1
        elif i_be <= i_t2 and i_be < 10**9:
            k = k1 + i_be
            px = (min(be, o[k]) if long else max(be, o[k])) - sgn * slip
            exits.append((px, q2))
            reason, exit_min = "T1_BE", k
        elif i_t2 < 10**9:
            exits.append((t2, q2))
            reason, exit_min = "T1_T2", k1 + i_t2
        else:
            px = (o[time_exit_min] if time_exit_min < n else c[last]) - sgn * slip
            exits.append((px, q2))
            reason, exit_min = "T1_TIME", time_exit_min
    else:
        px = (o[time_exit_min] if time_exit_min < n else c[last]) - sgn * slip
        exits = [(px, qty)]
        reason, exit_min = "TIME", time_exit_min

    gross = sum((px - entry) * sgn * q for px, q in exits)
    cost = costs.trade(side, entry, qty, exits)
    r_unit = risk * qty
    held = slice(j, min(exit_min, last) + 1)
    if long:
        mae = (np.min(l[held]) - entry) / risk
        mfe = (np.max(h[held]) - entry) / risk
    else:
        mae = (entry - np.max(h[held])) / risk
        mfe = (entry - np.min(l[held])) / risk
    return Fill(True, "", j, entry, exit_min, reason, win, gross / r_unit, (gross - cost) / r_unit, cost,
                gross - cost, qty, float(max(mae, -1.5)), float(mfe), t1_min)
