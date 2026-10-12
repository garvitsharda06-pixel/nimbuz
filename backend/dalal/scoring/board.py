"""Board selection (§5, §8.3): rank by conservative EV, portfolio pass, hysteresis. Never pads.

Pure functions: unit-tested in tests/test_board.py.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from dalal.config import BoardCfg, RiskCfg
from dalal.scoring.ideas import Idea

Corr = Callable[[str, str], float | None]


@dataclass
class BoardDecision:
    longs: list[Idea]
    shorts: list[Idea]
    added: list[Idea] = field(default_factory=list)
    displaced: list[Idea] = field(default_factory=list)
    rejected: dict[str, str] = field(default_factory=dict)      # candidate id -> reason


def corr_from_returns(rets: dict[str, np.ndarray], min_obs: int = 15) -> Corr:
    """Correlation of trailing daily returns (strictly before today)."""

    cache: dict[tuple[str, str], float | None] = {}

    def f(a: str, b: str) -> float | None:
        k = (a, b) if a < b else (b, a)
        if k in cache:
            return cache[k]
        x, y = rets.get(a), rets.get(b)
        val: float | None = None
        if x is not None and y is not None:
            m = np.isfinite(x) & np.isfinite(y)
            if m.sum() >= min_obs and np.std(x[m]) > 0 and np.std(y[m]) > 0:
                val = float(np.corrcoef(x[m], y[m])[0, 1])
        cache[k] = val
        return val

    return f


def net_beta(ideas: list[Idea], capital: float | None = None) -> float:
    """Net beta of the board in risk units: Σ side × β, each idea counting as one unit of risk
    (every idea is sized to the same ₹ risk). Missing β counts as 1."""
    return float(sum(i.sign * (i.beta if np.isfinite(i.beta) else 1.0) for i in ideas))


def reason_key(reason: str) -> str:
    """Stable category for a rejection reason (numbers stripped) for the board's filter counts."""
    r = reason.lower()
    for k, label in [("net beta", "net-beta cap"), ("sector cap", "sector cap"), ("near-duplicate", "correlated duplicate"),
                     ("already on the board", "symbol already on board"), ("better setup", "better setup same symbol"),
                     ("side full", "board side full"), ("hysteresis", "hysteresis"), ("max open", "max open ideas"),
                     ("n ", "N below minimum"), ("ci low", "CI below breakeven"), ("conservative ev", "conservative EV ≤ 0"),
                     ("stability", "WF stability"), ("demoted", "setup demoted"), ("no out-of-sample", "no OOS stats"),
                     ("provenance", "provenance mismatch"), ("size rounds", "size rounds to 0"), ("guardrail", "guardrail")]:
        if r.startswith(k) or k in r:
            return label
    return reason[:40]


def fits(c: Idea, board: list[Idea], cfg: BoardCfg, risk: RiskCfg, corr: Corr) -> str | None:
    """None if candidate fits the portfolio, else the reason it does not."""
    same = [i for i in board if i.side == c.side]
    if sum(1 for i in same if i.sector == c.sector) >= risk.max_per_sector_per_side:
        return f"sector cap: {risk.max_per_sector_per_side} {c.sector} {c.side}s already"
    for i in same:
        r = corr(c.key, i.key)
        if r is not None and r > cfg.correlation_max:
            return f"near-duplicate of {i.symbol} (20-day corr {r:.2f})"
    nb = net_beta([*board, c])
    if abs(nb) > cfg.max_net_beta and abs(nb) > abs(net_beta(board)):
        return f"net beta {nb:+.2f} would exceed ±{cfg.max_net_beta:g}"
    if len(board) >= risk.max_open_ideas:
        return f"max open ideas {risk.max_open_ideas}"
    return None


def select_board(incumbents: list[Idea], candidates: list[Idea], cfg: BoardCfg, risk: RiskCfg, corr: Corr
                 ) -> BoardDecision:
    board = [i for i in incumbents if i.active]
    dec = BoardDecision([], [])
    on_board = {i.symbol for i in board}
    best: dict[str, Idea] = {}
    for c in candidates:                         # one candidate per symbol: the best conservative EV
        if c.symbol in on_board:
            dec.rejected[c.id] = "symbol already on the board"
            continue
        if c.symbol not in best or c.cons_ev > best[c.symbol].cons_ev:
            if c.symbol in best:
                dec.rejected[best[c.symbol].id] = "a better setup on the same symbol"
            best[c.symbol] = c
        else:
            dec.rejected[c.id] = "a better setup on the same symbol"
    for c in sorted(best.values(), key=lambda x: (-x.cons_ev, x.symbol)):
        side = [i for i in board if i.side == c.side]
        if len(side) < cfg.max_per_side:
            why = fits(c, board, cfg, risk, corr)
            if why is None:
                board.append(c)
                dec.added.append(c)
            else:
                dec.rejected[c.id] = why
            continue
        watching = [i for i in side if i.status == "WATCHING"]
        if not watching:
            dec.rejected[c.id] = "board side full (all ideas live)"
            continue
        weakest = min(watching, key=lambda x: x.cons_ev)
        need = weakest.cons_ev + abs(weakest.cons_ev) * cfg.hysteresis_pct / 100.0
        if c.cons_ev < need or c.cons_ev <= weakest.cons_ev:
            dec.rejected[c.id] = f"not ≥{cfg.hysteresis_pct:g}% better than {weakest.symbol} (hysteresis)"
            continue
        rest = [i for i in board if i is not weakest]
        why = fits(c, rest, cfg, risk, corr)
        if why is not None:
            dec.rejected[c.id] = why
            continue
        weakest.status = "DISPLACED"
        dec.displaced.append(weakest)
        board = [*rest, c]
        dec.added.append(c)
    dec.longs = sorted([i for i in board if i.side == "long"], key=lambda x: -x.cons_ev)
    dec.shorts = sorted([i for i in board if i.side == "short"], key=lambda x: -x.cons_ev)
    return dec
