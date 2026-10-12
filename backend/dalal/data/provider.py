"""Provider-agnostic market-data interface (§6).

Every provider (Upstox, replay, synthetic, and future Angel One / Fyers / Kite / Dhan
adapters) implements this protocol. The live engine only sees `MinuteBatch` and `Tick`
events, so live and replay run the exact same pipeline.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol, runtime_checkable

import pandas as pd

from dalal.data.models import Instrument, MinuteBatch, Tick


@dataclass
class IndexRoles:
    """Which instrument keys play which role (benchmark, bank index, VIX, sector indices)."""

    nifty: str
    banknifty: str | None = None
    vix: str | None = None
    sectors: dict[str, str] = field(default_factory=dict)

    def all_keys(self) -> list[str]:
        keys = [self.nifty, self.banknifty, self.vix, *self.sectors.values()]
        return sorted({k for k in keys if k})


@dataclass
class FeedStatus:
    connected: bool
    last_event_ts: float | None = None   # unix seconds of last event received
    reconnects: int = 0
    detail: str = ""


@runtime_checkable
class MarketDataProvider(Protocol):
    name: str
    provenance: str          # "upstox" | "synthetic"
    realtime: bool           # False for delayed/EOD sources: they can never drive live signals

    async def instruments(self) -> list[Instrument]: ...

    def index_roles(self) -> IndexRoles: ...

    async def historical_1m(self, inst: Instrument, start: date, end: date) -> pd.DataFrame: ...


class StreamingProvider(Protocol):
    """Providers that can drive the live engine."""

    def stream(self, keys: list[str]) -> AsyncIterator[MinuteBatch | Tick]: ...

    def status(self) -> FeedStatus: ...

    async def close(self) -> None: ...
