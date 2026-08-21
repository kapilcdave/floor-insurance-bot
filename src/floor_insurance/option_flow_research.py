"""Locked opening option-flow SPY 0DTE credit-spread research."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, time, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
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
    "splits; and both bullish and bearish trades in training and validation. "
    "The final chronological holdout remains sealed."
)

DATA_LIMITATION = (
    "The free indicative option bars are modified delayed trade derivatives. "
    "Bar direction is not buyer/seller initiation, so this tests an auditable "
    "proxy rather than replicating signed option order flow."
)


@dataclass(frozen=True)
class OptionFlowSettings:
    symbol: str = "SPY"
    width: Decimal = Decimal("1")
    signal_start: time = time(9, 30)
    signal_end: time = time(10, 0)
    entry_time: time = time(10, 0)
    hard_close: time = time(15, 0)
    signal_radius: int = 1
    minimum_signal_volume: Decimal = Decimal("1000")
    flow_threshold: Decimal = Decimal("0.20")
    adverse_fill_per_leg: Decimal = Decimal("0.02")
    fees_per_spread: Decimal = Decimal("0.10")
    maximum_risk_dollars: Decimal = Decimal("100")
    max_hard_close_mark_age_minutes: int = 5


@dataclass(frozen=True)
class OptionFlowResult:
    trading_date: str
    entered: bool
    reason: str
    direction: str = "none"
    flow_score: Decimal = Decimal("0")
    signal_volume: Decimal = Decimal("0")
    short_strike: Decimal = Decimal("0")
    long_strike: Decimal = Decimal("0")
    raw_entry_credit: Decimal = Decimal("0")
    entry_credit: Decimal = Decimal("0")
    maximum_risk: Decimal = Decimal("0")
    exit_debit: Decimal = Decimal("0")
    pnl: Decimal = Decimal("0")
    width: Decimal = Decimal("0")


def _at(bars: list[PriceBar], moment: time) -> PriceBar | None:
    return next((bar for bar in bars if bar.timestamp.time() == moment), None)


def flow_center(spot: Decimal) -> Decimal:
    return spot.to_integral_value(rounding=ROUND_HALF_UP)


def signal_contracts(
    trading_date: date, center: Decimal, settings: OptionFlowSettings
) -> dict[str, str]:
    return {
        f"{kind}_{offset:+d}": occ_option_for(
            settings.symbol,
            trading_date,
            kind,
            center + Decimal(offset),
        )
        for kind in ("C", "P")
        for offset in range(-settings.signal_radius, settings.signal_radius + 1)
    }


def spread_contracts(
    trading_date: date,
    spot: Decimal,
    direction: str,
    settings: OptionFlowSettings,
) -> tuple[Decimal, Decimal, str, str]:
    if direction == "bullish":
        short = spot.to_integral_value(rounding=ROUND_FLOOR)
        long = short - settings.width
        kind = "P"
    elif direction == "bearish":
        short = spot.to_integral_value(rounding=ROUND_CEILING)
        long = short + settings.width
        kind = "C"
    else:
        raise ValueError("direction must be bullish or bearish")
    return (
        short,
        long,
        occ_option_for(settings.symbol, trading_date, kind, short),
        occ_option_for(settings.symbol, trading_date, kind, long),
    )


def required_symbols(
    trading_date: date, spot: Decimal, settings: OptionFlowSettings
) -> list[str]:
    center = flow_center(spot)
    symbols = set(signal_contracts(trading_date, center, settings).values())
    for direction in ("bullish", "bearish"):
        *_, short_symbol, long_symbol = spread_contracts(
            trading_date, spot, direction, settings
        )
        symbols.update((short_symbol, long_symbol))
    return sorted(symbols)


def option_flow_score(
    option_bars: dict[str, list[PriceBar]],
    contracts: dict[str, str],
    settings: OptionFlowSettings,
) -> tuple[Decimal | None, Decimal]:
    signed = Decimal("0")
    total = Decimal("0")
    for label, symbol in contracts.items():
        call = label.startswith("C_")
        for bar in option_bars.get(symbol, []):
            moment = bar.timestamp.time()
            if not (settings.signal_start <= moment < settings.signal_end):
                continue
            volume = max(Decimal("0"), bar.volume)
            total += volume
            if bar.close == bar.open:
                continue
            direction = Decimal("1") if bar.close > bar.open else Decimal("-1")
            signed += direction * volume * (Decimal("1") if call else Decimal("-1"))
    if total <= 0:
        return None, total
    return (signed / total).quantize(RATIO), total


def _bounded_debit(value: Decimal, width: Decimal) -> Decimal:
    return max(Decimal("0"), min(width, value)).quantize(
        CENT, rounding=ROUND_CEILING
    )


def simulate_option_flow(
    trading_date: str,
    underlying_bars: list[PriceBar],
    option_bars: dict[str, list[PriceBar]],
    settings: OptionFlowSettings,
) -> OptionFlowResult:
    entry = _at(underlying_bars, settings.entry_time)
    if entry is None:
        return OptionFlowResult(trading_date, False, "underlying entry bar missing")
    day = date.fromisoformat(trading_date)
    contracts = signal_contracts(day, flow_center(entry.open), settings)
    score, volume = option_flow_score(option_bars, contracts, settings)
    if score is None or volume < settings.minimum_signal_volume:
        return OptionFlowResult(
            trading_date,
            False,
            "signal volume is below minimum",
            flow_score=score or Decimal("0"),
            signal_volume=volume,
        )
    if abs(score) < settings.flow_threshold:
        return OptionFlowResult(
            trading_date,
            False,
            "absolute flow score is below threshold",
            flow_score=score,
            signal_volume=volume,
        )

    direction = "bullish" if score > 0 else "bearish"
    short_strike, long_strike, short_symbol, long_symbol = spread_contracts(
        day, entry.open, direction, settings
    )
    short_bars = option_bars.get(short_symbol, [])
    long_bars = option_bars.get(long_symbol, [])
    short_entry = _at(short_bars, settings.entry_time)
    long_entry = _at(long_bars, settings.entry_time)
    if short_entry is None or long_entry is None:
        return OptionFlowResult(
            trading_date,
            False,
            "one or both spread legs lack an exact entry bar",
            direction,
            score,
            volume,
            short_strike,
            long_strike,
        )

    raw_credit = (short_entry.open - long_entry.open).quantize(
        CENT, rounding=ROUND_FLOOR
    )
    entry_credit = (
        raw_credit - settings.adverse_fill_per_leg * Decimal("2")
    ).quantize(CENT, rounding=ROUND_FLOOR)
    maximum_risk = (
        (settings.width - entry_credit) * HUNDRED + settings.fees_per_spread
    ).quantize(CENT)
    if (
        entry_credit <= 0
        or entry_credit >= settings.width
        or maximum_risk > settings.maximum_risk_dollars
    ):
        return OptionFlowResult(
            trading_date,
            False,
            "entry credit or maximum risk failed",
            direction,
            score,
            volume,
            short_strike,
            long_strike,
            raw_credit,
            entry_credit,
            maximum_risk,
        )

    short_by_time = {bar.timestamp: bar for bar in short_bars}
    long_by_time = {bar.timestamp: bar for bar in long_bars}
    timestamps = sorted(set(short_by_time) & set(long_by_time))
    exit_marks: list[tuple[datetime, Decimal]] = []
    for timestamp in timestamps:
        if timestamp.time() <= settings.entry_time or timestamp.time() > settings.hard_close:
            continue
        debit = _bounded_debit(
            short_by_time[timestamp].close
            - long_by_time[timestamp].close
            + settings.adverse_fill_per_leg * Decimal("2"),
            settings.width,
        )
        exit_marks.append((timestamp, debit))

    reason = "hard_close_missing_mark"
    exit_debit = settings.width
    exact = next(
        (item for item in exit_marks if item[0].time() == settings.hard_close), None
    )
    if exact is not None:
        reason, exit_debit = "hard_close", exact[1]
    elif exit_marks:
        timestamp, debit = exit_marks[-1]
        hard_close_at = datetime.combine(
            timestamp.date(), settings.hard_close, timestamp.tzinfo
        )
        age = hard_close_at - timestamp
        if timedelta(0) <= age <= timedelta(
            minutes=settings.max_hard_close_mark_age_minutes
        ):
            reason, exit_debit = "hard_close_last_mark", debit

    pnl = (
        (entry_credit - exit_debit) * HUNDRED - settings.fees_per_spread
    ).quantize(CENT)
    return OptionFlowResult(
        trading_date,
        True,
        reason,
        direction,
        score,
        volume,
        short_strike,
        long_strike,
        raw_credit,
        entry_credit,
        maximum_risk,
        exit_debit,
        pnl,
        settings.width,
    )


def option_flow_metrics(results: list[OptionFlowResult]) -> dict[str, object]:
    traded = [result for result in results if result.entered]
    pnls = [result.pnl for result in traded]
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [pnl for pnl in pnls if pnl < 0]
    cumulative = Decimal("0")
    peak = Decimal("0")
    max_drawdown = Decimal("0")
    for pnl in pnls:
        cumulative += pnl
        peak = max(peak, cumulative)
        max_drawdown = min(max_drawdown, cumulative - peak)
    gross_profit = sum(wins, Decimal("0"))
    gross_loss = abs(sum(losses, Decimal("0")))
    skips: dict[str, int] = {}
    exits: dict[str, int] = {}
    for result in results:
        target = exits if result.entered else skips
        target[result.reason] = target.get(result.reason, 0) + 1
    return {
        "sessions": len(results),
        "trades": len(traded),
        "bullish_trades": sum(r.direction == "bullish" for r in traded),
        "bearish_trades": sum(r.direction == "bearish" for r in traded),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(pnls), 4) if pnls else None,
        "average_absolute_flow_score": (
            str(Decimal(str(mean(abs(r.flow_score) for r in traded))).quantize(RATIO))
            if traded
            else None
        ),
        "average_entry_credit": (
            str(Decimal(str(mean(r.entry_credit for r in traded))).quantize(CENT))
            if traded
            else None
        ),
        "average_maximum_risk": (
            str(Decimal(str(mean(r.maximum_risk for r in traded))).quantize(CENT))
            if traded
            else None
        ),
        "total_pnl": str(sum(pnls, Decimal("0")).quantize(CENT)),
        "average_pnl": (
            str(Decimal(str(mean(pnls))).quantize(CENT)) if pnls else None
        ),
        "profit_factor": (
            str((gross_profit / gross_loss).quantize(RATIO)) if gross_loss else None
        ),
        "max_drawdown": str(max_drawdown.quantize(CENT)),
        "worst_trade": str(min(pnls).quantize(CENT)) if pnls else None,
        "exits": exits,
        "skips": skips,
    }


def viable(report: dict[str, dict[str, object]]) -> bool:
    for split, minimum in (("train", 100), ("validation", 30)):
        metrics = report[split]
        stress = report[f"{split}_stress"]
        if int(metrics["trades"]) < minimum:
            return False
        if int(metrics["bullish_trades"]) == 0 or int(metrics["bearish_trades"]) == 0:
            return False
        average = metrics["average_pnl"]
        factor = metrics["profit_factor"]
        stress_average = stress["average_pnl"]
        if average is None or Decimal(str(average)) <= 0:
            return False
        if factor is None or Decimal(str(factor)) < Decimal("1.25"):
            return False
        if stress_average is None or Decimal(str(stress_average)) <= 0:
            return False
        if Decimal(str(metrics["max_drawdown"])) < Decimal("-500"):
            return False
    return True


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate preregistered SPY opening option-flow credit spreads"
    )
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--oos-start", type=date.fromisoformat, required=True)
    parser.add_argument(
        "--cache-dir", type=Path, default=Path("state/option-flow-cache")
    )
    parser.add_argument("--report-out", type=Path)
    return parser


def _settings_payload(settings: OptionFlowSettings) -> dict[str, object]:
    return {key: str(value) for key, value in asdict(settings).items()}


def main() -> int:
    args = _parser().parse_args()
    config = Config.from_env()
    data = HistoricalData(config, args.cache_dir)
    sessions = data.stock_sessions(args.start, args.end, "SPY")
    dates = list(sessions)
    splits = research_splits(dates, args.oos_start)
    allowed = splits["train"] | splits["validation"]
    base = OptionFlowSettings()
    stress = replace(base, adverse_fill_per_leg=Decimal("0.03"))
    rows: dict[str, list[OptionFlowResult]] = {
        "train": [],
        "validation": [],
        "train_stress": [],
        "validation_stress": [],
    }
    fetched = 0
    for position, trading_date in enumerate(dates, start=1):
        if trading_date not in allowed:
            continue
        underlying = sessions[trading_date]
        entry = _at(underlying, base.entry_time)
        options: dict[str, list[PriceBar]] = {}
        if entry is not None:
            day = date.fromisoformat(trading_date)
            options = data.option_bars(
                day, required_symbols(day, entry.open, base)
            )
            fetched += 1
            if fetched % 25 == 0:
                print(f"[{position}/{len(dates)}] {trading_date}", file=sys.stderr)
        split = "train" if trading_date in splits["train"] else "validation"
        rows[split].append(
            simulate_option_flow(trading_date, underlying, options, base)
        )
        rows[f"{split}_stress"].append(
            simulate_option_flow(trading_date, underlying, options, stress)
        )

    metrics = {name: option_flow_metrics(values) for name, values in rows.items()}
    combined_pnls = [
        result.pnl
        for split in ("train", "validation")
        for result in rows[split]
        if result.entered
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
            value
            for value in locked
            if (args.cache_dir / f"options-{value}.json").exists()
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
