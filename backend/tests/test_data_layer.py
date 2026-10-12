from __future__ import annotations

from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from dalal.clock import IST, SessionTimes, TradingCalendar
from dalal.config import load_settings
from dalal.data.candles import BarBuilder, resample
from dalal.data.models import Tick
from dalal.data.quality import FeedMonitor, check_candles
from dalal.data.store import CandleStore
from dalal.data.synthetic import SynthParams, generate
from dalal.data.universe import ReferenceSets, build_universe


def _bars(day: date, n: int = 30, start_px: float = 100.0) -> pd.DataFrame:
    ts = pd.date_range(datetime(day.year, day.month, day.day, 9, 15, tzinfo=IST), periods=n, freq="1min")
    px = start_px + np.arange(n) * 0.1
    return pd.DataFrame({"ts": ts, "open": px, "high": px + 0.2, "low": px - 0.2, "close": px + 0.05,
                         "volume": 100.0, "oi": np.nan})


def test_store_roundtrip_merge_and_tz(tmp_path):
    st = CandleStore(tmp_path, "synthetic")
    d1, d2 = date(2024, 1, 2), date(2024, 1, 3)
    st.write("X|A", _bars(d1))
    st.write("X|A", _bars(d2))
    st.write("X|A", _bars(d2))       # idempotent merge
    df = st.read("X|A")
    assert len(df) == 60
    assert str(df["ts"].dt.tz) == "Asia/Kolkata"
    assert st.days("X|A") == [d1, d2]
    assert len(st.read_day("X|A", d2)) == 30
    daily = st.daily("X|A")
    assert list(daily["date"]) == [d1, d2] and daily["volume"].iloc[0] == 3000
    naive = _bars(d1)
    naive["ts"] = naive["ts"].dt.tz_localize(None)
    with pytest.raises(ValueError):
        st.write("X|B", naive)


def test_resample_anchored_and_complete_only():
    df = _bars(date(2024, 1, 2), n=12)
    r5 = resample(df, 5)
    # 12 minutes → buckets 09:15 (5), 09:20 (5), 09:25 (2, incomplete → dropped)
    assert len(r5) == 2
    assert r5["ts"].iloc[0].strftime("%H:%M") == "09:15"
    assert r5["volume"].iloc[0] == 500
    assert r5["open"].iloc[1] == df["open"].iloc[5] and r5["close"].iloc[1] == df["close"].iloc[9]
    assert len(resample(df, 5, complete_only=False)) == 3


def test_bar_builder_volume_from_cumulative():
    b = BarBuilder()
    t0 = datetime(2024, 1, 2, 9, 15, 5, tzinfo=IST)
    assert b.on_tick(Tick("K", t0, 100.0, volume_cum=1000)) == []
    b.on_tick(Tick("K", t0 + timedelta(seconds=20), 101.0, volume_cum=1300))
    b.on_tick(Tick("K", t0 + timedelta(seconds=40), 99.5, volume_cum=1500))
    closed = b.on_tick(Tick("K", t0 + timedelta(seconds=60), 100.5, volume_cum=1600))
    assert len(closed) == 1
    bar = closed[0]
    assert (bar.open, bar.high, bar.low, bar.close) == (100.0, 101.0, 99.5, 99.5)
    assert bar.volume == 500
    rest = b.close_until(datetime(2024, 1, 2, 9, 17, tzinfo=IST))
    assert len(rest) == 1 and rest[0].volume == 100


def test_synthetic_is_deterministic_and_clean(tmp_path):
    p = SynthParams(symbols=8, days=5, seed=3, start=date(2024, 2, 1))
    a = generate(tmp_path / "a", p)
    b = generate(tmp_path / "b", p)
    k = a.keys()[0]
    pd.testing.assert_frame_equal(a.read(k), b.read(k))
    for key in a.keys():
        inst = {i.key: i for i in a.instruments()[0]}[key]
        rep = check_candles(key, a.read(key), is_index=inst.kind == "INDEX")
        assert rep.ok, (key, rep.issues)
        assert rep.issues["missing_minutes"] == 0
    assert a.manifest()["kind"].startswith("SYNTHETIC")


def test_quality_detects_bad_rows():
    df = _bars(date(2024, 1, 2))
    df.loc[3, "high"] = df.loc[3, "low"] - 1
    df.loc[5, "volume"] = -1
    df = pd.concat([df, df.iloc[[7]]])
    rep = check_candles("K", df)
    assert not rep.ok
    assert rep.issues["ohlc_inconsistent"] >= 1 and rep.issues["negative_volume"] == 1 and rep.issues["duplicate_ts"] == 1


def test_feed_monitor_fails_closed():
    m = FeedMonitor(stale_seconds=5)
    now = datetime(2024, 1, 2, 10, 0, tzinfo=IST)
    assert not m.health(now, True).signals_allowed          # never connected
    m.on_connect()
    m.on_event(now, now)
    assert m.health(now + timedelta(seconds=2), True).signals_allowed
    h = m.health(now + timedelta(seconds=6), True)
    assert h.state == "STALE" and not h.signals_allowed
    m.on_disconnect()
    assert m.health(now, True).state == "DISCONNECTED"
    m.on_connect()
    for _ in range(5):
        m.on_event(now - timedelta(seconds=3), now)          # sustained 3 s exchange→local lag
    assert m.health(now, True).state == "DEGRADED"


def test_universe_filters(syn_store, settings):
    insts, roles = syn_store.instruments()
    days = syn_store.days(roles["nifty"])
    u = build_universe(syn_store, insts, days[-1], settings.universe, roles["nifty"])
    assert u.members, "expected some eligible synthetic names"
    reasons = " ".join(u.excluded.values())
    assert "median traded value" in reasons or "price" in reasons
    for m in u.members:
        assert m.median_tv_cr >= 50 and m.last_close >= 50
    sym = u.members[0].symbol
    u2 = build_universe(syn_store, insts, days[-1], settings.universe, roles["nifty"],
                        ReferenceSets(asm_gsm={sym}, event_risk={u.members[1].symbol}))
    assert u2.excluded[sym] == "ASM/GSM list"
    assert "event risk" in u2.excluded[u.members[1].symbol]


def test_universe_uses_only_prior_days(syn_store, settings):
    """Changing today's bars must not change today's universe (no look-ahead)."""
    insts, roles = syn_store.instruments()
    days = syn_store.days(roles["nifty"])
    a = build_universe(syn_store, insts, days[-1], settings.universe, roles["nifty"])
    assert all(m.last_close > 0 for m in a.members)
    hist_last = {m.key: syn_store.daily(m.key).set_index("date").loc[days[-2], "close"] for m in a.members}
    assert all(abs(m.last_close - hist_last[m.key]) < 1e-9 for m in a.members)


def test_calendar_and_session():
    cal = TradingCalendar.from_config()
    assert not cal.is_trading_day(date(2026, 1, 26))          # Republic Day
    assert not cal.is_trading_day(date(2026, 10, 17))         # Saturday
    assert cal.is_trading_day(date(2026, 10, 12))
    assert cal.previous_trading_day(date(2026, 10, 5)) == date(2026, 10, 1)   # skips weekend + 2 Oct
    s = SessionTimes.from_cfg(load_settings().session)
    assert s.minutes == 375
