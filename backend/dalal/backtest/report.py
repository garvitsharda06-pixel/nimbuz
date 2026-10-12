"""Tearsheets (§9) and plain-English verdicts."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from dalal.backtest.robustness import breakdowns, monte_carlo
from dalal.scoring.stats import bucket_stats, max_drawdown, reliability
from dalal.scoring.statsbook import StatsBook


def _clean(x: Any) -> Any:
    if isinstance(x, dict):
        return {k: _clean(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_clean(v) for v in x]
    if isinstance(x, (np.floating, float)):
        return None if not np.isfinite(x) else round(float(x), 6)
    if isinstance(x, np.integer):
        return int(x)
    if isinstance(x, np.bool_):
        return bool(x)
    return x


def verdict(book: StatsBook, setup: str, side: str) -> tuple[str, str]:
    e = book.entries.get(StatsBook.key(setup, side, "*"))
    if e is None or e.n == 0:
        return "NO DATA", "No out-of-sample instances."
    regimes_ok = [x for x in book.entries.values() if x.setup == setup and x.side == side and x.qualified]
    if e.n < book.gate.get("min_n", 200):
        return "INSUFFICIENT DATA", f"Only {e.n} OOS instances (need {book.gate.get('min_n', 200)})."
    if regimes_ok:
        names = ", ".join(sorted({x.regime for x in regimes_ok}))
        return "EDGE", (f"Passes the gate ({names}): conservative EV {max(x.cons_ev for x in regimes_ok):+.3f}R, "
                        f"WF stability {e.stability:.0%}, DSR {e.dsr:.2f}.")
    if e.expectancy > 0:
        return "MARGINAL", (f"Positive OOS expectancy {e.expectancy:+.3f}R but not proven: " + "; ".join(e.reasons))
    return "NO EDGE", f"OOS expectancy {e.expectancy:+.3f}R after costs. Kept off the live board."


def _equity(g: pd.DataFrame, max_points: int = 400) -> list[dict[str, Any]]:
    if g.empty:
        return []
    g = g.sort_values(["day", "t"])
    cum_r = g["r_net"].cumsum().to_numpy()
    cum_inr = g["pnl_inr"].cumsum().to_numpy()
    peak = np.maximum.accumulate(np.concatenate([[0.0], cum_r]))[1:]
    dd = peak - cum_r
    idx = np.unique(np.linspace(0, len(g) - 1, min(max_points, len(g))).astype(int))
    days = g["day"].astype(str).to_numpy()
    return [{"day": days[i], "cum_r": cum_r[i], "cum_inr": cum_inr[i], "dd_r": dd[i]} for i in idx]


def _hist(x: np.ndarray, bins: np.ndarray) -> list[dict[str, float]]:
    x = x[np.isfinite(x)]
    if len(x) == 0:
        return []
    c, e = np.histogram(np.clip(x, bins[0], bins[-1]), bins=bins)
    return [{"lo": float(e[i]), "hi": float(e[i + 1]), "n": int(c[i])} for i in range(len(c))]


def tearsheet(setup: str, wf: pd.DataFrame, book: StatsBook, dsr: dict[str, Any],
              sensitivity: list[dict[str, Any]] | None = None, label: str = "") -> dict[str, Any]:
    all_ = wf[wf["setup"] == setup]
    oos = all_[all_["fold"] >= 0]
    ins = all_[all_["fold"] < 0]
    sides: dict[str, Any] = {}
    for side in ["long", "short"]:
        o = oos[oos["side"] == side]
        i = ins[ins["side"] == side]
        v, why = verdict(book, setup, side)
        sides[side] = {
            "verdict": v, "why": why,
            "oos": bucket_stats(o["win"].to_numpy(), o["r_net"].to_numpy()).as_dict(),
            "is": bucket_stats(i["win"].to_numpy(), i["r_net"].to_numpy()).as_dict(),
            "full": bucket_stats(all_[all_["side"] == side]["win"].to_numpy(),
                                 all_[all_["side"] == side]["r_net"].to_numpy()).as_dict(),
            "regimes": [e.as_dict() for e in book.entries.values() if e.setup == setup and e.side == side],
            "dsr": dsr.get(side, {}),
            "mc": monte_carlo(o.sort_values(["day", "t"])["r_net"].to_numpy()),
        }
    folds = []
    for k, g in oos.groupby("fold"):
        folds.append({"fold": int(k), "start": str(g["day"].min()), "end": str(g["day"].max()), "n": len(g),
                      "hit": float(g["win"].mean()), "exp_r": float(g["r_net"].mean()),
                      "pred": float(g["p_oos"].mean())})
    n_days = max(1, oos["day"].nunique()) if not oos.empty else 1
    wins, losses = oos[oos["r_net"] > 0], oos[oos["r_net"] <= 0]
    return _clean({
        "setup": setup, "label": label, "n_oos": len(oos), "n_is": len(ins),
        "sides": sides,
        "equity_oos": _equity(oos), "equity_full": _equity(all_),
        "max_dd_r": max_drawdown(oos.sort_values(["day", "t"])["r_net"].to_numpy()) if not oos.empty else None,
        "folds": folds,
        "calibration": reliability(oos["p_oos"].to_numpy(), oos["win"].to_numpy()) if not oos.empty else [],
        "breakdowns": breakdowns(oos),
        "mae_hist_losers": _hist(oos.loc[~oos["win"], "mae_r"].to_numpy(), np.linspace(-1.5, 0, 16)),
        "mfe_hist_winners": _hist(oos.loc[oos["win"], "mfe_r"].to_numpy(), np.linspace(0, 4, 17)),
        "avg_mae_r": float(oos["mae_r"].mean()) if not oos.empty else None,
        "avg_mfe_r": float(oos["mfe_r"].mean()) if not oos.empty else None,
        "avg_win_r": float(wins["r_net"].mean()) if len(wins) else None,
        "avg_loss_r": float(losses["r_net"].mean()) if len(losses) else None,
        "avg_win_inr": float(wins["pnl_inr"].mean()) if len(wins) else None,
        "avg_loss_inr": float(losses["pnl_inr"].mean()) if len(losses) else None,
        "avg_cost_inr": float(oos["cost_inr"].mean()) if not oos.empty else None,
        "trades_per_day": len(oos) / n_days,
        "exit_reasons": oos["exit_reason"].value_counts().to_dict() if not oos.empty else {},
        "sensitivity": sensitivity or [],
    })
