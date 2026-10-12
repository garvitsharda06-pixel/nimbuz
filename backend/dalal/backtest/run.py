"""End-to-end research run: backtest → walk-forward → calibration → stats book → tearsheets."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from dalal.backtest.engine import Research
from dalal.backtest.report import _clean, tearsheet, verdict
from dalal.backtest.robustness import SENSITIVITY_GRIDS, deflated_sharpe, param_grid
from dalal.backtest.walkforward import walk_forward
from dalal.clock import IST
from dalal.config import Settings
from dalal.data.store import CandleStore
from dalal.scoring.calibration import forward_calibration
from dalal.scoring.statsbook import StatsBook, build_statsbook
from dalal.setups.library import SETUP_BY_NAME, build_setups


def research_dir(store: CandleStore) -> Path:
    d = store.base / "research"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ledger(path: Path) -> dict[str, int]:
    return json.loads(path.read_text()) if path.exists() else {}


def run_research(settings: Settings, store: CandleStore, sensitivity: bool = False, symbols: list[str] | None = None,
                 setups: list[str] | None = None, progress: Callable[[str], None] | None = None,
                 out_dir: Path | None = None) -> dict[str, Any]:
    base = build_setups(settings, setups)
    variants = []
    if sensitivity:
        for st in base:
            for combo in param_grid(SENSITIVITY_GRIDS.get(st.name, {})):
                if all(st.params.get(k) == v for k, v in combo.items()):
                    continue
                tag = ",".join(f"{k}={v}" for k, v in combo.items())
                variants.append(SETUP_BY_NAME[st.name](params={**st.params, **combo},
                                                       last_new_idea=settings.session.last_new_idea, tag=tag))
    res = Research(settings, store, base + variants, symbols=symbols, progress=progress).run()
    out = out_dir or research_dir(store)
    out.mkdir(parents=True, exist_ok=True)
    inst = res.instances
    if inst.empty:
        summary = {"provenance": store.provenance, "error": "no instances", "created": datetime.now(tz=IST).isoformat()}
        (out / "summary.json").write_text(json.dumps(summary, indent=1))
        return summary
    is_var = inst["setup"].str.contains("@")
    base_inst, var_inst = inst[~is_var].copy(), inst[is_var].copy()
    bt, sc = settings.backtest, settings.scoring
    wf, folds = walk_forward(base_inst, bt.wf_train_months, bt.wf_test_months, bt.wf_embargo_days,
                             sc.prior_strength, sc.min_bucket_n)
    cal = forward_calibration(wf)
    wf["p_cal_oos"] = np.nan
    if cal.get("n"):
        wf.loc[cal["index"], "p_cal_oos"] = cal["p_cal"]

    # ---- multiple-testing ledger: every variant ever tried counts (§9) ----
    ledger_path = out / "trials.json"
    ledger = _ledger(ledger_path)
    for st in base:
        n_var = sum(1 for v in variants if v.name == st.name)
        ledger[st.name] = ledger.get(st.name, 0) + 1 + n_var
    ledger_path.write_text(json.dumps(ledger, indent=1))

    # ---- sensitivity grid (full-sample expectancy per variant) ----
    sens: dict[str, list[dict[str, Any]]] = {}
    sr_var: dict[str, float] = {}
    if sensitivity:
        allv = pd.concat([base_inst, var_inst])
        allv["base_setup"] = allv["setup"].str.split("@").str[0]
        for name, g in allv.groupby("base_setup"):
            rows = []
            srs = []
            for v, gg in g.groupby("setup"):
                params = dict(kv.split("=") for kv in v.split("@")[1].split(",")) if "@" in v else \
                    {k: str(settings.setup_params(name).get(k, SETUP_BY_NAME[name].defaults.get(k)))
                     for k in SENSITIVITY_GRIDS.get(name, {})}
                r = gg["r_net"].to_numpy()
                rows.append({"variant": v, "params": params, "n": len(gg), "exp_r": float(r.mean()),
                             "hit": float(gg["win"].mean())})
                if len(r) > 30 and r.std() > 0:
                    srs.append(r.mean() / r.std())
            sens[name] = rows
            if len(srs) > 1:
                sr_var[name] = float(np.var(srs, ddof=1))

    dsr: dict[tuple[str, str], dict[str, Any]] = {}
    oos = wf[wf["fold"] >= 0]
    for (s_, side), g in oos.groupby(["setup", "side"]):
        dsr[(s_, side)] = deflated_sharpe(g.sort_values(["day", "t"])["r_net"].to_numpy(), ledger.get(s_, 1),
                                          sr_var.get(s_))
    book = build_statsbook(wf, sc, settings.board, store.provenance, settings.config_hash(), res.days,
                           {k: v.get("dsr", float("nan")) for k, v in dsr.items()}, cal)
    book.save(out / "statsbook.json")
    wf.to_parquet(out / "instances.parquet", index=False)

    ts_dir = out / "tearsheets"
    ts_dir.mkdir(exist_ok=True)
    verdicts = []
    for st in base:
        ts = tearsheet(st.name, wf, book, {sd: dsr.get((st.name, sd), {}) for sd in ("long", "short")},
                       sens.get(st.name), st.label)
        ts["provenance"] = store.provenance
        (ts_dir / f"{st.name}.json").write_text(json.dumps(ts, indent=1, default=str))
        for side in ("long", "short"):
            v, why = verdict(book, st.name, side)
            e = book.entries.get(StatsBook.key(st.name, side, "*"))
            verdicts.append({"setup": st.name, "label": st.label, "side": side, "verdict": v, "why": why,
                             "n_oos": e.n if e else 0, "hit": e.hit if e else None,
                             "exp_r": e.expectancy if e else None, "cons_ev": e.cons_ev if e else None,
                             "stability": e.stability if e else None, "dsr": e.dsr if e else None,
                             "qualified_regimes": sorted(x.regime for x in book.entries.values()
                                                         if x.setup == st.name and x.side == side and x.qualified)})
    summary = _clean({
        "provenance": store.provenance,
        "synthetic": store.provenance == "synthetic",
        "created": datetime.now(tz=IST).isoformat(),
        "config_hash": settings.config_hash(),
        "data_range": list(res.days), "n_symbols": res.n_symbols, "elapsed_s": res.elapsed_s,
        "signals_total": res.signals_total, "void_counts": res.void_counts,
        "n_instances": len(base_inst), "n_oos": int((wf["fold"] >= 0).sum()),
        "folds": [{"k": f.k, "train_end": str(f.train_end), "test_start": str(f.test_start),
                   "test_end": str(f.test_end)} for f in folds],
        "calibration": {k: v for k, v in cal.items() if k not in ("p_cal", "index")},
        "verdicts": verdicts, "trials": ledger, "sensitivity_run": sensitivity,
    })
    (out / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    return summary
