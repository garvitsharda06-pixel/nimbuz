"""Data quality: live feed health monitor (§6) and offline checks on stored candles."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time

import numpy as np
import pandas as pd

from dalal.clock import IST


@dataclass
class Health:
    state: str = "INIT"                 # INIT | OK | DEGRADED | STALE | DISCONNECTED
    signals_allowed: bool = False
    last_event_age_s: float | None = None
    latency_ms: float | None = None     # exchange ts -> local receive
    clock_drift_ms: float | None = None
    missing_bars: int = 0
    reconnects: int = 0
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return self.__dict__.copy()


class FeedMonitor:
    """Tracks feed freshness against a clock (wall clock in live, replay clock in replay).

    Fail-closed: signals are allowed only when state is OK.
    """

    def __init__(self, stale_seconds: float = 5.0, max_drift_ms: float = 2000.0):
        self.stale_seconds = stale_seconds
        self.max_drift_ms = max_drift_ms
        self.last_event: datetime | None = None
        self.latencies: list[float] = []
        self.drift_ms: float | None = None
        self.connected = False
        self.reconnects = 0
        self.missing_bars = 0
        self.expected_keys: set[str] = set()

    def on_connect(self) -> None:
        if not self.connected and self.last_event is not None:
            self.reconnects += 1
        self.connected = True

    def on_disconnect(self) -> None:
        self.connected = False

    def on_event(self, exchange_ts: datetime, recv_ts: datetime) -> None:
        self.last_event = recv_ts
        lat = (recv_ts - exchange_ts).total_seconds() * 1000.0
        self.latencies.append(lat)
        if len(self.latencies) > 500:
            self.latencies = self.latencies[-500:]
        self.drift_ms = float(np.median(self.latencies[-50:]))

    def on_minute(self, keys_with_bars: set[str]) -> None:
        if self.expected_keys:
            self.missing_bars = len(self.expected_keys - keys_with_bars)

    def health(self, now: datetime, market_open: bool) -> Health:
        h = Health(reconnects=self.reconnects, missing_bars=self.missing_bars)
        if self.latencies:
            h.latency_ms = round(float(np.median(self.latencies[-20:])), 1)
        h.clock_drift_ms = None if self.drift_ms is None else round(self.drift_ms, 1)
        if self.last_event is not None:
            h.last_event_age_s = round((now - self.last_event).total_seconds(), 2)
        if not self.connected:
            h.state, h.reasons = "DISCONNECTED", ["feed disconnected"]
        elif not market_open:
            h.state, h.reasons = "OK", ["market closed"]
        elif h.last_event_age_s is None or h.last_event_age_s > self.stale_seconds:
            h.state = "STALE"
            h.reasons = [f"no data for {h.last_event_age_s}s (> {self.stale_seconds}s)"]
        elif h.clock_drift_ms is not None and abs(h.clock_drift_ms) > self.max_drift_ms:
            h.state = "DEGRADED"
            h.reasons = [f"clock drift/latency {h.clock_drift_ms:.0f} ms > {self.max_drift_ms:.0f} ms"]
        elif self.expected_keys and self.missing_bars > 0.2 * len(self.expected_keys):
            h.state = "DEGRADED"
            h.reasons = [f"{self.missing_bars}/{len(self.expected_keys)} instruments missing last minute bar"]
        else:
            h.state = "OK"
        h.signals_allowed = h.state == "OK" and market_open
        return h


# ---------------- offline checks ----------------

@dataclass
class QualityReport:
    key: str
    rows: int
    days: int
    issues: dict[str, int] = field(default_factory=dict)
    suspicious_gaps: list[tuple[str, float]] = field(default_factory=list)  # (date, gap %)

    @property
    def ok(self) -> bool:
        hard = {"ohlc_inconsistent", "negative_volume", "duplicate_ts", "out_of_session", "naive_ts"}
        return not any(self.issues.get(k, 0) for k in hard)


def check_candles(key: str, df: pd.DataFrame, open_t: time = time(9, 15), close_t: time = time(15, 30),
                  is_index: bool = False, gap_flag: float = 0.30) -> QualityReport:
    rep = QualityReport(key=key, rows=len(df), days=0)
    if df.empty:
        rep.issues["empty"] = 1
        return rep
    if df["ts"].dt.tz is None:
        rep.issues["naive_ts"] = len(df)
        return rep
    t = df["ts"].dt.tz_convert(IST)
    rep.days = int(t.dt.date.nunique())
    bad = (df["low"] > df[["open", "close"]].min(axis=1) + 1e-9) | (df["high"] < df[["open", "close"]].max(axis=1) - 1e-9)
    rep.issues["ohlc_inconsistent"] = int(bad.sum())
    rep.issues["negative_volume"] = int((df["volume"] < 0).sum())
    rep.issues["duplicate_ts"] = int(df["ts"].duplicated().sum())
    tod = t.dt.time
    rep.issues["out_of_session"] = int(((tod < open_t) | (tod >= close_t)).sum())
    # gaps: expected minutes per day vs present
    per_day = t.dt.date.value_counts()
    full = (close_t.hour * 60 + close_t.minute) - (open_t.hour * 60 + open_t.minute)
    rep.issues["missing_minutes"] = int((full - per_day.clip(upper=full)).sum())
    if not is_index:
        rep.issues["zero_volume_bars"] = int((df["volume"] == 0).sum())
        daily = df.assign(d=t.dt.date).groupby("d").agg(o=("open", "first"), c=("close", "last"))
        gap = daily["o"] / daily["c"].shift(1) - 1
        for d, g in gap.dropna().items():
            if abs(g) > gap_flag:
                rep.suspicious_gaps.append((str(d), round(float(g) * 100, 2)))
    return rep


def gap_exclusion_days(rep: QualityReport) -> set[date]:
    """Days around unexplained >30 % gaps (probable unadjusted corporate actions)."""
    out: set[date] = set()
    for d, _ in rep.suspicious_gaps:
        out.add(date.fromisoformat(d))
    return out
