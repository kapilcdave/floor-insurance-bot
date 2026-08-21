"""Online conformal SPY 0DTE iron-condor rejection screen."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, time, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from pathlib import Path
from statistics import mean

from .config import Config
from .directional import PriceBar
from .directional_backtest import HistoricalData, research_splits
from .fifty_credit_research import moving_block_bootstrap
from .iron_fly_research import occ_option_for

CENT = Decimal("0.01")
HUNDRED = Decimal("100")
RATIO = Decimal("0.0001")

ACCEPTANCE_RULE = (
    "At least 100 training and 30 validation trades; positive average P&L and "
    "profit factor at least 1.25 on both base splits; maximum drawdown no worse "
    "than -$500 on either base split; positive average P&L on both stress "
    "splits; and 85% through 95% conformal containment on both base splits. "
    "The final chronological holdout remains sealed."
)

DATA_LIMITATION = (
    "Alpaca Basic historical option bars are not synchronized executable OPRA "
    "quotes. Explicit adverse fills make this a rejection screen rather than "
    "proof of an atomic live fill."
)


@dataclass(frozen=True)
class ConformalSettings:
    symbol: str = "SPY"
    width: Decimal = Decimal("1")
    regression_window: int = 120
    calibration_window: int = 40
    short_mean_window: int = 5
    long_mean_window: int = 20
    ridge_lambda: float = 0.001
    coverage: float = 0.90
    entry_time: time = time(11, 0)
    hard_close: time = time(15, 0)
    adverse_fill_per_leg: Decimal = Decimal("0.005")
    minimum_credit: Decimal = Decimal("0.10")
    fees_per_condor: Decimal = Decimal("0.20")
    maximum_risk_dollars: Decimal = Decimal("100")
    max_hard_close_mark_age_minutes: int = 5


@dataclass(frozen=True)
class ForecastObservation:
    trading_date: str
    features: tuple[float, float, float, float]
    log_target: float


@dataclass(frozen=True)
class ConformalForecast:
    point_move: Decimal
    residual_quantile: Decimal
    upper_move: Decimal
    calibration_count: int


@dataclass(frozen=True)
class ConformalResult:
    trading_date: str
    entered: bool
    reason: str
    forecasted: bool = False
    upper_move: Decimal = Decimal("0")
    actual_move: Decimal = Decimal("0")
    contained: bool = False
    short_put_strike: Decimal = Decimal("0")
    short_call_strike: Decimal = Decimal("0")
    raw_entry_credit: Decimal = Decimal("0")
    entry_credit: Decimal = Decimal("0")
    maximum_risk: Decimal = Decimal("0")
    exit_debit: Decimal = Decimal("0")
    pnl: Decimal = Decimal("0")


def _at(bars: list[PriceBar], moment: time) -> PriceBar | None:
    return next((bar for bar in bars if bar.timestamp.time() == moment), None)


def morning_realized_scale(bars: list[PriceBar], entry_time: time = time(11, 0)) -> Decimal | None:
    morning = [bar for bar in bars if time(9, 30) <= bar.timestamp.time() < entry_time]
    entry = _at(bars, entry_time)
    if not morning or entry is None or morning[0].open <= 0:
        return None
    prices = [float(morning[0].open)] + [float(bar.close) for bar in morning]
    if any(price <= 0 for price in prices):
        return None
    realized_variance = sum(
        math.log(current / previous) ** 2
        for previous, current in zip(prices[:-1], prices[1:], strict=True)
    )
    scale = math.sqrt(realized_variance) * float(entry.open)
    return Decimal(str(max(0.01, scale))).quantize(Decimal("0.0001"))


def session_target_move(
    bars: list[PriceBar],
    entry_time: time = time(11, 0),
    hard_close: time = time(15, 0),
) -> Decimal | None:
    entry = _at(bars, entry_time)
    close = _at(bars, hard_close)
    if entry is None or close is None:
        return None
    return abs(close.close - entry.open).quantize(CENT)


def forecast_features(
    morning_scale: Decimal | None,
    prior_targets: list[Decimal],
    settings: ConformalSettings,
) -> tuple[float, float, float, float] | None:
    if morning_scale is None or len(prior_targets) < settings.long_mean_window:
        return None
    floor = Decimal("0.01")
    short_mean = Decimal(str(mean(prior_targets[-settings.short_mean_window :])))
    long_mean = Decimal(str(mean(prior_targets[-settings.long_mean_window :])))
    values = (morning_scale, short_mean, long_mean)
    return (1.0, *(math.log(float(max(floor, value))) for value in values))


def _solve(matrix: list[list[float]], values: list[float]) -> list[float]:
    size = len(values)
    augmented = [row[:] + [value] for row, value in zip(matrix, values, strict=True)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) < 1e-12:
            raise ValueError("ridge system is singular")
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        divisor = augmented[column][column]
        augmented[column] = [value / divisor for value in augmented[column]]
        for row in range(size):
            if row == column:
                continue
            factor = augmented[row][column]
            augmented[row] = [
                value - factor * reference
                for value, reference in zip(augmented[row], augmented[column], strict=True)
            ]
    return [augmented[row][-1] for row in range(size)]


def ridge_coefficients(observations: list[ForecastObservation], ridge_lambda: float) -> list[float]:
    if not observations:
        raise ValueError("at least one observation is required")
    width = len(observations[0].features)
    gram = [[0.0 for _ in range(width)] for _ in range(width)]
    rhs = [0.0 for _ in range(width)]
    for observation in observations:
        for row, left in enumerate(observation.features):
            rhs[row] += left * observation.log_target
            for column, right in enumerate(observation.features):
                gram[row][column] += left * right
    for index in range(1, width):
        gram[index][index] += ridge_lambda
    return _solve(gram, rhs)


def _predict(coefficients: list[float], features: tuple[float, ...]) -> float:
    return sum(
        coefficient * feature for coefficient, feature in zip(coefficients, features, strict=True)
    )


def nearest_rank(values: list[float], probability: float) -> float:
    if not values or not 0 < probability <= 1:
        raise ValueError("values and a probability in (0, 1] are required")
    ordered = sorted(values)
    rank = max(1, math.ceil(probability * len(ordered)))
    return ordered[rank - 1]


def conformal_forecast(
    observations: list[ForecastObservation],
    current_features: tuple[float, float, float, float] | None,
    settings: ConformalSettings,
) -> ConformalForecast | None:
    required = settings.regression_window + settings.calibration_window
    if current_features is None or len(observations) < required:
        return None
    residuals: list[float] = []
    for index in range(len(observations) - settings.calibration_window, len(observations)):
        history = observations[index - settings.regression_window : index]
        coefficients = ridge_coefficients(history, settings.ridge_lambda)
        prediction = _predict(coefficients, observations[index].features)
        residuals.append(abs(observations[index].log_target - prediction))
    current_history = observations[-settings.regression_window :]
    coefficients = ridge_coefficients(current_history, settings.ridge_lambda)
    log_prediction = _predict(coefficients, current_features)
    residual = nearest_rank(residuals, settings.coverage)
    point = Decimal(str(math.exp(log_prediction))).quantize(Decimal("0.0001"))
    upper = Decimal(str(math.exp(log_prediction + residual))).quantize(Decimal("0.0001"))
    return ConformalForecast(
        point_move=point,
        residual_quantile=Decimal(str(residual)).quantize(Decimal("0.0001")),
        upper_move=upper,
        calibration_count=len(residuals),
    )


def conformal_strikes(
    spot: Decimal, upper_move: Decimal, settings: ConformalSettings
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    short_put = (spot - upper_move).to_integral_value(rounding=ROUND_FLOOR)
    short_call = (spot + upper_move).to_integral_value(rounding=ROUND_CEILING)
    return (
        short_put - settings.width,
        short_put,
        short_call,
        short_call + settings.width,
    )


def conformal_symbols(
    trading_date: date,
    spot: Decimal,
    upper_move: Decimal,
    settings: ConformalSettings,
) -> dict[str, str]:
    long_put, short_put, short_call, long_call = conformal_strikes(spot, upper_move, settings)
    return {
        "long_put": occ_option_for(settings.symbol, trading_date, "P", long_put),
        "short_put": occ_option_for(settings.symbol, trading_date, "P", short_put),
        "short_call": occ_option_for(settings.symbol, trading_date, "C", short_call),
        "long_call": occ_option_for(settings.symbol, trading_date, "C", long_call),
    }


def _bounded_debit(value: Decimal, width: Decimal) -> Decimal:
    return max(Decimal("0"), min(width, value)).quantize(CENT, rounding=ROUND_CEILING)


def simulate_conformal_condor(
    trading_date: str,
    underlying_bars: list[PriceBar],
    option_bars: dict[str, list[PriceBar]],
    forecast: ConformalForecast | None,
    settings: ConformalSettings,
) -> ConformalResult:
    actual_move = session_target_move(underlying_bars, settings.entry_time, settings.hard_close)
    if forecast is None:
        return ConformalResult(
            trading_date,
            False,
            "insufficient conformal history",
            actual_move=actual_move or Decimal("0"),
        )
    if actual_move is None:
        return ConformalResult(
            trading_date,
            False,
            "underlying target bar missing",
            forecasted=True,
            upper_move=forecast.upper_move,
        )
    contained = actual_move <= forecast.upper_move
    entry = _at(underlying_bars, settings.entry_time)
    if entry is None:
        return ConformalResult(
            trading_date,
            False,
            "underlying entry bar missing",
            True,
            forecast.upper_move,
            actual_move,
            contained,
        )
    day = date.fromisoformat(trading_date)
    strikes = conformal_strikes(entry.open, forecast.upper_move, settings)
    names = conformal_symbols(day, entry.open, forecast.upper_move, settings)
    legs = {name: option_bars.get(symbol, []) for name, symbol in names.items()}
    entries = {name: _at(bars, settings.entry_time) for name, bars in legs.items()}
    if any(bar is None for bar in entries.values()):
        return ConformalResult(
            trading_date,
            False,
            "one or more condor legs lack an exact entry bar",
            True,
            forecast.upper_move,
            actual_move,
            contained,
            strikes[1],
            strikes[2],
        )

    long_put = entries["long_put"]
    short_put = entries["short_put"]
    short_call = entries["short_call"]
    long_call = entries["long_call"]
    assert long_put and short_put and short_call and long_call
    raw_credit = (short_put.open + short_call.open - long_put.open - long_call.open).quantize(
        CENT, rounding=ROUND_FLOOR
    )
    entry_credit = (raw_credit - settings.adverse_fill_per_leg * Decimal("4")).quantize(
        CENT, rounding=ROUND_FLOOR
    )
    maximum_risk = ((settings.width - entry_credit) * HUNDRED + settings.fees_per_condor).quantize(
        CENT
    )
    if (
        entry_credit < settings.minimum_credit
        or entry_credit >= settings.width
        or maximum_risk > settings.maximum_risk_dollars
    ):
        return ConformalResult(
            trading_date,
            False,
            "entry credit or maximum risk failed",
            True,
            forecast.upper_move,
            actual_move,
            contained,
            strikes[1],
            strikes[2],
            raw_credit,
            entry_credit,
            maximum_risk,
        )

    by_timestamp = {name: {bar.timestamp: bar for bar in bars} for name, bars in legs.items()}
    synchronized = sorted(
        set(by_timestamp["long_put"])
        & set(by_timestamp["short_put"])
        & set(by_timestamp["short_call"])
        & set(by_timestamp["long_call"])
    )
    marks: list[tuple[datetime, Decimal]] = []
    for timestamp in synchronized:
        if timestamp.time() <= settings.entry_time or timestamp.time() > settings.hard_close:
            continue
        debit = _bounded_debit(
            by_timestamp["short_put"][timestamp].close
            + by_timestamp["short_call"][timestamp].close
            - by_timestamp["long_put"][timestamp].close
            - by_timestamp["long_call"][timestamp].close
            + settings.adverse_fill_per_leg * Decimal("4"),
            settings.width,
        )
        marks.append((timestamp, debit))

    reason = "hard_close_missing_mark"
    exit_debit = settings.width
    exact = next((item for item in marks if item[0].time() == settings.hard_close), None)
    if exact is not None:
        reason, exit_debit = "hard_close", exact[1]
    elif marks:
        timestamp, debit = marks[-1]
        hard_close_at = datetime.combine(timestamp.date(), settings.hard_close, timestamp.tzinfo)
        if (
            timedelta(0)
            <= hard_close_at - timestamp
            <= timedelta(minutes=settings.max_hard_close_mark_age_minutes)
        ):
            reason, exit_debit = "hard_close_last_mark", debit

    pnl = ((entry_credit - exit_debit) * HUNDRED - settings.fees_per_condor).quantize(CENT)
    return ConformalResult(
        trading_date,
        True,
        reason,
        True,
        forecast.upper_move,
        actual_move,
        contained,
        strikes[1],
        strikes[2],
        raw_credit,
        entry_credit,
        maximum_risk,
        exit_debit,
        pnl,
    )


def conformal_metrics(results: list[ConformalResult]) -> dict[str, object]:
    forecasted = [result for result in results if result.forecasted]
    traded = [result for result in results if result.entered]
    pnls = [result.pnl for result in traded]
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [pnl for pnl in pnls if pnl < 0]
    cumulative = Decimal("0")
    peak = Decimal("0")
    drawdown = Decimal("0")
    for pnl in pnls:
        cumulative += pnl
        peak = max(peak, cumulative)
        drawdown = min(drawdown, cumulative - peak)
    skips: dict[str, int] = {}
    exits: dict[str, int] = {}
    for result in results:
        target = exits if result.entered else skips
        target[result.reason] = target.get(result.reason, 0) + 1
    gross_profit = sum(wins, Decimal("0"))
    gross_loss = abs(sum(losses, Decimal("0")))
    return {
        "sessions": len(results),
        "forecast_sessions": len(forecasted),
        "contained_sessions": sum(result.contained for result in forecasted),
        "containment": (
            round(sum(result.contained for result in forecasted) / len(forecasted), 4)
            if forecasted
            else None
        ),
        "average_upper_move": (
            str(Decimal(str(mean(result.upper_move for result in forecasted))).quantize(CENT))
            if forecasted
            else None
        ),
        "trades": len(traded),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(pnls), 4) if pnls else None,
        "average_entry_credit": (
            str(Decimal(str(mean(result.entry_credit for result in traded))).quantize(CENT))
            if traded
            else None
        ),
        "average_maximum_risk": (
            str(Decimal(str(mean(result.maximum_risk for result in traded))).quantize(CENT))
            if traded
            else None
        ),
        "total_pnl": str(sum(pnls, Decimal("0")).quantize(CENT)),
        "average_pnl": (str(Decimal(str(mean(pnls))).quantize(CENT)) if pnls else None),
        "profit_factor": (str((gross_profit / gross_loss).quantize(RATIO)) if gross_loss else None),
        "max_drawdown": str(drawdown.quantize(CENT)),
        "worst_trade": str(min(pnls).quantize(CENT)) if pnls else None,
        "exits": exits,
        "skips": skips,
    }


def viable(report: dict[str, dict[str, object]]) -> bool:
    for split, minimum in (("train", 100), ("validation", 30)):
        metrics = report[split]
        stress = report[f"{split}_stress"]
        containment = metrics["containment"]
        if int(metrics["trades"]) < minimum:
            return False
        if containment is None or not 0.85 <= float(containment) <= 0.95:
            return False
        if metrics["average_pnl"] is None or Decimal(str(metrics["average_pnl"])) <= 0:
            return False
        if metrics["profit_factor"] is None or Decimal(str(metrics["profit_factor"])) < Decimal(
            "1.25"
        ):
            return False
        if stress["average_pnl"] is None or Decimal(str(stress["average_pnl"])) <= 0:
            return False
        if Decimal(str(metrics["max_drawdown"])) < Decimal("-500"):
            return False
    return True


def _settings_payload(settings: ConformalSettings) -> dict[str, object]:
    return {key: str(value) for key, value in asdict(settings).items()}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate the preregistered online conformal SPY 0DTE condor"
    )
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--oos-start", type=date.fromisoformat, required=True)
    parser.add_argument("--cache-dir", type=Path, default=Path("state/conformal-condor-cache"))
    parser.add_argument("--report-out", type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    config = Config.from_env()
    data = HistoricalData(config, args.cache_dir)
    sessions = data.stock_sessions(args.start, args.end, "SPY")
    dates = list(sessions)
    splits = research_splits(dates, args.oos_start)
    allowed = splits["train"] | splits["validation"]
    base = ConformalSettings()
    stress = replace(base, adverse_fill_per_leg=Decimal("0.01"))
    rows: dict[str, list[ConformalResult]] = {
        "train": [],
        "validation": [],
        "train_stress": [],
        "validation_stress": [],
    }
    prior_targets: list[Decimal] = []
    observations: list[ForecastObservation] = []
    fetched = 0
    for position, trading_date in enumerate(dates, start=1):
        if trading_date not in allowed:
            continue
        bars = sessions[trading_date]
        scale = morning_realized_scale(bars, base.entry_time)
        features = forecast_features(scale, prior_targets, base)
        forecast = conformal_forecast(observations, features, base)
        options: dict[str, list[PriceBar]] = {}
        entry = _at(bars, base.entry_time)
        if forecast is not None and entry is not None:
            day = date.fromisoformat(trading_date)
            symbols = sorted(conformal_symbols(day, entry.open, forecast.upper_move, base).values())
            options = data.option_bars(day, symbols)
            fetched += 1
            if fetched % 25 == 0:
                print(f"[{position}/{len(dates)}] {trading_date}", file=sys.stderr)
        split = "train" if trading_date in splits["train"] else "validation"
        rows[split].append(simulate_conformal_condor(trading_date, bars, options, forecast, base))
        rows[f"{split}_stress"].append(
            simulate_conformal_condor(trading_date, bars, options, forecast, stress)
        )
        target = session_target_move(bars, base.entry_time, base.hard_close)
        if features is not None and target is not None:
            observations.append(
                ForecastObservation(
                    trading_date,
                    features,
                    math.log(float(max(Decimal("0.01"), target))),
                )
            )
        if target is not None:
            prior_targets.append(target)

    metrics = {name: conformal_metrics(values) for name, values in rows.items()}
    combined_pnls = [
        result.pnl for split in ("train", "validation") for result in rows[split] if result.entered
    ]
    locked = sorted(splits["out_of_sample"])
    report = {
        "acceptance_rule": ACCEPTANCE_RULE,
        "data_limitation": DATA_LIMITATION,
        "development_passed": viable(metrics),
        "base_settings": _settings_payload(base),
        "stress_settings": _settings_payload(stress),
        "sessions_evaluated": len(allowed),
        "train_sessions": len(splits["train"]),
        "validation_sessions": len(splits["validation"]),
        "oos_revealed": False,
        "oos_start": locked[0],
        "oos_end": locked[-1],
        "oos_sessions": len(locked),
        "oos_option_cache_preexisting": [
            value for value in locked if (args.cache_dir / f"options-{value}.json").exists()
        ],
        "metrics": metrics,
        "bootstrap": (
            moving_block_bootstrap(combined_pnls)
            if len(combined_pnls) >= 30
            else {
                "paths": 0,
                "reason": "fewer than 30 trades; bootstrap would be misleading",
                "observed_trades": len(combined_pnls),
            }
        ),
    }
    encoded = json.dumps(report, indent=2) + "\n"
    if args.report_out:
        args.report_out.parent.mkdir(parents=True, exist_ok=True)
        args.report_out.write_text(encoded, encoding="utf-8")
        os.chmod(args.report_out, 0o600)
    print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
