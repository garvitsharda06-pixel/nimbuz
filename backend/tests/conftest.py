from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from dalal.config import Settings, load_settings
from dalal.data.store import CandleStore
from dalal.data.synthetic import SynthParams, generate


@pytest.fixture(scope="session")
def syn_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("data")
    generate(root, SynthParams(symbols=16, days=40, seed=11, start=date(2024, 1, 1)))
    return root


@pytest.fixture(scope="session")
def syn_store(syn_root: Path) -> CandleStore:
    return CandleStore(syn_root, "synthetic")


@pytest.fixture()
def settings(syn_root: Path) -> Settings:
    return load_settings(overrides={"data": {"root": str(syn_root), "provider": "synthetic"},
                                    "universe": {"lookback_days": 10}})
