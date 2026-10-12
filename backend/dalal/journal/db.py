"""SQLite journal (§10): every idea ever shown, its full snapshot, every status change, paper
trades and manual entries. Ideas are logged whether or not the owner takes them."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from dalal.backtest.costs import CostModel, load_costs
from dalal.clock import IST
from dalal.live.tracker import Transition
from dalal.scoring.ideas import Idea

SCHEMA = """
CREATE TABLE IF NOT EXISTS ideas (
  id TEXT PRIMARY KEY, day TEXT, mode TEXT, provenance TEXT, signal_ts TEXT, symbol TEXT, key TEXT, sector TEXT,
  setup TEXT, side TEXT, regime TEXT, grade TEXT, entry REAL, stop REAL, t1 REAL, t2 REAL, rr1 REAL, rr2 REAL,
  p REAL, ci_lo REAL, ci_hi REAL, n INTEGER, expectancy REAL, cons_ev REAL, breakeven REAL, qty INTEGER,
  risk_inr REAL, notional REAL, valid_until TEXT, expires_at TEXT, gate_bypassed INTEGER, stats_level TEXT,
  model_version TEXT, config_hash TEXT, snapshot TEXT, thesis TEXT, invalidation TEXT,
  status TEXT, status_ts TEXT, entry_px REAL, exit_px REAL, r_realised REAL, won INTEGER, resolved INTEGER DEFAULT 0,
  created_at TEXT
);
CREATE INDEX IF NOT EXISTS ideas_day ON ideas(day, mode);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, idea_id TEXT, ts TEXT, old TEXT, new TEXT, price REAL, note TEXT
);
CREATE INDEX IF NOT EXISTS events_idea ON events(idea_id);
CREATE TABLE IF NOT EXISTS paper (
  id INTEGER PRIMARY KEY AUTOINCREMENT, idea_id TEXT UNIQUE, day TEXT, mode TEXT, taken_ts TEXT, take_status TEXT,
  qty INTEGER, entry_px REAL, exit_px REAL, pnl_inr REAL, cost_inr REAL, r REAL, status TEXT, notes TEXT
);
CREATE TABLE IF NOT EXISTS manual (
  id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT, ts TEXT, symbol TEXT, side TEXT, qty INTEGER, entry REAL,
  stop REAL, exit REAL, pnl_inr REAL, r REAL, notes TEXT
);
"""

TERMINAL = ("STOPPED", "T2_HIT", "CLOSED", "EXPIRED", "INVALIDATED", "DISPLACED")


class Journal:
    """Thread-safe SQLite journal. Implements the engine's JournalSink protocol."""

    def __init__(self, path: Path, costs_short: CostModel | None = None):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.cash = load_costs("cash")
        self.short_costs = costs_short or self.cash
        with self._conn() as c:
            c.executescript(SCHEMA)
        self._open_ideas: dict[str, Idea] = {}

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            con = sqlite3.connect(self.path, timeout=10)
            con.row_factory = sqlite3.Row
            try:
                yield con
                con.commit()
            finally:
                con.close()

    # ------------------------------------------------------------------ sink
    def log_idea(self, i: Idea) -> None:
        now = datetime.now(tz=IST).isoformat()
        row = {
            "id": i.id, "day": i.day, "mode": i.mode, "provenance": i.provenance, "signal_ts": i.signal_ts,
            "symbol": i.symbol, "key": i.key, "sector": i.sector, "setup": i.setup, "side": i.side, "regime": i.regime,
            "grade": i.grade, "entry": i.entry, "stop": i.stop, "t1": i.t1, "t2": i.t2, "rr1": i.rr1, "rr2": i.rr2,
            "p": _f(i.p), "ci_lo": _f(i.ci_lo), "ci_hi": _f(i.ci_hi), "n": i.n, "expectancy": _f(i.expectancy),
            "cons_ev": _f(i.cons_ev), "breakeven": _f(i.breakeven), "qty": i.qty, "risk_inr": i.risk_inr,
            "notional": i.notional, "valid_until": i.valid_until, "expires_at": i.expires_at,
            "gate_bypassed": int(i.gate_bypassed), "stats_level": i.stats_level, "model_version": i.model_version,
            "config_hash": i.config_hash, "snapshot": json.dumps(i.snapshot, default=_json_default),
            "thesis": json.dumps(i.thesis), "invalidation": i.invalidation, "status": i.status,
            "status_ts": i.status_ts, "created_at": now,
        }
        cols = ",".join(row)
        qs = ",".join("?" * len(row))
        with self._conn() as c:
            c.execute(f"INSERT OR REPLACE INTO ideas ({cols}) VALUES ({qs})", list(row.values()))
            c.execute("INSERT INTO events (idea_id, ts, old, new, price, note) VALUES (?,?,?,?,?,?)",
                      (i.id, i.signal_ts, "", "WATCHING", i.last_price, "shown on board"))
        self._open_ideas[i.id] = i

    def log_transition(self, i: Idea, tr: Transition) -> None:
        resolved = tr.new in TERMINAL
        won = int(i.t1_done) if resolved and i.status != "DISPLACED" else None
        with self._conn() as c:
            c.execute("INSERT INTO events (idea_id, ts, old, new, price, note) VALUES (?,?,?,?,?,?)",
                      (i.id, tr.ts, tr.old, tr.new, _f(tr.price), tr.note))
            c.execute("UPDATE ideas SET status=?, status_ts=?, entry_px=?, exit_px=?, r_realised=?, won=?, resolved=? "
                      "WHERE id=?", (tr.new, tr.ts, _f(i.entry_px), _f(i.exit_px), _f(i.r_realised), won,
                                     int(resolved), i.id))
            if tr.new == "TRIGGERED":           # paper trades taken while WATCHING fill with the idea
                c.execute("UPDATE paper SET entry_px=? WHERE idea_id=? AND status='OPEN' AND entry_px IS NULL",
                          (_f(i.entry_px), i.id))
            if resolved:
                self._settle_paper(c, i)
        if resolved:
            self._open_ideas.pop(i.id, None)

    def pnl_today(self, day: str, mode: str, prices: dict[str, float]) -> float:
        """Realised paper P&L today + mark-to-market of open paper trades (for the daily loss guardrail)."""
        with self._conn() as c:
            realised = c.execute("SELECT COALESCE(SUM(pnl_inr),0) FROM paper WHERE day=? AND mode=? AND status='CLOSED'",
                                 (day, mode)).fetchone()[0]
            manual = c.execute("SELECT COALESCE(SUM(pnl_inr),0) FROM manual WHERE day=?", (day,)).fetchone()[0]
            open_rows = c.execute("SELECT p.idea_id, p.qty, p.entry_px, i.side, i.key FROM paper p JOIN ideas i "
                                  "ON p.idea_id=i.id WHERE p.day=? AND p.mode=? AND p.status='OPEN' "
                                  "AND p.entry_px IS NOT NULL", (day, mode)).fetchall()
        mtm = 0.0
        for r in open_rows:
            px = prices.get(r["key"])
            if px is not None:
                mtm += (px - r["entry_px"]) * r["qty"] * (1 if r["side"] == "long" else -1)
        return float(realised) + float(manual) + mtm

    # ------------------------------------------------------------------ paper trading
    def take_paper(self, i: Idea, now: datetime) -> dict[str, Any]:
        if not i.active:
            raise ValueError(f"idea is {i.status}; paper trades can only be taken on live ideas")
        entry = i.entry_px if i.status in ("TRIGGERED", "T1_HIT") else None
        if i.status in ("TRIGGERED", "T1_HIT") and i.last_price is not None:
            entry = i.last_price                       # taken after the trigger: fill at the current price
        with self._conn() as c:
            c.execute("INSERT OR IGNORE INTO paper (idea_id, day, mode, taken_ts, take_status, qty, entry_px, status) "
                      "VALUES (?,?,?,?,?,?,?,?)", (i.id, i.day, i.mode, now.isoformat(), i.status, i.qty, _f(entry), "OPEN"))
        return {"idea_id": i.id, "qty": i.qty, "entry_px": entry, "taken_at": now.isoformat(), "status": "OPEN"}

    def _settle_paper(self, c: sqlite3.Connection, i: Idea) -> None:
        row = c.execute("SELECT * FROM paper WHERE idea_id=? AND status='OPEN'", (i.id,)).fetchone()
        if row is None:
            return
        entry = row["entry_px"] if row["entry_px"] is not None else i.entry_px
        if entry is None or i.status in ("EXPIRED", "INVALIDATED", "DISPLACED"):
            c.execute("UPDATE paper SET status='VOID', pnl_inr=0, r=0 WHERE id=?", (row["id"],))
            return
        exit_px = i.exit_px if i.exit_px is not None else entry
        sg = 1 if i.side == "long" else -1
        risk_ps = abs(entry - i.stop) or 1e-9
        if row["take_status"] == "WATCHING" and i.r_realised is not None:
            # taken before the trigger: identical to the idea's own fills (partial at T1, costs included)
            r = i.r_realised
            costs = None
            pnl = r * risk_ps * row["qty"]
        else:
            # taken after the trigger at the then-current price; single exit at the idea's final exit
            costs = (self.cash if i.side == "long" else self.short_costs).trade(i.side, entry, row["qty"],
                                                                                   [(exit_px, row["qty"])])
            pnl = (exit_px - entry) * sg * row["qty"] - costs
            r = pnl / (risk_ps * row["qty"])
        c.execute("UPDATE paper SET status='CLOSED', exit_px=?, pnl_inr=?, cost_inr=?, r=?, entry_px=? WHERE id=?",
                  (exit_px, pnl, costs, r, entry, row["id"]))

    def add_manual(self, d: dict[str, Any]) -> int:
        sg = 1 if d["side"] == "long" else -1
        pnl = None
        r = None
        if d.get("exit") is not None:
            pnl = (d["exit"] - d["entry"]) * sg * d["qty"]
            if d.get("stop"):
                r = (d["exit"] - d["entry"]) * sg / abs(d["entry"] - d["stop"])
        with self._conn() as c:
            cur = c.execute("INSERT INTO manual (day, ts, symbol, side, qty, entry, stop, exit, pnl_inr, r, notes) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                            (d["day"], d.get("ts") or datetime.now(tz=IST).isoformat(), d["symbol"].upper(), d["side"],
                             int(d["qty"]), d["entry"], d.get("stop"), d.get("exit"), pnl, r, d.get("notes", "")))
            return int(cur.lastrowid or 0)

    # ------------------------------------------------------------------ queries
    def ideas(self, mode: str | None = None, day: str | None = None, include_demo: bool = False,
              provenance: str | None = None, limit: int = 2000) -> list[dict[str, Any]]:
        q = "SELECT * FROM ideas WHERE 1=1"
        args: list[Any] = []
        if mode:
            q += " AND mode=?"
            args.append(mode)
        if day:
            q += " AND day=?"
            args.append(day)
        if provenance:
            q += " AND provenance=?"
            args.append(provenance)
        if not include_demo:
            q += " AND gate_bypassed=0"
        q += " ORDER BY signal_ts DESC LIMIT ?"
        args.append(limit)
        with self._conn() as c:
            rows = [dict(r) for r in c.execute(q, args).fetchall()]
        for r in rows:
            r["thesis"] = json.loads(r["thesis"] or "[]")
            r["snapshot"] = json.loads(r["snapshot"] or "{}")
        return rows

    def events(self, idea_id: str) -> list[dict[str, Any]]:
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM events WHERE idea_id=? ORDER BY id", (idea_id,))]

    def paper_trades(self, mode: str | None = None) -> list[dict[str, Any]]:
        with self._conn() as c:
            q = "SELECT p.*, i.symbol, i.side, i.setup FROM paper p JOIN ideas i ON p.idea_id=i.id"
            if mode:
                return [dict(r) for r in c.execute(q + " WHERE p.mode=? ORDER BY p.id DESC", (mode,))]
            return [dict(r) for r in c.execute(q + " ORDER BY p.id DESC")]

    def manual_entries(self) -> list[dict[str, Any]]:
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM manual ORDER BY id DESC")]

    def paper_ids(self) -> set[str]:
        with self._conn() as c:
            return {r[0] for r in c.execute("SELECT idea_id FROM paper")}

    def unresolved(self, before_day: str) -> list[dict[str, Any]]:
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM ideas WHERE resolved=0 AND day<?", (before_day,))]

    def force_resolve(self, idea_id: str, status: str, note: str, r: float | None, won: int | None,
                      exit_px: float | None = None, entry_px: float | None = None) -> None:
        now = datetime.now(tz=IST).isoformat()
        with self._conn() as c:
            c.execute("INSERT INTO events (idea_id, ts, old, new, price, note) VALUES (?,?,?,?,?,?)",
                      (idea_id, now, "", status, exit_px, note))
            c.execute("UPDATE ideas SET status=?, status_ts=?, r_realised=?, won=?, resolved=1, exit_px=?, entry_px=? "
                      "WHERE id=?", (status, now, r, won, exit_px, entry_px, idea_id))


def _f(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v and v not in (float("inf"), float("-inf")) else None


def _json_default(o: Any) -> Any:
    try:
        return float(o)
    except (TypeError, ValueError):
        return str(o)
