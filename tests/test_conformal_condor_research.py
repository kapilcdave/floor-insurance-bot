import math
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from floor_insurance.conformal_condor_research import (
    ConformalForecast,
    ConformalResult,
    ConformalSettings,
    ForecastObservation,
    conformal_forecast,
    conformal_metrics,
    conformal_strikes,
    conformal_symbols,
    forecast_features,
    morning_realized_scale,
    nearest_rank,
    ridge_coefficients,
    simulate_conformal_condor,
    viable,
)
from floor_insurance.directional import PriceBar

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


def test_morning_scale_and_features_use_only_prior_targets():
    bars = [bar(9, 30, "100"), bar(10, 0, "101"), bar(10, 59, "100"), bar(11, 0, "100")]
    scale = morning_realized_scale(bars)
    assert scale is not None and scale > 0
    targets = [Decimal(str(index / 10)) for index in range(1, 21)]
    features = forecast_features(scale, targets, ConformalSettings())
    assert features is not None
    assert features[0] == 1.0
    assert math.isclose(features[2], math.log(Decimal("1.8")), rel_tol=1e-9)


def test_ridge_recovers_a_simple_linear_relationship():
    observations = [
        ForecastObservation(str(index), (1.0, float(index), 0.0, 0.0), 2.0 + 3.0 * index)
        for index in range(1, 10)
    ]
    coefficients = ridge_coefficients(observations, 0.000001)
    assert math.isclose(coefficients[0], 2.0, abs_tol=0.001)
    assert math.isclose(coefficients[1], 3.0, abs_tol=0.001)


def test_nearest_rank_and_online_conformal_history_requirement():
    assert nearest_rank([1.0, 4.0, 2.0, 3.0], 0.75) == 3.0
    settings = ConformalSettings(regression_window=4, calibration_window=2)
    observations = [
        ForecastObservation(str(index), (1.0, float(index), 1.0, 1.0), math.log(index + 1))
        for index in range(6)
    ]
    forecast = conformal_forecast(observations, (1.0, 6.0, 1.0, 1.0), settings)
    assert forecast is not None
    assert forecast.calibration_count == 2
    assert forecast.upper_move >= forecast.point_move > 0


def test_conformal_strikes_round_outward():
    assert conformal_strikes(Decimal("100"), Decimal("1.20"), ConformalSettings()) == (
        Decimal("97"),
        Decimal("98"),
        Decimal("102"),
        Decimal("103"),
    )


def test_profitable_condor_charges_half_cent_per_leg_each_way():
    settings = ConformalSettings()
    forecast = ConformalForecast(Decimal("1"), Decimal("0.2"), Decimal("1.2"), 40)
    day = datetime(2026, 5, 1).date()
    symbols = conformal_symbols(day, Decimal("100"), forecast.upper_move, settings)
    options = {
        symbols["long_put"]: [bar(11, 0, "0.05"), bar(15, 0, "0.01")],
        symbols["short_put"]: [bar(11, 0, "0.25"), bar(15, 0, "0.05")],
        symbols["short_call"]: [bar(11, 0, "0.25"), bar(15, 0, "0.05")],
        symbols["long_call"]: [bar(11, 0, "0.05"), bar(15, 0, "0.01")],
    }
    result = simulate_conformal_condor(
        "2026-05-01",
        [bar(11, 0, "100"), bar(15, 0, "100.50")],
        options,
        forecast,
        settings,
    )
    assert result.entered and result.contained
    assert result.entry_credit == Decimal("0.38")
    assert result.exit_debit == Decimal("0.10")
    assert result.pnl == Decimal("27.80")


def test_metrics_enforce_calibration_and_economic_gates():
    winners = [
        ConformalResult(
            str(index),
            True,
            "hard_close",
            True,
            Decimal("2"),
            Decimal("1"),
            True,
            pnl=Decimal("10"),
        )
        for index in range(90)
    ]
    breaches = [
        ConformalResult(
            f"b{index}",
            True,
            "hard_close",
            True,
            Decimal("2"),
            Decimal("3"),
            False,
            pnl=Decimal("-1"),
        )
        for index in range(10)
    ]
    train_rows = winners + breaches
    validation_rows = train_rows[:30]
    report = {
        "train": conformal_metrics(train_rows),
        "validation": conformal_metrics(validation_rows),
        "train_stress": conformal_metrics(train_rows),
        "validation_stress": conformal_metrics(validation_rows),
    }
    assert report["train"]["containment"] == 0.9
    assert not viable(report)  # Validation has only winners and no defined profit factor.
