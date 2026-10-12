"""The live tracker must reproduce the backtester's outcome exactly (same rules, same R)."""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pytest

from dalal.backtest.costs import load_costs
from dalal.backtest.simulate import simulate
from dalal.clock import IST
from dalal.live.tracker import Tracker
from tests.test_board import idea as mk_idea


@pytest.mark.parametrize("seed", range(40))
def test_tracker_matches_simulator(seed):
    rng = np.random.default_rng(seed)
    side = "long" if seed % 2 == 0 else "short"
    sg = 1 if side == "long" else -1
    n = 375
    steps = rng.normal(0, 0.12, n * 5).reshape(n, 5).cumsum(axis=1)
    base = 100 + np.concatenate([[0], rng.normal(0, 0.15, n - 1).cumsum()])
    o = np.round(base, 2)
    path = o[:, None] + steps
    c = np.round(path[:, -1], 2)
    h = np.round(np.maximum(path.max(1), np.maximum(o, c)), 2)
    lo = np.round(np.minimum(path.min(1), np.minimum(o, c)), 2)
    start = 30 + seed
    px0 = c[start - 1]
    trig = round(px0 + sg * 0.2, 2)
    stop = round(trig - sg * 0.6, 2)
    t1, t2 = round(trig + sg * 0.6, 2), round(trig + sg * 1.2, 2)
    costs = load_costs("cash")
    slip, valid_until, exit_min = 0.05, start + 29, 360
    f = simulate(side, o, h, lo, c, start, trig, stop, t1, t2, valid_until, exit_min, 0.05, slip, costs, 500000, 0.5)
    i = mk_idea("X", side=side)
    i.entry, i.stop, i.t1, i.t2, i.qty = trig, stop, t1, t2, f.qty or 1000
    tr = Tracker(costs, costs, tick=0.05)
    t0 = datetime(2025, 1, 2, 9, 15, tzinfo=IST)
    for m in range(start, n):
        tr.on_bar(i, m, t0 + timedelta(minutes=m), o[m], h[m], lo[m], c[m], slip, valid_until, exit_min)
        if i.status in ("EXPIRED", "INVALIDATED", "STOPPED", "T2_HIT", "CLOSED"):
            break
    if not f.triggered:
        assert i.status in ("EXPIRED", "INVALIDATED")
        return
    assert i.entry_px == pytest.approx(f.entry_px)
    assert tr.won(i) == f.win
    assert i.r_realised == pytest.approx(f.r_net, rel=1e-9, abs=1e-9)
