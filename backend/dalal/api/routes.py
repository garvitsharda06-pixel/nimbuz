"""Board, idea, research and settings routes."""

from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI, HTTPException

from dalal.live.runtime import Runtime
from dalal.scoring.similar import SimilarIndex
from dalal.scoring.statsbook import StatsBook


def register(app: FastAPI, rt: Runtime) -> None:
    research = rt.store.base / "research"
    similar = SimilarIndex(research / "instances.parquet")

    @app.get("/api/board")
    def board() -> dict[str, Any]:
        eng = rt.engine
        if eng is None or eng.day is None:
            return {"type": "board", "longs": [], "shorts": [], "resolved": [], "guard": {"allowed": False,
                                                                                        "reasons": ["idle"]}}
        return eng.board_msg(None)

    @app.get("/api/market")
    def market() -> dict[str, Any]:
        eng = rt.engine
        return {} if eng is None else eng.market

    @app.get("/api/idea/{idea_id}")
    def idea(idea_id: str) -> dict[str, Any]:
        eng = rt.engine
        if eng is None or idea_id not in eng.ideas:
            raise HTTPException(404, "unknown idea")
        i = eng.ideas[idea_id]
        entry = None
        if eng.book is not None:
            e = eng.book.entries.get(StatsBook.key(i.setup, i.side, i.stats_level or "*"))
            entry = e.as_dict() if e else None
        t = int(i.id.rsplit("-", 1)[-1])
        return {"card": eng.card(i), "stats": entry,
                "similar": similar.nearest(i.setup, i.side, i.snapshot, t, i.rr1, before_day=i.day)}

    @app.get("/api/research/summary")
    def research_summary() -> dict[str, Any]:
        p = research / "summary.json"
        if not p.exists():
            return {"available": False, "provenance": rt.provenance}
        return {"available": True, **json.loads(p.read_text())}

    @app.get("/api/research/tearsheet/{setup}")
    def tearsheet(setup: str) -> dict[str, Any]:
        p = research / "tearsheets" / f"{setup}.json"
        if not p.exists():
            raise HTTPException(404, "no tearsheet; run `dalal backtest`")
        return json.loads(p.read_text())

    @app.get("/api/research/statsbook")
    def statsbook() -> dict[str, Any]:
        p = research / "statsbook.json"
        if not p.exists():
            return {"available": False}
        return {"available": True, **json.loads(p.read_text())}

    @app.get("/api/settings")
    def settings() -> dict[str, Any]:
        from dalal.config import get_secrets

        sec = get_secrets()
        s = rt.settings
        return {
            "settings": s.model_dump(mode="json"), "config_hash": s.config_hash(),
            "secrets": {"upstox_token": sec.upstox_analytics_token is not None,
                        "telegram": sec.telegram_bot_token is not None and bool(sec.telegram_chat_id)},
            "provenance": rt.provenance,
        }

    try:  # phase-5 routes (journal, paper trading, notes) if present
        from dalal.api import journal_routes  # type: ignore[attr-defined]
        journal_routes.register(app, rt)
    except ImportError:
        pass
