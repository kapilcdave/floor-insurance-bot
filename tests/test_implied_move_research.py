from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from floor_insurance.credit_structure import occ_put_for
from floor_insurance.directional import PriceBar
from floor_insurance.implied_move_research import (
    ImpliedMoveSettings,
    atm_straddle_symbols,
    implied_move_at,
    implied_move_metrics,
    occ_call_for,
    required_spread_symbols,
    settings_grid,
    simulate_implied_move_spread,
    spread_strikes,
    viable,
)

ET = ZoneInfo("America/New_York")
DAY = date(2026, 8, 18)


def bars(value: str = "100.80") -> list[PriceBar]:
    opened = datetime(2026, 8, 18, 10, 0, tzinfo=ET)
    return [
        PriceBar(opened + timedelta(minutes=minute), *(Decimal(value),) * 4)
        for minute in range(301)
    ]


def option_bars(values: dict[int, str]) -> list[PriceBar]:
    opened = datetime(2026, 8, 18, 10, 0, tzinfo=ET)
    return [
        PriceBar(opened + timedelta(minutes=minute), *(Decimal(value),) * 4)
        for minute, value in sorted(values.items())
    ]


def config(**changes) -> ImpliedMoveSettings:
    values = {
        "move_multiple": Decimal("1"),
        "take_profit_fraction": Decimal("0.5"),
        "slippage_per_leg": Decimal("0.02"),
    }
    values.update(changes)
    return ImpliedMoveSettings(**values)


def options(
    short: dict[int, str],
    long: dict[int, str],
) -> dict[str, list[PriceBar]]:
    call, put = atm_straddle_symbols(DAY, Decimal("100.80"))
    return {
        call: option_bars({0: "2.10"}),
        put: option_bars({0: "1.90"}),
        occ_put_for("SPY", DAY, Decimal("96")): option_bars(short),
        occ_put_for("SPY", DAY, Decimal("95")): option_bars(long),
    }


def test_grid_is_six_predeclared_variants():
    grid = settings_grid()
    assert len(grid) == 6
    assert len({item.label for item in grid}) == 6
    assert "move1_tp0.5_stop2x" in {item.label for item in grid}
    assert "move1.25_hold_stop2x" in {item.label for item in grid}


def test_straddle_and_spread_symbols_are_derived_without_future_data():
    call, put = atm_straddle_symbols(DAY, Decimal("100.80"))
    assert call == "SPY260818C00100000"
    assert put == "SPY260818P00100000"
    marks = {call: option_bars({0: "2.10"}), put: option_bars({0: "1.90"})}
    assert implied_move_at(marks, call, put, config().entry_time) == Decimal("4.00")
    assert spread_strikes(
        Decimal("100.80"), Decimal("4"), Decimal("1"), Decimal("1")
    ) == (Decimal("96"), Decimal("95"))
    assert required_spread_symbols(
        DAY, Decimal("100.80"), Decimal("4"), settings_grid()
    ) == [
        "SPY260818P00094000",
        "SPY260818P00095000",
        "SPY260818P00096000",
    ]


def test_missing_exact_straddle_mark_skips_instead_of_looking_ahead():
    call = occ_call_for("SPY", DAY, Decimal("100"))
    put = occ_put_for("SPY", DAY, Decimal("100"))
    marks = {
        call: option_bars({1: "2.10"}),
        put: option_bars({1: "1.90"}),
    }
    result = simulate_implied_move_spread(DAY.isoformat(), bars(), marks, config())
    assert result.entered is False
    assert result.reason == "ATM straddle entry marks missing"


def test_entry_exit_and_costs_charge_every_leg():
    result = simulate_implied_move_spread(
        DAY.isoformat(),
        bars(),
        options({0: "0.50", 10: "0.20"}, {0: "0.20", 10: "0.05"}),
        config(take_profit_fraction=None),
    )
    assert result.entered is True
    assert result.credit == Decimal("0.26")
    assert result.exit_debit == Decimal("0.19")
    assert result.pnl == Decimal("6.90")


def test_spread_stop_precedes_profit_target():
    result = simulate_implied_move_spread(
        DAY.isoformat(),
        bars(),
        options({0: "0.50", 10: "0.70"}, {0: "0.20", 10: "0.05"}),
        config(),
    )
    assert result.reason == "spread_stop"
    assert result.exit_debit == Decimal("0.69")
    assert result.pnl == Decimal("-43.10")


def test_viability_requires_cost_stress_and_real_samples():
    winners = [
        simulate_implied_move_spread(
            DAY.isoformat(),
            bars(),
            options({0: "0.50", 10: "0.20"}, {0: "0.20", 10: "0.05"}),
            config(take_profit_fraction=None),
        )
        for _ in range(130)
    ]
    metrics = implied_move_metrics(winners)
    report = {
        "train": {**metrics, "trades": 100, "profit_factor": "2"},
        "validation": {**metrics, "trades": 30, "profit_factor": "2"},
        "train_stress": metrics,
        "validation_stress": metrics,
    }
    assert viable(report) is True
    assert viable({**report, "validation": {**metrics, "trades": 29}}) is False
    assert viable(
        {**report, "validation_stress": {**metrics, "average_pnl": "-0.01"}}
    ) is False
