"""IST time helpers and the trading-session calendar."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from dalal.config import SessionCfg, load_yaml

IST = ZoneInfo("Asia/Kolkata")


def now_ist() -> datetime:
    return datetime.now(tz=IST)


def parse_hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def at(d: date, t: time) -> datetime:
    return datetime.combine(d, t, tzinfo=IST)


def to_ist(ts: datetime | pd.Timestamp) -> pd.Timestamp:
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        raise ValueError("naive timestamp: all timestamps must be tz-aware (IST)")
    return t.tz_convert(IST)


@dataclass(frozen=True)
class SessionTimes:
    open: time
    close: time
    first_signal: time
    last_new_idea: time
    idea_expiry: time
    resolve_at: time

    @classmethod
    def from_cfg(cls, cfg: SessionCfg) -> SessionTimes:
        return cls(
            open=parse_hhmm(cfg.open),
            close=parse_hhmm(cfg.close),
            first_signal=parse_hhmm(cfg.first_signal),
            last_new_idea=parse_hhmm(cfg.last_new_idea),
            idea_expiry=parse_hhmm(cfg.idea_expiry),
            resolve_at=parse_hhmm(cfg.resolve_at),
        )

    @property
    def minutes(self) -> int:
        return (self.close.hour * 60 + self.close.minute) - (self.open.hour * 60 + self.open.minute)


@dataclass
class TradingCalendar:
    """Weekday + holiday calendar. Holidays come from the broker API when available,
    else from config/calendar.yaml (see DECISIONS D-031)."""

    holidays: set[date] = field(default_factory=set)
    special_open: dict[date, tuple[time | None, time | None]] = field(default_factory=dict)
    source: str = "config"

    @classmethod
    def from_config(cls) -> TradingCalendar:
        raw = load_yaml("calendar.yaml")
        hol: set[date] = set()
        for _yr, days in (raw.get("holidays") or {}).items():
            for d in days or []:
                hol.add(date.fromisoformat(str(d)))
        special: dict[date, tuple[time | None, time | None]] = {}
        for s in raw.get("special_sessions") or []:
            o = parse_hhmm(s["open"]) if s.get("open") else None
            c = parse_hhmm(s["close"]) if s.get("close") else None
            special[date.fromisoformat(str(s["date"]))] = (o, c)
        return cls(holidays=hol, special_open=special, source="config/calendar.yaml")

    def add_holidays(self, days: Iterable[date], source: str) -> None:
        self.holidays |= set(days)
        self.source = source

    def is_trading_day(self, d: date) -> bool:
        if d in self.special_open:
            o, c = self.special_open[d]
            return o is not None and c is not None
        return d.weekday() < 5 and d not in self.holidays

    def trading_days(self, start: date, end: date) -> list[date]:
        out, d = [], start
        while d <= end:
            if self.is_trading_day(d):
                out.append(d)
            d += timedelta(days=1)
        return out

    def previous_trading_day(self, d: date) -> date:
        p = d - timedelta(days=1)
        while not self.is_trading_day(p):
            p -= timedelta(days=1)
        return p

    def next_trading_day(self, d: date) -> date:
        n = d + timedelta(days=1)
        while not self.is_trading_day(n):
            n += timedelta(days=1)
        return n


def market_status(ts: datetime, sess: SessionTimes, cal: TradingCalendar) -> str:
    """PRE_OPEN / OPEN / CLOSED / HOLIDAY."""
    d = ts.astimezone(IST).date()
    if not cal.is_trading_day(d):
        return "HOLIDAY"
    t = ts.astimezone(IST).time()
    if time(9, 0) <= t < sess.open:
        return "PRE_OPEN"
    if sess.open <= t < sess.close:
        return "OPEN"
    return "CLOSED"
