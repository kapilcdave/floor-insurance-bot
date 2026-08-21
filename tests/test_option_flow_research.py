from datetime import date, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from floor_insurance.directional import PriceBar
from floor_insurance.option_flow_research import (
    OptionFlowResult,
    OptionFlowSettings,
    flow_center,
    option_flow_metrics,
    option_flow_score,
    required_symbols,
    signal_contracts,
    simulate_option_flow,
    spread_contracts,
    viable,
)

ET = ZoneInfo("America/New_York")
DAY = date(2026, 8, 18)


def bar(
    hour: int,
    minute: int,
    open_value: str,
    *,
    close: str | None = None,
    volume: str = "0",
) -> PriceBar:
    opened = Decimal(open_value)
    closed = Decimal(close or open_value)
    return PriceBar(
        datetime(2026, 8, 18, hour, minute, tzinfo=ET),
        opened,
        max(opened, closed),
        min(opened, closed),
        closed,
        Decimal(volume),
    )


def settings(**changes) -> OptionFlowSettings:
    return OptionFlowSettings(**changes)


def test_centers_signal_universe_and_spread_direction():
    config = settings()
    assert flow_center(Decimal("100.49")) == Decimal("100")
    assert flow_center(Decimal("100.50")) == Decimal("101")
    contracts = signal_contracts(DAY, Decimal("101"), config)
    assert len(contracts) == 6
    assert contracts["C_-1"] == "SPY260818C00100000"
    assert contracts["P_+1"] == "SPY260818P00102000"
    assert spread_contracts(DAY, Decimal("100.20"), "bullish", config)[:2] == (
        Decimal("100"),
        Decimal("99"),
    )
    assert spread_contracts(DAY, Decimal("100.20"), "bearish", config)[:2] == (
        Decimal("101"),
        Decimal("102"),
    )
    assert len(required_symbols(DAY, Decimal("100.20"), config)) == 7


def test_flow_score_signs_calls_and_puts_oppositely_and_counts_flat_volume():
    config = settings()
    contracts = signal_contracts(DAY, Decimal("100"), config)
    bars = {
        contracts["C_+0"]: [bar(9, 40, "1", close="1.1", volume="600")],
        contracts["P_+0"]: [bar(9, 41, "1", close="0.9", volume="200")],
        contracts["C_+1"]: [bar(9, 42, "1", volume="200")],
    }
    score, volume = option_flow_score(bars, contracts, config)
    assert volume == Decimal("1000")
    assert score == Decimal("0.8000")


def bullish_marks(config: OptionFlowSettings) -> tuple[list[PriceBar], dict[str, list[PriceBar]]]:
    underlying = [bar(10, 0, "100.20")]
    contracts = signal_contracts(DAY, flow_center(Decimal("100.20")), config)
    options = {
        contracts["C_+0"]: [bar(9, 45, "1", close="1.1", volume="1200")]
    }
    _, _, short_symbol, long_symbol = spread_contracts(
        DAY, Decimal("100.20"), "bullish", config
    )
    options[short_symbol] = options.get(short_symbol, []) + [
        bar(10, 0, "0.70"),
        bar(15, 0, "0.20"),
    ]
    options[long_symbol] = [bar(10, 0, "0.20"), bar(15, 0, "0.10")]
    return underlying, options


def test_simulation_books_two_leg_costs_and_hard_close():
    config = settings()
    underlying, options = bullish_marks(config)
    result = simulate_option_flow(DAY.isoformat(), underlying, options, config)
    assert result.entered is True
    assert result.direction == "bullish"
    assert result.entry_credit == Decimal("0.46")
    assert result.maximum_risk == Decimal("54.10")
    assert result.exit_debit == Decimal("0.14")
    assert result.pnl == Decimal("31.90")


def test_signal_and_missing_exit_fail_conservatively():
    config = settings()
    underlying, options = bullish_marks(config)
    low_score = simulate_option_flow(
        DAY.isoformat(),
        underlying,
        options,
        settings(flow_threshold=Decimal("1.01")),
    )
    assert low_score.entered is False
    assert "threshold" in low_score.reason

    for values in options.values():
        values[:] = [bar for bar in values if bar.timestamp.time() != time(15, 0)]
    missing = simulate_option_flow(DAY.isoformat(), underlying, options, config)
    assert missing.entered is True
    assert missing.reason == "hard_close_missing_mark"
    assert missing.pnl == Decimal("-54.10")


def test_metrics_and_viability_require_both_directions():
    values = [
        OptionFlowResult("2026-01-01", True, "hard_close", "bullish", pnl=Decimal("20")),
        OptionFlowResult("2026-01-02", True, "hard_close", "bearish", pnl=Decimal("-5")),
    ] * 75
    metrics = option_flow_metrics(values)
    assert metrics["trades"] == 150
    assert metrics["profit_factor"] == "4.0000"
    report = {
        "train": {**metrics, "trades": 100, "max_drawdown": "-25"},
        "validation": {**metrics, "trades": 30, "max_drawdown": "-25"},
        "train_stress": metrics,
        "validation_stress": metrics,
    }
    assert viable(report) is True
    assert viable(
        {**report, "validation": {**report["validation"], "bearish_trades": 0}}
    ) is False
