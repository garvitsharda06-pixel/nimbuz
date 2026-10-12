"""Morning note (08:45 IST) and end-of-day report (15:45 IST), §11.

Deterministic templates filled ONLY with numbers this system computed (DECISIONS D-008).
`numbers_subset_check` is the guard any future LLM rephrasing must pass: every number in the
prose must already exist in the facts.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from dalal.clock import IST
from dalal.config import Settings
from dalal.data.reference import ReferenceData
from dalal.data.store import CandleStore
from dalal.data.universe import build_universe
from dalal.pipeline import MarketBuilder, eligibility, load_index_panels, setup_context, symbol_features
from dalal.regime.classifier import REGIMES
from dalal.risk.guardrails import load_blackouts

_NUM = re.compile(r"[-+]?\d[\d,]*\.?\d*")


def numbers_in(text: str) -> set[str]:
    return {m.replace(",", "").lstrip("+").rstrip(".") for m in _NUM.findall(text)}


TEMPLATE_CONSTANTS = "1) 2) 3) 4) 5) 6) 08:45 15:45 5d 20-day 1-year 5-minute 15-min D-009 T1"


def numbers_subset_check(text: str, facts: dict[str, Any], constants: str = TEMPLATE_CONSTANTS) -> tuple[bool, set[str]]:
    """True if every number in `text` appears in the facts (formatted exactly as displayed) or in
    the fixed template constants. This is the guard for any LLM rephrasing: it may not add numbers."""
    allowed = numbers_in(json.dumps(facts, default=str)) | numbers_in(constants)
    extra = {n for n in numbers_in(text) if n not in allowed}
    return (not extra, extra)


class _Fx:
    """Records every formatted value that goes into the prose (the "display facts")."""

    def __init__(self) -> None:
        self.d: dict[str, str] = {}

    def __call__(self, key: str, value: str) -> str:
        self.d[key] = value
        return value


def _f(x: Any, nd: int = 2) -> str:
    return "—" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x:,.{nd}f}"


def _ordinal(s: str) -> str:
    if not s.isdigit():
        return ""
    n = int(s)
    return "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def _p(x: Any, nd: int = 2) -> str:
    return "—" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x:+.{nd}f}%"


def market_on_day(settings: Settings, store: CandleStore, day: date) -> dict[str, Any]:
    """Close-of-day market facts for a stored day (regime, breadth, RS leaders, shockers)."""
    insts, roles = store.instruments()
    lo = day - timedelta(days=100)

    def reader(k: str) -> pd.DataFrame:
        return store.read(k, lo, day)

    idx = load_index_panels(store, roles, reader)
    mb = MarketBuilder(idx)
    cache = []
    for inst in [i for i in insts if i.kind == "EQ" and store.has(i.key)]:
        p, ctx, F = symbol_features(inst.key, reader(inst.key), idx)
        if not len(p.days) or p.days[-1] != day:
            continue
        el = eligibility(ctx, settings.universe)
        mb.add_breadth(p.days, el, F, ctx)
        cache.append((inst, p, ctx, F, el))
    mkt = mb.build()
    if not len(mkt.days) or mkt.days[-1] != day:
        return {}
    t = 74
    rows = []
    for inst, p, ctx, F, el in cache:
        if not el[-1]:
            continue
        setup_context(inst, p, ctx, F, mkt, idx)
        rows.append({"symbol": inst.symbol, "sector": inst.sector, "rs_z": float(F["rs_mkt_z"][-1, t]),
                     "rvol": float(F["rvol_cum"][-1, t]), "chg_pct": float(F["ret_pdc"][-1, t] * 100),
                     "close": float(F["c"][-1, t]), "dh20": float(np.nanmax(p.dh[-20:])), "dl20": float(np.nanmin(p.dl[-20:])),
                     "range7": [float(x) for x in (p.dh[-7:] - p.dl[-7:])]})
    reg = int(mkt.regime[-1, t])
    return {"regime": REGIMES[reg] if reg >= 0 else "UNKNOWN", "score": float(mkt.score[-1, t]),
            "breadth_pct": float(mkt.breadth[-1, t] * 100) if np.isfinite(mkt.breadth[-1, t]) else None,
            "adv_pct": float(mkt.adv_dec[-1, t] * 100) if np.isfinite(mkt.adv_dec[-1, t]) else None,
            "stocks": rows}


def _index_facts(store: CandleStore, key: str | None, day: date) -> dict[str, Any] | None:
    if not key or not store.has(key):
        return None
    d = store.daily(key, day - timedelta(days=400), day)
    if d.empty or d["date"].iloc[-1] != day:
        return None
    last, prev = d.iloc[-1], (d.iloc[-2] if len(d) > 1 else None)
    chg = (last["close"] / prev["close"] - 1) * 100 if prev is not None else None
    five = (last["close"] / d.iloc[-6]["close"] - 1) * 100 if len(d) > 5 else None
    pctile = float((d["close"] <= last["close"]).mean() * 100) if len(d) > 50 else None
    return {"close": float(last["close"]), "high": float(last["high"]), "low": float(last["low"]),
            "chg_pct": chg, "chg5_pct": five, "pctile_1y": pctile}


def morning_note(settings: Settings, store: CandleStore, today: date, book: Any | None,
                 live_perf: dict[str, Any] | None = None) -> dict[str, Any]:
    insts, roles = store.instruments()
    days = store.days(roles["nifty"])
    prev = [d for d in days if d < today]
    if not prev:
        return {"kind": "morning", "day": str(today), "text": "Insufficient data for a morning note.", "facts": {}}
    P = prev[-1]
    nifty = _index_facts(store, roles.get("nifty"), P)
    bank = _index_facts(store, roles.get("banknifty"), P)
    vix = _index_facts(store, roles.get("vix"), P)
    mk = market_on_day(settings, store, P)
    stocks = mk.get("stocks", [])
    lead = sorted(stocks, key=lambda x: -x["rs_z"])[:5]
    lag = sorted(stocks, key=lambda x: x["rs_z"])[:5]
    shock = sorted(stocks, key=lambda x: -x["rvol"])[:5]
    near_hi = [s for s in stocks if s["dh20"] and s["close"] >= 0.995 * s["dh20"]][:5]
    near_lo = [s for s in stocks if s["dl20"] and s["close"] <= 1.005 * s["dl20"]][:5]
    nr7 = [s for s in stocks if len(s["range7"]) == 7 and s["range7"][-1] <= min(s["range7"])][:5]
    u = build_universe(store, insts, today, settings.universe, roles["nifty"])
    ref = ReferenceData(settings.data.root_path / "reference")
    ev = ref.events(today)
    blk = [b for b in load_blackouts() if b.day == today]
    qualified = book.qualified_setups() if book else []
    facts: dict[str, Any] = {
        "day": str(today), "prev_day": str(P), "provenance": store.provenance,
        "nifty": nifty, "banknifty": bank, "vix": vix, "prev_regime": mk.get("regime"), "prev_score": mk.get("score"),
        "prev_breadth_pct": mk.get("breadth_pct"), "prev_adv_pct": mk.get("adv_pct"),
        "universe": len(u.members), "excluded": len(u.excluded),
        "rs_leaders": [(s["symbol"], round(s["rs_z"], 1)) for s in lead],
        "rs_laggards": [(s["symbol"], round(s["rs_z"], 1)) for s in lag],
        "volume_shockers": [(s["symbol"], round(s["rvol"], 1)) for s in shock],
        "near_20d_high": [s["symbol"] for s in near_hi], "near_20d_low": [s["symbol"] for s in near_lo],
        "nr7": [s["symbol"] for s in nr7],
        "events": ev.data or [], "blackouts": [b.kind for b in blk], "qualified_setups": qualified,
        "live": live_perf or {},
    }
    fx = _Fx()
    L: list[str] = []
    syn = " · SYNTHETIC DATA (plumbing demo, not market data)" if store.provenance == "synthetic" else ""
    L.append(f"DALAL DESK: MORNING NOTE · {fx('date', f'{today:%a %d %b %Y}')} · 08:45 IST{syn}")
    L.append("")
    L.append("1) Overnight & global cues")
    L.append("   US index futures, crude, GIFT Nifty: — (no permitted, reliable source configured). "
             "FII/DII provisional flows: — (disabled; see DECISIONS D-009).")
    L.append("")
    L.append(f"2) Index levels (prev session {fx('prev', f'{P:%d %b}')})")
    if nifty:
        L.append(f"   NIFTY {fx('n_c', _f(nifty['close']))} ({fx('n_chg', _p(nifty['chg_pct']))}; 5d "
                 f"{fx('n_5d', _p(nifty['chg5_pct']))}). Range {fx('n_lo', _f(nifty['low']))}–{fx('n_hi', _f(nifty['high']))}: "
                 "these are today's PDL / PDH reference levels.")
    if bank:
        L.append(f"   BANKNIFTY {fx('b_c', _f(bank['close']))} ({fx('b_chg', _p(bank['chg_pct']))}). "
                 f"Range {fx('b_lo', _f(bank['low']))}–{fx('b_hi', _f(bank['high']))}.")
    if vix:
        pct_s = _f(vix["pctile_1y"], 0)
        L.append(f"   INDIA VIX {fx('v_c', _f(vix['close']))} ({fx('v_chg', _p(vix['chg_pct']))}), "
                 f"{fx('v_pct', pct_s)}{_ordinal(pct_s)} percentile of its 1-year range.")
    L.append("")
    L.append("3) Regime read")
    if mk:
        score_s = f"{mk['score']:+.0f}"
        L.append(f"   Prior session closed in {str(mk['regime']).replace('_', ' ')} (trend score "
                 f"{fx('score', score_s)}), breadth "
                 f"{fx('breadth', _f(mk['breadth_pct'], 0))}% of the universe above VWAP, advancers {fx('adv', _f(mk['adv_pct'], 0))}%.")
    L.append("   No overnight data feed is configured, so no opening regime is forecast; the classifier updates from the "
             "first 5-minute candle.")
    L.append("")
    L.append("4) Events & risk")
    L.append(f"   Universe today: {fx('uni', str(len(u.members)))} names eligible, {fx('exc', str(len(u.excluded)))} excluded "
             "(liquidity, price, ASM/GSM, events).")
    ev_txt = ", ".join(e.get("symbol", "") + " " + e.get("kind", "") for e in (ev.data or [])) or "none listed"
    blk_txt = f" · market blackout: {', '.join(facts['blackouts'])}" if facts["blackouts"] else ""
    L.append(f"   Company events: {fx('events', ev_txt)}{blk_txt}")
    L.append("")
    L.append("5) Watchlist (from the prior session's close)")
    L.append(f"   RS leaders vs Nifty: {fx('lead', ', '.join(f'{s} {z:+.1f}σ' for s, z in facts['rs_leaders']) or '—')}")
    L.append(f"   RS laggards: {fx('lag', ', '.join(f'{s} {z:+.1f}σ' for s, z in facts['rs_laggards']) or '—')}")
    L.append(f"   Volume shockers (RVOL): {fx('shock', ', '.join(f'{s} {v:.1f}×' for s, v in facts['volume_shockers']) or '—')}")
    L.append(f"   Closing near 20-day high: {fx('nh', ', '.join(facts['near_20d_high']) or '—')} · near 20-day low: "
             f"{fx('nl', ', '.join(facts['near_20d_low']) or '—')} · NR7: {fx('nr7', ', '.join(facts['nr7']) or '—')}")
    L.append("")
    L.append("6) Model status")
    L.append(f"   Setups that pass the live-board gate: {', '.join(qualified) if qualified else 'none: the board stays empty rather than padded'}.")
    lp = live_perf or {}
    if lp.get("hit_rate"):
        h = lp["hit_rate"]
        hit_s, pred_s = f"{h['hit'] * 100:.0f}", f"{h['pred_mean'] * 100:.0f}"
        brier_s, tot_s = f"{lp.get('brier', float('nan')):.3f}", f"{lp.get('total_r', 0):+.1f}"
        L.append(f"   Live forward record: {fx('lk', str(h['k']))}/{fx('ln', str(h['n']))} T1-first "
                 f"({fx('lhit', hit_s)}%) vs predicted {fx('lpred', pred_s)}% · Brier {fx('lbrier', brier_s)} · "
                 f"total {fx('ltot', tot_s)}R.")
    else:
        L.append("   Live forward record: insufficient data.")
    text = "\n".join(L)
    facts["display"] = fx.d
    return {"kind": "morning", "day": str(today), "text": text, "facts": facts,
            "created": datetime.now(tz=IST).isoformat()}


def eod_report(day: date, mode: str, journal_rows: list[dict[str, Any]], perf_all: dict[str, Any],
               market: dict[str, Any] | None, provenance: str, divergence: dict[str, Any] | None = None) -> dict[str, Any]:
    today = [r for r in journal_rows if r["day"] == str(day) and r["mode"] == mode]
    trig = [r for r in today if r.get("entry_px") is not None]
    wins = [r for r in trig if r.get("won") == 1]
    tot = sum((r.get("r_realised") or 0) for r in trig)
    pred = np.mean([r["p"] for r in trig if r.get("p") is not None]) if trig else None
    facts: dict[str, Any] = {"day": str(day), "mode": mode, "shown": len(today), "triggered": len(trig), "t1_first": len(wins),
             "total_r": round(tot, 2), "pred_mean": None if pred is None else round(float(pred), 3),
             "regime_close": (market or {}).get("regime"), "breadth_close": (market or {}).get("breadth_pct"),
             "live_hit": perf_all.get("hit_rate"), "brier": perf_all.get("brier"),
             "demoted": [k for k, v in (divergence or {}).get("entries", {}).items() if v.get("demoted")]}
    syn = " · SYNTHETIC DATA" if provenance == "synthetic" else ""
    fx = _Fx()
    L = [f"DALAL DESK: END-OF-DAY · {fx('date', f'{day:%a %d %b %Y}')} · {mode}{syn}", ""]
    n_long = sum(r["side"] == "long" for r in today)
    L.append(f"What fired: {fx('shown', str(len(today)))} ideas shown ({fx('nl', str(n_long))} long / "
             f"{fx('ns', str(len(today) - n_long))} short), {fx('trig', str(len(trig)))} triggered.")
    if trig:
        pred_txt = f" (predicted {fx('pred', f'{pred * 100:.0f}')}%)" if pred is not None else ""
        L.append(f"Outcomes: {fx('wins', str(len(wins)))}/{len(trig)} reached T1 before the stop{pred_txt}; "
                 f"net {fx('tot', f'{tot:+.2f}')}R after costs.")
    for r in sorted(today, key=lambda x: x["signal_ts"]):
        res = fx(f"r_{r['id']}", f"{r['r_realised']:+.2f}R") if r.get("r_realised") is not None else "—"
        line = f"  {fx('t_' + r['id'], r['signal_ts'][11:16])} {fx('s_' + r['id'], r['symbol']):<10} " \
               f"{r['side']:<5} {r['setup']:<15} {r['status']:<11} {res}{'  [demo]' if r.get('gate_bypassed') else ''}"
        L.append(line)
    if market:
        L.append(f"Regime at the close: {str(market.get('regime', '—')).replace('_', ' ')}; breadth "
                 f"{fx('breadth', _f(market.get('breadth_pct'), 0))}% above VWAP.")
    h = perf_all.get("hit_rate")
    if h:
        hit_s, pred_s, brier_s = f"{h['hit'] * 100:.0f}", f"{h['pred_mean'] * 100:.0f}", f"{perf_all.get('brier', float('nan')):.3f}"
        L.append(f"Calibration update ({mode}, all days): {fx('k', str(h['k']))}/{fx('n', str(h['n']))} = {fx('hit', hit_s)}% "
                 f"realised vs {fx('pr', pred_s)}% predicted · Brier {fx('brier', brier_s)}.")
    else:
        L.append("Calibration update: insufficient data.")
    if facts["demoted"]:
        L.append(f"Demoted (live results diverge from backtest): {', '.join(facts['demoted'])}.")
    facts["display"] = fx.d
    return {"kind": "eod", "day": str(day), "mode": mode, "text": "\n".join(L), "facts": facts,
            "created": datetime.now(tz=IST).isoformat()}


def save_note(dirpath: Path, note: dict[str, Any]) -> Path:
    dirpath.mkdir(parents=True, exist_ok=True)
    p = dirpath / f"{note['day']}-{note['kind']}{'-' + note['mode'].lower() if note.get('mode') else ''}.json"
    p.write_text(json.dumps(note, indent=1, default=str))
    return p


def list_notes(dirpath: Path) -> list[dict[str, Any]]:
    if not dirpath.exists():
        return []
    out = []
    for p in sorted(dirpath.glob("*.json"), reverse=True):
        d = json.loads(p.read_text())
        out.append({"day": d["day"], "kind": d["kind"], "mode": d.get("mode"), "text": d["text"], "created": d.get("created")})
    return out
