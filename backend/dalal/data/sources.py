"""Event sources that drive the live engine: real ticks → minute batches, and stored-day replay."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from dalal.clock import IST, now_ist
from dalal.data.candles import BarBuilder
from dalal.data.models import Bar, MinuteBatch, Tick
from dalal.data.provider import FeedStatus
from dalal.data.store import CandleStore


class TickMinuteSource:
    """Turns a tick stream into Ticks + MinuteBatch events closed on a wall-clock timer.

    A minute bar closes at boundary + grace (default 300 ms) so a 5-min candle is
    available well inside the 1 s budget (§11 performance target).
    """

    def __init__(self, ticks: AsyncIterator[Tick], grace_ms: int = 300,
                 now: Callable[[], datetime] = now_ist, status: Callable[[], FeedStatus] | None = None):
        self._ticks = ticks
        self.grace = grace_ms / 1000.0
        self.now = now
        self.builder = BarBuilder()
        self._status = status
        self.clock: datetime | None = None

    def status(self) -> FeedStatus:
        return self._status() if self._status else FeedStatus(connected=True)

    async def stream(self) -> AsyncIterator[MinuteBatch | Tick]:
        q: asyncio.Queue[Tick | datetime | None] = asyncio.Queue()

        async def pump() -> None:
            try:
                async for t in self._ticks:
                    await q.put(t)
            finally:
                await q.put(None)

        async def timer() -> None:
            while True:
                n = self.now()
                nxt = (n.replace(second=0, microsecond=0) + timedelta(minutes=1))
                await asyncio.sleep(max(0.0, (nxt - n).total_seconds() + self.grace))
                await q.put(nxt)

        tasks = [asyncio.create_task(pump()), asyncio.create_task(timer())]
        pending: dict[str, Bar] = {}
        try:
            while True:
                item = await q.get()
                if item is None:
                    break
                if isinstance(item, Tick):
                    for b in self.builder.on_tick(item):
                        pending[b.key] = b
                    self.clock = item.recv_ts or item.ts
                    yield item
                else:
                    for b in self.builder.close_until(item):
                        pending[b.key] = b
                    batch = {k: b for k, b in pending.items() if b.ts + timedelta(minutes=1) <= item}
                    for k in batch:
                        pending.pop(k)
                    self.clock = item
                    yield MinuteBatch(close_ts=item, bars=batch)
        finally:
            for t in tasks:
                t.cancel()


class ReplaySource:
    """Replays a stored day through the same pipeline at 1×–60× (speed=0 → as fast as possible).

    Emits three synthetic price ticks per bar (open, extreme, close) for UI animation, then
    the MinuteBatch at the minute close. Pausable; speed adjustable while running.
    """

    def __init__(self, store: CandleStore, day: date, keys: list[str], speed: float = 30.0,
                 emit_ticks: bool = True, start_time: str | None = None):
        self.store, self.day, self.keys = store, day, keys
        self.speed = speed
        self.emit_ticks = emit_ticks
        self.paused = False
        self.stopped = False
        self.clock: datetime | None = None
        self.start_time = start_time
        self._minutes: list[tuple[datetime, dict[str, Bar]]] = []
        self._load()

    def _load(self) -> None:
        per_min: dict[datetime, dict[str, Bar]] = {}
        for k in self.keys:
            df = self.store.read_day(k, self.day)
            if df.empty:
                continue
            arr = df[["open", "high", "low", "close", "volume", "oi"]].to_numpy()
            for ts, row in zip(df["ts"], arr, strict=True):
                t = ts.to_pydatetime()
                per_min.setdefault(t, {})[k] = Bar(k, t, float(row[0]), float(row[1]), float(row[2]), float(row[3]),
                                                    float(row[4]), None if np.isnan(row[5]) else float(row[5]))
        self._minutes = sorted(per_min.items())

    @property
    def n_minutes(self) -> int:
        return len(self._minutes)

    def status(self) -> FeedStatus:
        return FeedStatus(connected=not self.stopped, last_event_ts=None, detail=f"replay {self.day} @ {self.speed:g}x")

    async def stream(self) -> AsyncIterator[MinuteBatch | Tick]:
        skip_until = None
        if self.start_time:
            hh, mm = map(int, self.start_time.split(":"))
            skip_until = datetime.combine(self.day, datetime.min.time(), tzinfo=IST).replace(hour=hh, minute=mm)
        for ts, bars in self._minutes:
            if self.stopped:
                return
            fast = skip_until is not None and ts < skip_until
            while self.paused and not self.stopped:
                await asyncio.sleep(0.05)
            interval = 0.0 if (self.speed <= 0 or fast) else 60.0 / self.speed
            if self.emit_ticks and interval > 0:
                for frac, pick in ((0.25, "o"), (0.5, "x"), (0.75, "c")):
                    await asyncio.sleep(interval / 4)
                    tts = ts + timedelta(seconds=60 * frac)
                    self.clock = tts
                    for b in bars.values():
                        px = b.open if pick == "o" else b.close if pick == "c" else (b.high if b.close >= b.open else b.low)
                        yield Tick(b.key, tts, px, recv_ts=tts)
                await asyncio.sleep(interval / 4)
            elif interval > 0:
                await asyncio.sleep(interval)
            else:
                await asyncio.sleep(0)
            self.clock = ts + timedelta(minutes=1)
            yield MinuteBatch(close_ts=self.clock, bars=bars)
        self.stopped = True


def latest_day(store: CandleStore, key: str, before: date | None = None) -> date | None:
    days = store.days(key)
    if before:
        days = [d for d in days if d < before]
    return days[-1] if days else None


def history_frame(store: CandleStore, key: str, day: date, lookback_days: int = 30) -> pd.DataFrame:
    """1m bars for the `lookback_days` calendar-ish window strictly before `day` (warm-up)."""
    start = day - timedelta(days=int(lookback_days * 1.6) + 5)
    df = store.read(key, start, day - timedelta(days=1))
    return df
