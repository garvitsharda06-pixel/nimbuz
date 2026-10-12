"""Phase 5 acceptance: every shown idea is logged with a full snapshot and auto-resolved; paper
trades settle; the daily-loss guardrail sees paper P&L; live results update calibration and
demote divergent setups; notes contain only computed numbers."""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime

import pytest

from dalal.clock import IST
from dalal.config import load_settings
from dalal.journal.db import Journal
from dalal.journal.performance import divergence, performance
from dalal.live.engine import attach_engine
from dalal.live.runtime import Runtime
from dalal.live.services import Services
from dalal.live.tracker import Transition
from dalal.notify.notes import eod_report, morning_note, numbers_subset_check
from dalal.notify.telegram import format_idea
from dalal.risk.guardrails import check_guardrails
from tests.test_board import idea as mk_idea


@pytest.fixture(scope="module")
def session(syn_root, tmp_path_factory):
    root = tmp_path_factory.mktemp("j")
    s = load_settings(overrides={"data": {"root": str(syn_root), "provider": "synthetic"},
                                 "board": {"demo_bypass_gates": True}})
    rt = Runtime(s)
    if not (rt.store.base / "research" / "statsbook.json").exists():
        from dalal.backtest.run import run_research
        run_research(load_settings(overrides={"data": {"root": str(syn_root)},
                                              "backtest": {"wf_train_months": 1, "wf_test_months": 1}}), rt.store)
    attach_engine(rt)
    sv = Services(rt)
    sv.journal = Journal(root / "journal.sqlite")
    sv.notes_dir = root / "notes"
    sv.feedback_path = root / "live_feedback.json"
    sv.attach()
    rt.services = sv
    day = rt.stored_days()[-4]
    taken: list[str] = []

    async def run():
        await rt.start_replay(day, speed=0)
        # take a paper trade on the first idea as soon as one exists
        while rt.task and not rt.task.done():
            await asyncio.sleep(0)
            eng = rt.engine
            if not taken and eng.ideas:
                i = next(iter(eng.ideas.values()))
                if i.active:
                    sv.journal.take_paper(i, rt.clock())
                    taken.append(i.id)
        await rt.task

    asyncio.run(run())
    return s, rt, sv, day, taken


def test_every_shown_idea_logged_and_resolved(session):
    _s, rt, sv, day, _ = session
    rows = sv.journal.ideas(mode="REPLAY", day=str(day), include_demo=True)
    assert len(rows) == len(rt.engine.ideas) > 0
    for r in rows:
        assert r["resolved"] == 1, r["status"]
        assert r["model_version"] and r["config_hash"] and r["snapshot"]
        assert r["status"] in ("STOPPED", "T2_HIT", "CLOSED", "EXPIRED", "INVALIDATED", "DISPLACED")
        ev = sv.journal.events(r["id"])
        assert ev[0]["new"] == "WATCHING" and ev[-1]["new"] == r["status"]
        if r["entry_px"] is not None and r["status"] != "DISPLACED":
            assert r["won"] in (0, 1) and r["r_realised"] is not None


def test_paper_trade_settles_to_idea_outcome(session):
    _s, rt, sv, _day, taken = session
    assert taken
    p = next(x for x in sv.journal.paper_trades() if x["idea_id"] == taken[0])
    i = rt.engine.ideas[taken[0]]
    assert p["status"] in ("CLOSED", "VOID")
    if p["status"] == "CLOSED" and i.r_realised is not None:
        assert p["r"] == pytest.approx(i.r_realised)


def test_eod_note_written_on_replay_end_with_only_computed_numbers(session):
    _s, _rt, sv, day, _ = session
    notes = list(sv.notes_dir.glob(f"{day}-eod-replay.json"))
    assert notes
    note = json.loads(notes[0].read_text())
    ok, extra = numbers_subset_check(note["text"], note["facts"])
    assert ok, extra
    assert "What fired" in note["text"] and "SYNTHETIC" in note["text"]


def test_morning_note_only_computed_numbers(syn_store, settings):
    days = syn_store.days(syn_store.instruments()[1]["nifty"])
    note = morning_note(settings, syn_store, days[-1], None, None)
    ok, extra = numbers_subset_check(note["text"], note["facts"])
    assert ok, extra
    for section in ("1) Overnight", "2) Index levels", "3) Regime", "4) Events", "5) Watchlist", "6) Model status"):
        assert section in note["text"]
    assert "—" in note["text"]                       # no source for global cues → dash, never invented
    bad = note["text"] + "\nNIFTY target 31,337 by Friday."
    assert not numbers_subset_check(bad, note["facts"])[0]


def test_daily_loss_guardrail_uses_paper_pnl(tmp_path):
    j = Journal(tmp_path / "j.sqlite")
    s = load_settings()
    i = mk_idea("LOSS", status="WATCHING")
    i.day, i.mode, i.qty, i.entry, i.stop = "2025-01-02", "REPLAY", 5000, 100.0, 98.0
    j.log_idea(i)
    j.take_paper(i, datetime(2025, 1, 2, 10, tzinfo=IST))
    i.status, i.entry_px = "TRIGGERED", 100.0
    j.log_transition(i, Transition(i.id, "WATCHING", "TRIGGERED", "2025-01-02T10:05:00+05:30", 100.0))
    pnl = j.pnl_today("2025-01-02", "REPLAY", {i.key: 97.5})      # MTM: −2.5 × 5000 = −12,500 (> 2 % of 5 L)
    assert pnl == pytest.approx(-12500)
    g = check_guardrails(datetime(2025, 1, 2, 10, 30, tzinfo=IST), s.session, s.risk, kill_switch=False, feed_ok=True,
                         feed_reason="", pnl_today_inr=pnl, open_ideas=1)
    assert not g.allowed and any("Daily loss" in r for r in g.reasons)


def _rows(n: int, p: float, wins: int, setup: str = "orb") -> list[dict]:
    return [{"id": f"x{i}", "day": "2025-01-02", "mode": "LIVE", "signal_ts": f"2025-01-02T10:{i % 60:02d}:00+05:30",
             "setup": setup, "side": "long", "regime": "TREND_UP", "p": p, "won": 1 if i < wins else 0,
             "r_realised": 1.0 if i < wins else -1.0, "entry_px": 100.0, "resolved": 1, "status": "STOPPED",
             "gate_bypassed": 0} for i in range(n)]


def test_live_calibration_and_demotion():
    rows = _rows(60, 0.65, 18)                       # 30 % live vs 65 % predicted
    perf = performance(rows)
    assert perf["hit_rate"]["n"] == 60 and perf["hit_rate"]["hit"] == pytest.approx(0.3)
    assert perf["reliability"] and perf["brier"] > 0.2
    fb = divergence(rows, {"orb|long|*": {}, "orb|long|TREND_UP": {}})
    assert fb["entries"]["orb|long|*"]["demoted"] is True
    ok = divergence(_rows(60, 0.55, 33), {"orb|long|*": {}})
    assert ok["entries"]["orb|long|*"]["demoted"] is False
    few = divergence(_rows(10, 0.65, 0), {"orb|long|*": {}})
    assert few["entries"]["orb|long|*"]["demoted"] is False          # too few live instances to judge


def test_demoted_setup_is_kept_off_the_board(session, syn_root, tmp_path):
    """The engine applies live feedback: a demoted setup's candidates are rejected."""
    s = load_settings(overrides={"data": {"root": str(syn_root), "provider": "synthetic"}})
    rt = Runtime(s)
    eng = attach_engine(rt)
    book = eng.load_book()
    assert book is not None
    fb = {"entries": {k: {"live_n": 50, "live_hit": 0.1, "demoted": True} for k in book.entries}}
    (rt.store.base / "research" / "live_feedback.json").write_text(json.dumps(fb))
    try:
        b2 = eng.load_book()
        assert all(e.demoted for e in b2.entries.values())
    finally:
        (rt.store.base / "research" / "live_feedback.json").unlink()


def test_telegram_format_marks_demo_and_synthetic():
    i = mk_idea("AAA")
    i.gate_bypassed = True
    i.thesis = ["a", "b", "c"]
    txt = format_idea(i.card())
    assert txt.startswith("[SYNTHETIC DATA]") and "[DEMO" in txt and "Stop 99.00" in txt


def test_eod_report_function():
    rows = _rows(4, 0.6, 2)
    for r in rows:
        r.update({"symbol": "AAA", "id": r["id"]})
    note = eod_report(date(2025, 1, 2), "LIVE", rows, performance(rows), {"regime": "RANGE", "breadth_pct": 55.0}, "upstox")
    assert "2/4 reached T1" in note["text"]
    assert numbers_subset_check(note["text"], note["facts"])[0]
