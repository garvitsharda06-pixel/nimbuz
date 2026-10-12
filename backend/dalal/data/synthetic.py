"""SYNTHETIC market generator: for building and testing the pipeline offline.

THIS IS NOT MARKET DATA. Everything written here carries provenance="synthetic" and every
UI surface fed by it is watermarked (DECISIONS D-002). Results computed on it say nothing
about real NSE stocks.

Model (per trading day):
- day type for the market factor: TREND_UP / TREND_DOWN / RANGE / CHOP
- market daily volatility follows a log-AR(1) (volatility clustering)
- overnight gaps (market, sector, idiosyncratic, rare news gaps)
- U-shaped intraday volatility and volume profiles
- stock return = beta * market + loading * sector + idiosyncratic
- optional planted idiosyncratic momentum (`idio_momentum` > 0) used only by the
  known-answer test: the research engine must find an edge when one is planted and
  must reject the null dataset (`idio_momentum` = 0).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

import numpy as np
import pandas as pd
from scipy.signal import lfilter

from dalal.clock import IST, TradingCalendar
from dalal.data.models import Instrument
from dalal.data.store import CandleStore

SECTORS = ["BANK", "IT", "AUTO", "PHARMA", "METAL", "FMCG", "ENERGY", "FIN"]
DAY_TYPES = ["TREND_UP", "TREND_DOWN", "RANGE", "CHOP"]
DAY_TYPE_P = [0.2, 0.2, 0.45, 0.15]
SUB = 5          # sub-steps per minute (gives realistic intra-minute high/low)
MINUTES = 375    # 09:15 → 15:30


@dataclass
class SynthParams:
    symbols: int = 40
    days: int = 750
    seed: int = 7
    start: date = date(2023, 1, 2)
    idio_momentum: float = 0.0
    trend_day_drift_bps: float = 60.0


def _profile() -> np.ndarray:
    m = np.arange(MINUTES)
    p = 1.0 + 1.6 * np.exp(-m / 18.0) + 0.7 * np.exp(-(MINUTES - 1 - m) / 25.0)
    return p / np.sqrt(np.mean(p**2))


def synthetic_roles() -> dict[str, Any]:
    return {
        "nifty": "SYN_INDEX|Nifty 50",
        "banknifty": "SYN_INDEX|Nifty Bank",
        "vix": "SYN_INDEX|India VIX",
        "sectors": {s: ("SYN_INDEX|Nifty Bank" if s == "BANK" else f"SYN_INDEX|{s}") for s in SECTORS},
    }


def build_instruments(p: SynthParams, rng: np.random.Generator) -> tuple[list[Instrument], dict[str, dict[str, float]]]:
    insts: list[Instrument] = []
    params: dict[str, dict[str, float]] = {}
    for i in range(p.symbols):
        sector = SECTORS[i % len(SECTORS)]
        sym = f"S{sector[:4]}{i // len(SECTORS) + 1:02d}"
        key = f"SYN_EQ|{sym}"
        price = float(np.exp(rng.normal(np.log(700), 0.9)))
        if i % 13 == 5:
            price = float(rng.uniform(20, 45))      # a few sub-₹50 names to exercise the price filter
        tv_cr = float(np.exp(rng.normal(np.log(250), 0.9)))
        if i % 11 == 3:
            tv_cr = float(rng.uniform(10, 40))      # a few illiquid names to exercise the liquidity filter
        adv = tv_cr * 1e7 / price
        lot = int(max(1, round(600000 / price / 25.0)) * 25)
        insts.append(Instrument(key=key, symbol=sym, name=f"Synthetic {sector} {i}", segment="SYN_EQ",
                                kind="EQ", lot_size=lot, tick_size=0.05, sector=sector, is_fno=True))
        params[key] = {
            "price": price,
            "adv": adv,
            "beta": float(rng.uniform(0.7, 1.4)),
            "load": float(rng.uniform(0.5, 1.0)),
            "idio": float(rng.uniform(0.009, 0.02)),
        }
    roles = synthetic_roles()
    for name, key in [("Nifty 50", roles["nifty"]), ("Nifty Bank", roles["banknifty"]), ("India VIX", roles["vix"])]:
        insts.append(Instrument(key=str(key), symbol=name, segment="SYN_INDEX", kind="INDEX", sector="INDEX"))
    for s, key in roles["sectors"].items():
        if s == "BANK":
            continue  # Nifty Bank doubles as the BANK sector index
        insts.append(Instrument(key=key, symbol=f"Nifty {s}", segment="SYN_INDEX", kind="INDEX", sector=s))
    return insts, params


def _round_tick(x: np.ndarray, tick: float = 0.05) -> np.ndarray:
    return np.round(x / tick) * tick


def generate(store_root, p: SynthParams, cal: TradingCalendar | None = None) -> CandleStore:
    rng = np.random.default_rng(p.seed)
    cal = cal or TradingCalendar.from_config()
    days: list[date] = []
    d = p.start
    while len(days) < p.days:
        if cal.is_trading_day(d):
            days.append(d)
        d += timedelta(days=1)

    insts, sp = build_instruments(p, rng)
    stocks = [i for i in insts if i.kind == "EQ"]
    keys = [i.key for i in stocks]
    n = len(keys)
    sec_idx = np.array([SECTORS.index(i.sector) for i in stocks])
    beta = np.array([sp[k]["beta"] for k in keys])
    load = np.array([sp[k]["load"] for k in keys])
    idio = np.array([sp[k]["idio"] for k in keys])
    adv = np.array([sp[k]["adv"] for k in keys])
    price = np.array([sp[k]["price"] for k in keys])

    roles = synthetic_roles()
    nifty_lvl, sec_lvl = 22000.0, rng.uniform(8000, 40000, len(SECTORS))
    vix_lvl = 14.0
    prof = _profile()
    prof_sub = np.repeat(prof, SUB)
    steps = MINUTES * SUB
    vol_prof = (1.0 + 2.2 * np.exp(-np.arange(MINUTES) / 20.0) + 1.2 * np.exp(-(MINUTES - 1 - np.arange(MINUTES)) / 30.0))
    vol_prof /= vol_prof.sum()
    log_sig = np.log(0.009)
    alpha = 1.0 / (15 * SUB)
    c = float(np.clip(p.idio_momentum, 0.0, 0.95))

    out: dict[str, list[pd.DataFrame]] = {k: [] for k in keys}
    idx_out: dict[str, list[pd.DataFrame]] = {
        str(roles["nifty"]): [], str(roles["vix"]): [], **{v: [] for v in roles["sectors"].values()}
    }
    open_t = time(9, 15)

    for d in days:
        ts = pd.date_range(datetime.combine(d, open_t, tzinfo=IST), periods=MINUTES, freq="1min")
        dt_type = rng.choice(DAY_TYPES, p=DAY_TYPE_P)
        log_sig = 0.93 * log_sig + 0.07 * np.log(0.009) + rng.normal(0, 0.18)
        sig_m = float(np.exp(log_sig)) * (1.6 if dt_type == "CHOP" else 1.0)
        sig_step = sig_m / np.sqrt(steps) * prof_sub

        # ---- market factor ----
        drift_total = p.trend_day_drift_bps / 1e4 * (1 if dt_type == "TREND_UP" else -1 if dt_type == "TREND_DOWN" else 0)
        gap_m = rng.normal(0, 0.35 * sig_m) + 0.25 * drift_total
        z = rng.normal(0, 1, steps)
        r_m = z * sig_step + drift_total / steps
        if dt_type == "RANGE":
            # mean-revert towards the open (OU on the cumulative path)
            cum, kappa = 0.0, 0.02
            r_adj = np.empty(steps)
            for t in range(steps):
                r_adj[t] = r_m[t] - kappa * cum
                cum += r_adj[t]
            r_m = r_adj

        # ---- sectors ----
        sec_sig = 0.006
        sec_drift = rng.normal(0, 0.003, len(SECTORS)) * (1.5 if dt_type.startswith("TREND") else 1.0)
        r_s = rng.normal(0, 1, (len(SECTORS), steps)) * (sec_sig / np.sqrt(steps)) * prof_sub + (sec_drift / steps)[:, None]
        gap_s = rng.normal(0, 0.003, len(SECTORS))

        # ---- stocks ----
        e = rng.normal(0, 1, (n, steps)) * (idio[:, None] / np.sqrt(steps)) * prof_sub
        if c > 0:
            e = lfilter([1.0, -(1.0 - alpha)], [1.0, -(1.0 - alpha + c * alpha)], e, axis=1)
        r_i = beta[:, None] * r_m[None, :] + load[:, None] * r_s[sec_idx] + e
        news = rng.random(n) < 0.02
        gap_i = beta * gap_m + load * gap_s[sec_idx] + rng.normal(0, 0.006, n) + news * rng.choice([-1, 1], n) * rng.uniform(0.03, 0.06, n)

        open_px = price * np.exp(gap_i)
        path = open_px[:, None] * np.exp(np.cumsum(r_i, axis=1))
        sub = path.reshape(n, MINUTES, SUB)
        o = np.concatenate([open_px[:, None], sub[:, :-1, -1]], axis=1)
        h = np.maximum(sub.max(axis=2), o)
        lo = np.minimum(sub.min(axis=2), o)
        cl = sub[:, :, -1]
        o, h, lo, cl = (_round_tick(x) for x in (o, h, lo, cl))
        h = np.maximum.reduce([h, o, cl])
        lo = np.minimum.reduce([lo, o, cl])

        day_factor = np.exp(rng.normal(0, 0.25, n)) * (1.4 if dt_type in ("CHOP", "TREND_UP", "TREND_DOWN") else 1.0) * (1 + 1.5 * news)
        min_ret = np.abs(np.log(cl / o)) / (idio[:, None] / np.sqrt(MINUTES))
        vol = adv[:, None] * day_factor[:, None] * vol_prof[None, :] * (0.6 + 0.4 * np.minimum(min_ret, 5)) * np.exp(rng.normal(0, 0.3, (n, MINUTES)))
        vol = np.maximum(np.round(vol), 1.0)
        for j, k in enumerate(keys):
            out[k].append(pd.DataFrame({"ts": ts, "open": o[j], "high": h[j], "low": lo[j], "close": cl[j],
                                        "volume": vol[j], "oi": np.nan}))
        price = cl[:, -1].copy()

        # ---- indices (minute OHLC from the factor paths) ----
        def _idx_frame(level0: float, r: np.ndarray, gap: float, ts: pd.DatetimeIndex = ts) -> tuple[pd.DataFrame, float]:
            op = level0 * np.exp(gap)
            pth = (op * np.exp(np.cumsum(r))).reshape(MINUTES, SUB)
            io = np.concatenate([[op], pth[:-1, -1]])
            ih, il, ic = np.maximum(pth.max(1), io), np.minimum(pth.min(1), io), pth[:, -1]
            f = pd.DataFrame({"ts": ts, "open": np.round(io, 2), "high": np.round(ih, 2), "low": np.round(il, 2),
                              "close": np.round(ic, 2), "volume": 0.0, "oi": np.nan})
            return f, float(ic[-1])

        f, nifty_lvl = _idx_frame(nifty_lvl, r_m, gap_m)
        idx_out[str(roles["nifty"])].append(f)
        for si, s in enumerate(SECTORS):
            f, sec_lvl[si] = _idx_frame(sec_lvl[si], r_m + r_s[si], gap_m + gap_s[si])
            idx_out[roles["sectors"][s]].append(f)
        # VIX: anchored to the vol regime, moves inversely to the market intraday
        target = 100 * sig_m * np.sqrt(252) * 0.95
        vix_open = vix_lvl + 0.5 * (target - vix_lvl) + rng.normal(0, 0.3)
        vpath = vix_open * np.exp(-4.0 * np.cumsum(r_m) + np.cumsum(rng.normal(0, 0.0004, steps)))
        vp = vpath.reshape(MINUTES, SUB)
        vo = np.concatenate([[vix_open], vp[:-1, -1]])
        idx_out[str(roles["vix"])].append(pd.DataFrame({
            "ts": ts, "open": np.round(vo, 2), "high": np.round(np.maximum(vp.max(1), vo), 2),
            "low": np.round(np.minimum(vp.min(1), vo), 2), "close": np.round(vp[:, -1], 2), "volume": 0.0, "oi": np.nan}))
        vix_lvl = float(vp[-1, -1])

    store = CandleStore(store_root, "synthetic")
    for k, frames in {**out, **idx_out}.items():
        store.write(k, pd.concat(frames, ignore_index=True), merge=False)
    store.write_instruments(insts, roles)
    store.write_manifest({
        "kind": "SYNTHETIC: NOT MARKET DATA",
        "created": datetime.now(tz=IST).isoformat(),
        "params": {**p.__dict__, "start": p.start.isoformat()},
        "days": [days[0].isoformat(), days[-1].isoformat()],
        "n_days": len(days),
    })
    return store
