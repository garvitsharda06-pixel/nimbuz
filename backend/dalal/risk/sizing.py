"""Position sizing (§10): qty = (capital × risk %) ÷ |entry − stop|, rounded to lot rules and
capped by MIS notional (capital × leverage)."""

from __future__ import annotations

from dataclasses import dataclass

from dalal.backtest.simulate import position_qty
from dalal.config import RiskCfg


@dataclass(frozen=True)
class Size:
    qty: int
    risk_inr: float
    notional: float
    capped_by_margin: bool


def size_position(entry: float, stop: float, cfg: RiskCfg, lot: int = 1) -> Size:
    q = position_qty(cfg.capital, cfg.risk_per_trade_pct, entry, stop, lot, cfg.mis_leverage)
    uncapped = int((cfg.capital * cfg.risk_per_trade_pct / 100.0) // max(abs(entry - stop), 1e-9))
    return Size(q, round(q * abs(entry - stop), 2), round(q * entry, 2), q < (uncapped // max(lot, 1)) * max(lot, 1))
