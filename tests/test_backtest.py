from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from floor_insurance.backtest import BacktestSettings, Row, metrics, run_backtest, split_dates

ET = ZoneInfo("America/New_York")


def session(day: int, *, stop: bool = False):
    entered = datetime(2026, 1, day, 9, 45, tzinfo=ET)
    return [
        Row(
            entered,
            Decimal("550"),
            Decimal("535"),
            Decimal("534"),
            Decimal("0.60"),
            Decimal("0.65"),
            Decimal("0.08"),
            Decimal("0.10"),
        ),
        Row(
            entered + timedelta(minutes=5),
            Decimal("537" if stop else "550"),
            Decimal("535"),
            Decimal("534"),
            Decimal("0.15"),
            Decimal("0.30"),
            Decimal("0.08"),
            Decimal("0.10"),
        ),
    ]


def test_chronological_split_keeps_last_twenty_percent_locked():
    dates = [f"2026-01-{day:02d}" for day in range(1, 11)]
    splits = split_dates(dates)
    assert splits["train"] == set(dates[:6])
    assert splits["validation"] == set(dates[6:8])
    assert splits["out_of_sample"] == set(dates[8:])


def test_simulation_uses_bid_ask_and_stop_before_take_profit():
    sessions = {f"2026-01-{day:02d}": session(day, stop=day == 2) for day in range(1, 11)}
    results = run_backtest(sessions, BacktestSettings())
    assert results[0].reason == "take_profit"
    assert results[0].pnl == Decimal("28.00")
    assert results[1].reason == "emergency_stop"
    assert results[1].pnl == Decimal("28.00")
    report = metrics(results)
    assert report["trades"] == 10
    assert report["emergency_stops"] == 1
