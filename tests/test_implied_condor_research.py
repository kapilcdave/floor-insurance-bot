from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from floor_insurance.directional import PriceBar
from floor_insurance.implied_condor_research import (
    CondorResult,
    CondorSettings,
    condor_metrics,
    condor_strikes,
    condor_symbols,
    simulate_condor,
    viable,
)
from floor_insurance.implied_move_research import atm_straddle_symbols

ET = ZoneInfo("America/New_York")


def bar(hour: int, minute: int, price: str) -> PriceBar:
    value = Decimal(price)
    return PriceBar(
        datetime(2026, 5, 1, hour, minute, tzinfo=ET),
        value,
        value,
        value,
        value,
        Decimal("100"),
    )


def option_bars(settings: CondorSettings) -> dict[str, list[PriceBar]]:
    day = datetime(2026, 5, 1).date()
    atm_call, atm_put = atm_straddle_symbols(day, Decimal("100"))
    symbols = condor_symbols(day, Decimal("100"), Decimal("1.50"), settings)
    return {
        atm_call: [bar(11, 0, "0.75")],
        atm_put: [bar(11, 0, "0.75")],
        symbols["long_put"]: [bar(11, 0, "0.05"), bar(15, 0, "0.01")],
        symbols["short_put"]: [bar(11, 0, "0.25"), bar(15, 0, "0.05")],
        symbols["short_call"]: [bar(11, 0, "0.25"), bar(15, 0, "0.05")],
        symbols["long_call"]: [bar(11, 0, "0.05"), bar(15, 0, "0.01")],
    }


def test_condor_strikes_use_implied_move_and_outward_wings():
    assert condor_strikes(Decimal("100"), Decimal("1.50"), CondorSettings()) == (
        Decimal("97"),
        Decimal("98"),
        Decimal("102"),
        Decimal("103"),
    )


def test_profitable_condor_charges_entry_exit_friction_and_fees():
    settings = CondorSettings()
    result = simulate_condor(
        "2026-05-01",
        [bar(11, 0, "100")],
        option_bars(settings),
        Decimal("1.00"),
        settings,
    )

    assert result.entered
    assert result.reason == "hard_close"
    assert result.raw_entry_credit == Decimal("0.40")
    assert result.entry_credit == Decimal("0.36")
    assert result.exit_debit == Decimal("0.12")
    assert result.maximum_risk == Decimal("64.20")
    assert result.pnl == Decimal("23.80")


def test_richness_gate_rejects_before_condor_legs_are_needed():
    settings = CondorSettings()
    day = datetime(2026, 5, 1).date()
    call, put = atm_straddle_symbols(day, Decimal("100"))
    result = simulate_condor(
        "2026-05-01",
        [bar(11, 0, "100")],
        {call: [bar(11, 0, "0.50")], put: [bar(11, 0, "0.50")]},
        Decimal("1.00"),
        settings,
    )

    assert not result.entered
    assert result.reason == "implied move is below richness gate"


def test_missing_close_mark_is_charged_at_full_width():
    settings = CondorSettings()
    options = option_bars(settings)
    for symbol, values in list(options.items()):
        options[symbol] = [value for value in values if value.timestamp.time().hour == 11]
    result = simulate_condor(
        "2026-05-01",
        [bar(11, 0, "100")],
        options,
        Decimal("1.00"),
        settings,
    )

    assert result.entered
    assert result.reason == "hard_close_missing_mark"
    assert result.exit_debit == Decimal("1")
    assert result.pnl == Decimal("-64.20")


def test_metrics_and_promotion_rule_require_both_splits_and_stress():
    wins = [CondorResult(str(index), True, "hard_close", pnl=Decimal("10")) for index in range(100)]
    validation = wins[:30]
    report = {
        "train": condor_metrics(wins),
        "validation": condor_metrics(validation),
        "train_stress": condor_metrics(wins),
        "validation_stress": condor_metrics(validation),
    }
    assert report["train"]["trades"] == 100
    assert not viable(report)  # Profit factor is undefined without a losing trade.

    mixed_train = wins + [CondorResult("loss", True, "hard_close", pnl=Decimal("-1"))]
    mixed_validation = validation + [CondorResult("loss", True, "hard_close", pnl=Decimal("-1"))]
    report = {
        "train": condor_metrics(mixed_train),
        "validation": condor_metrics(mixed_validation),
        "train_stress": condor_metrics(mixed_train),
        "validation_stress": condor_metrics(mixed_validation),
    }
    assert viable(report)
