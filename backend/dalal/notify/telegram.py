"""Telegram alerts to the owner's OWN chat only (personal use; no broadcast; SEBI RA rules).

Uses the Bot API `sendMessage` (https://core.telegram.org/bots/api#sendmessage). Disabled
unless TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are set in .env. Never raises into the engine.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from dalal.log import get_logger

log = get_logger("telegram")


def format_idea(card: dict[str, Any]) -> str:
    side = "🟢 LONG" if card["side"] == "long" else "🔴 SHORT"
    p = card.get("p")
    lines = [
        f"{side} {card['symbol']} · {card['setup_label']} · {card['grade']}",
        f"Entry {card['entry_zone'][0]:.2f}–{card['entry_zone'][1]:.2f} | Stop {card['stop']:.2f} | "
        f"T1 {card['t1']:.2f} | T2 {card['t2']:.2f}",
        f"P(T1 first) {p * 100:.0f}% [{(card.get('ci_lo') or 0) * 100:.0f}–{(card.get('ci_hi') or 0) * 100:.0f}] "
        f"N {card['n']} · cons EV {card.get('cons_ev') or 0:+.2f}R" if p is not None else "P(T1 first) —",
        f"Qty {card['qty']} · risk ₹{card['risk_inr']:,.0f} · valid to {card['valid_until'][11:16]}",
        *[f"• {t}" for t in card.get("thesis", [])],
        f"✕ {card.get('invalidation', '')}",
    ]
    if card.get("gate_bypassed"):
        lines.insert(0, "[DEMO: gate bypassed, synthetic data, not a trade idea]")
    if card.get("provenance") == "synthetic":
        lines.insert(0, "[SYNTHETIC DATA]")
    return "\n".join(lines)


def format_update(card: dict[str, Any], new: str) -> str:
    return f"{card['symbol']} {card['side'].upper()} · {card['setup_label']}: {new.replace('_', ' ')}"


class Telegram:
    def __init__(self, token: str | None, chat_id: str | None, min_interval_s: float = 1.1):
        self.token, self.chat_id = token, chat_id
        self.min_interval_s = min_interval_s
        self._last = 0.0
        self.sent = 0
        self.errors = 0

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    async def send(self, text: str, client: httpx.AsyncClient | None = None) -> bool:
        if not self.enabled:
            return False
        wait = self.min_interval_s - (time.monotonic() - self._last)
        if wait > 0:
            await asyncio.sleep(wait)
        self._last = time.monotonic()
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        try:
            own = client is None
            c = client or httpx.AsyncClient(timeout=10)
            try:
                r = await c.post(url, json={"chat_id": self.chat_id, "text": text[:4000], "disable_web_page_preview": True})
            finally:
                if own:
                    await c.aclose()
            ok = r.status_code == 200
        except Exception as e:  # network errors never break the engine
            log.warning("telegram_failed", error=type(e).__name__)
            ok = False
        self.sent += int(ok)
        self.errors += int(not ok)
        return ok
