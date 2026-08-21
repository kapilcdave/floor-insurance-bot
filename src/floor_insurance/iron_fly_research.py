"""Locked historical simulation for a variance-premium SPY 0DTE iron fly.

Option bars are trade aggregates, not synchronized NBBO quotes. Explicit
adverse fills make this a rejection screen rather than proof of executable
multi-leg prices.
"""

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

CENT = Decimal("0.01")
HUNDRED = Decimal("100")
RATIO = Decimal("0.0001")

ACCEPTANCE_RULE = (
    "At least 100 training and 30 validation trades; positive average P&L and "
    "profit factor at least 1.25 on both base splits; maximum drawdown no worse "
    "than -$500 on either base split; and positive average P&L on both stress "
    "splits. The final chronological holdout remains sealed."
)

DATA_LIMITATION = (
    "Alpaca one-minute historical option bars are trade aggregates, not "
    "contemporaneous synchronized NBBO quotes. Explicit four-leg adverse fills "
    "make this a rejection screen; they cannot prove a live iron-fly fill."
)


@dataclass(frozen=True)
class IronFlySettings:
    symbol: str = "SPY"
    wing_width: Decimal = Decimal("2")
    lookback_sessions: int = 20
    richness_multiple: Decimal = Decimal("1.25")
    entry_time: time = time(11, 0)
    hard_close: time = time(15, 0)
    adverse_fill_per_leg: Decimal = Decimal("0.02")
    fees_per_fly: Decimal = Decimal("0.20")
    maximum_risk_dollars: Decimal = Decimal("100")
    max_hard_close_mark_age_minutes: int = 5


@dataclass(frozen=True)
class IronFlyResult:
    trading_date: str
    entered: bool
    reason: str
    center_strike: Decimal = Decimal("0")
    realized_reference: Decimal = Decimal("0")
    implied_move_proxy: Decimal = Decimal("0")
    richness_ratio: Decimal = Decimal("0")
    raw_entry_credit: Decimal = Decimal("0")
    entry_credit: Decimal = Decimal("0")
    maximum_risk: Decimal = Decimal("0")
    exit_debit: Decimal = Decimal("0")
    pnl: Decimal = Decimal("0")
    wing_width: Decimal = Decimal("0")


def _at(bars: list[PriceBar], moment: time) -> PriceBar | None:
    return next((bar for bar in bars if bar.timestamp.time() == moment), None)


def center_strike(spot: Decimal) -> Decimal:
    return spot.to_integral_value(rounding=ROUND_HALF_UP)


def occ_option_for(
    symbol: str, expiration: date, option_type: str, strike: Decimal
) -> str:
    kind = option_type.upper()
    if kind not in {"P", "C"}:
        raise ValueError("option_type must be P or C")
    scaled = int(strike * Decimal("1000"))
    return f"{symbol}{expiration:%y%m%d}{kind}{scaled:08d}"


def iron_fly_symbols(
    trading_date: date, center: Decimal, settings: IronFlySettings
) -> dict[str, str]:
    return {
        "long_put": occ_option_for(
            settings.symbol, trading_date, "P", center - settings.wing_width
        ),
        "short_put": occ_option_for(settings.symbol, trading_date, "P", center),
        "short_call": occ_option_for(settings.symbol, trading_date, "C", center),
        "long_call": occ_option_for(
            settings.symbol, trading_date, "C", center + settings.wing_width
        ),
    }


def session_absolute_move(
    bars: list[PriceBar], entry_time: time = time(11, 0)
) -> Decimal | None:
    entry = _at(bars, entry_time)
    closes = [bar for bar in bars if entry_time < bar.timestamp.time() <= time(16, 0)]
    if entry is None or not closes:
        return None
    return abs(closes[-1].close - entry.open).quantize(CENT)


def trailing_realized_reference(
    prior_moves: list[Decimal], lookback_sessions: int
) -> Decimal | None:
    if lookback_sessions < 1:
        raise ValueError("lookback_sessions must be positive")
    if len(prior_moves) < lookback_sessions:
        return None
    values = prior_moves[-lookback_sessions:]
    return Decimal(str(mean(values))).quantize(CENT)


def _bounded_debit(value: Decimal, width: Decimal) -> Decimal:
    return max(Decimal("0"), min(width, value)).quantize(
        CENT, rounding=ROUND_CEILING
    )


def simulate_iron_fly(
    trading_date: str,
    underlying_bars: list[PriceBar],
    option_bars: dict[str, list[PriceBar]],
    realized_reference: Decimal | None,
    settings: IronFlySettings,
) -> IronFlyResult:
    if realized_reference is None:
        return IronFlyResult(trading_date, False, "insufficient realized-move history")
    if realized_reference <= 0:
        return IronFlyResult(trading_date, False, "realized-move reference is not positive")

    underlying_entry = _at(underlying_bars, settings.entry_time)
    if underlying_entry is None:
        return IronFlyResult(
            trading_date,
            False,
            "underlying entry bar missing",
            realized_reference=realized_reference,
        )

    center = center_strike(underlying_entry.open)
    symbols = iron_fly_symbols(date.fromisoformat(trading_date), center, settings)
    legs = {name: option_bars.get(symbol, []) for name, symbol in symbols.items()}
    entries = {name: _at(bars, settings.entry_time) for name, bars in legs.items()}
    if any(bar is None for bar in entries.values()):
        return IronFlyResult(
            trading_date,
            False,
            "one or more legs lack an exact entry bar",
            center,
            realized_reference,
        )

    long_put = entries["long_put"]
    short_put = entries["short_put"]
    short_call = entries["short_call"]
    long_call = entries["long_call"]
    assert long_put and short_put and short_call and long_call
    implied = (short_put.open + short_call.open).quantize(CENT)
    richness = (implied / realized_reference).quantize(RATIO)
    if richness < settings.richness_multiple:
        return IronFlyResult(
            trading_date,
            False,
            "implied move is below richness gate",
            center,
            realized_reference,
            implied,
            richness,
        )

    raw_credit = (
        short_put.open + short_call.open - long_put.open - long_call.open
    ).quantize(CENT, rounding=ROUND_FLOOR)
    entry_credit = (
        raw_credit - settings.adverse_fill_per_leg * Decimal("4")
    ).quantize(CENT, rounding=ROUND_FLOOR)
    if entry_credit <= 0 or entry_credit >= settings.wing_width:
        return IronFlyResult(
            trading_date,
            False,
            "modeled entry credit is outside the wing width",
            center,
            realized_reference,
            implied,
            richness,
            raw_credit,
            entry_credit,
        )
    maximum_risk = (
        (settings.wing_width - entry_credit) * HUNDRED + settings.fees_per_fly
    ).quantize(CENT)
    if maximum_risk > settings.maximum_risk_dollars:
        return IronFlyResult(
            trading_date,
            False,
            "maximum risk exceeds $100 gate",
            center,
            realized_reference,
            implied,
            richness,
            raw_credit,
            entry_credit,
            maximum_risk,
        )

    by_timestamp = {
        name: {bar.timestamp: bar for bar in bars} for name, bars in legs.items()
    }
    synchronized = sorted(
        set(by_timestamp["long_put"])
        & set(by_timestamp["short_put"])
        & set(by_timestamp["short_call"])
        & set(by_timestamp["long_call"])
    )
    exit_marks: list[tuple[datetime, Decimal]] = []
    exit_slippage = settings.adverse_fill_per_leg * Decimal("4")
    for timestamp in synchronized:
        if timestamp.time() <= settings.entry_time or timestamp.time() > settings.hard_close:
            continue
        debit = _bounded_debit(
            by_timestamp["short_put"][timestamp].close
            + by_timestamp["short_call"][timestamp].close
            - by_timestamp["long_put"][timestamp].close
            - by_timestamp["long_call"][timestamp].close
            + exit_slippage,
            settings.wing_width,
        )
        exit_marks.append((timestamp, debit))

    reason = "hard_close_missing_mark"
    exit_debit = settings.wing_width
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
        (entry_credit - exit_debit) * HUNDRED - settings.fees_per_fly
    ).quantize(CENT)
    return IronFlyResult(
        trading_date,
        True,
        reason,
        center,
        realized_reference,
        implied,
        richness,
        raw_credit,
        entry_credit,
        maximum_risk,
        exit_debit,
        pnl,
        settings.wing_width,
    )


def iron_fly_metrics(results: list[IronFlyResult]) -> dict[str, object]:
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
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(pnls), 4) if pnls else None,
        "average_implied_move": (
            str(Decimal(str(mean(r.implied_move_proxy for r in traded))).quantize(CENT))
            if traded
            else None
        ),
        "average_realized_reference": (
            str(Decimal(str(mean(r.realized_reference for r in traded))).quantize(CENT))
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


def _settings_payload(settings: IronFlySettings) -> dict[str, object]:
    return {key: str(value) for key, value in asdict(settings).items()}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate the preregistered SPY 0DTE variance-premium iron fly"
    )
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--oos-start", type=date.fromisoformat, required=True)
    parser.add_argument("--cache-dir", type=Path, default=Path("state/iron-fly-cache"))
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
    base = IronFlySettings()
    stress = replace(base, adverse_fill_per_leg=Decimal("0.03"))
    rows: dict[str, list[IronFlyResult]] = {
        "train": [],
        "validation": [],
        "train_stress": [],
        "validation_stress": [],
    }
    prior_moves: list[Decimal] = []
    fetched = 0
    for position, trading_date in enumerate(dates, start=1):
        if trading_date not in allowed:
            continue
        underlying = sessions[trading_date]
        reference = trailing_realized_reference(prior_moves, base.lookback_sessions)
        options: dict[str, list[PriceBar]] = {}
        entry = _at(underlying, base.entry_time)
        if reference is not None and entry is not None:
            day = date.fromisoformat(trading_date)
            names = iron_fly_symbols(day, center_strike(entry.open), base)
            options = data.option_bars(day, sorted(names.values()))
            fetched += 1
            if fetched % 25 == 0:
                print(f"[{position}/{len(dates)}] {trading_date}", file=sys.stderr)
        split = "train" if trading_date in splits["train"] else "validation"
        rows[split].append(
            simulate_iron_fly(trading_date, underlying, options, reference, base)
        )
        rows[f"{split}_stress"].append(
            simulate_iron_fly(trading_date, underlying, options, reference, stress)
        )
        move = session_absolute_move(underlying, base.entry_time)
        if move is not None:
            prior_moves.append(move)

    metrics = {name: iron_fly_metrics(values) for name, values in rows.items()}
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
