from floor_insurance.models import DailyState, Phase
from floor_insurance.state import StateStore


def test_state_round_trip_and_daily_reset(tmp_path):
    store = StateStore(tmp_path / "nested" / "state.json")
    state = DailyState("2026-08-18", phase=Phase.OPEN, losses=2)
    store.save(state)
    loaded = store.load("2026-08-18")
    assert loaded.phase == Phase.OPEN
    assert loaded.losses == 2
    reset = store.load("2026-08-19")
    assert reset.phase == Phase.IDLE
    assert reset.losses == 0

