from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from floor_insurance.directional import PriceBar
from floor_insurance.surface_butterfly_research import (
    SurfaceResult,
    SurfaceSettings,
    butterfly_contracts,
    required_symbols,
    select_surface_candidate,
    simulate_surface_butterfly,
    surface_metrics,
    viable,
)

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


def candidate_options(settings: SurfaceSettings) -> dict[str, list[PriceBar]]:
    day = datetime(2026, 5, 1).date()
    calls = butterfly_contracts(day, Decimal("100"), "C", settings)
    puts = butterfly_contracts(day, Decimal("100"), "P", settings)
    return {
        calls[0]: [bar(11, 0, "1.50"), bar(12, 0, "0.70")],
        calls[1]: [bar(11, 0, "1.00"), bar(12, 0, "0.40")],
        calls[2]: [bar(11, 0, "0.50"), bar(12, 0, "0.20")],
        puts[0]: [bar(11, 0, "0.20")],
        puts[1]: [bar(11, 0, "0.30")],
        puts[2]: [bar(11, 0, "0.52")],
    }


def test_required_surface_has_nine_strikes_for_calls_and_puts():
    symbols = required_symbols(datetime(2026, 5, 1).date(), Decimal("100"), SurfaceSettings())
    assert len(symbols) == 18
    assert sum("C" in symbol[9:10] for symbol in symbols) == 9
    assert sum("P" in symbol[9:10] for symbol in symbols) == 9


def test_selects_cheaper_call_butterfly_with_large_parity_gap():
    settings = SurfaceSettings()
    candidate = select_surface_candidate(
        datetime(2026, 5, 1).date(),
        Decimal("100"),
        candidate_options(settings),
        settings,
    )
    assert candidate is not None
    assert candidate.kind == "C"
    assert candidate.center == Decimal("100")
    assert candidate.parity_gap == Decimal("0.12")
    assert candidate.raw_entry_debit == Decimal("0.00")
    assert candidate.entry_debit == Decimal("0.02")


def test_surface_trade_marks_one_hour_convergence_after_costs():
    settings = SurfaceSettings()
    result = simulate_surface_butterfly(
        "2026-05-01",
        [bar(11, 0, "100")],
        candidate_options(settings),
        settings,
    )
    assert result.entered
    assert result.reason == "timed_exit"
    assert result.exit_credit == Decimal("0.08")
    assert result.pnl == Decimal("5.80")


def test_rejects_when_call_put_gap_is_too_small():
    settings = SurfaceSettings()
    options = candidate_options(settings)
    put_symbols = butterfly_contracts(datetime(2026, 5, 1).date(), Decimal("100"), "P", settings)
    options[put_symbols[2]] = [bar(11, 0, "0.40")]
    result = simulate_surface_butterfly("2026-05-01", [bar(11, 0, "100")], options, settings)
    assert not result.entered


def test_metrics_and_viability_require_both_option_types():
    calls = [
        SurfaceResult(str(index), True, "timed_exit", "C", pnl=Decimal("10"))
        for index in range(100)
    ]
    puts = [
        SurfaceResult(f"p{index}", True, "timed_exit", "P", pnl=Decimal("-1"))
        for index in range(10)
    ]
    training = calls + puts
    validation = training[:30]
    report = {
        "train": surface_metrics(training),
        "validation": surface_metrics(validation),
        "train_stress": surface_metrics(training),
        "validation_stress": surface_metrics(validation),
    }
    assert report["train"]["calls"] == 100
    assert report["train"]["puts"] == 10
    assert not viable(report)
