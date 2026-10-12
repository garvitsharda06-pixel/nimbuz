"""Shared signal pipeline used by BOTH the research engine and the live engine.

symbol bars → panel → daily context → intraday features → (+ market context) →
relative strength → setup context → setups.
"""

from __future__ import annotations

import warnings
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from dalal.config import UniverseCfg
from dalal.data.models import Instrument
from dalal.data.store import CandleStore
from dalal.features.core import Features, daily_context, intraday, relative_strength
from dalal.features.panel import N5, SymbolPanel, panel_from_1m
from dalal.regime.classifier import REGIMES, MarketContext, classify, sector_ranks, take_rows
from dalal.setups.base import LONG, SetupContext

Reader = Callable[[str], pd.DataFrame]


@dataclass
class Series3:
    panel: SymbolPanel
    ctx: Features
    F: Features


@dataclass
class IndexData:
    roles: dict[str, Any]
    nifty: Series3
    bank: Series3 | None = None
    vix: Series3 | None = None
    sectors: dict[str, Series3] = field(default_factory=dict)

    @property
    def days(self) -> np.ndarray:
        return self.nifty.panel.days


def align(src_days: np.ndarray, dst_days: np.ndarray) -> np.ndarray:
    pos = {d: i for i, d in enumerate(src_days)}
    return np.array([pos.get(d, -1) for d in dst_days], dtype=int)


def _index_series(key: str | None, df_reader: Reader, partial: int | None = None) -> Series3 | None:
    if not key:
        return None
    df = df_reader(key)
    if df.empty:
        return None
    p = panel_from_1m(key, df, partial)
    ctx = daily_context(p)
    return Series3(p, ctx, intraday(p, ctx, is_index=True))


def load_index_panels(store: CandleStore | None, roles: dict[str, Any], reader: Reader,
                      partial_minutes: int | None = None) -> IndexData:
    nifty = _index_series(roles["nifty"], reader, partial_minutes)
    if nifty is None:
        raise RuntimeError(f"benchmark {roles['nifty']} has no data")
    bank = _index_series(roles.get("banknifty"), reader, partial_minutes)
    vix = _index_series(roles.get("vix"), reader, partial_minutes)
    sectors: dict[str, Series3] = {}
    for name, key in (roles.get("sectors") or {}).items():
        s = bank if key == roles.get("banknifty") and bank is not None else _index_series(key, reader, partial_minutes)
        if s is not None:
            sectors[name] = s
    return IndexData(roles, nifty, bank, vix, sectors)


def symbol_features(key: str, df: pd.DataFrame, idx: IndexData, partial_minutes: int | None = None
                    ) -> tuple[SymbolPanel, Features, Features]:
    p = panel_from_1m(key, df, partial_minutes)
    rows = align(idx.days, p.days)
    idx_dc = take_rows(idx.nifty.panel.dc, rows)
    ctx = daily_context(p, idx_dc)
    return p, ctx, intraday(p, ctx)


def eligibility(ctx: Features, u: UniverseCfg) -> np.ndarray:
    with np.errstate(invalid="ignore"):
        return (ctx["med_tv20"] >= u.min_median_traded_value_cr * 1e7) & (ctx["pdc"] >= u.min_price)


class MarketBuilder:
    """Accumulates breadth over the eligible universe and classifies the regime."""

    def __init__(self, idx: IndexData):
        self.idx = idx
        D = len(idx.days)
        self.above = np.zeros((D, N5))
        self.n = np.zeros((D, N5))
        self.adv = np.zeros((D, N5))
        self.dec = np.zeros((D, N5))

    def add_breadth(self, sym_days: np.ndarray, elig: np.ndarray, F: Features, ctx: Features) -> None:
        rows = align(sym_days, self.idx.days)          # symbol row for each market day
        ok_rows = rows >= 0
        mrow = np.where(ok_rows)[0]
        srow = rows[ok_rows]
        e = elig[srow][:, None] & F["visible"][srow]
        with np.errstate(invalid="ignore"):
            self.above[mrow] += e & (F["c"][srow] > F["vwap"][srow])
            self.adv[mrow] += e & (F["c"][srow] > ctx["pdc"][srow][:, None])
            self.dec[mrow] += e & (F["c"][srow] < ctx["pdc"][srow][:, None])
        self.n[mrow] += e

    def build(self) -> MarketContext:
        idx = self.idx
        with np.errstate(invalid="ignore", divide="ignore"), warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            breadth = np.where(self.n > 0, self.above / self.n, np.nan)
            adv_dec = np.where(self.adv + self.dec > 0, self.adv / (self.adv + self.dec), np.nan)
            nF = idx.nifty.F
            if idx.vix is not None:
                vrows = align(idx.vix.panel.days, idx.days)
                vix = take_rows(idx.vix.F["c"], vrows)
                vix_chg = take_rows(idx.vix.F["ret_pdc"], vrows)
            else:
                vix = np.full_like(nF["c"], np.nan)
                vix_chg = np.zeros_like(nF["c"])
            reg, score = classify(nF, breadth, np.nan_to_num(vix_chg))
            sec = {n: take_rows(s.F["ret_pdc"], align(s.panel.days, idx.days)) for n, s in idx.sectors.items()}
            bank = take_rows(idx.bank.F["ret_pdc"], align(idx.bank.panel.days, idx.days)) if idx.bank else \
                np.full_like(nF["c"], np.nan)
        return MarketContext(days=idx.days, regime=reg, score=score, breadth=breadth, adv_dec=adv_dec,
                             nifty_ret=nF["ret_pdc"], nifty_c=nF["c"], nifty_dc=idx.nifty.panel.dc, vix=vix,
                             vix_chg=vix_chg, rv_ratio=nF["rv_ratio"], bank_ret=bank, sector_ret=sec,
                             sector_rank=sector_ranks(sec))


def setup_context(inst: Instrument, p: SymbolPanel, ctx: Features, F: Features, mkt: MarketContext,
                  idx: IndexData) -> SetupContext:
    rows = mkt.align(p.days)
    sec_ret = take_rows(mkt.sector_ret[inst.sector], rows) if inst.sector in mkt.sector_ret else None
    idx_ret = take_rows(mkt.nifty_ret, rows)
    relative_strength(F, ctx, idx_ret, sec_ret)
    nF = idx.nifty.F
    nrows = align(idx.nifty.panel.days, p.days)
    with np.errstate(invalid="ignore"):
        idx_above = take_rows(nF["c"], nrows) > take_rows(nF["vwap"], nrows)
    n_sec = max(1, len(mkt.sector_rank))
    srank = take_rows(mkt.sector_rank[inst.sector], rows) if inst.sector in mkt.sector_rank else \
        np.full_like(F["c"], (n_sec + 1) / 2)
    regime = take_rows(mkt.regime.astype(float), rows)
    regime = np.where(np.isnan(regime), -1, regime).astype(int)
    F["_breadth"] = take_rows(mkt.breadth, rows)
    F["_vix_chg"] = take_rows(mkt.vix_chg, rows)
    F["_nifty_ret"] = idx_ret
    F["_score"] = take_rows(mkt.score.astype(float), rows)
    F["_sector_rank"] = srank
    return SetupContext(F=F, ctx=ctx, regime=regime, idx_ret=idx_ret, idx_above_vwap=idx_above,
                        sector_rank=srank, n_sectors=n_sec, tick=inst.tick_size or 0.05)


SNAPSHOT_KEYS = ["rvol_cum", "rvol_bar", "ret_pdc", "ret_open", "rs_mkt_z", "rs_sec_z", "trend15", "rv_ratio"]


def snapshot(F: Features, ctx: Features, sc: SetupContext, d: int, t: int, side: str) -> dict[str, Any]:
    """Salient features at signal time (journal snapshot, thesis drivers, meta-model inputs)."""
    sgn = 1.0 if side == LONG else -1.0
    c = F["c"][d, t]

    def f(x: float) -> float:
        return float(x) if np.isfinite(x) else float("nan")

    out: dict[str, Any] = {k: f(F[k][d, t]) for k in SNAPSHOT_KEYS}
    out.update({
        "gap_atr": f(F["gap_atr"][d]),
        "dist_vwap_atr": f((c - F["vwap"][d, t]) / F["atr5"][d, t]) * sgn,
        "dist_ema_atr": f((c - F["ema20"][d, t]) / F["atr5"][d, t]) * sgn,
        "ema_slope_bps": f(F["ema_slope"][d, t] / c * 1e4) * sgn,
        "atr5_pct": f(F["atr5"][d, t] / c * 100),
        "atr14_pct": f(ctx["atr14"][d] / c * 100),
        "sector_rank": f(sc.sector_rank[d, t]),
        "breadth": f(F["_breadth"][d, t]),
        "vix_chg": f(F["_vix_chg"][d, t]),
        "nifty_ret": f(F["_nifty_ret"][d, t]),
        "mkt_score": f(F["_score"][d, t]),
        "beta": f(ctx["beta"][d]),
        "or_h": f(F["or_h"][d, t]), "or_l": f(F["or_l"][d, t]),
        "vwap": f(F["vwap"][d, t]), "ema20": f(F["ema20"][d, t]),
        "pdh": f(ctx["pdh"][d]), "pdl": f(ctx["pdl"][d]), "close": f(c),
        "regime_name": REGIMES[int(sc.regime[d, t])] if sc.regime[d, t] >= 0 else "UNKNOWN",
    })
    return out


META_FEATURES = ["rvol_cum", "rvol_bar", "ret_pdc", "ret_open", "rs_mkt_z", "rs_sec_z", "trend15", "rv_ratio",
                 "gap_atr", "dist_vwap_atr", "dist_ema_atr", "ema_slope_bps", "atr5_pct", "atr14_pct", "sector_rank",
                 "breadth", "vix_chg", "nifty_ret", "mkt_score", "beta", "t", "rr1"]
