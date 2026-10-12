"""Meta-model: ships only if it beats the rule book out-of-sample; end-to-end on a planted edge."""

from __future__ import annotations

import asyncio
from datetime import date

import numpy as np
import pandas as pd
import pytest

from dalal.backtest.run import run_research
from dalal.config import load_settings
from dalal.data.store import CandleStore
from dalal.data.synthetic import SynthParams, generate
from dalal.live.engine import attach_engine
from dalal.live.runtime import Runtime
from dalal.scoring.metamodel import compare, shap_bullets


def _wf(n: int = 2000, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    win = rng.random(n) < 0.5
    return pd.DataFrame({"win": win, "p_cal_oos": np.full(n, 0.5) + rng.normal(0, 0.01, n),
                         "r_net": np.where(win, 1.0, -1.0)})


def test_ship_gate():
    wf = _wf()
    good = pd.Series(np.where(wf["win"], 0.7, 0.3), index=wf.index)
    assert compare(wf, good).shipped
    rand = pd.Series(np.random.default_rng(9).random(len(wf)), index=wf.index)
    rep = compare(wf, rand)
    assert not rep.shipped and "does NOT ship" in rep.reason
    tiny = compare(wf.iloc[:50], good.iloc[:50])
    assert not tiny.shipped and "insufficient" in tiny.reason


def test_shap_bullets_text():
    b = shap_bullets([("rs_mkt_z", 0.2, 1.84), ("rvol_cum", 0.1, 2.1), ("t", 0.05, 12.0)])
    assert b[0] == "RS vs Nifty +1.8σ (in the trade's direction)" and b[1].startswith("Volume 2.1×")


@pytest.mark.slow
def test_end_to_end_planted_edge_produces_real_ideas(tmp_path):
    """With a planted edge: setups pass the gate, the meta-model is evaluated, and the live
    engine shows NON-bypassed ideas on a replayed out-of-sample day."""
    root = tmp_path / "edge"
    generate(root, SynthParams(symbols=16, days=300, seed=21, idio_momentum=0.6, trend_day_drift_bps=0,
                               start=date(2024, 1, 1)))
    store = CandleStore(root, "synthetic")
    days = store.days(store.instruments()[1]["nifty"])
    replay_days = days[-6:]
    over = {"data": {"root": str(root), "provider": "synthetic"},
            "backtest": {"wf_train_months": 4, "wf_test_months": 2, "end": str(days[-7])},
            "board": {"gate": {"min_n": 100}}}
    s = load_settings(overrides=over)
    summ = run_research(s, store)
    assert any(v["verdict"] == "EDGE" for v in summ["verdicts"])
    assert summ["data_range"][1] < str(replay_days[0])                 # replay days are out-of-sample
    assert "ship" in summ["meta_model"]["reason"]

    async def go():
        rt = Runtime(s)
        eng = attach_engine(rt)
        ideas, rej = [], {}
        for d in replay_days:
            await rt.start_replay(d, speed=0)
            await rt.task
            ideas += list(eng.ideas.values())
            for k, v in eng.rejections.items():
                rej[k] = rej.get(k, 0) + v
        return eng, ideas, rej

    eng, ideas, rej = asyncio.run(go())
    real = [i for i in ideas if not i.gate_bypassed]
    assert real, f"no qualifying ideas; rejections: {rej}"
    for i in real:
        assert i.n >= 100 and i.cons_ev > 0 and i.ci_lo > 0
        if eng.meta is not None:
            assert i.snapshot.get("p_model") == "lightgbm-meta"
