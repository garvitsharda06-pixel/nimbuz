"""Historical candle store: one Parquet file per instrument, queried with DuckDB (D-007)."""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from dalal.clock import IST
from dalal.data.models import BAR_COLUMNS, Instrument, empty_bars

_SAFE = re.compile(r"[^A-Za-z0-9_.-]")


def key_to_filename(key: str) -> str:
    return _SAFE.sub("_", key.replace("|", "__")) + ".parquet"


class CandleStore:
    """Data under `<root>/<provenance>/` with:
    - candles_1m/<key>.parquet   (ts UTC instant + IST tz metadata, o/h/l/c/volume/oi)
    - instruments.json           (instrument snapshot incl. sectors, index roles)
    - manifest.json              (provenance, created, generator params / download log)
    """

    def __init__(self, root: Path, provenance: str):
        self.provenance = provenance
        self.base = Path(root) / provenance
        self.candles_dir = self.base / "candles_1m"
        self.candles_dir.mkdir(parents=True, exist_ok=True)

    # ---------- metadata ----------
    def write_manifest(self, manifest: dict[str, Any]) -> None:
        manifest = {"provenance": self.provenance, **manifest}
        (self.base / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str))

    def manifest(self) -> dict[str, Any]:
        p = self.base / "manifest.json"
        return json.loads(p.read_text()) if p.exists() else {"provenance": self.provenance}

    def write_instruments(self, instruments: list[Instrument], roles: dict[str, Any]) -> None:
        payload = {"roles": roles, "instruments": [i.to_dict() for i in instruments]}
        (self.base / "instruments.json").write_text(json.dumps(payload, indent=1))

    def instruments(self) -> tuple[list[Instrument], dict[str, Any]]:
        p = self.base / "instruments.json"
        if not p.exists():
            return [], {}
        payload = json.loads(p.read_text())
        return [Instrument.from_dict(d) for d in payload["instruments"]], payload.get("roles", {})

    # ---------- candles ----------
    def path(self, key: str) -> Path:
        return self.candles_dir / key_to_filename(key)

    def has(self, key: str) -> bool:
        return self.path(key).exists()

    def write(self, key: str, df: pd.DataFrame, merge: bool = True) -> int:
        """Write 1m bars for `key`, de-duplicated on ts. Returns resulting row count."""
        df = _normalize(df)
        p = self.path(key)
        if merge and p.exists():
            old = self.read(key)
            df = pd.concat([old, df], ignore_index=True)
            df = df.drop_duplicates("ts", keep="last")
        df = df.sort_values("ts").reset_index(drop=True)
        out = df.copy()
        out["ts"] = out["ts"].dt.tz_convert("UTC")
        out.to_parquet(p, index=False)
        return len(df)

    def read(self, key: str, start: date | None = None, end: date | None = None) -> pd.DataFrame:
        p = self.path(key)
        if not p.exists():
            return empty_bars()
        if start is None and end is None:
            df = pd.read_parquet(p)
        else:
            lo = datetime.combine(start or date(1990, 1, 1), time(0, 0), tzinfo=IST).astimezone(UTC)
            hi = datetime.combine(end or date(2100, 1, 1), time(23, 59, 59), tzinfo=IST).astimezone(UTC)
            con = duckdb.connect()
            try:
                df = con.execute(
                    "SELECT * FROM read_parquet(?) WHERE ts >= ? AND ts <= ? ORDER BY ts",
                    [str(p), lo, hi],
                ).df()
            finally:
                con.close()
        return _normalize(df)

    def read_day(self, key: str, d: date) -> pd.DataFrame:
        return self.read(key, d, d)

    def keys(self) -> list[str]:
        """Instrument keys that have candle files (keys come from instruments.json)."""
        insts, _ = self.instruments()
        return sorted(i.key for i in insts if self.has(i.key))

    def days(self, key: str) -> list[date]:
        p = self.path(key)
        if not p.exists():
            return []
        con = duckdb.connect()
        try:
            rows = con.execute(
                "SELECT DISTINCT CAST(timezone('Asia/Kolkata', ts) AS DATE) AS d FROM read_parquet(?) ORDER BY d",
                [str(p)],
            ).fetchall()
        finally:
            con.close()
        return [r[0] for r in rows]

    def daily(self, key: str, start: date | None = None, end: date | None = None) -> pd.DataFrame:
        """Daily OHLCV + traded value (₹) aggregated from 1m bars, via DuckDB."""
        p = self.path(key)
        if not p.exists():
            return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume", "traded_value"])
        con = duckdb.connect()
        try:
            q = """
            WITH b AS (SELECT CAST(timezone('Asia/Kolkata', ts) AS DATE) AS d, ts, open, high, low, close, volume
                       FROM read_parquet(?))
            SELECT d AS date, arg_min(open, ts) AS open, max(high) AS high, min(low) AS low,
                   arg_max(close, ts) AS close, sum(volume) AS volume,
                   sum(volume * (high + low + close) / 3.0) AS traded_value
            FROM b WHERE (? IS NULL OR d >= ?) AND (? IS NULL OR d <= ?)
            GROUP BY d ORDER BY d
            """
            df = con.execute(q, [str(p), start, start, end, end]).df()
        finally:
            con.close()
        df["date"] = pd.to_datetime(df["date"]).dt.date
        return df


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if "oi" not in df.columns:
        df["oi"] = float("nan")
    ts = pd.to_datetime(df["ts"])
    if ts.dt.tz is None:
        raise ValueError("candle timestamps must be tz-aware")
    df["ts"] = ts.dt.tz_convert(IST)
    for c in ["open", "high", "low", "close", "volume", "oi"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
    return df[BAR_COLUMNS].reset_index(drop=True)
