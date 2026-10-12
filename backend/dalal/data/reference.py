"""Reference data (§6): daily, cached, best-effort, NEVER blocking.

Each fetcher returns a RefResult with `stale=True` (and the last cached value, if any)
when the source fails. The UI shows stale panels in amber. Polite: a fixed honest
User-Agent, ≥2 s between requests to one host, timeouts, and a disk cache.
URLs marked UNVERIFIED could not be checked from the build sandbox (DECISIONS D-009).
"""

from __future__ import annotations

import csv
import io
import json
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import httpx

from dalal.clock import IST
from dalal.config import CONFIG_DIR

UA = "DalalDesk/0.1 (personal research terminal; low-rate daily fetch)"

# UNVERIFIED URLs (best-effort; failures mark the panel stale)
FO_BAN_URL = "https://nsearchives.nseindia.com/content/fo/fo_secban.csv"
NIFTY500_URL = "https://niftyindices.com/IndexConstituent/ind_nifty500list.csv"
NIFTYBANK_URL = "https://niftyindices.com/IndexConstituent/ind_niftybanklist.csv"
SEC_BHAV_URL = "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{ddmmyyyy}.csv"

# Nifty 500 "Industry" → sector code used for sector indices (config sector_index_keys)
INDUSTRY_TO_SECTOR = {
    "Financial Services": "FIN",
    "Information Technology": "IT",
    "Automobile and Auto Components": "AUTO",
    "Healthcare": "PHARMA",
    "Metals & Mining": "METAL",
    "Fast Moving Consumer Goods": "FMCG",
    "Oil Gas & Consumable Fuels": "ENERGY",
    "Power": "ENERGY",
    "Realty": "REALTY",
    "Media Entertainment & Publication": "MEDIA",
    "Construction": "INFRA",
    "Capital Goods": "INFRA",
    "Construction Materials": "INFRA",
    "Telecommunication": "INFRA",
}


@dataclass
class RefResult:
    name: str
    data: Any
    fetched_at: str | None
    stale: bool
    source: str
    error: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "fetched_at": self.fetched_at, "stale": self.stale,
                "source": self.source, "error": self.error}


class ReferenceData:
    def __init__(self, cache_dir: Path, timeout: float = 10.0, min_interval_s: float = 2.0):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.min_interval_s = min_interval_s
        self._last_hit: dict[str, float] = {}

    # ---- cache ----
    def _cache_path(self, name: str) -> Path:
        return self.cache_dir / f"{name}.json"

    def _save(self, name: str, data: Any, source: str) -> RefResult:
        now = datetime.now(tz=IST).isoformat()
        self._cache_path(name).write_text(json.dumps({"data": data, "fetched_at": now, "source": source}))
        return RefResult(name, data, now, False, source)

    def _cached(self, name: str, source: str, error: str) -> RefResult:
        p = self._cache_path(name)
        if p.exists():
            c = json.loads(p.read_text())
            return RefResult(name, c["data"], c["fetched_at"], True, c.get("source", source), error)
        return RefResult(name, None, None, True, source, error)

    def _get(self, url: str) -> str:
        host = httpx.URL(url).host
        wait = self.min_interval_s - (time.monotonic() - self._last_hit.get(host, 0.0))
        if wait > 0:
            time.sleep(wait)
        self._last_hit[host] = time.monotonic()
        r = httpx.get(url, headers={"User-Agent": UA}, timeout=self.timeout, follow_redirects=True)
        r.raise_for_status()
        return r.text

    # ---- fetchers ----
    def fo_ban(self) -> RefResult:
        try:
            return self._save("fo_ban", sorted(parse_fo_ban(self._get(FO_BAN_URL))), FO_BAN_URL)
        except Exception as e:
            return self._cached("fo_ban", FO_BAN_URL, f"{type(e).__name__}")

    def sector_map(self) -> RefResult:
        try:
            m = parse_nifty500(self._get(NIFTY500_URL))
            try:
                for sym in parse_constituents(self._get(NIFTYBANK_URL)):
                    m[sym] = "BANK"
            except Exception:
                pass
            m.update(load_sector_overrides())
            return self._save("sector_map", m, NIFTY500_URL)
        except Exception as e:
            r = self._cached("sector_map", NIFTY500_URL, f"{type(e).__name__}")
            if r.data is None:
                r.data = load_sector_overrides()
            return r

    def delivery(self, d: date) -> RefResult:
        url = SEC_BHAV_URL.format(ddmmyyyy=d.strftime("%d%m%Y"))
        try:
            return self._save(f"delivery_{d.isoformat()}", parse_sec_bhav(self._get(url)), url)
        except Exception as e:
            return self._cached(f"delivery_{d.isoformat()}", url, f"{type(e).__name__}")

    def asm_gsm(self) -> RefResult:
        rows = _read_csv(CONFIG_DIR / "reference" / "asm_gsm.csv")
        return RefResult("asm_gsm", sorted({r["symbol"].strip().upper() for r in rows if r.get("symbol")}),
                         None, False, "config/reference/asm_gsm.csv (owner-maintained)")

    def events(self, d: date) -> RefResult:
        rows = _read_csv(CONFIG_DIR / "reference" / "events.csv")
        today = [r for r in rows if r.get("date") == d.isoformat()]
        return RefResult("events", today, None, False, "config/reference/events.csv (owner-maintained)")


# ---- parsers (pure; unit-tested) ----

def parse_fo_ban(text: str) -> set[str]:
    out = set()
    for line in text.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 2 and parts[0].isdigit() and parts[1]:
            out.add(parts[1].upper())
    return out


def parse_nifty500(text: str) -> dict[str, str]:
    out = {}
    for r in csv.DictReader(io.StringIO(text)):
        sym = (r.get("Symbol") or "").strip().upper()
        ind = (r.get("Industry") or "").strip()
        if sym:
            out[sym] = INDUSTRY_TO_SECTOR.get(ind, ind.upper()[:12] or "UNKNOWN")
    return out


def parse_constituents(text: str) -> list[str]:
    return [(r.get("Symbol") or "").strip().upper() for r in csv.DictReader(io.StringIO(text)) if r.get("Symbol")]


def parse_sec_bhav(text: str) -> dict[str, dict[str, float | str]]:
    out: dict[str, dict[str, float | str]] = {}
    rdr = csv.DictReader(io.StringIO(text))
    for r in rdr:
        r = {k.strip(): (v or "").strip() for k, v in r.items() if k}
        if r.get("SERIES") not in ("EQ", "BE", "BZ"):
            continue
        try:
            dp = float(r.get("DELIV_PER", "nan"))
        except ValueError:
            dp = float("nan")
        out[r["SYMBOL"]] = {"series": r["SERIES"], "deliv_pct": dp}
    return out


def load_sector_overrides() -> dict[str, str]:
    rows = _read_csv(CONFIG_DIR / "reference" / "sectors_override.csv")
    return {r["symbol"].strip().upper(): r["sector"].strip().upper() for r in rows if r.get("symbol") and r.get("sector")}


def _read_csv(p: Path) -> list[dict[str, str]]:
    if not p.exists():
        return []
    return list(csv.DictReader(io.StringIO(p.read_text())))
