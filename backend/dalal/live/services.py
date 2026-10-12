"""Session services around the engine: journal, Telegram, scheduled notes, live feedback."""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from pathlib import Path
from typing import Any

from dalal.clock import IST, now_ist, parse_hhmm
from dalal.config import get_secrets
from dalal.journal.db import Journal
from dalal.journal.performance import divergence, performance, write_feedback
from dalal.log import get_logger
from dalal.notify.notes import eod_report, morning_note, save_note
from dalal.notify.telegram import Telegram, format_idea, format_update

log = get_logger("services")


class Services:
    def __init__(self, rt: Any):
        self.rt = rt
        self.s = rt.settings
        base: Path = rt.store.base
        self.journal = Journal(self.s.data.root_path / "journal.sqlite")
        self.notes_dir = base / "notes"
        self.feedback_path = base / "research" / "live_feedback.json"
        sec = get_secrets()
        self.telegram = Telegram(sec.telegram_bot_token.get_secret_value() if sec.telegram_bot_token else None,
                                 sec.telegram_chat_id) if self.s.notify.telegram else Telegram(None, None)
        self._task: asyncio.Task[None] | None = None
        self._sched: Any = None

    # ------------------------------------------------------------------ lifecycle
    def attach(self) -> None:
        if self.rt.engine is not None:
            self.rt.engine.journal = self.journal
        self.rt.on_session_end = self.on_session_end
        self.rt.on_session_start = self.on_session_start

    async def start(self) -> None:
        self._task = asyncio.create_task(self._forward())
        try:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
            from apscheduler.triggers.cron import CronTrigger

            sched = AsyncIOScheduler(timezone="Asia/Kolkata")
            m, e = parse_hhmm(self.s.notify.morning_note_at), parse_hhmm(self.s.notify.eod_report_at)
            sched.add_job(self.scheduled_morning, CronTrigger(day_of_week="mon-fri", hour=m.hour, minute=m.minute))
            sched.add_job(self.scheduled_eod, CronTrigger(day_of_week="mon-fri", hour=e.hour, minute=e.minute))
            sched.start()
            self._sched = sched
        except Exception as ex:  # scheduler is a convenience; never block the app
            log.warning("scheduler_unavailable", error=repr(ex))

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        if self._sched:
            self._sched.shutdown(wait=False)

    # ------------------------------------------------------------------ bus → Telegram
    async def _forward(self) -> None:
        q = self.rt.bus.subscribe()
        try:
            while True:
                m = await q.get()
                if not self.telegram.enabled or self.rt.mode != "LIVE":
                    continue                                   # alerts only for LIVE sessions (no replay spam)
                if m.get("type") == "idea_new":
                    await self.telegram.send(format_idea(m["card"]))
                elif m.get("type") == "idea_update" and m.get("new") in ("TRIGGERED", "T1_HIT", "T2_HIT", "STOPPED"):
                    await self.telegram.send(format_update(m["card"], m["new"]))
        except asyncio.CancelledError:
            pass
        finally:
            self.rt.bus.unsubscribe(q)

    # ------------------------------------------------------------------ notes
    def live_perf(self) -> dict[str, Any]:
        return performance(self.journal.ideas(mode="LIVE", provenance=self.rt.provenance))

    def make_morning(self, day: date) -> dict[str, Any]:
        eng = self.rt.engine
        book = eng.load_book() if eng is not None else None
        note = morning_note(self.s, self.rt.store, day, book, self.live_perf())
        save_note(self.notes_dir, note)
        return note

    def make_eod(self, day: date, mode: str) -> dict[str, Any]:
        rows = self.journal.ideas(mode=mode, provenance=self.rt.provenance, include_demo=True)
        perf = performance([r for r in rows if not r["gate_bypassed"]])
        div = self.update_feedback() if mode == "LIVE" else None
        eng = self.rt.engine
        note = eod_report(day, mode, rows, perf, eng.market if eng is not None else None, self.rt.provenance, div)
        save_note(self.notes_dir, note)
        return note

    def update_feedback(self) -> dict[str, Any]:
        """Live forward results → live hit rates and demotions the engine applies to the stats book."""
        eng = self.rt.engine
        book = eng.load_book() if eng is not None else None
        if book is None:
            return {}
        rows = self.journal.ideas(mode="LIVE", provenance=self.rt.provenance)
        fb = divergence(rows, {k: v.as_dict() for k, v in book.entries.items()})
        write_feedback(self.feedback_path, fb)
        return fb

    async def scheduled_morning(self) -> None:
        d = now_ist().date()
        if not self.rt.cal.is_trading_day(d):
            return
        note = await asyncio.to_thread(self.make_morning, d)
        self.rt.bus.publish({"type": "note", **{k: note[k] for k in ("kind", "day", "text")}})
        await self.telegram.send(note["text"])

    async def scheduled_eod(self) -> None:
        d = now_ist().date()
        if not self.rt.cal.is_trading_day(d) or self.rt.mode != "LIVE":
            return
        note = await asyncio.to_thread(self.make_eod, d, "LIVE")
        self.rt.bus.publish({"type": "note", **{k: note[k] for k in ("kind", "day", "text")}})
        await self.telegram.send(note["text"])

    def on_session_start(self, day: date, mode: str) -> None:
        if mode == "REPLAY":
            try:
                self.make_morning(day)
            except Exception as e:
                log.warning("morning_note_failed", error=repr(e))

    def on_session_end(self, day: date | None, mode: str) -> None:
        if day is None:
            return
        try:
            note = self.make_eod(day, mode)
            self.rt.bus.publish({"type": "note", **{k: note[k] for k in ("kind", "day", "text")}})
        except Exception as e:
            log.warning("eod_failed", error=repr(e))

    def resolve_stale(self, today: date) -> int:
        """Ideas left unresolved from earlier days (e.g. the server stopped mid-session) are
        resolved from stored 1-minute bars with the backtester's rules; if bars are missing,
        they are marked UNRESOLVED_NO_DATA rather than guessed."""
        from dalal.backtest.costs import load_costs
        from dalal.backtest.simulate import simulate
        from dalal.features.panel import N1, OPEN_MIN, panel_from_1m

        n = 0
        cash = load_costs("cash")
        for r in self.journal.unresolved(str(today)):
            d = date.fromisoformat(r["day"])
            df = self.rt.store.read_day(r["key"], d)
            if df.empty:
                self.journal.force_resolve(r["id"], "EXPIRED", "no stored bars to resolve: outcome unknown", None, None)
                n += 1
                continue
            p = panel_from_1m(r["key"], df)
            sig = datetime.fromisoformat(r["signal_ts"])
            start = sig.hour * 60 + sig.minute - OPEN_MIN
            vu = datetime.fromisoformat(r["valid_until"])
            f = simulate(r["side"], p.o1[0], p.h1[0], p.l1[0], p.c1[0], start, r["entry"], r["stop"], r["t1"], r["t2"],
                         vu.hour * 60 + vu.minute - OPEN_MIN - 1, min(N1 - 15, 360), 0.05, 0.0, cash,
                         self.s.risk.capital, self.s.risk.risk_per_trade_pct)
            status = "EXPIRED" if not f.triggered else ("T2_HIT" if f.exit_reason == "T1_T2" else
                                                         "STOPPED" if f.exit_reason == "STOP" else "CLOSED")
            self.journal.force_resolve(r["id"], status, "resolved after restart from stored bars",
                                       f.r_net if f.triggered else None, int(f.win) if f.triggered else None,
                                       None, f.entry_px if f.triggered else None)
            n += 1
        return n


def ist_today() -> date:
    return datetime.now(tz=IST).date()
