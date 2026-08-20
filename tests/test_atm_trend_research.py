from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from floor_insurance.atm_trend_research import (
    AtmTrendSettings,
    atm_metrics,
    atm_strikes,
    required_symbols,
    settings_grid,
    simulate_atm_trend,
    viable,
)
from floor_insurance.credit_structure import occ_put_for
from floor_insurance.directional import PriceBar
from floor_insurance.trend import TrendMode

ET = ZoneInfo("America/New_York")
DAY = date(2026, 8, 18)


def bars(value: str = "100.80") -> list[PriceBar]:
    opened = datetime(2026, 8, 18, 9, 45, tzinfo=ET)
    return [
        PriceBar(opened + timedelta(minutes=minute), *(Decimal(value),) * 4)
        for minute in range(316)
    ]


def option_bars(values: dict[int, str]) -> list[PriceBar]:
    opened = datetime(2026, 8, 18, 9, 45, tzinfo=ET)
    return [
        PriceBar(opened + timedelta(minutes=minute), *(Decimal(value),) * 4)
        for minute, value in sorted(values.items())
    ]


def options(short: dict[int, str], long: dict[int, str]):
    return {
        occ_put_for("SPY", DAY, Decimal("100")): option_bars(short),
        occ_put_for("SPY", DAY, Decimal("99")): option_bars(long),
    }


def prior_above() -> list[Decimal]:
    return [Decimal("100")] * 20 + [Decimal("110")]


def config(**changes) -> AtmTrendSettings:
    values = {
        "trend_mode": TrendMode.ABOVE,
        "stop_debit_multiple": Decimal("2"),
        "take_profit_fraction": Decimal("0.5"),
        "slippage_per_side": Decimal("0"),
        "fees_per_spread": Decimal("0"),
    }
    values.update(changes)
    return AtmTrendSettings(**values)


def test_grid_is_declared_before_results_and_contains_twelve_variants():
    grid = settings_grid()
    assert len(grid) == 12
    assert len({settings.label for settings in grid}) == 12
    assert "above_stop2x_tp0.5" in {settings.label for settings in grid}
    assert "crossover_hold_no_tp" in {settings.label for settings in grid}


def test_atm_strikes_and_symbols_use_the_put_at_or_below_spot():
    assert atm_strikes(Decimal("100.80"), Decimal("1")) == (
        Decimal("100"),
        Decimal("99"),
    )
    assert required_symbols(DAY, Decimal("100.80"), config()) == [
        "SPY260818P00100000",
        "SPY260818P00099000",
    ]
    assert required_symbols(
        DAY, Decimal("100.80"), config(symbol="QQQ")
    ) == [
        "QQQ260818P00100000",
        "QQQ260818P00099000",
    ]


def test_signal_uses_only_supplied_prior_closes():
    result = simulate_atm_trend(
        DAY.isoformat(),
        [Decimal("100")] * 20,
        bars(),
        options({0: "0.60"}, {0: "0.10"}),
        config(),
    )
    assert result.eligible is False
    assert result.entered is False


def test_take_profit_and_spread_stop_use_modeled_spread_debit():
    take_profit = simulate_atm_trend(
        DAY.isoformat(),
        prior_above(),
        bars(),
        options({0: "0.60", 10: "0.25"}, {0: "0.10", 10: "0.05"}),
        config(),
    )
    assert take_profit.reason == "take_profit"
    assert take_profit.credit == Decimal("0.50")
    assert take_profit.pnl == Decimal("30.00")

    stopped = simulate_atm_trend(
        DAY.isoformat(),
        prior_above(),
        bars(),
        options({0: "0.50", 10: "0.90"}, {0: "0.10", 10: "0.10"}),
        config(stop_debit_multiple=Decimal("1.5"), take_profit_fraction=None),
    )
    assert stopped.reason == "spread_stop"
    assert stopped.exit_debit == Decimal("0.80")
    assert stopped.pnl == Decimal("-40.00")


def test_metrics_and_viability_require_both_splits_and_real_sample():
    winners = [
        simulate_atm_trend(
            DAY.isoformat(),
            prior_above(),
            bars(),
            options({0: "0.60", 10: "0.25"}, {0: "0.10", 10: "0.05"}),
            config(),
        )
        for _ in range(130)
    ]
    report = atm_metrics(winners)
    assert report["trades"] == 130
    assert report["average_pnl"] == "30.00"

    candidate = {
        "train": {**report, "trades": 100},
        "validation": {**report, "trades": 30},
    }
    assert viable(candidate) is True
    assert viable({**candidate, "validation": {**report, "trades": 29}}) is False
    assert (
        viable(
            {
                **candidate,
                "validation": {**report, "trades": 30, "average_pnl": "-0.01"},
            }
        )
        is False
    )
