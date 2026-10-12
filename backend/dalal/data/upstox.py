"""Upstox adapter: instrument master, V3 historical candles, holidays, WebSocket V3 feed.

Endpoints and wire format verified from the official SDK source (DECISIONS D-010).
We deliberately do NOT use the SDK's websocket feeder because it disables TLS
certificate verification; this client keeps verification on.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import time as _time
import uuid
from collections import deque
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import quote

import httpx
import pandas as pd

from dalal.clock import IST
from dalal.config import UpstoxCfg
from dalal.data.models import Instrument, Tick, empty_bars
from dalal.data.provider import FeedStatus, IndexRoles
from dalal.log import get_logger

log = get_logger("upstox")


class RateLimiter:
    """Sliding-window limiter enforcing all published windows at once (D-010)."""

    def __init__(self, per_second: int, per_minute: int, per_30min: int, safety: float = 0.8,
                 clock: Any = _time.monotonic, sleep: Any = asyncio.sleep):
        self.windows = [
            (1.0, max(1, int(per_second * safety))),
            (60.0, max(1, int(per_minute * safety))),
            (1800.0, max(1, int(per_30min * safety))),
        ]
        self.hits: deque[float] = deque()
        self.clock, self.sleep = clock, sleep
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = self.clock()
                while self.hits and now - self.hits[0] > 1800.0:
                    self.hits.popleft()
                wait = 0.0
                for span, cap in self.windows:
                    recent = [h for h in self.hits if now - h < span]
                    if len(recent) >= cap:
                        wait = max(wait, span - (now - recent[0]) + 0.01)
                if wait <= 0:
                    self.hits.append(now)
                    return
                await self.sleep(wait)


def _tick_size(raw: Any) -> float:
    # The instrument file reports tick size in paise on some segments (e.g. 5.0 == ₹0.05).
    # VERIFY-ON-FIRST-RUN: `dalal verify` prints a sample so this can be confirmed.
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return 0.05
    return v / 100.0 if v >= 1.0 else v


def parse_instruments(rows: list[dict[str, Any]], sector_map: dict[str, str] | None = None) -> list[Instrument]:
    """Parse the Upstox instrument master into equities (with F&O flag), futures and indices."""
    sector_map = sector_map or {}
    eq: dict[str, Instrument] = {}
    futs: list[Instrument] = []
    idx: list[Instrument] = []
    today_ms = int(datetime.now(tz=UTC).timestamp() * 1000)
    for r in rows:
        seg = r.get("segment", "")
        it = r.get("instrument_type", "")
        key = r.get("instrument_key", "")
        if not key:
            continue
        if seg == "NSE_EQ" and it == "EQ":
            sym = r.get("trading_symbol") or r.get("short_name") or ""
            eq[key] = Instrument(key=key, symbol=sym, name=r.get("name", ""), segment=seg, kind="EQ",
                                 lot_size=int(r.get("lot_size") or 1), tick_size=_tick_size(r.get("tick_size")),
                                 isin=r.get("isin", ""), sector=sector_map.get(sym, "UNKNOWN"))
        elif seg == "NSE_FO" and it == "FUT" and r.get("underlying_type", "EQUITY") == "EQUITY":
            exp = r.get("expiry")
            if isinstance(exp, (int, float)) and exp < today_ms - 86_400_000:
                continue
            futs.append(Instrument(key=key, symbol=r.get("trading_symbol", ""), name=r.get("name", ""), segment=seg,
                                   kind="FUT", lot_size=int(r.get("lot_size") or 1),
                                   tick_size=_tick_size(r.get("tick_size")), underlying_key=r.get("underlying_key", ""),
                                   expiry=datetime.fromtimestamp(exp / 1000, tz=IST).date().isoformat()
                                   if isinstance(exp, (int, float)) else str(exp or "")))
        elif seg == "NSE_INDEX":
            idx.append(Instrument(key=key, symbol=r.get("trading_symbol") or r.get("name", ""), name=r.get("name", ""),
                                  segment=seg, kind="INDEX", sector="INDEX"))
    # mark F&O equities and attach the nearest futures contract
    futs.sort(key=lambda f: f.expiry)
    for f in futs:
        u = eq.get(f.underlying_key)
        if u is None:
            continue
        u.is_fno = True
        if not u.futures_key:
            u.futures_key = f.key
            u.lot_size = f.lot_size if f.lot_size else u.lot_size
    near = [f for f in futs if eq.get(f.underlying_key) and eq[f.underlying_key].futures_key == f.key]
    return [*eq.values(), *near, *idx]


def parse_candles(payload: dict[str, Any]) -> pd.DataFrame:
    candles = ((payload or {}).get("data") or {}).get("candles") or []
    if not candles:
        return empty_bars()
    df = pd.DataFrame(candles).iloc[:, :7]
    df.columns = ["ts", "open", "high", "low", "close", "volume", "oi"][: df.shape[1]]
    df["ts"] = pd.to_datetime(df["ts"]).dt.tz_convert(IST)
    if "oi" not in df.columns:
        df["oi"] = float("nan")
    return df.sort_values("ts").reset_index(drop=True)


def month_chunks(start: date, end: date) -> list[tuple[date, date]]:
    """[from, to] windows of at most ~1 month (V3 minute-candle rule, D-010)."""
    out, s = [], start
    while s <= end:
        e = min(end, (pd.Timestamp(s) + pd.DateOffset(months=1) - pd.Timedelta(days=1)).date())
        out.append((s, e))
        s = e + timedelta(days=1)
    return out


class UpstoxREST:
    def __init__(self, cfg: UpstoxCfg, token: str | None, client: httpx.AsyncClient | None = None):
        self.cfg = cfg
        self.token = token
        rl = cfg.rate_limits
        self.limiter = RateLimiter(rl.per_second, rl.per_minute, rl.per_30min, cfg.safety_factor)
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.client = client or httpx.AsyncClient(base_url=cfg.rest_base, headers=headers, timeout=30.0)

    async def close(self) -> None:
        await self.client.aclose()

    async def _get(self, path: str, retries: int = 4) -> dict[str, Any]:
        delay = 1.0
        for attempt in range(retries + 1):
            await self.limiter.acquire()
            r = await self.client.get(path)
            if r.status_code == 429 or r.status_code >= 500:
                if attempt == retries:
                    r.raise_for_status()
                log.warning("upstox_retry", path=path.split("/")[2] if path.count("/") > 2 else path,
                            status=r.status_code, wait=delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)
                continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError("unreachable")

    async def instrument_rows(self) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=120.0) as c:
            r = await c.get(self.cfg.instruments_url)
            r.raise_for_status()
            raw = r.content
        try:
            raw = gzip.decompress(raw)
        except OSError:
            pass
        return json.loads(raw)

    async def historical_1m(self, key: str, start: date, end: date) -> pd.DataFrame:
        frames = []
        for s, e in month_chunks(start, end):
            path = f"/v3/historical-candle/{quote(key, safe='')}/minutes/1/{e.isoformat()}/{s.isoformat()}"
            frames.append(parse_candles(await self._get(path)))
        frames = [f for f in frames if not f.empty]
        if not frames:
            return empty_bars()
        return pd.concat(frames, ignore_index=True).drop_duplicates("ts").sort_values("ts").reset_index(drop=True)

    async def intraday_1m(self, key: str) -> pd.DataFrame:
        return parse_candles(await self._get(f"/v3/historical-candle/intraday/{quote(key, safe='')}/minutes/1"))

    async def holidays(self) -> list[date]:
        data = (await self._get("/v2/market/holidays")).get("data") or []
        out = []
        for h in data:
            closed = h.get("closed_exchanges") or []
            if "NSE" in closed and h.get("date"):
                out.append(date.fromisoformat(str(h["date"])[:10]))
        return out


# ---------------- WebSocket V3 feed ----------------

def build_sub_request(keys: list[str], mode: str, method: str = "sub") -> bytes:
    """Subscription request: JSON sent as a BINARY frame (per official SDK)."""
    return json.dumps({"guid": str(uuid.uuid4()), "method": method,
                       "data": {"mode": mode, "instrumentKeys": keys}}).encode("utf-8")


def decode_feed(buf: bytes, recv_ts: datetime) -> tuple[list[Tick], dict[str, Any]]:
    from dalal.data.proto import MarketDataFeedV3_pb2 as pb

    fr = pb.FeedResponse()
    fr.ParseFromString(buf)
    meta: dict[str, Any] = {"type": pb.Type.Name(fr.type), "current_ts": fr.currentTs}
    if fr.HasField("marketInfo"):
        meta["segment_status"] = {k: pb.MarketStatus.Name(v) for k, v in fr.marketInfo.segmentStatus.items()}
    ticks: list[Tick] = []
    for key, feed in fr.feeds.items():
        which = feed.WhichOneof("FeedUnion")
        ltpc = vtt = oi = bid = ask = None
        if which == "ltpc":
            ltpc = feed.ltpc
        elif which == "fullFeed":
            ff = feed.fullFeed
            sub = ff.WhichOneof("FullFeedUnion")
            if sub == "marketFF":
                m = ff.marketFF
                ltpc, vtt, oi = m.ltpc, float(m.vtt), (float(m.oi) if m.oi else None)
                if m.marketLevel.bidAskQuote:
                    q = m.marketLevel.bidAskQuote[0]
                    bid, ask = (q.bidP or None), (q.askP or None)
            elif sub == "indexFF":
                ltpc = ff.indexFF.ltpc
        elif which == "firstLevelWithGreeks":
            g = feed.firstLevelWithGreeks
            ltpc, vtt, oi = g.ltpc, float(g.vtt), (float(g.oi) if g.oi else None)
        if ltpc is None or not ltpc.ltp:
            continue
        ts = datetime.fromtimestamp(ltpc.ltt / 1000.0, tz=IST) if ltpc.ltt else recv_ts
        ticks.append(Tick(key=key, ts=ts, ltp=float(ltpc.ltp), volume_cum=vtt, oi=oi, bid=bid, ask=ask,
                          prev_close=float(ltpc.cp) or None, recv_ts=recv_ts))
    return ticks, meta


class UpstoxFeed:
    """Reconnecting WebSocket V3 client yielding Ticks. Exponential backoff 1→30 s."""

    def __init__(self, cfg: UpstoxCfg, token: str):
        self.cfg, self.token = cfg, token
        self._status = FeedStatus(connected=False)
        self._closed = False
        self.market_info: dict[str, Any] = {}

    def status(self) -> FeedStatus:
        return self._status

    async def close(self) -> None:
        self._closed = True

    async def ticks(self, keys: list[str]) -> AsyncIterator[Tick]:
        import websockets

        backoff = 1.0
        chunk = 1500 if self.cfg.feed_mode.startswith("full") else 2000
        while not self._closed:
            try:
                async with websockets.connect(
                    self.cfg.ws_url,
                    additional_headers={"Authorization": f"Bearer {self.token}", "Accept": "*/*"},
                    ping_interval=10, ping_timeout=10, max_size=None, open_timeout=15,
                ) as ws:
                    self._status.connected = True
                    self._status.detail = "connected"
                    backoff = 1.0
                    for i in range(0, len(keys), chunk):
                        await ws.send(build_sub_request(keys[i:i + chunk], self.cfg.feed_mode))
                    async for msg in ws:
                        if self._closed:
                            break
                        if not isinstance(msg, bytes):
                            continue
                        recv = datetime.now(tz=IST)
                        self._status.last_event_ts = recv.timestamp()
                        ticks, meta = decode_feed(msg, recv)
                        if "segment_status" in meta:
                            self.market_info = meta["segment_status"]
                        for t in ticks:
                            yield t
            except asyncio.CancelledError:
                raise
            except Exception as e:  # network errors, auth errors, protocol errors
                self._status.connected = False
                self._status.reconnects += 1
                self._status.detail = f"{type(e).__name__}: reconnecting in {backoff:.0f}s"
                log.warning("feed_disconnected", error=type(e).__name__, backoff=backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
        self._status.connected = False


class UpstoxProvider:
    name = "upstox"
    provenance = "upstox"
    realtime = True

    def __init__(self, cfg: UpstoxCfg, token: str | None, sector_map: dict[str, str] | None = None):
        self.cfg = cfg
        self.rest = UpstoxREST(cfg, token)
        self.token = token
        self.sector_map = sector_map or {}

    def index_roles(self) -> IndexRoles:
        k = self.cfg.index_keys
        return IndexRoles(nifty=k.get("nifty", "NSE_INDEX|Nifty 50"), banknifty=k.get("banknifty"),
                          vix=k.get("vix"), sectors=dict(self.cfg.sector_index_keys))

    async def instruments(self) -> list[Instrument]:
        return parse_instruments(await self.rest.instrument_rows(), self.sector_map)

    async def historical_1m(self, inst: Instrument, start: date, end: date) -> pd.DataFrame:
        return await self.rest.historical_1m(inst.key, start, end)

    def feed(self) -> UpstoxFeed:
        if not self.token:
            raise RuntimeError("UPSTOX_ANALYTICS_TOKEN is not set (.env)")
        return UpstoxFeed(self.cfg, self.token)
