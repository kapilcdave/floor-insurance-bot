from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from floor_insurance.delayed_option_flow_research import delayed_settings
from floor_insurance.directional import PriceBar
from floor_insurance.option_flow_research import (
    flow_center,
    option_flow_score,
    signal_contracts,
    simulate_option_flow,
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


def test_delayed_settings_freeze_signal_at_ten_and_entry_at_ten_fifteen():
    config = delayed_settings()
    assert str(config.signal_end) == "10:00:00"
    assert str(config.entry_time) == "10:15:00"
    contracts = signal_contracts(DAY, Decimal("100"), config)
    options = {
        contracts["C_+0"]: [
            bar(9, 59, "1", close="1.1", volume="1000"),
            bar(10, 0, "1", close="0.9", volume="1000"),
        ]
    }
    score, volume = option_flow_score(options, contracts, config)
    assert score == Decimal("1.0000")
    assert volume == Decimal("1000")


def test_delayed_trade_uses_ten_fifteen_spot_and_marks():
    config = delayed_settings()
    spot = Decimal("100.20")
    underlying = [bar(10, 15, str(spot))]
    contracts = signal_contracts(DAY, flow_center(spot), config)
    options = {
        contracts["C_+0"]: [bar(9, 59, "1", close="1.1", volume="1200")]
    }
    _, _, short_symbol, long_symbol = spread_contracts(DAY, spot, "bullish", config)
    options[short_symbol] = options.get(short_symbol, []) + [
        bar(10, 15, "0.70"),
        bar(15, 0, "0.20"),
    ]
    options[long_symbol] = [bar(10, 15, "0.20"), bar(15, 0, "0.10")]
    result = simulate_option_flow(DAY.isoformat(), underlying, options, config)
    assert result.entered is True
    assert result.entry_credit == Decimal("0.46")
    assert result.pnl == Decimal("31.90")
