"""Core market-data types."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal

import pandas as pd

BAR_COLUMNS = ["ts", "open", "high", "low", "close", "volume", "oi"]

Provenance = Literal["upstox", "synthetic", "replay"]


@dataclass
class Instrument:
    key: str                      # provider instrument key, e.g. "NSE_EQ|INE002A01018"
    symbol: str                   # trading symbol, e.g. "RELIANCE"
    name: str = ""
    segment: str = ""             # NSE_EQ | NSE_FO | NSE_INDEX | SYN_EQ | ...
    kind: Literal["EQ", "FUT", "INDEX"] = "EQ"
    lot_size: int = 1
    tick_size: float = 0.05
    isin: str = ""
    sector: str = "UNKNOWN"
    is_fno: bool = False
    underlying_key: str = ""      # for futures
    expiry: str = ""              # ISO date for futures
    futures_key: str = ""         # for equities: current near-month futures key, if any
    beta: float | None = None     # filled by research (vs benchmark)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Instrument:
        names = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in names})


@dataclass(frozen=True)
class Tick:
    key: str
    ts: datetime                  # exchange time (IST)
    ltp: float
    volume_cum: float | None = None   # cumulative day volume (vtt)
    oi: float | None = None
    bid: float | None = None
    ask: float | None = None
    prev_close: float | None = None
    recv_ts: datetime | None = None   # local receive time (for latency/drift)


@dataclass(frozen=True)
class Bar:
    key: str
    ts: datetime                  # bar START (IST)
    open: float
    high: float
    low: float
    close: float
    volume: float
    oi: float | None = None


@dataclass
class MinuteBatch:
    """All 1-minute bars that closed at `close_ts` (i.e. bars with ts = close_ts - 1min)."""

    close_ts: datetime
    bars: dict[str, Bar] = field(default_factory=dict)


def empty_bars() -> pd.DataFrame:
    df = pd.DataFrame({c: pd.Series(dtype="float64") for c in BAR_COLUMNS})
    df["ts"] = pd.to_datetime(df["ts"]).dt.tz_localize("Asia/Kolkata")
    return df


def bars_to_frame(bars: list[Bar]) -> pd.DataFrame:
    if not bars:
        return empty_bars()
    df = pd.DataFrame(
        {
            "ts": [b.ts for b in bars],
            "open": [b.open for b in bars],
            "high": [b.high for b in bars],
            "low": [b.low for b in bars],
            "close": [b.close for b in bars],
            "volume": [b.volume for b in bars],
            "oi": [b.oi if b.oi is not None else float("nan") for b in bars],
        }
    )
    df["ts"] = pd.to_datetime(df["ts"]).dt.tz_convert("Asia/Kolkata")
    return df
