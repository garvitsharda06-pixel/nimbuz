from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from dalal.data.models import MinuteBatch
from dalal.data.sources import ReplaySource


async def _collect(src: ReplaySource) -> list[MinuteBatch]:
    out = []
    async for ev in src.stream():
        if isinstance(ev, MinuteBatch):
            out.append(ev)
    return out


async def test_replay_full_day_in_order(syn_store):
    insts, roles = syn_store.instruments()
    day = syn_store.days(roles["nifty"])[5]
    keys = [i.key for i in insts]
    batches = await _collect(ReplaySource(syn_store, day, keys, speed=0))
    assert len(batches) == 375
    closes = [b.close_ts for b in batches]
    assert closes == sorted(closes)
    assert closes[0].strftime("%H:%M") == "09:16" and closes[-1].strftime("%H:%M") == "15:30"
    assert all(len(b.bars) == len(keys) for b in batches)


async def test_replay_is_deterministic(syn_store):
    insts, roles = syn_store.instruments()
    day = syn_store.days(roles["nifty"])[3]
    keys = [i.key for i in insts]
    a = await _collect(ReplaySource(syn_store, day, keys, speed=0))
    b = await _collect(ReplaySource(syn_store, day, keys, speed=0))
    assert [(x.close_ts, sorted((k, v.close) for k, v in x.bars.items())) for x in a] == \
           [(x.close_ts, sorted((k, v.close) for k, v in x.bars.items())) for x in b]


async def test_replay_speed_30x_paces_two_seconds_per_minute(syn_store):
    _insts, roles = syn_store.instruments()
    day = syn_store.days(roles["nifty"])[2]
    src = ReplaySource(syn_store, day, [roles["nifty"]], speed=30, emit_ticks=True)
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    n = 0
    async for ev in src.stream():
        if isinstance(ev, MinuteBatch):
            n += 1
            if n == 2:
                break
    assert 3.6 <= loop.time() - t0 <= 5.0      # 2 minutes at 30x ≈ 4 s


def test_api_replay_streams_minutes_over_ws(settings):
    from dalal.api.app import create_app

    app = create_app(settings)
    with TestClient(app) as c:
        days = c.get("/api/days").json()
        assert days["provenance"] == "synthetic" and len(days["days"]) == 40
        with c.websocket_connect("/ws") as ws:
            r = c.post("/api/replay/start", json={"date": days["days"][4], "speed": 0})
            assert r.status_code == 200 and r.json()["mode"] == "REPLAY"
            seen = 0
            for _ in range(5000):
                import orjson
                m = orjson.loads(ws.receive_bytes())
                if m["type"] == "minute":
                    seen += 1
                if seen >= 20:
                    break
            assert seen >= 20
        c.post("/api/stop")
        bad = c.post("/api/replay/start", json={"date": "1999-01-01", "speed": 0})
        assert bad.status_code == 400
        live = c.post("/api/live/start")
        assert live.status_code == 400 and "synthetic" in live.json()["detail"]
