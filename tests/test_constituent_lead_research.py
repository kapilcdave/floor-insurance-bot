from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from floor_insurance.constituent_lead_research import (
    CONSTITUENTS,
    LeadResult,
    LeadSettings,
    SpyObservation,
    block_bootstrap,
    chronological_splits,
    lead_metrics,
    lead_residual,
    nearest_rank_percentile,
    observed_return,
    simulate_lead_session,
    viable,
)
from floor_insurance.directional import PriceBar

ET = ZoneInfo("America/New_York")


def bar(minute: int, opened: str, closed: str | None = None) -> PriceBar:
    start = datetime(2026, 8, 18, 10, 55, tzinfo=ET)
    open_value = Decimal(opened)
    close_value = Decimal(closed or opened)
    return PriceBar(
        start + timedelta(minutes=minute),
        open_value,
        max(open_value, close_value),
        min(open_value, close_value),
        close_value,
    )


def test_observed_return_requires_exact_start_and_end_bars():
    settings = LeadSettings()
    assert observed_return([bar(0, "100"), bar(4, "100", "101")], settings) == Decimal("0.010000")
    assert observed_return([bar(0, "100")], settings) is None


def histories(residual: str = "0.001"):
    spy = {"2026-08-18": SpyObservation(Decimal("0"), Decimal("100"), Decimal("101"))}
    members = {
        symbol: {"2026-08-18": Decimal(residual)} for symbol in CONSTITUENTS
    }
    return spy, members


def test_residual_needs_every_fixed_constituent():
    spy, members = histories()
    assert lead_residual("2026-08-18", spy, members) == Decimal("0.001000")
    del members[CONSTITUENTS[-1]]["2026-08-18"]
    assert lead_residual("2026-08-18", spy, members) is None


def test_nearest_rank_threshold_and_no_lookahead_signal():
    assert nearest_rank_percentile(
        [Decimal(index) for index in range(1, 11)], Decimal("0.70")
    ) == Decimal("7")
    spy, members = histories("0.010")
    prior = [Decimal(index) / Decimal("1000") for index in range(1, 61)]
    result = simulate_lead_session(
        "2026-08-18", spy, members, prior, LeadSettings()
    )
    assert result.signaled is False
    assert result.threshold == Decimal("0.042")


def test_bullish_signal_books_thirty_minute_return_and_cost():
    spy, members = histories("0.010")
    prior = [Decimal("0.001")] * 60
    result = simulate_lead_session(
        "2026-08-18", spy, members, prior, LeadSettings()
    )
    assert result.signaled is True
    assert result.direction == "bullish"
    assert result.gross_return == Decimal("0.010000")
    assert result.net_return == Decimal("0.009900")


def test_metrics_viability_splits_and_bootstrap():
    wins = [
        LeadResult("2026-01-01", True, "exit", "bullish", net_return=Decimal("0.002"))
        for _ in range(80)
    ]
    losses = [
        LeadResult("2026-01-02", True, "exit", "bearish", net_return=Decimal("-0.001"))
        for _ in range(20)
    ]
    metrics = lead_metrics(wins + losses)
    assert metrics["signals"] == 100
    assert metrics["profit_factor"] == "8.0000"
    passing = {**metrics, "max_drawdown_percent": "-0.50"}
    report = {
        "train": passing,
        "validation": {**passing, "signals": 30},
        "train_stress": metrics,
        "validation_stress": metrics,
    }
    assert viable(report) is True
    assert viable({**report, "validation": {**report["validation"], "win_rate": 0.52}}) is False
    assert len(chronological_splits([str(i) for i in range(100)])["train"]) == 75
    first = block_bootstrap([Decimal("0.01"), Decimal("-0.005")], paths=20, block_length=2, seed=7)
    second = block_bootstrap([Decimal("0.01"), Decimal("-0.005")], paths=20, block_length=2, seed=7)
    assert first == second
