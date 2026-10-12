"""Configuration: YAML settings + .env secrets, with a stable config hash."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import date as Date
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = Path(os.environ.get("DALAL_CONFIG_DIR", REPO_ROOT / "config"))


class RateLimits(BaseModel):
    per_second: int = 25
    per_minute: int = 250
    per_30min: int = 1000


class UpstoxCfg(BaseModel):
    instruments_url: str = "https://assets.upstox.com/market-quote/instruments/exchange/complete.json.gz"
    rest_base: str = "https://api.upstox.com"
    ws_url: str = "wss://api.upstox.com/v3/feed/market-data-feed"
    feed_mode: str = "full"
    rate_limits: RateLimits = RateLimits()
    safety_factor: float = 0.8
    index_keys: dict[str, str] = Field(default_factory=dict)
    sector_index_keys: dict[str, str] = Field(default_factory=dict)


class DataCfg(BaseModel):
    root: str = "data"
    provider: Literal["upstox", "synthetic"] = "synthetic"
    history_years: int = 3
    stale_seconds: float = 5.0
    max_clock_drift_ms: int = 2000
    bar_close_grace_ms: int = 300
    upstox: UpstoxCfg = UpstoxCfg()

    @property
    def root_path(self) -> Path:
        p = Path(self.root)
        return p if p.is_absolute() else REPO_ROOT / p


class SessionCfg(BaseModel):
    tz: str = "Asia/Kolkata"
    open: str = "09:15"
    close: str = "15:30"
    first_signal: str = "09:20"
    last_new_idea: str = "14:45"
    idea_expiry: str = "15:10"
    resolve_at: str = "15:15"


class UniverseCfg(BaseModel):
    fno_only: bool = True
    include_nifty500: bool = False
    min_median_traded_value_cr: float = 50.0
    min_price: float = 50.0
    lookback_days: int = 20
    circuit_buffer_pct: float = 1.0
    exclude_event_risk: bool = True
    exclude_asm_gsm: bool = True
    exclude_ban_for_futures_shorts: bool = True


class RiskCfg(BaseModel):
    capital: float = 500_000.0
    risk_per_trade_pct: float = 0.5
    max_daily_loss_pct: float = 2.0
    max_open_ideas: int = 10
    max_per_sector_per_side: int = 2
    short_instrument: Literal["cash", "futures"] = "cash"
    mis_leverage: float = 5.0


class GateCfg(BaseModel):
    min_n: int = 200
    min_wf_stability: float = 0.6
    min_fold_trades: int = 20


class GradesCfg(BaseModel):
    a_plus: float = 0.25
    a: float = 0.10


class BoardCfg(BaseModel):
    max_per_side: int = 5
    hysteresis_pct: float = 20.0
    correlation_max: float = 0.8
    correlation_lookback_days: int = 20
    max_net_beta: float = 1.5
    demo_bypass_gates: bool = False
    gate: GateCfg = GateCfg()
    grades: GradesCfg = GradesCfg()


class ScoringCfg(BaseModel):
    prior_strength: float = 50.0
    ci_z: float = 1.96
    min_bucket_n: int = 30


class SlippageCfg(BaseModel):
    spread_bps: float = 3.0
    k_vol: float = 0.10


class BacktestCfg(BaseModel):
    validity_minutes: int = 30
    partial_at_t1: float = 0.5
    wf_train_months: int = 12
    wf_test_months: int = 3
    wf_embargo_days: int = 2
    slippage: SlippageCfg = SlippageCfg()
    start: Date | None = None
    end: Date | None = None


class SetupToggle(BaseModel):
    enabled: bool = True
    params: dict[str, Any] = Field(default_factory=dict)


class NotifyCfg(BaseModel):
    browser: bool = True
    sounds: bool = True
    telegram: bool = True
    morning_note_at: str = "08:45"
    eod_report_at: str = "15:45"


class ApiCfg(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8000
    cors_origins: list[str] = Field(default_factory=lambda: ["http://127.0.0.1:5173", "http://localhost:5173"])


class ReplayCfg(BaseModel):
    speed: float = 30.0
    date: Date | None = None


class SyntheticCfg(BaseModel):
    symbols: int = 40
    days: int = 750
    seed: int = 7
    start: Date = Date(2023, 1, 2)
    idio_momentum: float = 0.0
    trend_day_drift_bps: float = 60.0


class Settings(BaseModel):
    mode: Literal["live", "replay"] = "replay"
    data: DataCfg = DataCfg()
    session: SessionCfg = SessionCfg()
    universe: UniverseCfg = UniverseCfg()
    risk: RiskCfg = RiskCfg()
    board: BoardCfg = BoardCfg()
    scoring: ScoringCfg = ScoringCfg()
    backtest: BacktestCfg = BacktestCfg()
    setups: dict[str, SetupToggle] = Field(default_factory=dict)
    notify: NotifyCfg = NotifyCfg()
    api: ApiCfg = ApiCfg()
    replay: ReplayCfg = ReplayCfg()
    synthetic: SyntheticCfg = SyntheticCfg()

    def config_hash(self) -> str:
        """Stable short hash of every non-secret setting (journal snapshots carry it)."""
        blob = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()[:12]

    def setup_enabled(self, name: str) -> bool:
        t = self.setups.get(name)
        return True if t is None else t.enabled

    def setup_params(self, name: str) -> dict[str, Any]:
        t = self.setups.get(name)
        return {} if t is None else dict(t.params)


class Secrets(BaseSettings):
    """Secrets come only from the environment / .env. Never logged (see dalal.log)."""

    model_config = SettingsConfigDict(env_file=str(REPO_ROOT / ".env"), extra="ignore")

    upstox_analytics_token: SecretStr | None = None
    telegram_bot_token: SecretStr | None = None
    telegram_chat_id: str | None = None


def load_settings(path: Path | None = None, overrides: dict[str, Any] | None = None) -> Settings:
    path = path or CONFIG_DIR / "settings.yaml"
    raw: dict[str, Any] = {}
    if path.exists():
        raw = yaml.safe_load(path.read_text()) or {}
    if overrides:
        raw = _deep_merge(raw, overrides)
    return Settings.model_validate(raw)


def _deep_merge(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    out = dict(a)
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()


def get_secrets() -> Secrets:
    return Secrets()


def load_yaml(name: str) -> dict[str, Any]:
    p = CONFIG_DIR / name
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text()) or {}
