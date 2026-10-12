"""Runtime: owns the active session (LIVE or REPLAY), the feed monitor, the engine and the bus.

Live and replay use the same `_run` loop; only the event source differs (§6 replay provider).
"""

from __future__ import annotations

import asyncio
import contextlib
import time as _time
from datetime import date, datetime
from typing import Any

from dalal.clock import IST, SessionTimes, TradingCalendar, market_status, now_ist
from dalal.config import Settings, get_secrets
from dalal.data.models import Bar, Instrument, MinuteBatch, Tick
from dalal.data.quality import FeedMonitor
from dalal.data.sources import ReplaySource, TickMinuteSource
from dalal.data.store import CandleStore
from dalal.live.bus import Bus
from dalal.log import get_logger

log = get_logger("runtime")


class Runtime:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.bus = Bus()
        self.provenance = settings.data.provider
        self.store = CandleStore(settings.data.root_path, self.provenance)
        self.instruments: list[Instrument]
        self.instruments, self.roles = self.store.instruments()
        self.inst_by_key = {i.key: i for i in self.instruments}
        self.sess = SessionTimes.from_cfg(settings.session)
        self.cal = TradingCalendar.from_config()
        self.mode: str = "IDLE"
        self.source: ReplaySource | TickMinuteSource | None = None
        self.task: asyncio.Task[None] | None = None
        self.monitor = FeedMonitor(settings.data.stale_seconds, settings.data.max_clock_drift_ms)
        self.engine: Any = None                       # set by the app (dalal.live.engine.Engine)
        self.ltp: dict[str, float] = {}
        self.day_bars: dict[str, list[Bar]] = {}
        self.replay_day: date | None = None
        self._last_price_push = 0.0
        self._dirty: set[str] = set()
        self.kill_switch = False
        self.services: Any = None
        self.on_session_start: Any = None          # callable(day, mode), set by Services
        self.on_session_end: Any = None

    # ------------------------------------------------------------------ control
    def stored_days(self) -> list[date]:
        nifty = self.roles.get("nifty") if self.roles else None
        return self.store.days(nifty) if nifty else []

    def stream_keys(self) -> list[str]:
        keys = [i.key for i in self.instruments if i.kind in ("EQ", "INDEX")]
        return [k for k in keys if self.store.has(k)] if self.mode == "REPLAY" else keys

    async def start_replay(self, day: date | None = None, speed: float | None = None,
                           start_time: str | None = None) -> dict[str, Any]:
        await self.stop()
        days = self.stored_days()
        if not days:
            raise RuntimeError("no stored data: run `make synth` or `make download-data` first")
        day = day or self.settings.replay.date or days[-1]
        if day not in days:
            raise ValueError(f"{day} is not a stored trading day")
        self.mode = "REPLAY"
        self.replay_day = day
        self._reset_day()
        self.source = ReplaySource(self.store, day, self.stream_keys(),
                                   speed=self.settings.replay.speed if speed is None else speed,
                                   start_time=start_time)
        self.monitor = FeedMonitor(self.settings.data.stale_seconds, self.settings.data.max_clock_drift_ms)
        self.monitor.expected_keys = {k for k in self.stream_keys() if self.inst_by_key[k].kind == "EQ"}
        if self.engine is not None:
            self.engine.start_day(day, mode="REPLAY")
        if self.on_session_start is not None:
            await asyncio.to_thread(self.on_session_start, day, "REPLAY")
        self.task = asyncio.create_task(self._run())
        self.publish_status()
        return self.status()

    async def start_live(self) -> dict[str, Any]:
        await self.stop()
        if self.provenance != "upstox":
            raise RuntimeError("LIVE mode requires real-time provider data (provenance=upstox); synthetic is refused (D-002)")
        token = get_secrets().upstox_analytics_token
        if token is None:
            raise RuntimeError("UPSTOX_ANALYTICS_TOKEN missing in .env")
        from dalal.data.upstox import UpstoxFeed

        feed = UpstoxFeed(self.settings.data.upstox, token.get_secret_value())
        self.mode = "LIVE"
        self.replay_day = None
        self._reset_day()
        self.source = TickMinuteSource(feed.ticks(self.stream_keys()), self.settings.data.bar_close_grace_ms,
                                       status=feed.status)
        self.monitor.expected_keys = {i.key for i in self.instruments if i.kind == "EQ"}
        if self.engine is not None:
            self.engine.start_day(now_ist().date(), mode="LIVE")
        self.task = asyncio.create_task(self._run())
        self.publish_status()
        return self.status()

    async def stop(self) -> None:
        if isinstance(self.source, ReplaySource):
            self.source.stopped = True
        if self.task and not self.task.done():
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self.task
        self.task = None
        self.source = None
        self.mode = "IDLE"

    def control(self, speed: float | None = None, paused: bool | None = None) -> dict[str, Any]:
        if isinstance(self.source, ReplaySource):
            if speed is not None:
                self.source.speed = max(0.0, min(60.0, speed)) if speed > 0 else 0.0
            if paused is not None:
                self.source.paused = paused
        self.publish_status()
        return self.status()

    def set_kill_switch(self, on: bool) -> None:
        self.kill_switch = on
        if self.engine is not None:
            self.engine.kill_switch = on
        self.publish_status()

    # ------------------------------------------------------------------ loop
    def _reset_day(self) -> None:
        self.ltp.clear()
        self.day_bars = {}
        self._dirty.clear()

    def clock(self) -> datetime:
        if self.mode == "REPLAY" and isinstance(self.source, ReplaySource) and self.source.clock:
            return self.source.clock
        return now_ist()

    async def _run(self) -> None:
        assert self.source is not None
        self.monitor.on_connect()
        try:
            async for ev in self.source.stream():
                if isinstance(ev, Tick):
                    self._on_tick(ev)
                else:
                    self._on_minute(ev)
                await asyncio.sleep(0)
            if self.mode == "REPLAY" and self.on_session_end is not None:
                await asyncio.to_thread(self.on_session_end, self.replay_day, "REPLAY")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.error("runtime_loop_failed", error=repr(e))
            self.bus.publish({"type": "error", "message": f"session stopped: {type(e).__name__}: {e}"})
        finally:
            self.monitor.on_disconnect()
            if self.mode == "REPLAY":
                self.bus.publish({"type": "replay_end", "day": str(self.replay_day)})
                if isinstance(self.source, ReplaySource) and self.source.stopped:
                    self.mode = "IDLE"                      # finished: the board stays visible, session is idle
            self.publish_status()

    def _on_tick(self, t: Tick) -> None:
        self.ltp[t.key] = t.ltp
        self._dirty.add(t.key)
        self.monitor.on_event(t.ts, t.recv_ts or t.ts)
        if self.engine is not None:
            self.engine.on_tick(t)
        now = _time.monotonic()
        if now - self._last_price_push > 0.25:      # batch price updates (UI smoothness with 500+ symbols)
            self._last_price_push = now
            self.bus.publish({"type": "prices", "data": {k: self.ltp[k] for k in self._dirty}})
            self._dirty.clear()

    def _on_minute(self, b: MinuteBatch) -> None:
        t0 = _time.perf_counter()
        for k, bar in b.bars.items():
            self.day_bars.setdefault(k, []).append(bar)
            self.ltp[k] = bar.close
        if self.mode == "REPLAY":
            self.monitor.on_event(b.close_ts, b.close_ts)
        self.monitor.on_minute(set(b.bars))
        health = self.monitor.health(self.clock(), self.market_open())
        msgs: list[dict[str, Any]] = []
        if self.engine is not None:
            msgs = self.engine.on_minute(b, health)
        for m in msgs:
            self.bus.publish(m, sticky=m.get("sticky", False))
        self.bus.publish({"type": "minute", "close_ts": b.close_ts.isoformat(), "n": len(b.bars),
                          "bars": {k: [bar.open, bar.high, bar.low, bar.close, bar.volume]
                                   for k, bar in b.bars.items() if self.inst_by_key.get(k) and self.inst_by_key[k].kind == "INDEX"},
                          "prices": {k: bar.close for k, bar in b.bars.items()},
                          "engine_ms": round((_time.perf_counter() - t0) * 1000, 1)})
        self.bus.publish({"type": "health", **health.as_dict(), "sticky": True}, sticky=True)

    # ------------------------------------------------------------------ status
    def market_open(self) -> bool:
        return market_status(self.clock(), self.sess, self.cal) == "OPEN" if self.mode == "LIVE" else self.mode == "REPLAY"

    def status(self) -> dict[str, Any]:
        src = self.source
        return {
            "mode": self.mode,
            "provenance": self.provenance,
            "synthetic": self.provenance == "synthetic",
            "replay_day": str(self.replay_day) if self.replay_day else None,
            "speed": src.speed if isinstance(src, ReplaySource) else None,
            "paused": src.paused if isinstance(src, ReplaySource) else None,
            "clock": self.clock().astimezone(IST).isoformat(),
            "market_status": "REPLAY" if self.mode == "REPLAY" else market_status(now_ist(), self.sess, self.cal),
            "kill_switch": self.kill_switch,
            "n_instruments": len(self.instruments),
            "config_hash": self.settings.config_hash(),
        }

    def publish_status(self) -> None:
        self.bus.publish({"type": "status", **self.status()}, sticky=True)
