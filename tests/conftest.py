from __future__ import annotations

from dataclasses import replace

import pytest

from floor_insurance.config import Config


@pytest.fixture
def config(monkeypatch: pytest.MonkeyPatch, tmp_path):
    values = {
        "ALPACA_API_KEY": "test-key",
        "ALPACA_API_SECRET": "test-secret",
        "ALPACA_PAPER": "true",
        "DRY_RUN": "true",
        "OPTIONS_FEED": "indicative",
        "STOCK_FEED": "iex",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    return replace(Config.from_env(), state_path=tmp_path / "state.json")

