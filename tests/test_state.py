import pytest

from floor_insurance.models import DailyState, Phase
from floor_insurance.state import StateStore


def test_state_round_trip_and_daily_reset(tmp_path):
    store = StateStore(tmp_path / "nested" / "state.json")
    state = DailyState("2026-08-18", phase=Phase.OPEN, losses=2)
    store.save(state)
    loaded = store.load("2026-08-18")
    assert loaded.phase == Phase.OPEN
    assert loaded.losses == 2
    loaded.phase = Phase.DONE
    store.save(loaded)
    reset = store.load("2026-08-19")
    assert reset.phase == Phase.IDLE
    assert reset.losses == 0


def test_prior_day_open_state_requires_manual_reconciliation(tmp_path):
    store = StateStore(tmp_path / "state.json")
    store.save(DailyState("2026-08-18", phase=Phase.OPEN))
    with pytest.raises(RuntimeError, match="manual Alpaca reconciliation"):
        store.load("2026-08-19")
