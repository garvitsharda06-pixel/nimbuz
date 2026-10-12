from __future__ import annotations

import itertools
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from dalal.backtest.costs import CostModel, load_costs
from dalal.backtest.engine import Research
from dalal.backtest.robustness import deflated_sharpe, monte_carlo
from dalal.backtest.run import run_research
from dalal.backtest.simulate import position_qty, simulate
from dalal.backtest.walkforward import make_folds, walk_forward
from dalal.config import load_settings
from dalal.data.store import CandleStore
from dalal.data.synthetic import SynthParams, generate
from dalal.scoring.stats import breakeven_p, bucket_stats, ev, wilson

COST0 = CostModel("cash", 0, 0, 0, 0, 0, 0, 0)


def _day(path: list[tuple[float, float, float, float]]) -> tuple[np.ndarray, ...]:
    n = 375
    a = np.array(path + [path[-1]] * (n - len(path)), dtype=float)
    return a[:, 0], a[:, 1], a[:, 2], a[:, 3]


def _sim(path, trigger=101.0, stop=99.0, t1=103.0, t2=105.0, start=1, **kw):
    o, h, lo, c = _day(path)
    args = dict(valid_until_min=30, time_exit_min=360, tick=0.05, slip=0.0, costs=COST0, capital=100000,
                risk_pct=1.0, lot=1, leverage=5.0)
    args.update(kw)
    return simulate("long", o, h, lo, c, start, trigger, stop, t1, t2, **args)


def test_fill_rules():
    flat = (100, 100.5, 99.5, 100)
    # trigger then T1 then T2
    f = _sim([flat, flat, (100.5, 101.2, 100.4, 101.1), (101.1, 103.2, 101, 103.1), (103.1, 105.3, 103, 105.2)])
    assert f.triggered and f.win and f.exit_reason == "T1_T2"
    assert f.entry_px == 101.0 and f.qty == 500 and f.r_gross == pytest.approx(0.5 * 1 + 0.5 * 2)
    # stop trades before trigger → void
    f = _sim([flat, (100, 100.2, 98.9, 99.2), (99.2, 101.5, 99, 101.4)])
    assert not f.triggered and f.void_reason == "stop_before_trigger"
    # one minute touches both stop and T1 after entry → stop assumed first
    f = _sim([flat, (100.5, 101.2, 100.4, 101.1), (101, 103.5, 98.5, 101)])
    assert f.triggered and not f.win and f.exit_reason == "STOP" and f.r_gross == pytest.approx(-1)
    # gap through trigger fills at the open (worse)
    f = _sim([flat, (101.6, 101.8, 101.5, 101.7), (101.7, 104, 101.6, 103.9)])
    assert f.entry_px == 101.6
    # T1, then back to breakeven
    f = _sim([flat, (100.5, 101.2, 100.4, 101.1), (101.1, 103.2, 101.05, 102), (102, 102.1, 100.9, 101)])
    assert f.win and f.exit_reason == "T1_BE" and f.r_gross == pytest.approx(0.5)
    # nothing happens → time exit at 15:15 open
    f = _sim([flat, (100.5, 101.2, 100.4, 101.1)] + [(101.1, 101.3, 100.8, 101.2)] * 400)
    assert f.exit_reason == "TIME" and not f.win
    # never triggers inside the validity window → expired
    f = _sim([flat] * 40)
    assert not f.triggered and f.void_reason == "expired"
    # a touch of T1 is not a fill: limit needs a trade one tick through
    f = _sim([flat, (100.5, 101.2, 100.4, 101.1), (101.1, 103.0, 101.05, 102.9), (102.9, 102.95, 98.0, 98.5)])
    assert not f.win and f.exit_reason == "STOP"


def test_short_side_mirror():
    o, h, lo, c = _day([(100, 100.5, 99.5, 100), (99.5, 99.6, 98.8, 98.9), (98.9, 99.0, 96.9, 97.0)])
    f = simulate("short", o, h, lo, c, 1, 99.0, 101.0, 97.0, 95.0, 30, 360, 0.05, 0.0, COST0, 100000, 1.0)
    assert f.triggered and f.win and f.entry_px == 99.0


def test_cost_model_hand_calculation():
    cm = load_costs("cash")
    # buy 100 @ 1000 (₹1,00,000), sell 100 @ 1010 (₹1,01,000), Upstox intraday
    buy = min(20, 0.001 * 100000) + 0.0000307 * 100000 + 0.000001 * 100000 + 0.00003 * 100000
    buy += 0.18 * (20 + 0.0000307 * 100000 + 0.000001 * 100000)
    sell = 20 + 0.0000307 * 101000 + 0.000001 * 101000 + 0.00025 * 101000
    sell += 0.18 * (20 + 0.0000307 * 101000 + 0.000001 * 101000)
    assert cm.trade("long", 1000, 100, [(1010, 100)]) == pytest.approx(buy + sell, rel=1e-9)
    fut = load_costs("futures")
    assert fut.stt_sell == 0.0005                       # 0.05 % since 1 Apr 2026 (D-020)
    assert cm.stt_sell == 0.00025


def test_position_sizing():
    assert position_qty(500000, 0.5, 1000, 990, 1, 5.0) == 250          # ₹2,500 risk / ₹10
    assert position_qty(500000, 0.5, 1000, 999.9, 1, 5.0) == 2500       # capped by MIS notional (5× capital)
    assert position_qty(500000, 0.5, 1000, 990, 75, 5.0) == 225         # futures lot rounding


def test_stats_math():
    lo, hi = wilson(60, 100)
    assert lo == pytest.approx(0.5020, abs=1e-3) and hi == pytest.approx(0.6906, abs=1e-3)
    assert breakeven_p(1.0, -1.0) == pytest.approx(0.5)
    assert breakeven_p(2.0, -1.0) == pytest.approx(1 / 3)
    assert ev(0.55, 2.0, -1.0) == pytest.approx(0.65)
    b = bucket_stats(np.array([1, 1, 0, 0, 1]), np.array([1.0, 2.0, -1.0, -1.0, 1.0]))
    assert b.n == 5 and b.wins == 3 and b.expectancy == pytest.approx(0.4) and b.profit_factor == pytest.approx(2.0)


def test_deflated_sharpe_haircut():
    rng = np.random.default_rng(0)
    r = rng.normal(0.05, 1.0, 2000)
    one = deflated_sharpe(r, 1)
    many = deflated_sharpe(r, 50)
    assert many["dsr"] < one["dsr"]                 # more trials → bigger haircut
    mc = monte_carlo(r, 200)
    assert mc["dd_p95"] >= mc["dd_p50"]


def test_folds_have_embargo_and_no_overlap():
    days = list(pd.bdate_range("2023-01-02", "2024-12-31").date)
    folds = make_folds(days, 12, 3, 2)
    assert folds and folds[0].test_start == date(2024, 1, 2)
    for f in folds:
        assert f.train_end < f.test_start
        assert len([d for d in days if f.train_end <= d < f.test_start]) == 2
    for a, b in itertools.pairwise(folds):
        assert a.test_end == b.test_start


def test_walk_forward_predictions_use_only_past():
    rng = np.random.default_rng(1)
    days = list(pd.bdate_range("2023-01-02", "2024-06-28").date)
    rows = [{"day": d, "setup": "x", "side": "long", "regime": 0, "win": bool(rng.random() < (0.3 if d.year == 2023 else 0.9)),
             "r_net": 0.0} for d in days for _ in range(3)]
    wf, _ = walk_forward(pd.DataFrame(rows), 12, 3, 2, prior_strength=0, min_bucket_n=1)
    first = wf[wf["fold"] == 0]
    # the 2024 regime change must NOT leak into predictions for the first OOS fold (trained on 2023 only)
    assert first["p_oos"].mean() < 0.4


def test_research_is_deterministic(syn_store, settings):
    syms = [i.symbol for i in syn_store.instruments()[0] if i.kind == "EQ"][:3]
    a = Research(settings, syn_store, symbols=syms).run().instances
    b = Research(settings, syn_store, symbols=syms).run().instances
    pd.testing.assert_frame_equal(a, b)
    assert len(a) > 0


@pytest.mark.slow
def test_known_answer_planted_edge_found_noise_rejected(tmp_path: Path):
    """The research engine must find a planted momentum edge and reject pure noise."""
    over = {"backtest": {"wf_train_months": 3, "wf_test_months": 2}, "board": {"gate": {"min_n": 100}}}
    out = {}
    for name, mom in [("null", 0.0), ("edge", 0.6)]:
        root = tmp_path / name
        generate(root, SynthParams(symbols=12, days=260, seed=21, idio_momentum=mom, trend_day_drift_bps=0,
                                   start=date(2024, 1, 1)))
        s = load_settings(overrides={"data": {"root": str(root)}, **over})
        summ = run_research(s, CandleStore(root, "synthetic"), setups=["orb", "rel_strength"])
        out[name] = {(v["setup"], v["side"]): v for v in summ["verdicts"]}
    for k in [("orb", "long"), ("orb", "short")]:
        assert out["edge"][k]["exp_r"] > 0.05
        assert out["edge"][k]["exp_r"] > out["null"][k]["exp_r"] + 0.1
    assert not any(v["verdict"] == "EDGE" for v in out["null"].values())
    assert any(v["verdict"] == "EDGE" for v in out["edge"].values())
