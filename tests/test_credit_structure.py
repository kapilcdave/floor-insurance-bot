from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from floor_insurance.credit_experiments import (
    required_symbols,
    structure_grid,
    viable,
)
from floor_insurance.credit_structure import (
    CreditSettings,
    break_even_win_rate,
    credit_metrics,
    max_loss_per_contract,
    minimum_equity,
    occ_put,
    simulate_credit_spread,
    spread_strikes,
)
from floor_insurance.directional import PriceBar

ET = ZoneInfo("America/New_York")
DAY = date(2026, 8, 18)


def settings(**overrides) -> CreditSettings:
    base = {
        "buffer_dollars": Decimal("5"),
        "width": Decimal("1"),
        "stop_buffer": Decimal("1"),
        "take_profit_fraction": Decimal("0.5"),
        "min_credit_fraction": Decimal("0.05"),
    }
    base.update(overrides)
    return CreditSettings(**base)


def spy_session(*, low_at: dict[int, str] | None = None) -> list[PriceBar]:
    """A flat SPY session at 100, optionally dipping at a given minute offset."""

    opened = datetime(2026, 8, 18, 9, 45, tzinfo=ET)
    bars = []
    for minute in range(0, 316):
        low = Decimal((low_at or {}).get(minute, "100"))
        bars.append(
            PriceBar(
                opened + timedelta(minutes=minute),
                Decimal("100"),
                Decimal("100"),
                low,
                Decimal("100"),
            )
        )
    return bars


def option_series(values: dict[int, str]) -> list[PriceBar]:
    opened = datetime(2026, 8, 18, 9, 45, tzinfo=ET)
    return [
        PriceBar(
            opened + timedelta(minutes=minute),
            Decimal(value),
            Decimal(value),
            Decimal(value),
            Decimal(value),
        )
        for minute, value in sorted(values.items())
    ]


def legs(short: dict[int, str], long: dict[int, str], config: CreditSettings):
    short_strike, long_strike = spread_strikes(Decimal("100"), config)
    return {
        occ_put(DAY, short_strike): option_series(short),
        occ_put(DAY, long_strike): option_series(long),
    }


def test_max_loss_and_minimum_equity_expose_the_account_floor():
    assert max_loss_per_contract(Decimal("1"), Decimal("0.05")) == Decimal("95.00")
    assert minimum_equity(Decimal("1"), Decimal("0.05"), Decimal("0.01")) == Decimal(
        "9500.00"
    )
    # A wider spread needs a larger account, not a smaller one.
    assert minimum_equity(Decimal("3"), Decimal("0.15"), Decimal("0.01")) == Decimal(
        "28500.00"
    )
    with pytest.raises(ValueError):
        minimum_equity(Decimal("1"), Decimal("0.05"), Decimal("0"))


def test_break_even_win_rate_penalises_a_partial_profit_target():
    full = break_even_win_rate(Decimal("1"), Decimal("0.10"), None)
    half = break_even_win_rate(Decimal("1"), Decimal("0.10"), Decimal("0.5"))
    assert full == Decimal("0.9000")
    assert half == Decimal("0.9474")
    assert break_even_win_rate(Decimal("1"), Decimal("0"), None) is None
    assert break_even_win_rate(Decimal("1"), Decimal("1"), None) is None


def test_strikes_sit_a_whole_buffer_below_spot():
    assert spread_strikes(Decimal("100.90"), settings()) == (
        Decimal("95"),
        Decimal("94"),
    )
    assert spread_strikes(
        Decimal("100.90"), settings(width=Decimal("3"), buffer_dollars=Decimal("10"))
    ) == (Decimal("90"), Decimal("87"))


def test_occ_put_encodes_thousandths():
    assert occ_put(DAY, Decimal("95")) == "SPY260818P00095000"


def test_entry_is_skipped_when_the_credit_is_below_the_floor():
    config = settings()
    result = simulate_credit_spread(
        "2026-08-18",
        spy_session(),
        legs({0: "0.20"}, {0: "0.18"}, config),
        config,
    )
    assert result.entered is False
    assert result.reason == "credit 0.02 is below 0.05"


def test_entry_is_skipped_when_a_leg_did_not_trade():
    config = settings()
    short_strike, _ = spread_strikes(Decimal("100"), config)
    result = simulate_credit_spread(
        "2026-08-18",
        spy_session(),
        {occ_put(DAY, short_strike): option_series({0: "0.30"})},
        config,
    )
    assert result.entered is False
    assert "did not trade" in result.reason


def test_take_profit_captures_half_the_credit():
    config = settings()
    result = simulate_credit_spread(
        "2026-08-18",
        spy_session(),
        legs({0: "0.30", 10: "0.06"}, {0: "0.20", 10: "0.02"}, config),
        config,
    )
    assert result.entered is True
    assert result.credit == Decimal("0.10")
    assert result.reason == "take_profit"
    assert result.exit_debit == Decimal("0.04")
    assert result.pnl_per_contract == Decimal("6.00")


def test_the_stop_fires_on_an_intrabar_low_not_the_close():
    config = settings()
    # Short strike 95, stop buffer 1, so any low at or below 96 must trigger.
    result = simulate_credit_spread(
        "2026-08-18",
        spy_session(low_at={5: "96"}),
        legs({0: "0.30", 5: "0.90"}, {0: "0.20", 5: "0.40"}, config),
        config,
    )
    assert result.reason == "emergency_stop"
    assert result.exit_debit == Decimal("0.50")
    assert result.pnl_per_contract == Decimal("-40.00")


def test_holding_without_a_target_settles_at_the_hard_close():
    config = settings(take_profit_fraction=None)
    result = simulate_credit_spread(
        "2026-08-18",
        spy_session(),
        legs({0: "0.30", 315: "0.01"}, {0: "0.20", 315: "0.00"}, config),
        config,
    )
    assert result.reason == "hard_close"
    assert result.exit_debit == Decimal("0.01")
    assert result.pnl_per_contract == Decimal("9.00")


def test_costs_are_charged_on_both_sides():
    config = settings(
        slippage_per_side=Decimal("0.02"), fees_per_spread=Decimal("0.10")
    )
    result = simulate_credit_spread(
        "2026-08-18",
        spy_session(),
        legs({0: "0.30", 315: "0.01"}, {0: "0.20", 315: "0.00"}, config),
        config,
    )
    # Credit is reduced by slippage and the exit debit is increased by it.
    assert result.credit == Decimal("0.08")
    assert result.exit_debit == Decimal("0.03")
    assert result.pnl_per_contract == Decimal("4.90")


def test_metrics_report_the_break_even_implied_by_observed_losses():
    config = settings()
    results = [
        simulate_credit_spread(
            "2026-08-18",
            spy_session(),
            legs({0: "0.30", 10: "0.06"}, {0: "0.20", 10: "0.02"}, config),
            config,
        )
        for _ in range(3)
    ] + [
        simulate_credit_spread(
            "2026-08-18",
            spy_session(low_at={5: "96"}),
            legs({0: "0.30", 5: "0.90"}, {0: "0.20", 5: "0.40"}, config),
            config,
        )
    ]
    report = credit_metrics(results, config)
    assert report["trades"] == 4
    assert report["wins"] == 3
    assert report["losses"] == 1
    assert report["realised_win_rate"] == "0.7500"
    assert report["average_win"] == "6.00"
    assert report["average_loss"] == "40.00"
    # 40 / (40 + 6): a loss costs almost seven wins, so 87% would be required.
    assert report["observed_break_even_win_rate"] == "0.8696"
    assert report["win_rate_margin"] == "-0.1196"
    assert report["total_pnl_per_contract"] == "-22.00"
    assert report["minimum_equity_at_one_percent"] == "9000.00"


def test_grid_is_fixed_and_covers_every_declared_combination():
    grid = structure_grid()
    assert len(grid) == 36
    assert len({item.label for item in grid}) == 36
    assert "buf15_w1_stop3_tp0.5" in {item.label for item in grid}
    assert "buf5_w5_stop1_hold" in {item.label for item in grid}


def test_required_symbols_cover_every_leg_the_grid_can_ask_for():
    symbols = set(required_symbols(Decimal("100.90"), DAY))
    for config in structure_grid():
        short_strike, long_strike = spread_strikes(Decimal("100.90"), config)
        assert occ_put(DAY, short_strike) in symbols
        assert occ_put(DAY, long_strike) in symbols


def test_viability_needs_positive_expectancy_in_both_splits_and_a_real_sample():
    good = {
        "train": {"average_pnl_per_contract": "3.00", "trades": 150},
        "validation": {"average_pnl_per_contract": "1.00", "trades": 40},
    }
    assert viable(good) is True
    assert viable({**good, "train": {**good["train"], "trades": 99}}) is False
    assert (
        viable({**good, "validation": {"average_pnl_per_contract": "-0.01", "trades": 40}})
        is False
    )
    assert (
        viable({**good, "train": {"average_pnl_per_contract": None, "trades": 150}})
        is False
    )
