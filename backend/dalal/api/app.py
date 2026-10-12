"""FastAPI app: REST + WebSocket. Binds to 127.0.0.1 by default (see config.api)."""

from __future__ import annotations

import asyncio
from datetime import date as Date
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import orjson
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from dalal.config import Settings, get_settings
from dalal.data.candles import resample
from dalal.live.runtime import Runtime
from dalal.log import get_logger

STATIC = Path(__file__).parent / "static"
_OPTS = orjson.OPT_SERIALIZE_NUMPY | orjson.OPT_NON_STR_KEYS


def _default(o: Any) -> Any:
    if isinstance(o, (np.generic,)):
        return o.item()
    if isinstance(o, (Date, datetime)):
        return o.isoformat()
    if isinstance(o, (set, tuple)):
        return list(o)
    return str(o)


def dumps(m: Any) -> bytes:
    """WebSocket/JSON encoder: numpy scalars & arrays, dates, NaN → null."""
    return orjson.dumps(m, option=_OPTS, default=_default)


class NumpyJSONResponse(JSONResponse):
    def render(self, content: Any) -> bytes:
        return dumps(content)


class ReplayReq(BaseModel):
    date: Date | None = None
    speed: float | None = None
    start_time: str | None = None


class ControlReq(BaseModel):
    speed: float | None = None
    paused: bool | None = None


class KillReq(BaseModel):
    on: bool


def create_app(settings: Settings | None = None, runtime: Runtime | None = None) -> FastAPI:
    settings = settings or get_settings()
    rt = runtime or Runtime(settings)
    app = FastAPI(title="Dalal Desk", default_response_class=NumpyJSONResponse)
    app.state.rt = rt
    app.add_middleware(CORSMiddleware, allow_origins=settings.api.cors_origins, allow_methods=["*"],
                       allow_headers=["*"])

    if rt.engine is None:
        from dalal.live.engine import attach_engine
        attach_engine(rt)
    if rt.services is None:
        from dalal.live.services import Services
        rt.services = Services(rt)
        rt.services.attach()

    from dalal.api import routes
    routes.register(app, rt)

    @app.get("/api/status")
    def status() -> dict[str, Any]:
        return rt.status()

    @app.get("/api/days")
    def days() -> dict[str, Any]:
        return {"days": [str(d) for d in rt.stored_days()], "provenance": rt.provenance}

    @app.get("/api/instruments")
    def instruments() -> dict[str, Any]:
        return {"instruments": [i.to_dict() for i in rt.instruments], "roles": rt.roles}

    @app.get("/api/candles")
    def candles(key: str, day: Date | None = None, tf: int = 5) -> dict[str, Any]:
        if key not in rt.inst_by_key:
            raise HTTPException(404, "unknown instrument")
        d = day or rt.replay_day or (rt.stored_days()[-1] if rt.stored_days() else None)
        if d is None:
            return {"key": key, "bars": []}
        session_day = rt.replay_day if rt.mode == "REPLAY" else (rt.clock().date() if rt.mode == "LIVE" else None)
        if d == session_day:
            from dalal.data.models import bars_to_frame
            df = bars_to_frame(rt.day_bars.get(key, []))      # only what the session has "seen" so far
        else:
            df = rt.store.read_day(key, d)
        if tf > 1 and not df.empty:
            df = resample(df, tf, complete_only=False)
        return {"key": key, "day": str(d), "tf": tf, "bars": [
            [int(t.timestamp()), o, h, lo, c, v] for t, o, h, lo, c, v in
            zip(df["ts"], df["open"], df["high"], df["low"], df["close"], df["volume"], strict=True)]}

    @app.post("/api/replay/start")
    async def replay_start(req: ReplayReq) -> dict[str, Any]:
        try:
            return await rt.start_replay(req.date, req.speed, req.start_time)
        except (ValueError, RuntimeError) as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/api/replay/control")
    def replay_control(req: ControlReq) -> dict[str, Any]:
        return rt.control(req.speed, req.paused)

    @app.post("/api/live/start")
    async def live_start() -> dict[str, Any]:
        try:
            return await rt.start_live()
        except RuntimeError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/api/stop")
    async def stop() -> dict[str, Any]:
        await rt.stop()
        rt.publish_status()
        return rt.status()

    @app.post("/api/kill")
    def kill(req: KillReq) -> dict[str, Any]:
        rt.set_kill_switch(req.on)
        return rt.status()

    @app.websocket("/ws")
    async def ws(sock: WebSocket) -> None:
        await sock.accept()
        q = rt.bus.subscribe()
        try:
            for m in list(rt.bus.last.values()):
                await sock.send_bytes(dumps(m))
            await sock.send_bytes(dumps({"type": "status", **rt.status()}))
            while True:
                m = await q.get()
                await sock.send_bytes(dumps(m))
        except (WebSocketDisconnect, asyncio.CancelledError, RuntimeError):
            pass
        except Exception as e:  # never kill the socket silently
            get_logger("ws").error("ws_send_failed", error=repr(e)[:300])
        finally:
            rt.bus.unsubscribe(q)

    @app.get("/debug")
    def debug_page() -> FileResponse:
        return FileResponse(STATIC / "debug.html")

    import os

    dist = Path(os.environ.get("DALAL_UI_DIST", Path(__file__).resolve().parents[3] / "frontend" / "dist"))
    if dist.exists():
        from fastapi.staticfiles import StaticFiles
        app.mount("/", StaticFiles(directory=dist, html=True), name="ui")

    @app.on_event("startup")
    async def _startup() -> None:
        from dalal.clock import now_ist
        await rt.services.start()
        if rt.provenance != "synthetic":
            n = rt.services.resolve_stale(now_ist().date())
            if n:
                get_logger("app").info("resolved_stale_ideas", n=n)

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        await rt.stop()
        await rt.services.stop()

    return app
