"""Live engine (§4 phase 4): one pipeline for LIVE and REPLAY.

On every completed 5-minute candle (≤ 1 s budget):
  2-day panels (prev day + today so far) → features (same code as research) → breadth →
  regime → setups → stats-book gate → sizing → guardrails → board (portfolio + hysteresis)
On every 1-minute bar (and tick in LIVE): idea status tracking with the backtester's rules.
"""

from __future__ import annotations

import json
import time as _time
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from dalal.backtest.costs import load_costs
from dalal.backtest.simulate import slippage
from dalal.clock import IST, parse_hhmm
from dalal.data.models import Instrument, MinuteBatch, Tick
from dalal.data.quality import Health
from dalal.data.reference import ReferenceData
from dalal.features.core import Features, daily_context, intraday, slice_ctx
from dalal.features.panel import (
    N1,
    N5,
    OPEN_MIN,
    append_empty_day,
    bar5_close_hhmm,
    panel_from_1m,
    panel_from_arrays,
)
from dalal.live.tracker import Tracker, Transition
from dalal.pipeline import Series3, eligibility, snapshot
from dalal.regime.classifier import REGIMES, MarketContext, classify, sector_ranks
from dalal.risk.guardrails import check_guardrails, load_blackouts, symbol_blackouts
from dalal.scoring.board import corr_from_returns, reason_key, select_board
from dalal.scoring.candidates import RawSignal, make_candidate
from dalal.scoring.ideas import Idea
from dalal.scoring.statsbook import StatsBook
from dalal.setups.base import Setup, SetupContext
from dalal.setups.library import build_setups

FIELDS = ("o", "h", "l", "c", "v", "oi")


class JournalSink(Protocol):
    def log_idea(self, idea: Idea) -> None: ...

    def log_transition(self, idea: Idea, tr: Transition) -> None: ...

    def pnl_today(self, day: str, mode: str, prices: dict[str, float]) -> float: ...


class NullJournal:
    def log_idea(self, idea: Idea) -> None:
        return None

    def log_transition(self, idea: Idea, tr: Transition) -> None:
        return None

    def pnl_today(self, day: str, mode: str, prices: dict[str, float]) -> float:
        return 0.0


@dataclass
class Series:
    """Rolling state for one instrument: previous session + today's minute arrays."""

    inst: Instrument
    prev_day: date | None
    prev: dict[str, np.ndarray]
    today: dict[str, np.ndarray]
    ctx2: Features
    eligible: bool = False
    beta: float = float("nan")
    ret20: np.ndarray | None = None
    F: Features | None = None
    panel_days: np.ndarray = field(default_factory=lambda: np.array([], dtype=object))

    def panel(self, day: date, minutes: int):
        days = np.array([self.prev_day, day], dtype=object) if self.prev_day else np.array([day], dtype=object)
        stack = {k: (np.vstack([self.prev[k], self.today[k]]) if self.prev_day else self.today[k][None, :]) for k in FIELDS}
        return panel_from_arrays(self.inst.key, days, stack["o"], stack["h"], stack["l"], stack["c"], stack["v"],
                                 stack["oi"], partial_last_day_minutes=minutes)


def _nan_arrays() -> dict[str, np.ndarray]:
    return {k: np.full(N1, np.nan) for k in FIELDS}


class Engine:
    def __init__(self, rt: Any, journal: JournalSink | None = None):
        self.rt = rt
        self.s = rt.settings
        self.store = rt.store
        self.journal: JournalSink = journal or NullJournal()
        self.setups: list[Setup] = build_setups(self.s)
        cash, fut = load_costs("cash"), load_costs("futures")
        self.tracker = Tracker(cash, fut if self.s.risk.short_instrument == "futures" else cash)
        self.kill_switch = False
        self.book: StatsBook | None = None
        self.day: date | None = None
        self.mode = "IDLE"
        self.series: dict[str, Series] = {}
        self.index: dict[str, Series] = {}
        self.ideas: dict[str, Idea] = {}
        self.raw_signals: list[dict[str, Any]] = []
        self.rejections: Counter[str] = Counter()
        self.busy: dict[tuple[str, str, str], int] = {}
        self.market: dict[str, Any] = {}
        self.guard: dict[str, Any] = {"allowed": False, "reasons": ["not started"]}
        self.breadth = np.full((1, N5), np.nan)
        self.adv = np.full((1, N5), np.nan)
        self.timings: list[float] = []
        self.excluded: dict[str, str] = {}
        self.last_close_m = 0
        self.sparks: dict[str, dict[str, list[float | None]]] = {}
        self.blackouts = load_blackouts()

    # ------------------------------------------------------------------ day setup
    def book_path(self) -> Path:
        return self.store.base / "research" / "statsbook.json"

    def load_book(self) -> StatsBook | None:
        p = self.book_path()
        if not p.exists():
            return None
        b = StatsBook.load(p)
        live = self.store.base / "research" / "live_feedback.json"
        if live.exists():                                   # live hit rates + demotions (Phase 5)
            fb = json.loads(live.read_text())
            for k, v in fb.get("entries", {}).items():
                if k in b.entries:
                    e = b.entries[k]
                    e.live_n, e.live_hit, e.demoted = v.get("live_n", 0), v.get("live_hit"), bool(v.get("demoted"))
        return b

    def start_day(self, day: date, mode: str) -> None:
        t0 = _time.perf_counter()
        self.day, self.mode = day, mode
        self.book = self.load_book()
        self.ideas, self.raw_signals, self.busy = {}, [], {}
        self.rejections = Counter()
        self.breadth = np.full((1, N5), np.nan)
        self.adv = np.full((1, N5), np.nan)
        self.sparks = {}
        self.last_close_m = 0
        roles = self.rt.roles
        lo = day - timedelta(days=100)
        hi = day - timedelta(days=1)
        nifty_key = roles["nifty"]
        nifty_panel = append_empty_day(panel_from_1m(nifty_key, self.store.read(nifty_key, lo, hi)), day)
        nifty_dc = {d: c for d, c in zip(nifty_panel.days, nifty_panel.dc, strict=True)}
        self.index = {}
        idx_keys = {"nifty": nifty_key, "banknifty": roles.get("banknifty"), "vix": roles.get("vix")}
        for name, key in (roles.get("sectors") or {}).items():
            idx_keys[f"sector:{name}"] = key
        by_key: dict[str, Series] = {}
        for role, key in idx_keys.items():
            if not key or not self.store.has(key):
                continue
            ser = by_key.get(key) or self._init_series(self.rt.inst_by_key[key], day, lo, hi, None)
            if ser is not None:
                by_key[key] = ser                           # one object per instrument (e.g. Bank Nifty = BANK sector)
                self.index[role] = ser
        refs = ReferenceData(self.s.data.root_path / "reference")
        asm = set(refs.asm_gsm().data or [])
        events = {r.get("symbol", "").upper() for r in (refs.events(day).data or [])}
        blk = symbol_blackouts(day, self.blackouts)
        self.series, self.excluded = {}, {}
        for inst in self.rt.instruments:
            if inst.kind != "EQ" or not self.store.has(inst.key):
                continue
            ser = self._init_series(inst, day, lo, hi, nifty_dc)
            if ser is None:
                continue
            if not ser.eligible:
                self.excluded[inst.symbol] = "liquidity/price filter"
            elif self.s.universe.exclude_asm_gsm and inst.symbol in asm:
                ser.eligible, self.excluded[inst.symbol] = False, "ASM/GSM"
            elif self.s.universe.exclude_event_risk and (inst.symbol in events or inst.symbol in blk):
                ser.eligible, self.excluded[inst.symbol] = False, "event risk / blackout"
            self.series[inst.key] = ser
        self.rt.monitor.expected_keys = {k for k, s in self.series.items() if s.eligible}
        self.guard = {"allowed": False, "reasons": ["waiting for first 5-min candle"]}
        self.market = {"universe": sum(1 for s in self.series.values() if s.eligible), "excluded": len(self.excluded),
                       "init_ms": round((_time.perf_counter() - t0) * 1000)}

    def _init_series(self, inst: Instrument, day: date, lo: date, hi: date,
                     nifty_dc: dict[date, float] | None) -> Series | None:
        hist = self.store.read(inst.key, lo, hi)
        if hist.empty:
            return None
        p = panel_from_1m(inst.key, hist)
        pe = append_empty_day(p, day)
        idx_dc = np.array([nifty_dc.get(d, np.nan) for d in pe.days]) if nifty_dc is not None else None
        ctx = daily_context(pe, idx_dc)
        ctx2 = slice_ctx(ctx, slice(-2, None))
        prev = {"o": p.o1[-1], "h": p.h1[-1], "l": p.l1[-1], "c": p.c1[-1], "v": p.v1[-1], "oi": p.oi1[-1]}
        ser = Series(inst, p.days[-1], prev, _nan_arrays(), ctx2)
        if inst.kind == "EQ":
            ser.eligible = bool(eligibility(slice_ctx(ctx, slice(-1, None)), self.s.universe)[0])
            ser.beta = float(ctx["beta"][-1])
            ser.ret20 = ctx["ret20"][-1]
        return ser

    # ------------------------------------------------------------------ events
    def on_tick(self, t: Tick) -> None:
        for idea in self.ideas.values():
            if idea.key == t.key:
                idea.last_price = t.ltp
        if self.mode != "LIVE":
            return
        minute = (t.ts.astimezone(IST).hour * 60 + t.ts.astimezone(IST).minute) - OPEN_MIN
        for idea in [i for i in self.ideas.values() if i.key == t.key and i.status in ("WATCHING", "TRIGGERED", "T1_HIT")]:
            self._apply(idea, self.tracker.on_price(idea, minute, t.ts, t.ltp, self._slip(idea), self._valid_min(idea),
                                                    self._exit_min()))

    def on_minute(self, b: MinuteBatch, health: Health) -> list[dict[str, Any]]:
        if self.day is None:
            return []
        msgs: list[dict[str, Any]] = []
        for k, bar in b.bars.items():
            ser = self.series.get(k) or self._index_by_key(k)
            if ser is None:
                continue
            m = (bar.ts.astimezone(IST).hour * 60 + bar.ts.astimezone(IST).minute) - OPEN_MIN
            if 0 <= m < N1:
                for f, v in zip(FIELDS, (bar.open, bar.high, bar.low, bar.close, bar.volume,
                                         bar.oi if bar.oi is not None else np.nan), strict=True):
                    ser.today[f][m] = v
        # ---- tracking on 1-minute bars (REPLAY, and LIVE as a backstop to ticks) ----
        upd: list[Transition] = []
        for idea in list(self.ideas.values()):
            if idea.status not in ("WATCHING", "TRIGGERED", "T1_HIT"):
                continue
            ibar = b.bars.get(idea.key)
            m_close = self._minute(b.close_ts)
            if ibar is not None:
                m = self._minute(ibar.ts)
                upd += self._apply(idea, self.tracker.on_bar(idea, m, ibar.ts, ibar.open, ibar.high, ibar.low, ibar.close,
                                                             self._slip(idea), self._valid_min(idea), self._exit_min()))
            elif idea.status == "WATCHING" and m_close - 1 > self._valid_min(idea):
                idea.status = "EXPIRED"
                upd.append(Transition(idea.id, "WATCHING", "EXPIRED", b.close_ts.isoformat(), None, "no bar"))
        for tr in upd:
            msgs.append({"type": "idea_update", "id": tr.idea_id, "old": tr.old, "new": tr.new, "ts": tr.ts,
                         "price": tr.price, "note": tr.note, "card": self.card(self.ideas[tr.idea_id])})
        close_m = self._minute(b.close_ts)
        if close_m % 5 == 0 and 5 <= close_m <= N1:
            t0 = _time.perf_counter()
            msgs += self._on_5m(close_m // 5 - 1, close_m, b.close_ts, health)
            ms = (_time.perf_counter() - t0) * 1000
            self.timings.append(ms)
            msgs.append(self.board_msg(ms))
            msgs.append({"type": "market", "sticky": True, **self.market})
        elif upd:
            msgs.append(self.board_msg(None))
        return msgs

    # ------------------------------------------------------------------ 5-minute pipeline
    def _on_5m(self, t: int, close_m: int, close_ts: datetime, health: Health) -> list[dict[str, Any]]:
        assert self.day is not None
        self.last_close_m = close_m
        out: list[dict[str, Any]] = []
        # ---- indices ----
        idxF: dict[str, Series3] = {}
        for role, ser in self.index.items():
            p = ser.panel(self.day, close_m)
            idxF[role] = Series3(p, ser.ctx2, intraday(p, ser.ctx2, is_index=True))
        if "nifty" not in idxF:
            return out
        nifty = idxF["nifty"]
        days = nifty.panel.days
        # ---- stocks: ONE stacked panel for the whole universe (rows 2i = prev day, 2i+1 = today) ----
        keys = [k for k, ser in self.series.items() if ser.eligible]
        sers = [self.series[k] for k in keys]
        S = len(sers)
        if S == 0:
            return out
        stack = {f: np.empty((2 * S, N1)) for f in FIELDS}
        visible = np.empty(2 * S, dtype=int)
        sdays = np.empty(2 * S, dtype=object)
        for si, ser in enumerate(sers):
            for f in FIELDS:
                stack[f][2 * si] = ser.prev[f]
                stack[f][2 * si + 1] = ser.today[f]
            visible[2 * si], visible[2 * si + 1] = N1, close_m
            sdays[2 * si], sdays[2 * si + 1] = ser.prev_day, self.day
        P = panel_from_arrays("*", sdays, stack["o"], stack["h"], stack["l"], stack["c"], stack["v"], stack["oi"],
                              visible=visible)
        ctx = {k: np.concatenate([ser.ctx2[k] for ser in sers], axis=0) for k in sers[0].ctx2}
        F = intraday(P, ctx)
        today = np.arange(1, 2 * S, 2)
        c_t = F["c"][today, t]
        ok = np.isfinite(c_t)
        n = int(ok.sum())
        with np.errstate(invalid="ignore"):
            above = int((ok & (c_t > F["vwap"][today, t])).sum())
            adv = int((ok & (c_t > ctx["pdc"][today])).sum())
            dec = int((ok & (c_t < ctx["pdc"][today])).sum())
        self.breadth[0, t] = above / n if n else np.nan
        self.adv[0, t] = adv / (adv + dec) if adv + dec else np.nan
        # ---- market context (same arithmetic as research MarketBuilder) ----
        D = len(days)
        breadth = np.full((D, N5), np.nan)
        breadth[-1] = self.breadth[0]
        nF = nifty.F
        vix = idxF.get("vix")
        vix_chg = vix.F["ret_pdc"] if vix is not None and len(vix.panel.days) == D else np.zeros_like(nF["c"])
        reg, score = classify(nF, breadth, np.nan_to_num(vix_chg))
        sec = {r.split(":", 1)[1]: s_.F["ret_pdc"] for r, s_ in idxF.items() if r.startswith("sector:") and len(s_.panel.days) == D}
        mkt = MarketContext(days=days, regime=reg, score=score, breadth=breadth, adv_dec=np.full((D, N5), np.nan),
                            nifty_ret=nF["ret_pdc"], nifty_c=nF["c"], nifty_dc=nifty.panel.dc,
                            vix=vix.F["c"] if vix is not None and len(vix.panel.days) == D else np.full_like(nF["c"], np.nan),
                            vix_chg=vix_chg, rv_ratio=nF["rv_ratio"],
                            bank_ret=idxF["banknifty"].F["ret_pdc"] if "banknifty" in idxF else np.full_like(nF["c"], np.nan),
                            sector_ret=sec, sector_rank=sector_ranks(sec))
        regime_name = REGIMES[int(reg[-1, t])] if reg[-1, t] >= 0 else "UNKNOWN"
        # stacked market rows (only "today" rows are used; prev rows stay NaN / -1)
        n_sec = max(1, len(mkt.sector_rank))

        def bcast(row: np.ndarray, fill: float = np.nan) -> np.ndarray:
            a = np.full((2 * S, N5), fill, dtype=float)
            a[today] = row[None, :]
            return a

        regime_st = bcast(reg[-1].astype(float), -1).astype(int)
        idx_ret = bcast(nF["ret_pdc"][-1])
        with np.errstate(invalid="ignore"):
            idx_above = bcast((nF["c"][-1] > nF["vwap"][-1]).astype(float), 0).astype(bool)
        sec_ret = np.full((2 * S, N5), np.nan)
        srank = np.full((2 * S, N5), (n_sec + 1) / 2)
        for si, ser in enumerate(sers):
            sname = ser.inst.sector
            if sname in sec:
                sec_ret[2 * si + 1] = sec[sname][-1]
            if sname in mkt.sector_rank:
                srank[2 * si + 1] = mkt.sector_rank[sname][-1]
        from dalal.features.core import relative_strength
        relative_strength(F, ctx, idx_ret, sec_ret)
        F["_breadth"] = bcast(breadth[-1])
        F["_vix_chg"] = bcast(vix_chg[-1])
        F["_nifty_ret"] = idx_ret
        F["_score"] = bcast(score[-1].astype(float))
        F["_sector_rank"] = srank
        ticks = np.array([[ser.inst.tick_size or 0.05] for ser in sers for _ in (0, 1)])
        sc = SetupContext(F=F, ctx=ctx, regime=regime_st, idx_ret=idx_ret, idx_above_vwap=idx_above, sector_rank=srank,
                          n_sectors=n_sec, tick=ticks)  # type: ignore[arg-type]
        # ---- guardrails ----
        prices = {k: float(ser.today["c"][close_m - 1]) for k, ser in self.series.items()
                  if close_m - 1 < N1 and np.isfinite(ser.today["c"][close_m - 1])}
        pnl = self.journal.pnl_today(str(self.day), self.mode, prices)
        active = [i for i in self.ideas.values() if i.active]
        g = check_guardrails(close_ts, self.s.session, self.s.risk, kill_switch=self.kill_switch,
                             feed_ok=health.signals_allowed, feed_reason="; ".join(health.reasons) or health.state,
                             pnl_today_inr=pnl, open_ideas=len(active), blackouts=self.blackouts)
        self.guard = {"allowed": g.allowed, "reasons": g.reasons, "pnl_today": round(pnl, 2)}
        # ---- setups → candidates (one vectorised evaluation per setup for the whole universe) ----
        cands: list[Idea] = []
        valid_min = min(close_m + self.s.backtest.validity_minutes - 1, self._hhmm_min(self.s.session.idea_expiry) - 1)
        for st in self.setups:
            for side, ss in st.evaluate(sc).items():
                for j in np.where(ss.mask[today, t])[0]:
                    ser, row, key = sers[j], 2 * j + 1, keys[j]
                    self.raw_signals.append({"symbol": ser.inst.symbol, "setup": st.name, "side": side, "t": t,
                                             "trigger": float(ss.trigger[row, t]), "stop": float(ss.stop[row, t])})
                    bk = (key, st.name, side)
                    if self.busy.get(bk, -1) >= close_m:
                        continue
                    snap = snapshot(F, ctx, sc, row, t, side)
                    rs = RawSignal(key, ser.inst.symbol, ser.inst.sector, st.name, st.label, side, str(self.day), t,
                                   close_ts, float(ss.trigger[row, t]), float(ss.stop[row, t]), float(ss.t1[row, t]),
                                   float(ss.t2[row, t]), regime_name,
                                   st.invalidation(side, float(ss.stop[row, t]), snap), snap, ser.beta, ser.inst.lot_size)
                    idea, why = make_candidate(rs, self.book, self.s, self.rt.provenance, self.mode, n_sec)
                    self.busy[bk] = valid_min
                    if idea is None:
                        self.rejections[reason_key(why.split(";")[0])] += 1
                        continue
                    if not g.allowed:
                        self.rejections["guardrail"] += 1
                        continue
                    idea.last_price = float(F["c"][row, t])
                    cands.append(idea)
        feats = {k: (2 * si + 1, ser) for si, (k, ser) in enumerate(zip(keys, sers, strict=True))}
        # ---- board ----
        rets = {k: s.ret20 for k, s in self.series.items() if s.ret20 is not None}
        incumbents = [i for i in self.ideas.values() if i.active]
        dec_ = select_board(incumbents, cands, self.s.board, self.s.risk, corr_from_returns(rets))
        for reason in dec_.rejected.values():
            self.rejections[reason_key(reason)] += 1
        for i in dec_.displaced:
            self.journal.log_transition(i, Transition(i.id, "WATCHING", "DISPLACED", close_ts.isoformat(), None))
            out.append({"type": "idea_update", "id": i.id, "old": "WATCHING", "new": "DISPLACED",
                        "ts": close_ts.isoformat(), "price": None, "note": "displaced by a stronger idea",
                        "card": self.card(i)})
        for i in dec_.added:
            self.ideas[i.id] = i
            i.events.append({"ts": close_ts.isoformat(), "status": "WATCHING", "price": i.last_price, "note": "new"})
            self.journal.log_idea(i)
            out.append({"type": "idea_new", "card": self.card(i)})
        # ---- sparklines for active ideas ----
        for i in self.ideas.values():
            if i.key in feats:
                row = feats[i.key][0]
                cl = F["c"][row, : t + 1]
                vw = F["vwap"][row, : t + 1]
                self.sparks[i.id] = {"c": [None if not np.isfinite(x) else round(float(x), 2) for x in cl],
                                     "vwap": [None if not np.isfinite(x) else round(float(x), 2) for x in vw]}
        self._market_snapshot(idxF, F, feats, t, regime_name, float(score[-1, t]), mkt)
        return out

    # ------------------------------------------------------------------ helpers
    def _apply(self, idea: Idea, trs: list[Transition]) -> list[Transition]:
        for tr in trs:
            self.journal.log_transition(idea, tr)
            if tr.new in ("STOPPED", "T2_HIT", "CLOSED", "EXPIRED", "INVALIDATED"):
                self.busy[(idea.key, idea.setup, idea.side)] = self._minute(datetime.fromisoformat(tr.ts))
        return trs

    def _index_by_key(self, key: str) -> Series | None:
        for s in self.index.values():
            if s.inst.key == key:
                return s
        return None

    @staticmethod
    def _minute(ts: datetime) -> int:
        t = ts.astimezone(IST)
        return t.hour * 60 + t.minute - OPEN_MIN

    @staticmethod
    def _hhmm_min(hhmm: str) -> int:
        t = parse_hhmm(hhmm)
        return t.hour * 60 + t.minute - OPEN_MIN

    def _valid_min(self, idea: Idea) -> int:
        return self._minute(datetime.fromisoformat(idea.valid_until)) - 1

    def _exit_min(self) -> int:
        return self._hhmm_min(self.s.session.resolve_at)

    def _slip(self, idea: Idea) -> float:
        ser = self.series.get(idea.key)
        sig = ser.ctx2["sigma_d"][-1] / np.sqrt(N1) if ser is not None and np.isfinite(ser.ctx2["sigma_d"][-1]) else 0.001
        sl = self.s.backtest.slippage
        return slippage(idea.entry, sig, sl.spread_bps, sl.k_vol, 0.05)

    def card(self, i: Idea) -> dict[str, Any]:
        c = i.card()
        c["spark"] = self.sparks.get(i.id)
        c["events"] = i.events[-6:]
        return c

    def board_msg(self, engine_ms: float | None) -> dict[str, Any]:
        active = [i for i in self.ideas.values() if i.active]
        longs = sorted([i for i in active if i.side == "long"], key=lambda x: -x.cons_ev)
        shorts = sorted([i for i in active if i.side == "short"], key=lambda x: -x.cons_ev)
        resolved = sorted([i for i in self.ideas.values() if not i.active], key=lambda x: x.status_ts, reverse=True)
        b = self.book
        return {
            "type": "board", "sticky": True, "day": str(self.day), "mode": self.mode,
            "as_of": bar5_close_hhmm(self.last_close_m // 5 - 1) if self.last_close_m >= 5 else None,
            "longs": [self.card(i) for i in longs], "shorts": [self.card(i) for i in shorts],
            "resolved": [self.card(i) for i in resolved[:30]],
            "guard": self.guard, "rejections": dict(self.rejections.most_common(8)),
            "raw_signals_today": len(self.raw_signals),
            "book": None if b is None else {"provenance": b.provenance, "created": b.created,
                                            "data_range": list(b.data_range),
                                            "in_sample_day": bool(self.day is not None and b.data_range[1] and
                                                                  str(self.day) <= str(b.data_range[1])),
                                            "model_version": b.model_version, "qualified": b.qualified_setups(),
                                            "demo_bypass": bool(self.s.board.demo_bypass_gates and
                                                                self.rt.provenance == "synthetic")},
            "engine_ms": None if engine_ms is None else round(engine_ms, 1),
            "provenance": self.rt.provenance,
        }

    def _market_snapshot(self, idxF: dict[str, Series3], F: Features, feats: dict[str, tuple[int, Series]], t: int,
                         regime: str, score: float, mkt: MarketContext) -> None:
        def last(s: Series3 | None) -> dict[str, Any] | None:
            if s is None:
                return None
            c, r = s.F["c"][-1, t], s.F["ret_pdc"][-1, t]
            return {"ltp": None if not np.isfinite(c) else round(float(c), 2),
                    "chg_pct": None if not np.isfinite(r) else round(float(r) * 100, 2)}

        sectors = []
        for r, s in idxF.items():
            if r.startswith("sector:"):
                name = r.split(":", 1)[1]
                ret = s.F["ret_pdc"][-1, t]
                rank = mkt.sector_rank.get(name)
                sectors.append({"name": name, "ret_pct": None if not np.isfinite(ret) else round(float(ret) * 100, 2),
                                "rank": None if rank is None else int(rank[-1, t]),
                                "ret_open_pct": None if not np.isfinite(s.F["ret_open"][-1, t]) else
                                round(float(s.F["ret_open"][-1, t]) * 100, 2)})
        movers: list[dict[str, Any]] = []
        for _key, (row, ser) in feats.items():
            rv, r, z = F["rvol_cum"][row, t], F["ret_pdc"][row, t], F["rs_mkt_z"][row, t]
            if np.isfinite(rv) and np.isfinite(r):
                movers.append({"symbol": ser.inst.symbol, "sector": ser.inst.sector, "rvol": round(float(rv), 2),
                               "chg_pct": round(float(r) * 100, 2), "ltp": round(float(F["c"][row, t]), 2),
                               "rs_z": None if not np.isfinite(z) else round(float(z), 2)})
        movers.sort(key=lambda x: -x["rvol"])
        adv = round(float(self.adv[0, t]) * 100) if np.isfinite(self.adv[0, t]) else None
        self.market.update({
            "as_of": bar5_close_hhmm(t), "regime": regime, "regime_score": None if not np.isfinite(score) else score,
            "breadth_pct": None if not np.isfinite(self.breadth[0, t]) else round(float(self.breadth[0, t]) * 100, 1),
            "adv_pct": adv,
            "breadth_series": [None if not np.isfinite(x) else round(float(x) * 100, 1) for x in self.breadth[0, : t + 1]],
            "nifty": last(idxF.get("nifty")), "banknifty": last(idxF.get("banknifty")), "vix": last(idxF.get("vix")),
            "usdinr": None,
            "sectors": sorted(sectors, key=lambda x: (x["rank"] or 99)),
            "sector_rs_series": {r.split(":", 1)[1]: [None if not (np.isfinite(a) and np.isfinite(b)) else round(float(a - b) * 100, 3)
                                                      for a, b in zip(s.F["ret_pdc"][-1, : t + 1],
                                                                      idxF["nifty"].F["ret_pdc"][-1, : t + 1], strict=True)]
                                 for r, s in idxF.items() if r.startswith("sector:")},
            "volume_shockers": movers[:10],
            "rs_leaders": sorted([m for m in movers if m["rs_z"] is not None], key=lambda x: -x["rs_z"])[:5],
            "rs_laggards": sorted([m for m in movers if m["rs_z"] is not None], key=lambda x: x["rs_z"])[:5],
        })


def attach_engine(rt: Any) -> Engine:
    eng = Engine(rt)
    rt.engine = eng
    return eng
