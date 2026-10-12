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

    @app.post("/api/setups/{name}")
    def toggle_setup(name: str, enabled: bool) -> dict[str, Any]:
        """Enable/disable a setup (persisted to config/settings.local.yaml, applied to the engine now).
        A setup still has to pass the gate to reach the board."""
        from dalal.config import save_local_override
        from dalal.setups.library import SETUP_BY_NAME, build_setups

        if name not in SETUP_BY_NAME:
            raise HTTPException(404, "unknown setup")
        save_local_override({"setups": {name: {"enabled": enabled}}})
        cur = rt.settings.setups.get(name)
        from dalal.config import SetupToggle
        rt.settings.setups[name] = SetupToggle(enabled=enabled, params=cur.params if cur else {})
        if rt.engine is not None:
            rt.engine.setups = build_setups(rt.settings)
        return {"setup": name, "enabled": enabled, "active": [s.name for s in build_setups(rt.settings)]}

    @app.get("/api/setups")
    def setups() -> dict[str, Any]:
        from dalal.setups.library import ALL_SETUPS
        return {"setups": [{"name": c.name, "label": c.label, "enabled": rt.settings.setup_enabled(c.name)} for c in ALL_SETUPS]}

    @app.get("/api/rotation/daily")
    def rotation_daily(lookback: int = 10, tail: int = 5) -> dict[str, Any]:
        """Daily relative-rotation (JdK-style): RS = sector/Nifty; RS-ratio = 100·RS/SMA(RS, n);
        RS-momentum = 100·ratio/ratio[n-days-ago smoothing]. Last `tail` days per sector."""
        import numpy as np
        roles = rt.roles or {}
        nk = roles.get("nifty")
        if not nk:
            return {"sectors": []}
        nd = rt.store.daily(nk).set_index("date")["close"]
        if rt.mode == "REPLAY" and rt.replay_day is not None:
            nd = nd[nd.index < rt.replay_day]           # never peek past the replay day
        out = []
        for name, key in (roles.get("sectors") or {}).items():
            if not rt.store.has(key):
                continue
            sd = rt.store.daily(key).set_index("date")["close"].reindex(nd.index)
            rs = (sd / nd).to_numpy(float)
            if len(rs) < lookback * 3:
                continue
            sma = np.convolve(rs, np.ones(lookback) / lookback, mode="full")[: len(rs)]
            ratio = 100 * rs / sma
            ratio[: lookback - 1] = np.nan
            mom = 100 * ratio / np.roll(ratio, lookback // 2)
            mom[: lookback + lookback // 2] = np.nan
            pts = [[round(float(a), 3), round(float(b), 3)] for a, b in zip(ratio[-tail:], mom[-tail:], strict=True)
                   if np.isfinite(a) and np.isfinite(b)]
            out.append({"name": name, "tail": pts, "asof": str(nd.index[-1])})
        return {"sectors": out, "lookback": lookback}

    try:  # phase-5 routes (journal, paper trading, notes) if present
        from dalal.api import journal_routes
        journal_routes.register(app, rt)
    except ImportError:
        pass
