"""Indian transaction cost model (§9), rates from config/costs.yaml (DECISIONS D-020)."""

from __future__ import annotations

from dataclasses import dataclass

from dalal.config import load_yaml


@dataclass(frozen=True)
class CostModel:
    segment: str
    brokerage_pct: float
    brokerage_cap: float
    stt_sell: float
    exchange_txn: float
    sebi_fee: float
    stamp_buy: float
    gst: float

    def order(self, price: float, qty: float, is_buy: bool) -> float:
        """All-in ₹ cost of one executed order."""
        turnover = abs(price * qty)
        brokerage = min(self.brokerage_cap, self.brokerage_pct * turnover)
        txn = self.exchange_txn * turnover
        sebi = self.sebi_fee * turnover
        stt = 0.0 if is_buy else self.stt_sell * turnover
        stamp = self.stamp_buy * turnover if is_buy else 0.0
        gst = self.gst * (brokerage + txn + sebi)
        return brokerage + txn + sebi + stt + stamp + gst

    def trade(self, side: str, entry: float, qty: float, exits: list[tuple[float, float]]) -> float:
        """Round trip: one entry order + one order per exit leg [(price, qty), …]."""
        long = side == "long"
        c = self.order(entry, qty, is_buy=long)
        for px, q in exits:
            c += self.order(px, q, is_buy=not long)
        return c


def load_costs(segment: str = "cash") -> CostModel:
    raw = load_yaml("costs.yaml")
    key = "cash_intraday" if segment == "cash" else "futures"
    c = raw[key]
    return CostModel(segment=segment, brokerage_pct=float(c["brokerage"]["pct"]), brokerage_cap=float(c["brokerage"]["cap_inr"]),
                     stt_sell=float(c["stt_sell"]), exchange_txn=float(c["exchange_txn"]), sebi_fee=float(c["sebi_fee"]),
                     stamp_buy=float(c["stamp_buy"]), gst=float(c["gst"]))
