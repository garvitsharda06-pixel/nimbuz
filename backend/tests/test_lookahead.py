"""Look-ahead and parity tests (§9): perturbing future bars must never change past values;
the live (partial-day) path must equal the research (full-history) path exactly."""

from __future__ import annotations

from datetime import time

import numpy as np
import pandas as pd
import pytest

from dalal.clock import IST
from dalal.features.core import daily_context, intraday
from dalal.features.panel import panel_from_1m
from dalal.pipeline import MarketBuilder, eligibility, load_index_panels, setup_context, symbol_features
from dalal.setups.library import ALL_SETUPS

KEYS_F = ["vwap", "vwap_sd", "ema20", "ema9", "ema_slope", "atr5", "swing_lo3", "swing_hi3", "trend15", "or_h", "or_l",
          "day_hi", "day_lo", "rvol_cum", "rvol_bar", "ret_pdc", "rv", "rv_ratio"]


def _perturb_after(df: pd.DataFrame, cut: pd.Timestamp, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    out = df.copy()
    m = out["ts"] >= cut
    f = np.exp(rng.normal(0, 0.03, m.sum()))
    for c in ["open", "high", "low", "close"]:
        out.loc[m, c] = out.loc[m, c] * f
    out.loc[m, "high"] = out.loc[m, ["open", "high", "low", "close"]].max(axis=1)
    out.loc[m, "low"] = out.loc[m, ["open", "high", "low", "close"]].min(axis=1)
    out.loc[m, "volume"] = out.loc[m, "volume"] * rng.uniform(0.2, 5, m.sum())
    return out


def _eq(a: np.ndarray, b: np.ndarray) -> bool:
    return bool(np.array_equal(a, b, equal_nan=True))


@pytest.fixture(scope="module")
def one_symbol(syn_store):
    insts, roles = syn_store.instruments()
    eq = next(i for i in insts if i.kind == "EQ")
    return eq, syn_store.read(eq.key), roles


def test_features_ignore_future_bars(one_symbol):
    inst, df, _ = one_symbol
    days = sorted(df["ts"].dt.tz_convert(IST).dt.date.unique())
    d = days[30]
    cut = pd.Timestamp.combine(d, time(11, 0)).tz_localize(IST)
    p0 = panel_from_1m(inst.key, df)
    p1 = panel_from_1m(inst.key, _perturb_after(df, cut))
    c0, c1 = daily_context(p0), daily_context(p1)
    F0, F1 = intraday(p0, c0), intraday(p1, c1)
    di = list(p0.days).index(d)
    last_bar = (11 * 60 - (9 * 60 + 15)) // 5 - 1          # bar closing at 11:00 is complete before the cut
    for k in ["pdc", "atr14", "sigma_d", "med_tv20", "adv20"]:
        assert _eq(c0[k][: di + 1], c1[k][: di + 1]), k        # today's daily context uses only prior days
    for k in KEYS_F:
        assert _eq(F0[k][:di], F1[k][:di]), k                  # earlier days untouched
        assert _eq(F0[k][di, : last_bar + 1], F1[k][di, : last_bar + 1]), k
    assert not _eq(F0["vwap"][di], F1["vwap"][di])             # sanity: the perturbation did change the future


def test_live_partial_panel_equals_research(one_symbol, syn_store):
    """Live builds a short panel (45 prior days + today so far); research uses full history."""
    inst, df, _roles = one_symbol
    days = sorted(df["ts"].dt.tz_convert(IST).dt.date.unique())
    d = days[38]
    minutes_seen = 137                                       # 11:32 → bars up to 11:30 complete
    full = panel_from_1m(inst.key, df)
    Ff = intraday(full, daily_context(full))
    hist = df[df["ts"].dt.tz_convert(IST).dt.date <= d]
    hist = hist[hist["ts"].dt.tz_convert(IST).dt.date >= days[38 - 25]]      # only 25 prior days loaded
    today_cut = pd.Timestamp.combine(d, time(9, 15)).tz_localize(IST) + pd.Timedelta(minutes=minutes_seen)
    hist = hist[hist["ts"] < today_cut]
    part = panel_from_1m(inst.key, hist, partial_last_day_minutes=minutes_seen)
    Fp = intraday(part, daily_context(part))
    di = list(full.days).index(d)
    nb = minutes_seen // 5
    for k in KEYS_F:
        assert np.allclose(Ff[k][di, :nb], Fp[k][-1, :nb], equal_nan=True, rtol=0, atol=1e-12), k
    assert np.isnan(Fp["c"][-1, nb:]).all()                   # forming/future bars invisible


def test_signals_ignore_future(syn_store, settings):
    insts, roles = syn_store.instruments()
    stocks = [i for i in insts if i.kind == "EQ"][:4]
    nifty_df = syn_store.read(roles["nifty"])
    days = sorted(nifty_df["ts"].dt.tz_convert(IST).dt.date.unique())
    d = days[32]
    cut = pd.Timestamp.combine(d, time(12, 0)).tz_localize(IST)
    nb = (12 * 60 - (9 * 60 + 15)) // 5

    def run(perturb: bool):
        reader = (lambda k: _perturb_after(syn_store.read(k), cut, seed=hash(k) % 1000)) if perturb else syn_store.read
        idx = load_index_panels(syn_store, roles, reader)
        mb = MarketBuilder(idx)
        feats = {}
        for inst in stocks:
            p, ctx, F = symbol_features(inst.key, reader(inst.key), idx)
            mb.add_breadth(p.days, eligibility(ctx, settings.universe), F, ctx)
            feats[inst.key] = (inst, p, ctx, F)
        mkt = mb.build()
        masks = {}
        for key, (inst, p, ctx, F) in feats.items():
            sc = setup_context(inst, p, ctx, F, mkt, idx)
            di = list(p.days).index(d)
            for cls in ALL_SETUPS:
                for side, ss in cls().evaluate(sc).items():
                    masks[(key, cls.name, side)] = (ss.mask[di, :nb].copy(), ss.trigger[di, :nb].copy(),
                                                    ss.stop[di, :nb].copy())
        return mkt, masks

    m0, s0 = run(False)
    m1, s1 = run(True)
    mi = list(m0.days).index(d)
    assert _eq(m0.regime[mi, :nb], m1.regime[mi, :nb])
    assert _eq(m0.breadth[mi, :nb], m1.breadth[mi, :nb])
    for k in s0:
        assert _eq(s0[k][0], s1[k][0]), k
        assert _eq(np.where(s0[k][0], s0[k][1], np.nan), np.where(s1[k][0], s1[k][1], np.nan)), k


def test_oi_classification_and_causality():
    import numpy as np

    from dalal.features.oi import LONG_BUILDUP, LONG_UNWINDING, SHORT_BUILDUP, SHORT_COVERING, oi_frame

    c = np.full((2, 375), 100.0)
    oi = np.full((2, 375), 1000.0)
    c[1, :] = 101.0
    oi[1, :] = 1100.0                                   # price +1 %, OI +10 % → long build-up
    of = oi_frame(c, oi)
    assert of["oi_state"][1, 5] == LONG_BUILDUP and abs(of["oi_chg"][1, 5] - 0.1) < 1e-12
    for px, o, want in [(99.0, 1100.0, SHORT_BUILDUP), (101.0, 900.0, SHORT_COVERING), (99.0, 900.0, LONG_UNWINDING)]:
        c[1, :], oi[1, :] = px, o
        assert oi_frame(c, oi)["oi_state"][1, 10] == want
    c2, oi2 = c.copy(), oi.copy()
    c2[1, 200:] = 50.0                                  # future minutes must not change earlier bars
    oi2[1, 200:] = 1.0
    a, b = oi_frame(c, oi), oi_frame(c2, oi2)
    assert np.array_equal(a["oi_state"][1, :39], b["oi_state"][1, :39])
