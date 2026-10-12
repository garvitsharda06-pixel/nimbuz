"""Daily universe construction (§7): F&O names that pass liquidity/price/eligibility filters."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from dalal.config import UniverseCfg
from dalal.data.models import Instrument
from dalal.data.store import CandleStore


@dataclass
class Member:
    key: str
    symbol: str
    sector: str
    median_tv_cr: float
    last_close: float
    beta: float | None
    lot_size: int
    event_risk: bool = False
    ban: bool = False


@dataclass
class UniverseResult:
    asof: date
    members: list[Member] = field(default_factory=list)
    excluded: dict[str, str] = field(default_factory=dict)   # symbol -> reason

    def keys(self) -> list[str]:
        return [m.key for m in self.members]

    def by_key(self) -> dict[str, Member]:
        return {m.key: m for m in self.members}

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"asof": self.asof.isoformat(), "members": [asdict(m) for m in self.members],
                                    "excluded": self.excluded}, indent=1))


@dataclass
class ReferenceSets:
    asm_gsm: set[str] = field(default_factory=set)
    fo_ban: set[str] = field(default_factory=set)
    event_risk: set[str] = field(default_factory=set)       # symbols with results/board meeting today
    t2t: set[str] = field(default_factory=set)               # trade-to-trade series
    near_circuit: set[str] = field(default_factory=set)      # pinned near price band (live, best-effort)


def daily_stats(store: CandleStore, key: str, asof: date, lookback: int) -> pd.DataFrame:
    """Daily bars strictly BEFORE `asof` (no look-ahead into the trading day)."""
    df = store.daily(key)
    if df.empty:
        return df
    return df[df["date"] < asof].tail(lookback)


def estimate_beta(stock_daily: pd.DataFrame, index_daily: pd.DataFrame, min_obs: int = 20) -> float | None:
    if stock_daily.empty or index_daily.empty:
        return None
    a = stock_daily.set_index("date")["close"].pct_change()
    b = index_daily.set_index("date")["close"].pct_change()
    j = pd.concat([a, b], axis=1, keys=["s", "m"]).dropna()
    if len(j) < min_obs or j["m"].var() == 0:
        return None
    return float(np.cov(j["s"], j["m"])[0, 1] / j["m"].var())


def build_universe(
    store: CandleStore,
    instruments: list[Instrument],
    asof: date,
    cfg: UniverseCfg,
    nifty_key: str,
    refs: ReferenceSets | None = None,
    short_via_futures: bool = False,
) -> UniverseResult:
    refs = refs or ReferenceSets()
    res = UniverseResult(asof=asof)
    idx_daily = store.daily(nifty_key)
    idx_daily = idx_daily[idx_daily["date"] < asof].tail(60)
    for inst in instruments:
        if inst.kind != "EQ":
            continue
        sym = inst.symbol
        if cfg.fno_only and not inst.is_fno:
            res.excluded[sym] = "not in F&O"
            continue
        if cfg.exclude_asm_gsm and sym in refs.asm_gsm:
            res.excluded[sym] = "ASM/GSM list"
            continue
        if sym in refs.t2t:
            res.excluded[sym] = "trade-to-trade series"
            continue
        if sym in refs.near_circuit:
            res.excluded[sym] = "pinned near price band"
            continue
        if short_via_futures and cfg.exclude_ban_for_futures_shorts and sym in refs.fo_ban:
            res.excluded[sym] = "F&O ban (futures shorts)"
            continue
        if cfg.exclude_event_risk and sym in refs.event_risk:
            res.excluded[sym] = "event risk today (results/board meeting)"
            continue
        hist = daily_stats(store, inst.key, asof, cfg.lookback_days)
        if len(hist) < max(5, cfg.lookback_days // 2):
            res.excluded[sym] = f"insufficient history ({len(hist)}d)"
            continue
        med_tv_cr = float(hist["traded_value"].median()) / 1e7
        last = float(hist["close"].iloc[-1])
        if med_tv_cr < cfg.min_median_traded_value_cr:
            res.excluded[sym] = f"median traded value ₹{med_tv_cr:.1f} cr < {cfg.min_median_traded_value_cr:g}"
            continue
        if last < cfg.min_price:
            res.excluded[sym] = f"price ₹{last:.2f} < {cfg.min_price:g}"
            continue
        beta = estimate_beta(store.daily(inst.key).pipe(lambda d: d[d["date"] < asof].tail(60)), idx_daily)
        res.members.append(Member(key=inst.key, symbol=sym, sector=inst.sector, median_tv_cr=round(med_tv_cr, 2),
                                  last_close=last, beta=None if beta is None else round(beta, 3),
                                  lot_size=inst.lot_size, ban=sym in refs.fo_ban,
                                  event_risk=sym in refs.event_risk))
    res.members.sort(key=lambda m: -m.median_tv_cr)
    return res
