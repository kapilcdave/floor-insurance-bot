from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from floor_insurance.adaptive_iron_fly_research import (
    adaptive_metrics,
    required_symbols,
    simulate_adaptive_iron_fly,
)
from floor_insurance.directional import PriceBar
from floor_insurance.iron_fly_research import IronFlySettings, iron_fly_symbols

ET = ZoneInfo("America/New_York")
DAY = date(2026, 8, 18)


def bar(hour: int, minute: int, value: str) -> PriceBar:
    price = Decimal(value)
    return PriceBar(
        datetime(2026, 8, 18, hour, minute, tzinfo=ET),
        price,
        price,
        price,
        price,
    )


def test_required_symbols_share_center_legs_across_four_widths():
    symbols = required_symbols(DAY, Decimal("101"), IronFlySettings())
    assert len(symbols) == 10
    assert "SPY260818P00096000" in symbols
    assert "SPY260818C00106000" in symbols
    assert "SPY260818P00101000" in symbols
    assert "SPY260818C00101000" in symbols


def marks() -> dict[str, list[PriceBar]]:
    settings = IronFlySettings()
    center = Decimal("101")
    result: dict[str, list[PriceBar]] = {}
    center_entries = {"short_put": "1.20", "short_call": "1.30"}
    wing_entries = {
        Decimal("5"): ("0.01", "0.01"),
        Decimal("4"): ("0.05", "0.05"),
        Decimal("3"): ("0.16", "0.16"),
        Decimal("2"): ("0.30", "0.30"),
    }
    for width, (put_price, call_price) in wing_entries.items():
        symbols = iron_fly_symbols(DAY, center, replace(settings, wing_width=width))
        for name, symbol in symbols.items():
            entry = center_entries.get(name)
            if entry is None:
                entry = put_price if name == "long_put" else call_price
            result[symbol] = [bar(11, 0, entry), bar(15, 0, "0.20")]
    return result


def test_selects_widest_entry_time_candidate_below_hundred_dollar_risk():
    underlying = [bar(11, 0, "100.50"), bar(15, 59, "101.00")]
    result = simulate_adaptive_iron_fly(
        DAY.isoformat(), underlying, marks(), Decimal("1"), IronFlySettings()
    )
    assert result.entered is True
    assert result.wing_width == Decimal("3")
    assert result.entry_credit == Decimal("2.10")
    assert result.maximum_risk == Decimal("90.20")


def test_common_richness_failure_stops_without_width_fallback():
    result = simulate_adaptive_iron_fly(
        DAY.isoformat(),
        [bar(11, 0, "100.50")],
        marks(),
        Decimal("3"),
        IronFlySettings(),
    )
    assert result.entered is False
    assert result.reason == "implied move is below richness gate"
    assert result.wing_width == Decimal("5")


def test_widths_must_be_positive_and_widest_first():
    with pytest.raises(ValueError, match="widest first"):
        simulate_adaptive_iron_fly(
            DAY.isoformat(), [], {}, Decimal("1"), IronFlySettings(), (Decimal("2"), Decimal("3"))
        )
    with pytest.raises(ValueError, match="positive"):
        simulate_adaptive_iron_fly(
            DAY.isoformat(), [], {}, Decimal("1"), IronFlySettings(), (Decimal("0"),)
        )


def test_metrics_reports_selected_widths():
    underlying = [bar(11, 0, "100.50"), bar(15, 59, "101.00")]
    result = simulate_adaptive_iron_fly(
        DAY.isoformat(), underlying, marks(), Decimal("1"), IronFlySettings()
    )
    metrics = adaptive_metrics([result])
    assert metrics["average_wing_width"] == "3.00"
    assert metrics["selected_widths"] == {"3": 1}
