from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from floor_insurance.directional import PriceBar
from floor_insurance.intraday_surface_research import (
    intraday_metrics,
    required_intraday_symbols,
    settings_for_entry,
    simulate_intraday_surface,
)
from floor_insurance.surface_butterfly_research import (
    SurfaceSettings,
    butterfly_contracts,
)

ET = ZoneInfo("America/New_York")
DAY = datetime(2026, 5, 1).date()


def bar(hour: int, price: str) -> PriceBar:
    value = Decimal(price)
    return PriceBar(
        datetime(2026, 5, 1, hour, 0, tzinfo=ET),
        value,
        value,
        value,
        value,
        Decimal("100"),
    )


def option_bars() -> dict[str, list[PriceBar]]:
    settings = SurfaceSettings()
    calls = butterfly_contracts(DAY, Decimal("100"), "C", settings)
    puts = butterfly_contracts(DAY, Decimal("100"), "P", settings)
    return {
        calls[0]: [bar(10, "1.50"), bar(11, "1.50"), bar(12, "0.70")],
        calls[1]: [bar(10, "1.00"), bar(11, "1.00"), bar(12, "0.40")],
        calls[2]: [bar(10, "0.50"), bar(11, "0.50"), bar(12, "0.20")],
        puts[0]: [bar(10, "0.20"), bar(11, "0.20")],
        puts[1]: [bar(10, "0.30"), bar(11, "0.30")],
        puts[2]: [bar(10, "0.40"), bar(11, "0.52")],
    }


def test_entry_settings_hold_exactly_one_hour():
    settings = settings_for_entry(SurfaceSettings(), datetime.strptime("14:00", "%H:%M").time())
    assert settings.entry_time.hour == 14
    assert settings.exit_time.hour == 15


def test_intraday_strategy_skips_ten_and_takes_first_qualifying_eleven_scan():
    result = simulate_intraday_surface(
        DAY.isoformat(),
        [bar(10, "100"), bar(11, "100")],
        option_bars(),
        SurfaceSettings(),
    )

    assert result.entry_time == "11:00"
    assert result.result.entered
    assert result.result.pnl == Decimal("5.80")


def test_intraday_required_symbols_union_each_available_scan():
    symbols = required_intraday_symbols(
        DAY,
        [bar(10, "100"), bar(11, "105")],
        SurfaceSettings(),
    )

    assert len(symbols) == 28


def test_intraday_metrics_report_selected_entry_hours():
    result = simulate_intraday_surface(
        DAY.isoformat(),
        [bar(10, "100"), bar(11, "100")],
        option_bars(),
        SurfaceSettings(),
    )
    metrics = intraday_metrics([result])

    assert metrics["trades"] == 1
    assert metrics["entries_by_time"]["11:00"] == 1
    assert metrics["entries_by_time"]["10:00"] == 0
