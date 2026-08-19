from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from floor_insurance.directional import (
    Direction,
    DirectionalSettings,
    PriceBar,
    SignalModel,
    candidate_pairs,
    occ_symbol,
    opening_range_signal,
    select_debit_spread,
    simulate_debit_spread,
)
from floor_insurance.directional_backtest import research_splits
from floor_insurance.directional_experiments import accepted, experiment_settings

ET = ZoneInfo("America/New_York")


def stock_bars(*, bullish: bool = True) -> list[PriceBar]:
    opened = datetime(2026, 8, 18, 9, 30, tzinfo=ET)
    bars = []
    for minute in range(15):
        base = Decimal("100") + Decimal(minute) / Decimal("20")
        close = base + (Decimal("0.40") if bullish and minute == 14 else Decimal("0"))
        if not bullish:
            base = Decimal("100") - Decimal(minute) / Decimal("20")
            close = base - (Decimal("0.40") if minute == 14 else Decimal("0"))
        bars.append(
            PriceBar(
                opened + timedelta(minutes=minute),
                base,
                base + Decimal("0.05"),
                base - Decimal("0.05"),
                close,
                Decimal("100"),
                base,
            )
        )
    return bars


def option_bar(symbol_time: datetime, value: str) -> PriceBar:
    price = Decimal(value)
    return PriceBar(symbol_time, price, price, price, price, Decimal("10"), price)


def test_opening_range_breakout_generates_direction_without_future_bars():
    settings = DirectionalSettings()
    bars = stock_bars()
    bars.append(
        PriceBar(
            datetime(2026, 8, 18, 16, 0, tzinfo=ET),
            Decimal("50"),
            Decimal("50"),
            Decimal("50"),
            Decimal("50"),
        )
    )
    signal = opening_range_signal(bars, settings)
    assert signal is not None
    assert signal.direction == Direction.CALL
    assert signal.timestamp.time().isoformat() == "09:44:00"


def test_volume_confirmation_rejects_a_fading_breakout():
    settings = DirectionalSettings(
        signal_model=SignalModel.OPENING_RANGE_VOLUME,
        minimum_volume_ratio=Decimal("1"),
    )
    bars = stock_bars()
    bars = [
        PriceBar(
            bar.timestamp,
            bar.open,
            bar.high,
            bar.low,
            bar.close,
            Decimal("50") if index >= 10 else Decimal("100"),
            bar.vwap,
        )
        for index, bar in enumerate(bars)
    ]
    assert opening_range_signal(bars, settings) is None


def test_vwap_momentum_detects_persistent_move_without_breakout_requirement():
    settings = DirectionalSettings(
        signal_model=SignalModel.VWAP_MOMENTUM,
        minimum_momentum_fraction=Decimal("0.001"),
    )
    signal = opening_range_signal(stock_bars(), settings)
    assert signal is not None
    assert signal.direction == Direction.CALL


def test_vwap_reversion_fades_a_stretched_breakout():
    settings = DirectionalSettings(
        signal_model=SignalModel.VWAP_REVERSION,
        minimum_momentum_fraction=Decimal("0.001"),
    )
    signal = opening_range_signal(stock_bars(), settings)
    assert signal is not None
    assert signal.direction == Direction.PUT


def test_gap_continuation_requires_the_opening_move_to_hold():
    settings = DirectionalSettings(
        signal_model=SignalModel.GAP_CONTINUATION,
        minimum_gap_fraction=Decimal("0.002"),
    )
    signal = opening_range_signal(stock_bars(), settings, Decimal("99"))
    assert signal is not None
    assert signal.direction == Direction.CALL


def test_gap_fade_reverses_a_failed_gap():
    settings = DirectionalSettings(
        signal_model=SignalModel.GAP_FADE,
        minimum_gap_fraction=Decimal("0.002"),
    )
    signal = opening_range_signal(stock_bars(bullish=False), settings, Decimal("99"))
    assert signal is not None
    assert signal.direction == Direction.PUT


def test_occ_symbol_and_candidate_widths():
    settings = DirectionalSettings(candidate_radius=0)
    signal = opening_range_signal(stock_bars(), settings)
    assert signal is not None
    pairs = candidate_pairs(date(2026, 8, 18), signal, settings)
    assert pairs == [
        (
            occ_symbol(date(2026, 8, 18), Direction.CALL, Decimal("101")),
            occ_symbol(date(2026, 8, 18), Direction.CALL, Decimal("104")),
            Decimal("101"),
            Decimal("104"),
        )
    ]


def test_debit_spread_selects_two_to_one_and_hits_target():
    settings = DirectionalSettings(
        starting_equity=Decimal("5000"),
        risk_fraction=Decimal("0.021"),
        candidate_radius=0,
        slippage_per_side=Decimal("0.05"),
        fees_per_spread=Decimal("0.10"),
    )
    signal = opening_range_signal(stock_bars(), settings)
    assert signal is not None
    pairs = candidate_pairs(date(2026, 8, 18), signal, settings)
    long_symbol, short_symbol, _, _ = pairs[0]
    entered = datetime(2026, 8, 18, 9, 45, tzinfo=ET)
    entry = {
        long_symbol: option_bar(entered, "2.00"),
        short_symbol: option_bar(entered, "1.05"),
    }
    spread = select_debit_spread(signal, pairs, entry, settings)
    assert spread is not None
    assert spread.entry_debit == Decimal("1.00")
    assert spread.reward_risk == Decimal("2")

    later = entered + timedelta(minutes=30)
    result = simulate_debit_spread(
        "2026-08-18",
        signal,
        spread,
        {
            long_symbol: [entry[long_symbol], option_bar(later, "3.05")],
            short_symbol: [entry[short_symbol], option_bar(later, "0.00")],
        },
        Decimal("5000"),
        settings,
    )
    assert result.quantity == 1
    assert result.reason == "two_r_target"
    assert result.pnl == Decimal("199.90")
    assert result.r_multiple == Decimal("1.9990")


def test_five_thousand_account_skips_hundred_dollar_debit_at_one_percent():
    settings = DirectionalSettings(
        starting_equity=Decimal("5000"),
        risk_fraction=Decimal("0.01"),
        candidate_radius=0,
        slippage_per_side=Decimal("0.05"),
    )
    signal = opening_range_signal(stock_bars(), settings)
    assert signal is not None
    pairs = candidate_pairs(date(2026, 8, 18), signal, settings)
    long_symbol, short_symbol, _, _ = pairs[0]
    entered = datetime(2026, 8, 18, 9, 45, tzinfo=ET)
    entry = {
        long_symbol: option_bar(entered, "2.00"),
        short_symbol: option_bar(entered, "1.05"),
    }
    spread = select_debit_spread(signal, pairs, entry, settings)
    assert spread is not None
    result = simulate_debit_spread(
        "2026-08-18",
        signal,
        spread,
        {long_symbol: [entry[long_symbol]], short_symbol: [entry[short_symbol]]},
        Decimal("5000"),
        settings,
    )
    assert result.traded is False
    assert result.reason == "risk budget is below one spread debit"


def test_explicit_oos_boundary_stays_chronological_and_locked():
    dates = [f"2026-01-{day:02d}" for day in range(1, 13)]
    splits = research_splits(dates, date(2026, 1, 11))
    assert splits["train"] == set(dates[:7])
    assert splits["validation"] == set(dates[7:10])
    assert splits["out_of_sample"] == set(dates[10:])


def test_experiment_ledger_is_fixed_and_rejects_small_validation_samples():
    assert set(experiment_settings()) == {
        "breakout_1500",
        "volume_breakout_1200",
        "vwap_momentum_1130",
        "breakout_1030",
        "breakout_1200",
        "vwap_reversion_1130",
        "gap_continuation_1200",
        "gap_fade_1200",
    }
    report = {
        "train": {"total_pnl": "100", "profit_factor": "1.2"},
        "validation": {
            "total_pnl": "50",
            "profit_factor": "1.1",
            "trades": 8,
        },
    }
    assert accepted(report) is False
