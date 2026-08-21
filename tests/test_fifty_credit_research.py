from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from floor_insurance.credit_structure import occ_put_for
from floor_insurance.directional import PriceBar
from floor_insurance.fifty_credit_research import (
    FiftyCreditResult,
    FiftyCreditSettings,
    candidate_strikes,
    fifty_credit_metrics,
    moving_block_bootstrap,
    required_symbols,
    simulate_fifty_credit,
    viable,
)

ET = ZoneInfo("America/New_York")
DAY = date(2026, 8, 18)


def bar(
    minute: int,
    open_value: str,
    *,
    high: str | None = None,
    low: str | None = None,
    close: str | None = None,
) -> PriceBar:
    timestamp = datetime(2026, 8, 18, 10, 0, tzinfo=ET) + timedelta(minutes=minute)
    value = Decimal(open_value)
    return PriceBar(
        timestamp,
        value,
        Decimal(high or open_value),
        Decimal(low or open_value),
        Decimal(close or open_value),
    )


def underlying(spot: str = "100.80") -> list[PriceBar]:
    return [bar(0, spot)]


def options(
    short_strike: str,
    short: list[PriceBar],
    long: list[PriceBar],
) -> dict[str, list[PriceBar]]:
    short_value = Decimal(short_strike)
    return {
        occ_put_for("SPY", DAY, short_value): short,
        occ_put_for("SPY", DAY, short_value - Decimal("1")): long,
    }


def config(**changes) -> FiftyCreditSettings:
    values = {}
    values.update(changes)
    return FiftyCreditSettings(**values)


def test_candidates_are_farthest_first_and_symbols_cover_shared_legs():
    settings = config()
    assert candidate_strikes(Decimal("100.80"), settings)[0] == (
        Decimal("90"),
        Decimal("89"),
    )
    assert candidate_strikes(Decimal("100.80"), settings)[-1] == (
        Decimal("100"),
        Decimal("99"),
    )
    symbols = required_symbols(DAY, Decimal("100.80"), settings)
    assert len(symbols) == 12
    assert symbols[0] == "SPY260818P00089000"
    assert symbols[-1] == "SPY260818P00100000"


def test_entry_requires_raw_credit_plus_adverse_fill_but_books_fixed_limit():
    result = simulate_fifty_credit(
        DAY.isoformat(),
        underlying(),
        options(
            "98",
            [bar(0, "0.80"), bar(300, "0.01")],
            [bar(0, "0.20"), bar(300, "0.00")],
        ),
        config(),
    )
    assert result.entered is True
    assert result.short_strike == Decimal("98")
    assert result.raw_entry_credit == Decimal("0.60")
    assert result.entry_credit == Decimal("0.50")
    assert result.reason == "hard_close"
    assert result.exit_debit == Decimal("0.05")
    assert result.pnl == Decimal("44.90")

    skipped = simulate_fifty_credit(
        DAY.isoformat(),
        underlying(),
        options("98", [bar(0, "0.73")], [bar(0, "0.20")]),
        config(),
    )
    assert skipped.entered is False
    assert "raw credit threshold" in skipped.reason


def test_farthest_qualifying_spread_is_selected():
    marks = {}
    marks.update(options("96", [bar(0, "0.70")], [bar(0, "0.20")]))
    marks.update(
        options(
            "97",
            [bar(0, "0.90"), bar(300, "0.01")],
            [bar(0, "0.30"), bar(300, "0.00")],
        )
    )
    result = simulate_fifty_credit(DAY.isoformat(), underlying(), marks, config())
    assert result.short_strike == Decimal("97")


def test_stop_uses_adverse_intraminute_legs_and_can_pay_full_width():
    result = simulate_fifty_credit(
        DAY.isoformat(),
        underlying(),
        options(
            "98",
            [bar(0, "0.80"), bar(10, "0.70", high="1.10", close="0.70")],
            [bar(0, "0.20"), bar(10, "0.30", low="0.10", close="0.30")],
        ),
        config(),
    )
    assert result.reason == "spread_stop"
    assert result.exit_debit == Decimal("1.00")
    assert result.pnl == Decimal("-50.10")


def test_stress_stop_waits_one_exact_synchronized_minute():
    settings = config(adverse_fill_per_leg=Decimal("0.03"), stop_delay_bars=1)
    result = simulate_fifty_credit(
        DAY.isoformat(),
        underlying(),
        options(
            "98",
            [bar(0, "0.90"), bar(10, "0.80"), bar(11, "0.90")],
            [bar(0, "0.20"), bar(10, "0.10"), bar(11, "0.20")],
        ),
        settings,
    )
    assert result.reason == "spread_stop"
    assert result.exit_debit == Decimal("0.76")
    assert result.pnl == Decimal("-26.10")


def test_missing_hard_close_data_is_charged_as_full_loss():
    result = simulate_fifty_credit(
        DAY.isoformat(),
        underlying(),
        options("98", [bar(0, "0.80")], [bar(0, "0.20")]),
        config(),
    )
    assert result.reason == "hard_close_missing_mark"
    assert result.pnl == Decimal("-50.10")


def test_metrics_viability_and_bootstrap_are_deterministic():
    wins = [
        FiftyCreditResult("2026-01-01", True, "hard_close", pnl=Decimal("20"))
        for _ in range(120)
    ]
    losses = [
        FiftyCreditResult("2026-01-02", True, "spread_stop", pnl=Decimal("-10"))
        for _ in range(30)
    ]
    metrics = fifty_credit_metrics(wins + losses)
    assert metrics["trades"] == 150
    assert metrics["profit_factor"] == "8.0000"
    report = {
        "train": {**metrics, "trades": 100, "max_drawdown": "-30"},
        "validation": {**metrics, "trades": 30, "max_drawdown": "-30"},
        "train_stress": metrics,
        "validation_stress": metrics,
    }
    assert viable(report) is True
    assert viable(
        {**report, "validation": {**report["validation"], "profit_factor": "1.24"}}
    ) is False

    first = moving_block_bootstrap(
        [Decimal("10"), Decimal("-5"), Decimal("8")],
        paths=50,
        trades_per_path=20,
        block_length=2,
        seed=7,
    )
    second = moving_block_bootstrap(
        [Decimal("10"), Decimal("-5"), Decimal("8")],
        paths=50,
        trades_per_path=20,
        block_length=2,
        seed=7,
    )
    assert first == second
    assert first["paths"] == 50
