from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from floor_insurance.directional import (
    Direction,
    DirectionalSettings,
    PriceBar,
    SignalModel,
    VixRegime,
    candidate_pairs,
    occ_symbol,
    opening_range_signal,
    select_debit_spread,
    simulate_debit_spread,
    size_debit_spreads,
)
from floor_insurance.directional_backtest import research_splits, run_research
from floor_insurance.directional_experiments import (
    REGIME_PARTITIONS,
    accepted,
    experiment_settings,
    partition_audit,
)
from floor_insurance.volatility import VolatilityHistory

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
        "breakout_1500_low_vix",
        "breakout_1500_high_vix",
        "breakout_1500_contango",
        "breakout_1500_backwardation",
        "breakout_1500_cheap_1d",
        "breakout_1500_rich_1d",
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


def test_regime_variants_only_differ_from_the_baseline_by_the_volatility_filter():
    ledger = experiment_settings()
    baseline = ledger["breakout_1500"]
    for below, at_or_above in REGIME_PARTITIONS:
        for name in (below, at_or_above):
            variant = ledger[name]
            assert variant.vix_regime != VixRegime.ANY
            assert replace(variant, vix_regime=VixRegime.ANY) == baseline
    halves = {name for pair in REGIME_PARTITIONS for name in pair}
    assert halves == {
        name
        for name, settings in ledger.items()
        if settings.vix_regime != VixRegime.ANY
    }


def test_partition_audit_reports_sessions_the_halves_do_not_explain():
    experiments = {
        "breakout_1500": {
            "train": {"trades": 40, "total_pnl": "500.00"},
            "validation": {"trades": 12, "total_pnl": "-90.00"},
        },
        "breakout_1500_contango": {
            "train": {"trades": 25, "total_pnl": "700.00"},
            "validation": {"trades": 7, "total_pnl": "-40.00"},
        },
        "breakout_1500_backwardation": {
            "train": {"trades": 13, "total_pnl": "-100.00"},
            "validation": {"trades": 5, "total_pnl": "-50.00"},
        },
    }
    audit = partition_audit(experiments)
    assert len(audit) == 1
    assert audit[0]["family"] == [
        "breakout_1500_contango",
        "breakout_1500_backwardation",
    ]
    train, validation = audit[0]["splits"]
    assert train["unexplained_trades"] == 2
    assert train["unexplained_pnl"] == "-100.00"
    assert validation["unexplained_trades"] == 0
    assert validation["unexplained_pnl"] == "0.00"


def session_bars(day: date) -> list[PriceBar]:
    bars = [
        PriceBar(
            datetime.combine(day, bar.timestamp.timetz()),
            bar.open,
            bar.high,
            bar.low,
            bar.close,
            bar.volume,
            bar.vwap,
        )
        for bar in stock_bars()
    ]
    last = Decimal("101")
    bars.append(
        PriceBar(
            datetime(day.year, day.month, day.day, 16, 0, tzinfo=ET),
            last,
            last,
            last,
            last,
        )
    )
    return bars


class StubHistory:
    """Minimal HistoricalData stand-in that records every option request."""

    def __init__(self, days: list[date], volatility: VolatilityHistory | None):
        self.config = SimpleNamespace(stock_feed="iex")
        self.cache_dir = Path("state") / "does-not-exist"
        self.sessions = {day.isoformat(): session_bars(day) for day in days}
        self._volatility = volatility
        self.option_requests: list[date] = []

    def stock_sessions(self, start: date, end: date) -> dict[str, list[PriceBar]]:
        return self.sessions

    def volatility(self) -> VolatilityHistory:
        if self._volatility is None:
            raise AssertionError("unfiltered variants must not load volatility data")
        return self._volatility

    def option_bars(
        self, trading_date: date, symbols: list[str]
    ) -> dict[str, list[PriceBar]]:
        self.option_requests.append(trading_date)
        entered = datetime.combine(trading_date, datetime(2026, 1, 1, 9, 45).time(), ET)
        later = entered + timedelta(minutes=30)
        closed = datetime.combine(trading_date, datetime(2026, 1, 1, 15, 0).time(), ET)
        long_symbol = occ_symbol(trading_date, Direction.CALL, Decimal("101"))
        short_symbol = occ_symbol(trading_date, Direction.CALL, Decimal("104"))
        if trading_date.day % 2 == 0:
            return {
                long_symbol: [option_bar(entered, "1.00"), option_bar(later, "2.00")],
                short_symbol: [option_bar(entered, "0.55"), option_bar(later, "0.45")],
            }
        return {
            long_symbol: [option_bar(entered, "1.00"), option_bar(closed, "0.20")],
            short_symbol: [option_bar(entered, "0.55"), option_bar(closed, "0.05")],
        }


def january_sessions() -> list[date]:
    return [date(2026, 1, day) for day in range(5, 10)] + [
        date(2026, 1, day) for day in range(12, 19)
    ]


def volatility_history(contango_until: date) -> VolatilityHistory:
    days = [date(2026, 1, day) for day in range(1, 20)]
    return VolatilityHistory(
        {
            "vix": {day: Decimal("18") for day in days},
            "vix9d": {
                day: Decimal("18") if day < contango_until else Decimal("22")
                for day in days
            },
            "vix3m": {day: Decimal("20") for day in days},
        }
    )


def test_unfiltered_variants_never_consult_volatility_history():
    days = january_sessions()
    data = StubHistory(days, None)
    reports, metadata = run_research(
        data,
        days[0],
        days[-1],
        DirectionalSettings(),
        False,
        days[10],
        False,
    )
    traded = [result for result in reports["train"] if result.traded]
    assert traded
    assert {result.reason for result in traded} == {"two_r_target", "hard_close"}
    assert metadata["vix_regime"] == "any"
    assert metadata["volatility_source"] == "not used"
    assert metadata["sizing"] == "equity proportional, path dependent"


def test_fixed_sizing_ignores_the_running_equity():
    settings = DirectionalSettings(candidate_radius=0, fixed_contracts=3)
    signal = opening_range_signal(stock_bars(), settings)
    assert signal is not None
    pairs = candidate_pairs(date(2026, 8, 18), signal, settings)
    long_symbol, short_symbol, _, _ = pairs[0]
    entered = datetime(2026, 8, 18, 9, 45, tzinfo=ET)
    spread = select_debit_spread(
        signal,
        pairs,
        {
            long_symbol: option_bar(entered, "1.00"),
            short_symbol: option_bar(entered, "0.55"),
        },
        settings,
    )
    assert spread is not None
    assert size_debit_spreads(Decimal("5000"), spread, settings) == 3
    assert size_debit_spreads(Decimal("1"), spread, settings) == 3
    assert (
        size_debit_spreads(
            Decimal("1"), spread, replace(settings, fixed_contracts=None)
        )
        == 0
    )
    assert (
        size_debit_spreads(
            Decimal("5000"), spread, replace(settings, maximum_contracts=2)
        )
        == 2
    )


def test_constant_sizing_applies_the_risk_rule_to_the_starting_balance():
    settings = DirectionalSettings(candidate_radius=0, constant_sizing=True)
    signal = opening_range_signal(stock_bars(), settings)
    assert signal is not None
    pairs = candidate_pairs(date(2026, 8, 18), signal, settings)
    long_symbol, short_symbol, _, _ = pairs[0]
    entered = datetime(2026, 8, 18, 9, 45, tzinfo=ET)
    spread = select_debit_spread(
        signal,
        pairs,
        {
            long_symbol: option_bar(entered, "1.00"),
            short_symbol: option_bar(entered, "0.55"),
        },
        settings,
    )
    assert spread is not None
    proportional = replace(settings, constant_sizing=False)
    # $5,000 at 2% funds one $0.50 debit spread; a drawn-down balance funds none.
    assert size_debit_spreads(Decimal("5000"), spread, proportional) == 1
    assert size_debit_spreads(Decimal("2000"), spread, proportional) == 0
    assert size_debit_spreads(Decimal("2000"), spread, settings) == 1
    assert size_debit_spreads(Decimal("50000"), spread, settings) == 1


def test_regime_filter_blocks_sessions_before_any_option_data_is_requested():
    days = january_sessions()
    boundary = date(2026, 1, 13)
    data = StubHistory(days, volatility_history(boundary))
    reports, metadata = run_research(
        data,
        days[0],
        days[-1],
        DirectionalSettings(vix_regime=VixRegime.CONTANGO),
        False,
        days[10],
        False,
    )
    results = reports["train"] + reports["validation"]
    blocked = [
        result for result in results if result.reason.startswith("volatility regime")
    ]
    traded = [result for result in results if result.traded]
    assert blocked and traded
    assert data.option_requests == [
        date.fromisoformat(result.trading_date) for result in traded
    ]
    assert all(
        date.fromisoformat(result.trading_date) < boundary + timedelta(days=1)
        for result in traded
    )
    assert metadata["volatility_coverage"]["vix9d"]["sessions"] == 19


def test_regime_halves_reconcile_exactly_under_path_independent_sizing():
    days = january_sessions()
    boundary = date(2026, 1, 13)

    def totals(regime: VixRegime, **sizing: object) -> tuple[int, Decimal]:
        data = StubHistory(
            days, None if regime == VixRegime.ANY else volatility_history(boundary)
        )
        reports, metadata = run_research(
            data,
            days[0],
            days[-1],
            DirectionalSettings(vix_regime=regime, **sizing),  # type: ignore[arg-type]
            False,
            days[10],
            False,
        )
        assert "path independent" in str(metadata["sizing"])
        results = reports["train"] + reports["validation"]
        traded = [result for result in results if result.traded]
        return len(traded), sum((result.pnl for result in traded), Decimal("0"))

    for sizing in ({"constant_sizing": True}, {"fixed_contracts": 1}):
        unfiltered = totals(VixRegime.ANY, **sizing)
        contango = totals(VixRegime.CONTANGO, **sizing)
        backwardation = totals(VixRegime.BACKWARDATION, **sizing)
        assert contango[0] + backwardation[0] == unfiltered[0]
        assert contango[1] + backwardation[1] == unfiltered[1]
        assert contango[0] and backwardation[0]

    scaled = totals(VixRegime.ANY, fixed_contracts=5)
    single = totals(VixRegime.ANY, fixed_contracts=1)
    assert scaled[0] == single[0]
    assert scaled[1] == single[1] * 5

