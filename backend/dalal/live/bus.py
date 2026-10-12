"""Tiny in-process pub/sub used to fan events out to WebSocket clients."""

from __future__ import annotations

import asyncio
from typing import Any


class Bus:
    def __init__(self, maxsize: int = 2000):
        self._subs: set[asyncio.Queue[dict[str, Any]]] = set()
        self.maxsize = maxsize
        self.last: dict[str, dict[str, Any]] = {}   # latest message per type (snapshot for new clients)

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(self.maxsize)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        self._subs.discard(q)

    def publish(self, msg: dict[str, Any], sticky: bool = False) -> None:
        if sticky:
            self.last[msg["type"]] = msg
        for q in list(self._subs):
            if q.full():           # slow consumer: drop oldest rather than block the engine
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            q.put_nowait(msg)

    @property
    def n_subscribers(self) -> int:
        return len(self._subs)
