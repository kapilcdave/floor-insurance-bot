from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from floor_insurance.spread_estimator import (
    HOLDOUT_START,
    Bar,
    Estimate,
    SeriesSample,
    SpreadEstimatorError,
    abdi_ranaldo_spread,
    aggregate,
    bias_factors,
    bucket_for,
    calibrate,
    corwin_schultz_spread,
    moneyness_for,
    option_sessions,
    parse_symbol,
    range_decomposition_spread,
    roll_spread,
    session_samples,
    synthetic_bars,
    verdict,
)

ET = ZoneInfo("America/New_York")
START = datetime(2025, 6, 2, 9, 30, tzinfo=ET)


def bar(minute: int, high: float, low: float, close: float, volume: float = 100.0) -> Bar:
    return Bar(
        timestamp=START + timedelta(minutes=minute),
        open=(high + low) / 2,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


# --------------------------------------------------------------------------
# Recovery of a known spread
# --------------------------------------------------------------------------


@pytest.mark.parametrize("true_spread", [0.01, 0.02, 0.05])
def test_abdi_ranaldo_recovers_a_known_spread_within_ten_percent(true_spread):
    """The best-calibrated estimator must be nearly unbiased in a calm regime."""

    bars = synthetic_bars(spread=true_spread, price=1.00, minute_volatility=0.015)
    estimate = abdi_ranaldo_spread(bars)
    assert estimate.absolute is not None
    ratio = float(estimate.absolute) / true_spread
    assert 0.90 <= ratio <= 1.10


@pytest.mark.parametrize("true_spread", [0.01, 0.02, 0.05])
def test_corwin_schultz_runs_slightly_low_rather_than_high(true_spread):
    """On one-minute bars the published upward bias reverses; lock that in.

    If a future change makes Corwin-Schultz read high, the bias correction in
    `verdict` would be pushing estimates the wrong way, so this direction is
    worth asserting rather than merely documenting.
    """

    bars = synthetic_bars(spread=true_spread, price=1.00, minute_volatility=0.015)
    estimate = corwin_schultz_spread(bars)
    assert estimate.absolute is not None
    ratio = float(estimate.absolute) / true_spread
    assert 0.75 <= ratio <= 1.00


def test_roll_recovers_the_right_order_of_magnitude_but_reads_high():
    bars = synthetic_bars(spread=0.02, price=1.00, minute_volatility=0.015)
    estimate = roll_spread(bars)
    assert estimate.absolute is not None
    assert 1.00 <= float(estimate.absolute) / 0.02 <= 1.60


def test_a_wider_market_reads_wider_on_every_estimator():
    """Monotonicity in the truth is the weakest useful property; assert it."""

    tight = synthetic_bars(spread=0.01, price=1.00, minute_volatility=0.015, seed=7)
    wide = synthetic_bars(spread=0.06, price=1.00, minute_volatility=0.015, seed=7)
    for estimator in (roll_spread, corwin_schultz_spread, abdi_ranaldo_spread):
        low = estimator(tight).absolute
        high = estimator(wide).absolute
        assert low is not None and high is not None
        assert high > low, estimator.__name__


# --------------------------------------------------------------------------
# Failure modes reported rather than hidden
# --------------------------------------------------------------------------


def test_roll_is_undefined_on_a_pure_trend():
    """A monotone series has positive serial covariance and no bounce to measure."""

    bars = [bar(index, 1.00 + index * 0.01, 0.99 + index * 0.01, 1.00 + index * 0.01) for index in range(40)]
    estimate = roll_spread(bars)
    assert estimate.absolute is None
    assert "positive serial covariance" in estimate.note


def test_range_estimators_collapse_to_zero_on_a_locked_penny_market():
    """Every print on one tick gives high == low, so a range estimator reads zero.

    This is the documented cheap-wing failure. It must surface as an explicit
    zero, not as a plausible-looking small spread.
    """

    bars = [bar(index, 0.05, 0.05, 0.05) for index in range(40)]
    assert corwin_schultz_spread(bars).absolute == Decimal("0.000")


def test_estimators_refuse_a_window_with_too_few_bars():
    bars = [bar(index, 1.02, 0.98, 1.00) for index in range(3)]
    for estimator in (roll_spread, corwin_schultz_spread, abdi_ranaldo_spread):
        estimate = estimator(bars)
        assert estimate.absolute is None
        assert "fewer than" in estimate.note


def test_zero_volume_bars_are_treated_as_tape_gaps():
    """A minute with no volume is a hole in the tape, not a quiet market."""

    bars = [bar(index, 1.02, 0.98, 1.00, volume=0.0) for index in range(40)]
    assert corwin_schultz_spread(bars).absolute is None


# --------------------------------------------------------------------------
# Range decomposition
# --------------------------------------------------------------------------


def test_range_decomposition_recovers_the_intercept_and_the_delta():
    """Built so option range = 0.02 + 0.40 * underlying range, exactly."""

    spread, delta = 0.02, 0.40
    underlying: list[Bar] = []
    options: list[Bar] = []
    for index in range(60):
        spot_range = 0.05 + 0.01 * (index % 7)
        underlying.append(
            Bar(START + timedelta(minutes=index), 500.0, 500.0 + spot_range, 500.0, 500.0, 1000.0)
        )
        option_range = spread + delta * spot_range
        options.append(bar(index, 1.00 + option_range, 1.00, 1.00))

    estimate = range_decomposition_spread(options, underlying)
    assert estimate.absolute is not None
    assert abs(float(estimate.absolute) - spread) < 0.002
    assert estimate.slope is not None
    assert abs(float(estimate.slope) - delta) < 0.02
    assert estimate.r_squared is not None
    assert float(estimate.r_squared) > 0.99


def test_range_decomposition_needs_aligned_underlying_minutes():
    options = [bar(index, 1.02, 0.98, 1.00) for index in range(40)]
    estimate = range_decomposition_spread(options, [])
    assert estimate.absolute is None
    assert "aligned minutes" in estimate.note


# --------------------------------------------------------------------------
# Calibration
# --------------------------------------------------------------------------


def test_calibration_is_deterministic_for_a_fixed_seed():
    assert calibrate(seed=11) == calibrate(seed=11)


def test_calibration_covers_every_grid_case_with_a_ratio():
    report = calibrate()
    for case in report["cases"]:
        for name in ("roll", "corwin_schultz", "abdi_ranaldo"):
            assert f"{name}_ratio" in case, (case["case"], name)


def test_bias_factors_bracket_the_truth_from_both_sides():
    """Corwin-Schultz below one and Roll above it is what makes this a bracket."""

    factors = bias_factors()
    assert factors["corwin_schultz"] < Decimal("1")
    assert factors["roll"] > Decimal("1")
    assert Decimal("0.9") <= factors["abdi_ranaldo"] <= Decimal("1.15")


# --------------------------------------------------------------------------
# Bucketing and parsing
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "symbol,expected",
    [
        ("SPY240201C00480000", ("C", Decimal("480"))),
        ("SPY260522P00764500", ("P", Decimal("764.5"))),
    ],
)
def test_parse_symbol_reads_kind_and_strike(symbol, expected):
    assert parse_symbol(symbol) == expected


@pytest.mark.parametrize("symbol", ["SPY", "spy240201C00480000", "SPY240201X00480000"])
def test_parse_symbol_rejects_anything_else(symbol):
    assert parse_symbol(symbol) is None


@pytest.mark.parametrize(
    "distance,expected",
    [
        (Decimal("0"), "atm"),
        (Decimal("-0.5"), "atm"),
        (Decimal("1.5"), "near_1_2"),
        (Decimal("-2.5"), "near_2_3"),
        (Decimal("9"), "wing_3_plus"),
    ],
)
def test_moneyness_uses_absolute_distance(distance, expected):
    assert moneyness_for(distance) == expected


def test_bucket_for_returns_none_outside_the_session():
    assert bucket_for(datetime(2025, 6, 2, 8, 0, tzinfo=ET).time()) is None
    assert bucket_for(datetime(2025, 6, 2, 10, 30, tzinfo=ET).time()) == "10:00-11:00"


# --------------------------------------------------------------------------
# Cache handling and the sealed holdout
# --------------------------------------------------------------------------


def test_option_sessions_refuses_sessions_on_or_after_the_holdout(tmp_path):
    for stamp in ("2026-05-20", "2026-05-22", "2026-05-26", "2026-06-01"):
        (tmp_path / f"options-{stamp}.json").write_text("{}", encoding="utf-8")

    found = [stamp for stamp, _ in option_sessions(tmp_path)]
    assert found == ["2026-05-20", "2026-05-22"]
    assert HOLDOUT_START == date(2026, 5, 26)


def test_option_sessions_raises_when_only_holdout_sessions_exist(tmp_path):
    (tmp_path / "options-2026-06-01.json").write_text("{}", encoding="utf-8")
    with pytest.raises(SpreadEstimatorError):
        option_sessions(tmp_path)


def test_option_sessions_raises_on_a_missing_directory(tmp_path):
    with pytest.raises(SpreadEstimatorError):
        option_sessions(tmp_path / "absent")


def test_session_samples_skips_thin_windows():
    payload = {
        "SPY240201C00480000": [
            {
                "t": (START + timedelta(minutes=index)).isoformat(),
                "o": "1.00",
                "h": "1.02",
                "l": "0.98",
                "c": "1.00",
                "v": "1",
            }
            for index in range(40)
        ]
    }
    # Volume of one contract a minute cannot support an estimate.
    assert session_samples("2024-02-01", payload, [], minimum_volume=500.0) == []


# --------------------------------------------------------------------------
# Aggregation and verdict
# --------------------------------------------------------------------------


def sample(estimator: str, value: str | None, distance: str = "0") -> SeriesSample:
    return SeriesSample(
        trading_date="2024-02-01",
        symbol="SPY240201C00480000",
        kind="C",
        strike=Decimal("480"),
        distance=Decimal(distance),
        bucket="11:00-12:00",
        bars=30,
        volume=500.0,
        mean_price=1.0,
        estimates=(
            Estimate(estimator, Decimal(value) if value is not None else None, None, 10),
        ),
    )


def test_aggregate_counts_undefined_windows_separately():
    summary = aggregate(
        [sample("roll", "0.010"), sample("roll", None), sample("roll", "0.030")]
    )
    assert summary["overall"]["roll"]["count"] == "2"
    assert summary["overall"]["roll"]["median"] == "0.020"
    assert summary["undefined_windows"]["roll"] == 1


def test_aggregate_splits_by_moneyness():
    summary = aggregate(
        [sample("abdi_ranaldo", "0.010"), sample("abdi_ranaldo", "0.040", distance="5")]
    )
    assert summary["by_moneyness"]["atm"]["abdi_ranaldo"]["median"] == "0.010"
    assert summary["by_moneyness"]["wing_3_plus"]["abdi_ranaldo"]["median"] == "0.040"


def test_verdict_calls_a_measured_cost_below_the_fatal_level_survivable():
    summary = aggregate([sample("abdi_ranaldo", "0.011")])
    result = verdict(summary, Decimal("0.005"), fatal_per_unit=Decimal("0.03"))
    # 0.011 de-biased by 1.02 is 0.011, and half of that rounds to 0.006.
    assert result["bracket"]["primary_abdi_ranaldo"] == "0.006"
    assert "not what limits these strategies" in result["interpretation"]


def test_verdict_declares_a_line_dead_when_the_measured_cost_reaches_the_fatal_level():
    summary = aggregate([sample("abdi_ranaldo", "0.080")])
    result = verdict(summary, Decimal("0.005"), fatal_per_unit=Decimal("0.03"))
    assert "dead on cost alone" in result["interpretation"]


def test_verdict_says_nothing_when_no_estimator_produced_a_median():
    result = verdict(aggregate([]), Decimal("0.005"))
    assert "nothing can be concluded" in result["interpretation"]


# --------------------------------------------------------------------------
# Structural guarantees
# --------------------------------------------------------------------------


def test_module_reads_only_the_cache():
    """No client, no socket: this module must be unable to fetch fresh data.

    That is what keeps it from reaching past the sealed holdout boundary.
    """

    import floor_insurance.spread_estimator as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    for forbidden in ("AlpacaClient", "import requests", "requests.", "urlopen", "/v1beta1"):
        assert forbidden not in source, forbidden
