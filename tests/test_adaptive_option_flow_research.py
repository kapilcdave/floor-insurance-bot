from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from floor_insurance.adaptive_option_flow_research import (
    adaptive_metrics,
    required_symbols,
    simulate_adaptive_option_flow,
)
from floor_insurance.directional import PriceBar
from floor_insurance.option_flow_research import (
    OptionFlowSettings,
    flow_center,
    signal_contracts,
    spread_contracts,
)

ET = ZoneInfo("America/New_York")
DAY = date(2026, 8, 18)


def bar(hour: int, minute: int, value: str, *, close: str | None = None, volume: str = "0") -> PriceBar:
    opened = Decimal(value)
    closed = Decimal(close or value)
    return PriceBar(
        datetime(2026, 8, 18, hour, minute, tzinfo=ET),
        opened,
        max(opened, closed),
        min(opened, closed),
        closed,
        Decimal(volume),
    )


def test_required_symbols_include_three_candidate_hedges():
    config = OptionFlowSettings()
    symbols = required_symbols(DAY, Decimal("100.20"), config)
    assert "SPY260818P00097000" in symbols
    assert "SPY260818C00104000" in symbols
    assert len(symbols) == 11


def bullish_marks() -> tuple[list[PriceBar], dict[str, list[PriceBar]]]:
    config = OptionFlowSettings()
    spot = Decimal("100.20")
    underlying = [bar(10, 0, str(spot))]
    signals = signal_contracts(DAY, flow_center(spot), config)
    options: dict[str, list[PriceBar]] = {
        signals["C_+0"]: [bar(9, 45, "1", close="1.1", volume="1200")]
    }
    # Width 3 credit 1.80 -> $120.10 risk; width 2 credit 1.10 -> $90.10 risk.
    for width, long_price in ((Decimal("3"), "0.20"), (Decimal("2"), "0.90"), (Decimal("1"), "1.40")):
        configured = replace(config, width=width)
        _, _, short_symbol, long_symbol = spread_contracts(
            DAY, spot, "bullish", configured
        )
        options[short_symbol] = [bar(10, 0, "2.04"), bar(15, 0, "0.70")]
        options[long_symbol] = [bar(10, 0, long_price), bar(15, 0, "0.20")]
    return underlying, options


def test_selects_widest_width_that_passes_hundred_dollar_risk():
    underlying, options = bullish_marks()
    result = simulate_adaptive_option_flow(
        DAY.isoformat(), underlying, options, OptionFlowSettings()
    )
    assert result.entered is True
    assert result.width == Decimal("2")
    assert result.entry_credit == Decimal("1.10")
    assert result.maximum_risk == Decimal("90.10")


def test_common_signal_failure_does_not_try_other_widths():
    underlying, options = bullish_marks()
    result = simulate_adaptive_option_flow(
        DAY.isoformat(),
        underlying,
        options,
        replace(OptionFlowSettings(), flow_threshold=Decimal("1.01")),
    )
    assert result.entered is False
    assert "threshold" in result.reason
    assert result.width == Decimal("3")


def test_width_validation_and_metrics():
    with pytest.raises(ValueError, match="widest first"):
        simulate_adaptive_option_flow(
            DAY.isoformat(), [], {}, OptionFlowSettings(), (Decimal("1"), Decimal("2"))
        )
    with pytest.raises(ValueError, match="positive"):
        simulate_adaptive_option_flow(
            DAY.isoformat(), [], {}, OptionFlowSettings(), (Decimal("0"),)
        )
    underlying, options = bullish_marks()
    result = simulate_adaptive_option_flow(
        DAY.isoformat(), underlying, options, OptionFlowSettings()
    )
    metrics = adaptive_metrics([result])
    assert metrics["average_width"] == "2.00"
    assert metrics["selected_widths"] == {"2": 1}
