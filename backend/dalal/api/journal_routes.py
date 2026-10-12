"""Journal, paper trading, performance and notes routes (Phase 5)."""

from __future__ import annotations

from datetime import date as Date
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from dalal.clock import now_ist
from dalal.journal.performance import performance
from dalal.live.runtime import Runtime
from dalal.notify.notes import list_notes


class ManualReq(BaseModel):
    day: str
    symbol: str
    side: str
    qty: int
    entry: float
    stop: float | None = None
    exit: float | None = None
    notes: str = ""


def register(app: FastAPI, rt: Runtime) -> None:
    def svc() -> Any:
        s = getattr(rt, "services", None)
        if s is None:
            raise HTTPException(503, "services not started")
        return s

    @app.post("/api/paper/{idea_id}")
    def take_paper(idea_id: str) -> dict[str, Any]:
        eng = rt.engine
        if eng is None or idea_id not in eng.ideas:
            raise HTTPException(404, "unknown or expired idea")
        try:
            out = svc().journal.take_paper(eng.ideas[idea_id], rt.clock())
            eng.ideas[idea_id].paper = True
            return out
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/api/manual")
    def manual(req: ManualReq) -> dict[str, Any]:
        if req.side not in ("long", "short"):
            raise HTTPException(400, "side must be long or short")
        return {"id": svc().journal.add_manual(req.model_dump())}

    @app.get("/api/journal")
    def journal(mode: str | None = None, day: str | None = None, include_demo: bool = False) -> dict[str, Any]:
        j = svc().journal
        return {"ideas": j.ideas(mode=mode, day=day, include_demo=include_demo, provenance=rt.provenance),
                "paper": j.paper_trades(mode), "manual": j.manual_entries()}

    @app.get("/api/journal/{idea_id}/events")
    def events(idea_id: str) -> dict[str, Any]:
        return {"events": svc().journal.events(idea_id)}

    @app.get("/api/performance")
    def perf(mode: str = "LIVE", include_demo: bool = False) -> dict[str, Any]:
        rows = svc().journal.ideas(mode=mode, include_demo=include_demo, provenance=rt.provenance)
        out = performance(rows)
        out["mode"] = mode
        out["include_demo"] = include_demo
        out["provenance"] = rt.provenance
        fb = svc().feedback_path
        if fb.exists():
            import json
            out["feedback"] = json.loads(fb.read_text())
        return out

    @app.get("/api/notes")
    def notes() -> dict[str, Any]:
        return {"notes": list_notes(svc().notes_dir)}

    @app.post("/api/notes/generate")
    async def gen(kind: str = "morning", day: Date | None = None, mode: str = "REPLAY") -> dict[str, Any]:
        import asyncio
        d = day or (rt.replay_day if rt.mode == "REPLAY" and rt.replay_day else now_ist().date())
        if kind == "morning":
            return await asyncio.to_thread(svc().make_morning, d)
        return await asyncio.to_thread(svc().make_eod, d, mode)
