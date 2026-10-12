from __future__ import annotations

import json
from datetime import date, datetime

import pytest

from dalal.clock import IST
from dalal.data.reference import parse_fo_ban, parse_nifty500, parse_sec_bhav
from dalal.data.upstox import RateLimiter, build_sub_request, decode_feed, month_chunks, parse_candles, parse_instruments


def test_parse_instruments_marks_fno_and_near_future():
    exp1 = int(datetime(2099, 1, 30, tzinfo=IST).timestamp() * 1000)
    exp2 = int(datetime(2099, 2, 27, tzinfo=IST).timestamp() * 1000)
    rows = [
        {"segment": "NSE_EQ", "instrument_type": "EQ", "instrument_key": "NSE_EQ|INE1", "trading_symbol": "AAA",
         "lot_size": 1, "tick_size": 5.0, "name": "Aaa Ltd"},
        {"segment": "NSE_EQ", "instrument_type": "EQ", "instrument_key": "NSE_EQ|INE2", "trading_symbol": "BBB"},
        {"segment": "NSE_FO", "instrument_type": "FUT", "instrument_key": "NSE_FO|2", "underlying_key": "NSE_EQ|INE1",
         "underlying_type": "EQUITY", "expiry": exp2, "lot_size": 500, "trading_symbol": "AAA FUT FEB"},
        {"segment": "NSE_FO", "instrument_type": "FUT", "instrument_key": "NSE_FO|1", "underlying_key": "NSE_EQ|INE1",
         "underlying_type": "EQUITY", "expiry": exp1, "lot_size": 500, "trading_symbol": "AAA FUT JAN"},
        {"segment": "NSE_INDEX", "instrument_type": "INDEX", "instrument_key": "NSE_INDEX|Nifty 50", "name": "Nifty 50"},
    ]
    insts = {i.key: i for i in parse_instruments(rows, {"AAA": "IT"})}
    a = insts["NSE_EQ|INE1"]
    assert a.is_fno and a.futures_key == "NSE_FO|1" and a.lot_size == 500 and a.sector == "IT"
    assert a.tick_size == pytest.approx(0.05)
    assert not insts["NSE_EQ|INE2"].is_fno
    assert insts["NSE_INDEX|Nifty 50"].kind == "INDEX"
    assert "NSE_FO|2" not in insts            # only the near-month future is kept


def test_parse_candles_sorted_and_tz():
    payload = {"status": "success", "data": {"candles": [
        ["2025-01-03T09:16:00+05:30", 101, 102, 100, 101.5, 1200, 0],
        ["2025-01-03T09:15:00+05:30", 100, 101, 99, 100.5, 1000, 0]]}}
    df = parse_candles(payload)
    assert list(df["close"]) == [100.5, 101.5]
    assert str(df["ts"].dt.tz) == "Asia/Kolkata"
    assert parse_candles({"data": {"candles": []}}).empty


def test_month_chunks_cover_range_without_overlap():
    ch = month_chunks(date(2022, 1, 15), date(2022, 4, 10))
    assert ch[0][0] == date(2022, 1, 15) and ch[-1][1] == date(2022, 4, 10)
    for (_, e), (s2, _) in zip(ch, ch[1:], strict=False):
        assert (s2 - e).days == 1
    assert all((e - s).days <= 31 for s, e in ch)


async def test_rate_limiter_respects_windows():
    t = [0.0]
    slept: list[float] = []

    async def fake_sleep(dt: float) -> None:
        slept.append(dt)
        t[0] += dt

    rl = RateLimiter(per_second=5, per_minute=8, per_30min=1000, safety=1.0, clock=lambda: t[0], sleep=fake_sleep)
    for _ in range(8):
        await rl.acquire()
    assert t[0] >= 1.0          # 6th call had to wait for the 1 s window
    await rl.acquire()          # 9th call must wait for the 60 s window
    assert t[0] >= 60.0


def test_sub_request_is_binary_json():
    b = build_sub_request(["NSE_EQ|INE1"], "full")
    assert isinstance(b, bytes)
    d = json.loads(b)
    assert d["method"] == "sub" and d["data"] == {"mode": "full", "instrumentKeys": ["NSE_EQ|INE1"]}


def test_decode_feed_roundtrip():
    from dalal.data.proto import MarketDataFeedV3_pb2 as pb

    fr = pb.FeedResponse(type=pb.live_feed, currentTs=1700000000000)
    f = fr.feeds["NSE_EQ|INE1"]
    m = f.fullFeed.marketFF
    m.ltpc.ltp, m.ltpc.ltt, m.ltpc.cp = 101.25, 1700000000000, 100.0
    m.vtt, m.oi = 123456, 0
    q = m.marketLevel.bidAskQuote.add()
    q.bidP, q.askP = 101.2, 101.3
    idx = fr.feeds["NSE_INDEX|Nifty 50"].fullFeed.indexFF
    idx.ltpc.ltp, idx.ltpc.ltt = 24000.5, 1700000000000
    fr.feeds["NSE_EQ|INE2"].ltpc.ltp = 55.5
    ticks, meta = decode_feed(fr.SerializeToString(), datetime.now(tz=IST))
    by = {t.key: t for t in ticks}
    assert meta["type"] == "live_feed"
    assert by["NSE_EQ|INE1"].ltp == 101.25 and by["NSE_EQ|INE1"].volume_cum == 123456
    assert by["NSE_EQ|INE1"].bid == pytest.approx(101.2) and by["NSE_EQ|INE1"].prev_close == 100.0
    assert by["NSE_INDEX|Nifty 50"].ltp == 24000.5
    assert by["NSE_EQ|INE2"].ltp == 55.5


def test_reference_parsers():
    assert parse_fo_ban("Securities in Ban For Trade Date 01-JAN-2026:\n1,ABC\n2,XYZ\n") == {"ABC", "XYZ"}
    m = parse_nifty500("Company Name,Industry,Symbol,Series,ISIN Code\nA Ltd,Information Technology,AAA,EQ,INE1\n")
    assert m == {"AAA": "IT"}
    b = parse_sec_bhav("SYMBOL, SERIES, DELIV_PER\nAAA, EQ, 45.5\nBBB, BE, 90\nCCC, N1, 1\n")
    assert b["AAA"]["deliv_pct"] == 45.5 and b["BBB"]["series"] == "BE" and "CCC" not in b
