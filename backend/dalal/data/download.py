"""Rate-limited historical downloader (Upstox V3 1-minute candles → Parquet store)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta

from dalal.clock import IST
from dalal.data.models import Instrument
from dalal.data.store import CandleStore
from dalal.data.upstox import UpstoxProvider
from dalal.log import get_logger

log = get_logger("download")


async def download_history(
    provider: UpstoxProvider,
    store: CandleStore,
    instruments: list[Instrument],
    years: int,
    end: date | None = None,
    progress: Callable[[int, int, str, int], None] | None = None,
) -> dict[str, int]:
    """Incremental: for each instrument fetch only days after what is already stored.

    Upstox minute history starts Jan 2022 (D-010); earlier requests just return empty.
    """
    end = end or (datetime.now(tz=IST).date() - timedelta(days=1))
    start_floor = max(date(2022, 1, 1), end - timedelta(days=int(365.25 * years)))
    counts: dict[str, int] = {}
    for i, inst in enumerate(instruments):
        days = store.days(inst.key)
        start = (days[-1] + timedelta(days=1)) if days else start_floor
        if start > end:
            counts[inst.key] = 0
            continue
        try:
            df = await provider.historical_1m(inst, start, end)
        except Exception as e:  # keep going; the manifest records the failure
            log.warning("download_failed", key=inst.key, error=str(e)[:200])
            counts[inst.key] = -1
            continue
        n = 0 if df.empty else store.write(inst.key, df)
        counts[inst.key] = len(df)
        if progress:
            progress(i + 1, len(instruments), inst.symbol, n)
    return counts
