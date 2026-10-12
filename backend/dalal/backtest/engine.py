"""Event-driven research engine: signals on completed 5-min bars → stop-entry fills and
triple-barrier outcomes on 1-minute bars, with the full Indian cost model (§9).

The market/symbol computations live in `dalal.pipeline`, which the live engine also uses.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd

from dalal.backtest.costs import load_costs
from dalal.backtest.simulate import simulate, slippage
from dalal.config import Settings
from dalal.data.models import Instrument
from dalal.data.store import CandleStore
from dalal.features.panel import N1, bar5_close_hhmm, hhmm_to_minute, panel_from_1m
from dalal.pipeline import (
    MarketBuilder,
    attach_oi,
    eligibility,
    load_index_panels,
    setup_context,
    snapshot,
    symbol_features,
)
from dalal.setups.base import LONG, Setup
from dalal.setups.library import build_setups


@dataclass
class ResearchResult:
    instances: pd.DataFrame
    signals_total: int
    void_counts: dict[str, int]
    provenance: str
    days: tuple[str, str]
    n_symbols: int
    setups: list[str]
    elapsed_s: float
    meta: dict[str, Any] = field(default_factory=dict)


class Research:
    def __init__(self, settings: Settings, store: CandleStore, setups: list[Setup] | None = None,
                 start: date | None = None, end: date | None = None,
                 progress: Callable[[str], None] | None = None, symbols: list[str] | None = None):
        self.s = settings
        self.store = store
        self.setups = setups if setups is not None else build_setups(settings)
        self.start = start or settings.backtest.start
        self.end = end or settings.backtest.end
        self.progress = progress or (lambda m: None)
        self.symbols = symbols
        self.cash_costs = load_costs("cash")
        self.fut_costs = load_costs("futures")

    def _read(self, key: str) -> pd.DataFrame:
        lo = (self.start - timedelta(days=90)) if self.start else None
        return self.store.read(key, lo, self.end) if (lo or self.end) else self.store.read(key)

    def run(self) -> ResearchResult:
        t0 = time.time()
        insts, roles = self.store.instruments()
        stocks = [i for i in insts if i.kind == "EQ" and self.store.has(i.key)]
        if self.symbols:
            stocks = [i for i in stocks if i.symbol in self.symbols]
        idx = load_index_panels(self.store, roles, self._read)
        mb = MarketBuilder(idx)
        # ---- pass 1: breadth over the eligible universe ----
        for k, inst in enumerate(stocks):
            p, ctx, F = symbol_features(inst.key, self._read(inst.key), idx)
            mb.add_breadth(p.days, eligibility(ctx, self.s.universe), F, ctx)
            self.progress(f"pass1 {k + 1}/{len(stocks)} {inst.symbol}")
        mkt = mb.build()
        # ---- pass 2: setups → fills ----
        rows: list[dict[str, Any]] = []
        void: dict[str, int] = {}
        total = 0
        for k, inst in enumerate(stocks):
            p, ctx, F = symbol_features(inst.key, self._read(inst.key), idx)
            sc = setup_context(inst, p, ctx, F, mkt, idx)
            if inst.futures_key and self.store.has(inst.futures_key):
                fp = panel_from_1m(inst.futures_key, self._read(inst.futures_key))
                attach_oi(F, sc, p.days, fp.days, fp.c1, fp.oi1)
            elig = eligibility(ctx, self.s.universe)
            in_range = np.array([(self.start is None or d >= self.start) and (self.end is None or d <= self.end)
                                 for d in p.days])
            for st in self.setups:
                sig = st.evaluate(sc)
                for side, ss in sig.items():
                    mask = ss.mask & (elig & in_range)[:, None]
                    rows_, total_, void_ = self._simulate_signals(inst, p, ctx, F, sc, st, side, ss, mask)
                    rows.extend(rows_)
                    total += total_
                    for kk, vv in void_.items():
                        void[kk] = void.get(kk, 0) + vv
            self.progress(f"pass2 {k + 1}/{len(stocks)} {inst.symbol}: {len(rows)} instances")
        df = pd.DataFrame(rows)
        if not df.empty:
            df = df.sort_values(["day", "t", "symbol", "setup", "side"]).reset_index(drop=True)
        days = (str(mkt.days[0]), str(mkt.days[-1])) if len(mkt.days) else ("", "")
        return ResearchResult(df, total, void, self.store.provenance, days, len(stocks),
                              [s.variant for s in self.setups], round(time.time() - t0, 1), {"mkt": mkt})

    def _simulate_signals(self, inst: Instrument, p, ctx, F, sc, st: Setup, side: str, ss, mask):
        bt, risk, sess = self.s.backtest, self.s.risk, self.s.session
        expiry_min = hhmm_to_minute(sess.idea_expiry)
        exit_min = hhmm_to_minute(sess.resolve_at)
        short_fut = side != LONG and risk.short_instrument == "futures"
        costs = self.fut_costs if short_fut else self.cash_costs
        lot = inst.lot_size if short_fut else 1
        out: list[dict[str, Any]] = []
        void: dict[str, int] = {}
        busy_until: dict[int, int] = {}
        hits = np.argwhere(mask)
        for d, t in hits:
            start_min = 5 * (int(t) + 1)
            if start_min <= busy_until.get(int(d), -1):
                continue                           # same setup/side already live on this symbol-day
            valid_until = min(start_min + bt.validity_minutes - 1, expiry_min - 1)
            sig1m = ctx["sigma_d"][d] / np.sqrt(N1) if np.isfinite(ctx["sigma_d"][d]) else 0.001
            trig = float(ss.trigger[d, t])
            slip = slippage(trig, sig1m, bt.slippage.spread_bps, bt.slippage.k_vol, sc.tick)
            f = simulate(side, p.o1[d], p.h1[d], p.l1[d], p.c1[d], start_min, trig, float(ss.stop[d, t]),
                         float(ss.t1[d, t]), float(ss.t2[d, t]), valid_until, exit_min, sc.tick, slip, costs,
                         risk.capital, risk.risk_per_trade_pct, lot, risk.mis_leverage, bt.partial_at_t1)
            busy_until[int(d)] = f.exit_min if f.triggered else valid_until
            if not f.triggered:
                void[f.void_reason] = void.get(f.void_reason, 0) + 1
                continue
            rec = {
                "day": p.days[d], "t": int(t), "signal_time": bar5_close_hhmm(int(t)), "symbol": inst.symbol,
                "key": inst.key, "sector": inst.sector, "setup": st.variant, "side": side,
                "regime": int(sc.regime[d, t]), "trigger": trig, "stop": float(ss.stop[d, t]), "t1": float(ss.t1[d, t]),
                "t2": float(ss.t2[d, t]), "rr1": abs(float(ss.t1[d, t]) - trig) / abs(trig - float(ss.stop[d, t])),
                "entry_min": f.entry_min, "entry_px": f.entry_px, "exit_min": f.exit_min, "exit_reason": f.exit_reason,
                "win": bool(f.win), "r_gross": f.r_gross, "r_net": f.r_net, "cost_inr": f.cost_inr,
                "pnl_inr": f.pnl_inr, "qty": f.qty, "mae_r": f.mae_r, "mfe_r": f.mfe_r,
                "hold_min": f.exit_min - f.entry_min,
            }
            rec.update(snapshot(F, ctx, sc, int(d), int(t), side))
            out.append(rec)
        return out, len(hits), void
