"""Locked stock-only SPY constituent lead-lag rejection screen."""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from dataclasses import asdict, dataclass, replace
from datetime import date, time, timedelta
from decimal import ROUND_CEILING, Decimal
from pathlib import Path
from statistics import mean

from .config import Config
from .directional import PriceBar
from .directional_backtest import HistoricalData

RATIO = Decimal("0.000001")
BPS = Decimal("10000")
BOOTSTRAP_SEED = 20260821
BOOTSTRAP_PATHS = 10_000
BOOTSTRAP_BLOCK = 5

CONSTITUENTS = (
    "AAPL",
    "MSFT",
    "NVDA",
    "AMZN",
    "META",
    "GOOGL",
    "AVGO",
    "BRK.B",
    "JPM",
    "XOM",
    "LLY",
    "WMT",
)

ACCEPTANCE_RULE = (
    "At least 80 training and 25 validation signals; bullish and bearish "
    "signals in both; positive average net return, win rate above 52%, and "
    "profit factor at least 1.15 in both base splits; maximum drawdown no worse "
    "than -1.00% in either base split; and positive average net return in both "
    "two-basis-point stress splits. The final holdout remains undownloaded."
)

DATA_LIMITATION = (
    "The fixed equal-weight basket is a small survivorship-prone proxy for the "
    "paper's full constituent cross-section. Alpaca IEX bars reflect one "
    "exchange rather than consolidated SIP prices."
)


@dataclass(frozen=True)
class LeadSettings:
    observation_start: time = time(10, 55)
    observation_end: time = time(10, 59)
    entry_time: time = time(11, 0)
    exit_time: time = time(11, 30)
    lookback_sessions: int = 60
    threshold_percentile: Decimal = Decimal("0.70")
    round_trip_cost: Decimal = Decimal("0.0001")


@dataclass(frozen=True)
class SpyObservation:
    observed_return: Decimal
    entry_price: Decimal
    exit_price: Decimal


@dataclass(frozen=True)
class LeadResult:
    trading_date: str
    signaled: bool
    reason: str
    direction: str = "none"
    residual: Decimal = Decimal("0")
    threshold: Decimal = Decimal("0")
    gross_return: Decimal = Decimal("0")
    net_return: Decimal = Decimal("0")


def _at(bars: list[PriceBar], moment: time) -> PriceBar | None:
    return next((bar for bar in bars if bar.timestamp.time() == moment), None)


def observed_return(bars: list[PriceBar], settings: LeadSettings) -> Decimal | None:
    opened = _at(bars, settings.observation_start)
    closed = _at(bars, settings.observation_end)
    if opened is None or closed is None or opened.open <= 0:
        return None
    return ((closed.close / opened.open) - Decimal("1")).quantize(RATIO)


def member_return_history(
    sessions: dict[str, list[PriceBar]], settings: LeadSettings
) -> dict[str, Decimal]:
    result: dict[str, Decimal] = {}
    for trading_date, bars in sessions.items():
        value = observed_return(bars, settings)
        if value is not None:
            result[trading_date] = value
    return result


def spy_observation_history(
    sessions: dict[str, list[PriceBar]], settings: LeadSettings
) -> dict[str, SpyObservation]:
    result: dict[str, SpyObservation] = {}
    for trading_date, bars in sessions.items():
        value = observed_return(bars, settings)
        entry = _at(bars, settings.entry_time)
        exit_bar = _at(bars, settings.exit_time)
        if value is None or entry is None or exit_bar is None or entry.open <= 0:
            continue
        result[trading_date] = SpyObservation(value, entry.open, exit_bar.open)
    return result


def lead_residual(
    trading_date: str,
    spy: dict[str, SpyObservation],
    members: dict[str, dict[str, Decimal]],
    constituents: tuple[str, ...] = CONSTITUENTS,
) -> Decimal | None:
    observation = spy.get(trading_date)
    if observation is None:
        return None
    values: list[Decimal] = []
    for symbol in constituents:
        value = members.get(symbol, {}).get(trading_date)
        if value is None:
            return None
        values.append(value)
    basket = Decimal(str(mean(values)))
    return (basket - observation.observed_return).quantize(RATIO)


def nearest_rank_percentile(
    values: list[Decimal], percentile: Decimal
) -> Decimal:
    if not values:
        raise ValueError("percentile needs at least one value")
    if not Decimal("0") < percentile <= Decimal("1"):
        raise ValueError("percentile must be in (0, 1]")
    ordered = sorted(values)
    rank = int(
        (percentile * Decimal(len(ordered))).to_integral_value(rounding=ROUND_CEILING)
    )
    return ordered[rank - 1]


def simulate_lead_session(
    trading_date: str,
    spy: dict[str, SpyObservation],
    members: dict[str, dict[str, Decimal]],
    prior_absolute_residuals: list[Decimal],
    settings: LeadSettings,
) -> LeadResult:
    residual = lead_residual(trading_date, spy, members)
    if residual is None:
        return LeadResult(trading_date, False, "one or more exact bars are missing")
    if len(prior_absolute_residuals) < settings.lookback_sessions:
        return LeadResult(
            trading_date, False, "insufficient trailing residual history", residual=residual
        )
    threshold = nearest_rank_percentile(
        prior_absolute_residuals[-settings.lookback_sessions :],
        settings.threshold_percentile,
    )
    if abs(residual) < threshold or residual == 0:
        return LeadResult(
            trading_date,
            False,
            "lead residual is below trailing threshold",
            residual=residual,
            threshold=threshold,
        )
    observation = spy[trading_date]
    direction = "bullish" if residual > 0 else "bearish"
    multiplier = Decimal("1") if direction == "bullish" else Decimal("-1")
    underlying_return = observation.exit_price / observation.entry_price - Decimal("1")
    gross = (multiplier * underlying_return).quantize(RATIO)
    net = (gross - settings.round_trip_cost).quantize(RATIO)
    return LeadResult(
        trading_date,
        True,
        "thirty_minute_exit",
        direction,
        residual,
        threshold,
        gross,
        net,
    )


def chronological_splits(dates: list[str]) -> dict[str, set[str]]:
    if len(dates) < 10:
        raise ValueError("at least 10 development sessions are required")
    train_end = int(len(dates) * 0.75)
    return {
        "train": set(dates[:train_end]),
        "validation": set(dates[train_end:]),
    }


def lead_metrics(results: list[LeadResult]) -> dict[str, object]:
    signaled = [result for result in results if result.signaled]
    values = [result.net_return for result in signaled]
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value < 0]
    cumulative = Decimal("0")
    peak = Decimal("0")
    max_drawdown = Decimal("0")
    for value in values:
        cumulative += value
        peak = max(peak, cumulative)
        max_drawdown = min(max_drawdown, cumulative - peak)
    gross_profit = sum(wins, Decimal("0"))
    gross_loss = abs(sum(losses, Decimal("0")))
    skips: dict[str, int] = {}
    for result in results:
        if not result.signaled:
            skips[result.reason] = skips.get(result.reason, 0) + 1
    return {
        "sessions": len(results),
        "signals": len(signaled),
        "bullish_signals": sum(r.direction == "bullish" for r in signaled),
        "bearish_signals": sum(r.direction == "bearish" for r in signaled),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(values), 4) if values else None,
        "average_gross_bps": (
            str((Decimal(str(mean(r.gross_return for r in signaled))) * BPS).quantize(Decimal("0.01")))
            if signaled
            else None
        ),
        "average_net_bps": (
            str((Decimal(str(mean(values))) * BPS).quantize(Decimal("0.01")))
            if values
            else None
        ),
        "total_net_return_percent": str(
            (sum(values, Decimal("0")) * Decimal("100")).quantize(Decimal("0.01"))
        ),
        "profit_factor": (
            str((gross_profit / gross_loss).quantize(Decimal("0.0001")))
            if gross_loss
            else None
        ),
        "max_drawdown_percent": str(
            (max_drawdown * Decimal("100")).quantize(Decimal("0.01"))
        ),
        "skips": skips,
    }


def viable(report: dict[str, dict[str, object]]) -> bool:
    for split, minimum in (("train", 80), ("validation", 25)):
        metrics = report[split]
        stress = report[f"{split}_stress"]
        if int(metrics["signals"]) < minimum:
            return False
        if int(metrics["bullish_signals"]) == 0 or int(metrics["bearish_signals"]) == 0:
            return False
        average = metrics["average_net_bps"]
        factor = metrics["profit_factor"]
        stress_average = stress["average_net_bps"]
        if average is None or Decimal(str(average)) <= 0:
            return False
        if metrics["win_rate"] is None or Decimal(str(metrics["win_rate"])) <= Decimal("0.52"):
            return False
        if factor is None or Decimal(str(factor)) < Decimal("1.15"):
            return False
        if Decimal(str(metrics["max_drawdown_percent"])) < Decimal("-1.00"):
            return False
        if stress_average is None or Decimal(str(stress_average)) <= 0:
            return False
    return True


def block_bootstrap(
    returns: list[Decimal],
    *,
    paths: int = BOOTSTRAP_PATHS,
    block_length: int = BOOTSTRAP_BLOCK,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, object]:
    if len(returns) < block_length:
        return {"paths": 0, "reason": "too few signals"}
    randomizer = random.Random(seed)
    positive = 0
    means: list[Decimal] = []
    for _ in range(paths):
        path: list[Decimal] = []
        while len(path) < len(returns):
            start = randomizer.randrange(len(returns))
            for offset in range(block_length):
                path.append(returns[(start + offset) % len(returns)])
                if len(path) == len(returns):
                    break
        value = Decimal(str(mean(path)))
        means.append(value)
        positive += value > 0
    ordered = sorted(means)

    def at(fraction: Decimal) -> str:
        index = int((Decimal(len(ordered) - 1) * fraction).to_integral_value())
        return str((ordered[index] * BPS).quantize(Decimal("0.01")))

    return {
        "seed": seed,
        "paths": paths,
        "signals_per_path": len(returns),
        "block_length": block_length,
        "probability_positive_mean": round(positive / paths, 4),
        "average_bps_p05": at(Decimal("0.05")),
        "average_bps_median": at(Decimal("0.50")),
        "average_bps_p95": at(Decimal("0.95")),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate preregistered SPY constituent lead-lag signal"
    )
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--oos-start", type=date.fromisoformat, required=True)
    parser.add_argument("--oos-end", type=date.fromisoformat, required=True)
    parser.add_argument(
        "--cache-dir", type=Path, default=Path("state/constituent-lead-cache")
    )
    parser.add_argument("--report-out", type=Path)
    return parser


def _settings_payload(settings: LeadSettings) -> dict[str, object]:
    return {key: str(value) for key, value in asdict(settings).items()}


def main() -> int:
    args = _parser().parse_args()
    development_end = args.oos_start - timedelta(days=1)
    config = Config.from_env()
    data = HistoricalData(config, args.cache_dir)
    base = LeadSettings()
    stress = replace(base, round_trip_cost=Decimal("0.0002"))

    member_returns: dict[str, dict[str, Decimal]] = {}
    for position, symbol in enumerate(CONSTITUENTS, start=1):
        sessions = data.stock_sessions(args.start, development_end, symbol)
        member_returns[symbol] = member_return_history(sessions, base)
        print(f"[{position}/{len(CONSTITUENTS) + 1}] loaded {symbol}", file=sys.stderr)
        del sessions
    spy_sessions = data.stock_sessions(args.start, development_end, "SPY")
    spy = spy_observation_history(spy_sessions, base)
    dates = sorted(spy)
    print(f"[{len(CONSTITUENTS) + 1}/{len(CONSTITUENTS) + 1}] loaded SPY", file=sys.stderr)
    del spy_sessions

    splits = chronological_splits(dates)
    rows: dict[str, list[LeadResult]] = {
        "train": [],
        "validation": [],
        "train_stress": [],
        "validation_stress": [],
    }
    prior_absolute: list[Decimal] = []
    for trading_date in dates:
        split = "train" if trading_date in splits["train"] else "validation"
        rows[split].append(
            simulate_lead_session(
                trading_date, spy, member_returns, prior_absolute, base
            )
        )
        rows[f"{split}_stress"].append(
            simulate_lead_session(
                trading_date, spy, member_returns, prior_absolute, stress
            )
        )
        residual = lead_residual(trading_date, spy, member_returns)
        if residual is not None:
            prior_absolute.append(abs(residual))

    metrics = {name: lead_metrics(values) for name, values in rows.items()}
    combined = [
        result.net_return
        for split in ("train", "validation")
        for result in rows[split]
        if result.signaled
    ]
    report = {
        "acceptance_rule": ACCEPTANCE_RULE,
        "data_limitation": DATA_LIMITATION,
        "development_passed": viable(metrics),
        "constituents": list(CONSTITUENTS),
        "base_settings": _settings_payload(base),
        "stress_settings": _settings_payload(stress),
        "development_start": dates[0],
        "development_end": dates[-1],
        "development_sessions": len(dates),
        "train_sessions": len(splits["train"]),
        "validation_sessions": len(splits["validation"]),
        "oos_revealed": False,
        "oos_downloaded": False,
        "oos_start": args.oos_start.isoformat(),
        "oos_end": args.oos_end.isoformat(),
        "oos_sessions_expected": 60,
        "cache_files": sorted(path.name for path in args.cache_dir.glob("*.json")),
        "metrics": metrics,
        "bootstrap": block_bootstrap(combined),
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
