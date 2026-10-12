"""Phase 4 acceptance on a replayed day: live/replay signals == research signals; ideas appear,
update status and expire; the 5-minute pipeline stays inside the 1-second budget."""

from __future__ import annotations

import asyncio

import numpy as np
import pytest

from dalal.config import load_settings
from dalal.live.engine import attach_engine
from dalal.live.runtime import Runtime
from dalal.pipeline import MarketBuilder, eligibility, load_index_panels, setup_context, symbol_features
from dalal.setups.library import build_setups


def _research_signals(settings, store, day):
    insts, roles = store.instruments()
    stocks = [i for i in insts if i.kind == "EQ"]
    idx = load_index_panels(store, roles, store.read)
    mb = MarketBuilder(idx)
    cache = {}
    for inst in stocks:
        p, ctx, F = symbol_features(inst.key, store.read(inst.key), idx)
        el = eligibility(ctx, settings.universe)
        mb.add_breadth(p.days, el, F, ctx)
        cache[inst.key] = (inst, p, ctx, F, el)
    mkt = mb.build()
    out = set()
    for inst, p, ctx, F, el in cache.values():
        sc = setup_context(inst, p, ctx, F, mkt, idx)
        if day not in list(p.days):
            continue
        d = list(p.days).index(day)
        if not el[d]:
            continue
        for st in build_setups(settings):
            for side, ss in st.evaluate(sc).items():
                for t in np.where(ss.mask[d])[0]:
                    out.add((inst.symbol, st.name, side, int(t), round(float(ss.trigger[d, t]), 4),
                             round(float(ss.stop[d, t]), 4)))
    return out


async def _replay(settings, day):
    rt = Runtime(settings)
    eng = attach_engine(rt)
    msgs = []
    orig = rt.bus.publish

    def pub(m, sticky=False):
        msgs.append(m)
        orig(m, sticky)

    rt.bus.publish = pub
    await rt.start_replay(day, speed=0)
    await rt.task
    return rt, eng, msgs


@pytest.fixture(scope="module")
def replayed(syn_root):
    s = load_settings(overrides={"data": {"root": str(syn_root), "provider": "synthetic"},
                                 "board": {"demo_bypass_gates": True}})
    from dalal.backtest.run import run_research
    from dalal.data.store import CandleStore
    store = CandleStore(syn_root, "synthetic")
    # tiny walk-forward so a (synthetic) stats book exists for the engine to read
    s2 = load_settings(overrides={"data": {"root": str(syn_root)}, "backtest": {"wf_train_months": 1, "wf_test_months": 1}})
    run_research(s2, store)
    day = store.days(store.instruments()[1]["nifty"])[-2]
    rt, eng, msgs = asyncio.run(_replay(s, day))
    return s, store, day, rt, eng, msgs


def test_replay_signals_equal_research(replayed):
    s, store, day, _rt, eng, _ = replayed
    live = {(r["symbol"], r["setup"], r["side"], r["t"], round(r["trigger"], 4), round(r["stop"], 4))
            for r in eng.raw_signals}
    research = _research_signals(s, store, day)
    assert research, "expected some raw signals on the test day"
    assert live == research


def test_ideas_appear_update_and_resolve(replayed):
    _s, _store, _day, _rt, eng, msgs = replayed
    types = [m["type"] for m in msgs]
    assert "idea_new" in types and "idea_update" in types and "board" in types and "market" in types
    statuses = {i.status for i in eng.ideas.values()}
    assert statuses <= {"STOPPED", "T2_HIT", "CLOSED", "EXPIRED", "INVALIDATED", "DISPLACED"}, statuses
    assert all(not i.active for i in eng.ideas.values())          # nothing left open after 15:15
    for i in eng.ideas.values():
        assert i.gate_bypassed or not i.gate_reasons               # demo bypass is flagged on every card
        assert i.provenance == "synthetic" and len(i.thesis) == 3 and i.qty > 0
    board = [m for m in msgs if m["type"] == "board"][-1]
    assert len(board["longs"]) <= 5 and len(board["shorts"]) <= 5


def test_five_minute_pipeline_latency(replayed):
    eng = replayed[4]
    assert max(eng.timings) < 1000.0                               # §11: idea in UI within 1 s of candle close


def test_no_ideas_without_bypass_on_unqualified_book(syn_root):
    s = load_settings(overrides={"data": {"root": str(syn_root), "provider": "synthetic"}})
    from dalal.data.store import CandleStore
    store = CandleStore(syn_root, "synthetic")
    day = store.days(store.instruments()[1]["nifty"])[-3]
    _rt, eng, _msgs = asyncio.run(_replay(s, day))
    qualified = eng.book.qualified_setups() if eng.book else []
    if not qualified:
        assert not eng.ideas                                       # honest board: nothing qualifies → nothing shown
        assert any("N " in k or "conservative EV" in k or "CI low" in k for k in eng.rejections)
