from datetime import date, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from floor_insurance.directional import PriceBar
from floor_insurance.iron_fly_research import (
    IronFlyResult,
    IronFlySettings,
    center_strike,
    iron_fly_metrics,
    iron_fly_symbols,
    occ_option_for,
    session_absolute_move,
    simulate_iron_fly,
    trailing_realized_reference,
    viable,
)

ET = ZoneInfo("America/New_York")
DAY = date(2026, 8, 18)


def bar(
    hour: int,
    minute: int,
    value: str,
    *,
    close: str | None = None,
) -> PriceBar:
    price = Decimal(value)
    return PriceBar(
        datetime(2026, 8, 18, hour, minute, tzinfo=ET),
        price,
        price,
        price,
        Decimal(close or value),
    )


def config(**changes) -> IronFlySettings:
    return IronFlySettings(**changes)


def underlying(spot: str = "100.50") -> list[PriceBar]:
    return [bar(11, 0, spot), bar(15, 0, spot), bar(15, 59, spot, close="101.50")]


def option_marks(
    settings: IronFlySettings,
    center: Decimal = Decimal("101"),
    *,
    long_put: tuple[str, str] = ("0.30", "0.10"),
    short_put: tuple[str, str] = ("1.20", "0.60"),
    short_call: tuple[str, str] = ("1.30", "0.65"),
    long_call: tuple[str, str] = ("0.30", "0.10"),
    exit_time: time = time(15, 0),
) -> dict[str, list[PriceBar]]:
    values = {
        "long_put": long_put,
        "short_put": short_put,
        "short_call": short_call,
        "long_call": long_call,
    }
    symbols = iron_fly_symbols(DAY, center, settings)
    return {
        symbols[name]: [
            bar(11, 0, pair[0]),
            bar(exit_time.hour, exit_time.minute, pair[1]),
        ]
        for name, pair in values.items()
    }


def test_center_symbols_and_option_type_validation():
    settings = config()
    assert center_strike(Decimal("100.49")) == Decimal("100")
    assert center_strike(Decimal("100.50")) == Decimal("101")
    symbols = iron_fly_symbols(DAY, Decimal("101"), settings)
    assert symbols == {
        "long_put": "SPY260818P00099000",
        "short_put": "SPY260818P00101000",
        "short_call": "SPY260818C00101000",
        "long_call": "SPY260818C00103000",
    }
    try:
        occ_option_for("SPY", DAY, "X", Decimal("100"))
    except ValueError as exc:
        assert "P or C" in str(exc)
    else:
        raise AssertionError("invalid option type must fail")


def test_realized_reference_uses_only_completed_prior_values():
    bars = underlying()
    assert session_absolute_move(bars) == Decimal("1.00")
    moves = [Decimal(index) / Decimal("10") for index in range(1, 22)]
    assert trailing_realized_reference(moves, 20) == Decimal("1.15")
    assert trailing_realized_reference(moves[:19], 20) is None


def test_richness_and_risk_gates_are_independent():
    settings = config()
    marks = option_marks(settings)
    not_rich = simulate_iron_fly(
        DAY.isoformat(), underlying(), marks, Decimal("2.10"), settings
    )
    assert not_rich.entered is False
    assert not_rich.reason == "implied move is below richness gate"

    too_risky = simulate_iron_fly(
        DAY.isoformat(),
        underlying(),
        option_marks(
            settings,
            long_put=("0.71", "0.10"),
            long_call=("0.71", "0.10"),
        ),
        Decimal("1.00"),
        settings,
    )
    assert too_risky.entered is False
    assert too_risky.reason == "maximum risk exceeds $100 gate"


def test_trade_books_four_leg_adverse_fills_and_hard_close():
    settings = config()
    result = simulate_iron_fly(
        DAY.isoformat(), underlying(), option_marks(settings), Decimal("1.00"), settings
    )
    assert result.entered is True
    assert result.implied_move_proxy == Decimal("2.50")
    assert result.raw_entry_credit == Decimal("1.90")
    assert result.entry_credit == Decimal("1.82")
    assert result.maximum_risk == Decimal("18.20")
    assert result.exit_debit == Decimal("1.13")
    assert result.pnl == Decimal("68.80")


def test_stale_exit_uses_five_minute_mark_and_missing_is_full_width():
    settings = config()
    recent = simulate_iron_fly(
        DAY.isoformat(),
        underlying(),
        option_marks(settings, exit_time=time(14, 55)),
        Decimal("1"),
        settings,
    )
    assert recent.reason == "hard_close_last_mark"

    missing = simulate_iron_fly(
        DAY.isoformat(),
        underlying(),
        option_marks(settings, exit_time=time(14, 54)),
        Decimal("1"),
        settings,
    )
    assert missing.reason == "hard_close_missing_mark"
    assert missing.exit_debit == Decimal("2")
    assert missing.pnl == Decimal("-18.20")


def test_metrics_and_viability_gate():
    wins = [
        IronFlyResult("2026-01-01", True, "hard_close", pnl=Decimal("20"))
        for _ in range(120)
    ]
    losses = [
        IronFlyResult("2026-01-02", True, "hard_close", pnl=Decimal("-10"))
        for _ in range(30)
    ]
    metrics = iron_fly_metrics(wins + losses)
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
        {**report, "validation": {**report["validation"], "max_drawdown": "-501"}}
    ) is False
