from __future__ import annotations

from datetime import datetime

import numpy as np
import pytest

from dalal.clock import IST
from dalal.config import load_settings
from dalal.risk.guardrails import Blackout, check_guardrails
from dalal.risk.sizing import size_position
from dalal.scoring.board import corr_from_returns, net_beta, select_board
from dalal.scoring.calibration import Calibrator
from dalal.scoring.candidates import RawSignal, make_candidate
from dalal.scoring.explain import thesis
from dalal.scoring.ideas import Idea
from dalal.scoring.statsbook import BookEntry, StatsBook

S = load_settings()


def idea(sym: str, side: str = "long", ev: float = 0.2, sector: str = "IT", beta: float = 1.0, notional: float = 100000,
         status: str = "WATCHING") -> Idea:
    return Idea(id=f"{sym}-{side}", day="2025-01-02", signal_ts="", symbol=sym, key=sym, sector=sector, setup="orb",
                setup_label="ORB", side=side, regime="TREND_UP", grade="A", entry=100, entry_zone=(100, 100.2), stop=99,
                t1=101, t2=102, rr1=1, rr2=2, p=0.6, ci_lo=0.55, ci_hi=0.65, n=300, expectancy=0.2, cons_ev=ev,
                breakeven=0.5, win_r=1.2, loss_r=-1, qty=100, risk_inr=100, notional=notional, valid_until="",
                expires_at="", thesis=[], invalidation="", beta=beta, model_version="", config_hash="",
                provenance="synthetic", mode="REPLAY", status=status)


NOCORR = lambda a, b: None  # noqa: E731


def test_never_pads_and_caps_per_side():
    cands = [idea(f"S{i}", sector=f"SEC{i}", ev=0.1 + i / 100) for i in range(8)]
    d = select_board([], cands, S.board, S.risk, NOCORR)
    assert len(d.longs) == 5 and d.longs[0].symbol == "S7"      # top 5 by conservative EV
    d2 = select_board([], cands[:2], S.board, S.risk, NOCORR)
    assert len(d2.longs) == 2 and d2.shorts == []                # fewer qualify → show fewer


def test_sector_cap_correlation_and_beta():
    a, b, c = idea("A", ev=0.3), idea("B", ev=0.25), idea("C", ev=0.2)          # all IT longs
    d = select_board([], [a, b, c], S.board, S.risk, NOCORR)
    assert [i.symbol for i in d.longs] == ["A", "B"] and "sector cap" in d.rejected["C-long"]
    x, y = idea("X", ev=0.3, sector="BANK"), idea("Y", ev=0.2, sector="AUTO")
    d = select_board([], [x, y], S.board, S.risk, lambda p, q: 0.9)
    assert [i.symbol for i in d.longs] == ["X"] and "near-duplicate" in d.rejected["Y-long"]
    big = [idea(f"B{i}", ev=0.3 - i / 100, sector=f"S{i}", beta=1.4) for i in range(4)]
    d = select_board([], big, S.board, S.risk, NOCORR)
    assert abs(net_beta(d.longs)) <= S.board.max_net_beta and len(d.longs) == 3       # 4 × 1.4 = 5.6 > 5
    assert any("net beta" in r for r in d.rejected.values())
    hedge = select_board(d.longs, [idea("H", side="short", ev=0.1, sector="Z", beta=1.4)], S.board, S.risk, NOCORR)
    assert [i.symbol for i in hedge.shorts] == ["H"]                                     # shorts reduce net beta


def test_hysteresis_no_flicker():
    inc = [idea(f"I{i}", ev=0.2, sector=f"S{i}") for i in range(5)]
    slightly = idea("NEW", ev=0.23, sector="NEWSEC")                # +15 % < 20 % → stays out
    d = select_board(inc, [slightly], S.board, S.risk, NOCORR)
    assert "NEW" not in [i.symbol for i in d.longs] and "hysteresis" in d.rejected["NEW-long"]
    much = idea("NEW", ev=0.25, sector="NEWSEC")                    # +25 % → replaces the weakest
    d = select_board(inc, [much], S.board, S.risk, NOCORR)
    assert "NEW" in [i.symbol for i in d.longs] and len(d.longs) == 5 and d.displaced[0].status == "DISPLACED"


def test_triggered_ideas_are_never_displaced():
    inc = [idea(f"I{i}", ev=0.1, sector=f"S{i}", status="TRIGGERED") for i in range(5)]
    d = select_board(inc, [idea("NEW", ev=0.9, sector="Z")], S.board, S.risk, NOCORR)
    assert "NEW" not in [i.symbol for i in d.longs]


def test_one_idea_per_symbol():
    a1, a2 = idea("A", ev=0.2), idea("A", side="short", ev=0.3)
    a2.id = "A-short-2"
    d = select_board([], [a1, a2], S.board, S.risk, NOCORR)
    assert len(d.longs) + len(d.shorts) == 1 and d.shorts[0].cons_ev == 0.3


def test_corr_from_returns():
    rng = np.random.default_rng(0)
    x = rng.normal(0, 1, 20)
    f = corr_from_returns({"a": x, "b": x * 2 + 0.01, "c": rng.normal(0, 1, 20)})
    assert f("a", "b") == pytest.approx(1.0) and abs(f("a", "c")) < 0.8 and f("a", "zz") is None


def _entry(qualified: bool, **kw) -> BookEntry:
    base = dict(setup="orb", side="long", regime="*", n=400, wins=240, hit=0.6, ci_lo=0.55, ci_hi=0.65, win_r=1.2,
                loss_r=-1.0, expectancy=0.3, breakeven=0.45, profit_factor=1.6, p_raw=0.6, p_cal=0.6, ci_lo_cal=0.55,
                ci_hi_cal=0.65, cons_ev=0.21, stability=0.8, n_folds=6, dsr=0.97, qualified=qualified,
                reasons=[] if qualified else ["N 50 < 200"])
    base.update(kw)
    return BookEntry(**base)


def _book(entry: BookEntry, prov: str = "synthetic") -> StatsBook:
    return StatsBook(prov, "", "", ("", ""), "v1", {StatsBook.key("orb", "long", "*"): entry}, Calibrator())


def _sig() -> RawSignal:
    return RawSignal("K", "AAA", "IT", "orb", "ORB", "long", "2025-01-02", 5,
                     datetime(2025, 1, 2, 9, 45, tzinfo=IST), 100.0, 99.0, 101.0, 102.0, "TREND_UP", "Stop 99",
                     {"or_h": 99.9, "rvol_cum": 1.8, "rs_mkt_z": 1.9, "sector_rank": 1, "breadth": 0.7}, 1.1)


def test_gate_and_bypass_only_on_synthetic():
    i, why = make_candidate(_sig(), _book(_entry(False)), S, "synthetic", "REPLAY", 8)
    assert i is None and "N 50" in why
    demo = load_settings(overrides={"board": {"demo_bypass_gates": True}})
    i, _ = make_candidate(_sig(), _book(_entry(False)), demo, "synthetic", "REPLAY", 8)
    assert i is not None and i.gate_bypassed
    i, why = make_candidate(_sig(), _book(_entry(False), "upstox"), demo, "upstox", "LIVE", 8)
    assert i is None                                     # bypass refused on real data
    i, _ = make_candidate(_sig(), _book(_entry(True)), S, "synthetic", "REPLAY", 8)
    assert i is not None and not i.gate_bypassed and i.grade == "A" and i.qty == 2500 // 1 and i.risk_inr == 2500
    assert len(i.thesis) == 3 and "opening range high 99.90" in i.thesis[0]
    i, why = make_candidate(_sig(), _book(_entry(True), "upstox"), S, "synthetic", "REPLAY", 8)
    assert i is None and "provenance" in why             # never mix real stats with synthetic data


def test_sizing_and_guardrails():
    s = size_position(500.0, 495.0, S.risk)
    assert s.qty == 500 and s.risk_inr == 2500
    now = datetime(2025, 1, 2, 10, 0, tzinfo=IST)
    ok = check_guardrails(now, S.session, S.risk, kill_switch=False, feed_ok=True, feed_reason="", pnl_today_inr=0,
                          open_ideas=0)
    assert ok.allowed
    for kw, frag in [({"kill_switch": True}, "Kill"), ({"feed_ok": False}, "feed"), ({"pnl_today_inr": -10000}, "loss"),
                     ({"open_ideas": 10}, "Max open")]:
        args = dict(kill_switch=False, feed_ok=True, feed_reason="stale", pnl_today_inr=0, open_ideas=0)
        args.update(kw)
        g = check_guardrails(now, S.session, S.risk, **args)
        assert not g.allowed and any(frag in r for r in g.reasons)
    late = check_guardrails(datetime(2025, 1, 2, 14, 50, tzinfo=IST), S.session, S.risk, kill_switch=False,
                            feed_ok=True, feed_reason="", pnl_today_inr=0, open_ideas=0)
    assert not late.allowed
    bo = check_guardrails(now, S.session, S.risk, kill_switch=False, feed_ok=True, feed_reason="", pnl_today_inr=0,
                          open_ideas=0, blackouts=[Blackout(now.date(), "RBI_POLICY")])
    assert not bo.allowed


def test_thesis_has_only_computed_numbers():
    snap = {"or_h": 1234.5, "rvol_cum": 2.1, "rs_mkt_z": 1.8, "sector_rank": 1, "breadth": 0.68}
    t = thesis("orb", "long", snap, "TREND_UP", 15)
    assert t[0] == "Broke the 15-min opening range high 1234.50 on 2.1× normal volume"
    assert "RS vs Nifty +1.8σ" in t and len(t) == 3
