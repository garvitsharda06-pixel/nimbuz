"""Plain-English thesis bullets (§8.3 explainability). Every number here was computed by the
system; nothing is invented. With the meta-model, SHAP contributions pick the drivers."""

from __future__ import annotations

import math
from typing import Any, TypeGuard


def _fin(x: Any) -> TypeGuard[float]:
    return isinstance(x, (int, float)) and math.isfinite(x)


def primary_bullet(setup: str, side: str, s: dict[str, Any]) -> str:
    up = side == "long"
    rv = s.get("rvol_cum")
    rvs = f" on {rv:.1f}× normal volume" if _fin(rv) else ""
    if setup in ("orb", "oi_confirm"):
        lvl = s.get("or_h") if up else s.get("or_l")
        return f"Broke the 15-min opening range {'high' if up else 'low'} {lvl:.2f}{rvs}" if _fin(lvl) else "Opening-range break"
    if setup == "vwap_reclaim":
        return f"{'Reclaimed' if up else 'Rejected at'} VWAP {s.get('vwap', float('nan')):.2f} with the index {'above' if up else 'below'} its VWAP"
    if setup == "trend_pullback":
        return f"Pulled back to the 20-EMA {s.get('ema20', float('nan')):.2f} in a {'rising' if up else 'falling'} trend and resumed{rvs}"
    if setup == "pdhl_retest":
        lvl = s.get("pdh") if up else s.get("pdl")
        return f"Broke and retested the previous-day {'high' if up else 'low'} {lvl:.2f}" if _fin(lvl) else "PDH/PDL retest"
    if setup == "gap_go":
        return f"Gap {'up' if up else 'down'} of {abs(s.get('gap_atr', 0)):.1f}× ATR holding beyond the opening range{rvs}"
    if setup == "gap_fade":
        return f"Gap {'down' if up else 'up'} of {abs(s.get('gap_atr', 0)):.1f}× ATR failing back through VWAP"
    if setup == "rel_strength":
        z = s.get("rs_mkt_z", float("nan"))
        return f"Relative-strength {'leader' if up else 'laggard'}: {z:+.1f}σ vs Nifty, at a new intraday {'high' if up else 'low'}"
    if setup == "vwap_mr":
        return f"Stretched beyond VWAP {'−' if up else '+'}2σ in a range regime and turning back"
    return setup


def driver_bullets(side: str, s: dict[str, Any], n_sectors: int) -> list[tuple[float, str]]:
    """(salience, text) candidates. Salience ≈ how unusual the reading is, oriented to the side."""
    sg = 1.0 if side == "long" else -1.0
    out: list[tuple[float, str]] = []
    z = s.get("rs_mkt_z")
    if _fin(z) and z * sg > 0.5:
        out.append((abs(z), f"RS vs Nifty {z:+.1f}σ"))
    zs = s.get("rs_sec_z")
    if _fin(zs) and zs * sg > 0.5:
        out.append((abs(zs) * 0.8, f"RS vs sector {zs:+.1f}σ"))
    r = s.get("sector_rank")
    if _fin(r):
        good = r <= 2 if side == "long" else r >= n_sectors - 1
        if good:
            out.append((1.5, f"Sector rank {int(r)}/{n_sectors}"))
    rv = s.get("rvol_cum")
    if _fin(rv) and rv >= 1.3:
        out.append((min(rv, 4.0) - 0.5, f"Volume {rv:.1f}× normal for this time of day"))
    b = s.get("breadth")
    if _fin(b) and (b - 0.5) * sg > 0.1:
        out.append((abs(b - 0.5) * 6, f"Breadth {b:.0%} of universe above VWAP"))
    dv = s.get("dist_vwap_atr")
    if _fin(dv) and dv > 0.3:
        out.append((min(dv, 3.0), f"Holding {dv:.1f}× ATR(5m) {'above' if side == 'long' else 'below'} VWAP"))
    tr = s.get("trend15")
    if _fin(tr) and tr * sg > 0:
        out.append((1.0, "15-min trend aligned"))
    g = s.get("gap_atr")
    if _fin(g) and abs(g) >= 0.5:
        out.append((abs(g), f"Gapped {g:+.1f}× daily ATR"))
    oi = s.get("oi_state_text")
    if isinstance(oi, str) and oi and oi != "—":
        out.append((2.0, f"Futures: {oi}"))
    return out


def thesis(setup: str, side: str, s: dict[str, Any], regime: str, n_sectors: int,
           shap_drivers: list[str] | None = None) -> list[str]:
    bullets = [primary_bullet(setup, side, s)]
    drivers = shap_drivers or [t for _, t in sorted(driver_bullets(side, s, n_sectors), key=lambda x: -x[0])]
    for t in drivers:
        if len(bullets) >= 3:
            break
        bullets.append(t)
    sc = s.get("mkt_score")
    fallbacks = [f"Market regime {regime.replace('_', ' ').title()}" + (f" (score {sc:+.0f})" if _fin(sc) else "")]
    rv, z, b = s.get("rvol_cum"), s.get("rs_mkt_z"), s.get("breadth")
    if _fin(rv):
        fallbacks.append(f"Volume {rv:.1f}× normal for this time of day")
    if _fin(z):
        fallbacks.append(f"RS vs Nifty {z:+.1f}σ")
    if _fin(b):
        fallbacks.append(f"Breadth {b:.0%} of universe above VWAP")
    for f in fallbacks:
        if len(bullets) >= 3:
            break
        if f not in bullets:
            bullets.append(f)
    return bullets[:3]
