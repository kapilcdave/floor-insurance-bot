from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from floor_insurance.constituent_lead_research import (
    CONSTITUENTS,
    LeadSettings,
    SpyObservation,
    covered_window_return,
    simulate_sparse_lead_session,
    sparse_lead_residual,
)
from floor_insurance.directional import PriceBar

ET = ZoneInfo("America/New_York")


def bar(minute: int, opened: str, closed: str | None = None) -> PriceBar:
    start = datetime(2026, 8, 18, 10, 55, tzinfo=ET)
    first = Decimal(opened)
    last = Decimal(closed or opened)
    return PriceBar(
        start + timedelta(minutes=minute),
        first,
        max(first, last),
        min(first, last),
        last,
    )


def test_covered_window_allows_sparse_interior_bars_but_enforces_edges():
    settings = LeadSettings()
    assert covered_window_return(
        [bar(1, "100"), bar(3, "100", "101")], settings
    ) == Decimal("0.010000")
    assert covered_window_return([bar(2, "100"), bar(3, "101")], settings) is None
    assert covered_window_return([bar(1, "100"), bar(2, "101")], settings) is None


def histories(count: int = 8):
    spy = {"2026-08-18": SpyObservation(Decimal("0"), Decimal("100"), Decimal("101"))}
    members = {
        symbol: {"2026-08-18": Decimal("0.001")} for symbol in CONSTITUENTS[:count]
    }
    return spy, members


def test_sparse_residual_requires_eight_and_reports_member_count():
    spy, members = histories(8)
    assert sparse_lead_residual("2026-08-18", spy, members) == (
        Decimal("0.001000"),
        8,
    )
    spy, members = histories(7)
    assert sparse_lead_residual("2026-08-18", spy, members) is None


def test_sparse_signal_uses_same_threshold_cost_and_direction():
    spy, members = histories(9)
    result = simulate_sparse_lead_session(
        "2026-08-18",
        spy,
        members,
        [Decimal("0.0001")] * 60,
        LeadSettings(),
    )
    assert result.signaled is True
    assert result.direction == "bullish"
    assert result.member_count == 9
    assert result.net_return == Decimal("0.009900")
