"""Preregistered implied-move SPY 0DTE iron-condor rejection screen."""

from __future__ import annotations

import argparse
import json
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
from .implied_move_research import atm_straddle_symbols, implied_move_at
from .iron_fly_research import (
    occ_option_for,
    session_absolute_move,
    trailing_realized_reference,
)

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
    "Alpaca one-minute historical option bars are indicative trade aggregates, "
    "not synchronized executable OPRA quotes. Explicit adverse fills make this "
    "a rejection screen rather than proof of a live multi-leg fill."
)


@dataclass(frozen=True)
class CondorSettings:
    symbol: str = "SPY"
    width: Decimal = Decimal("1")
    short_move_multiple: Decimal = Decimal("0.75")
    lookback_sessions: int = 20
    richness_multiple: Decimal = Decimal("1.25")
    minimum_credit: Decimal = Decimal("0.15")
    entry_time: time = time(11, 0)
    hard_close: time = time(15, 0)
    adverse_fill_per_leg: Decimal = Decimal("0.01")
    fees_per_condor: Decimal = Decimal("0.20")
    maximum_risk_dollars: Decimal = Decimal("100")
    max_hard_close_mark_age_minutes: int = 5


@dataclass(frozen=True)
class CondorResult:
    trading_date: str
    entered: bool
    reason: str
    short_put_strike: Decimal = Decimal("0")
    short_call_strike: Decimal = Decimal("0")
    realized_reference: Decimal = Decimal("0")
    implied_move_proxy: Decimal = Decimal("0")
    richness_ratio: Decimal = Decimal("0")
    raw_entry_credit: Decimal = Decimal("0")
    entry_credit: Decimal = Decimal("0")
    maximum_risk: Decimal = Decimal("0")
    exit_debit: Decimal = Decimal("0")
    pnl: Decimal = Decimal("0")


def _at(bars: list[PriceBar], moment: time) -> PriceBar | None:
    return next((bar for bar in bars if bar.timestamp.time() == moment), None)


def condor_strikes(
    spot: Decimal, implied_move: Decimal, settings: CondorSettings
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    distance = implied_move * settings.short_move_multiple
    short_put = (spot - distance).to_integral_value(rounding=ROUND_FLOOR)
    short_call = (spot + distance).to_integral_value(rounding=ROUND_CEILING)
    return (
        short_put - settings.width,
        short_put,
        short_call,
        short_call + settings.width,
    )


def condor_symbols(
    trading_date: date,
    spot: Decimal,
    implied_move: Decimal,
    settings: CondorSettings,
) -> dict[str, str]:
    long_put, short_put, short_call, long_call = condor_strikes(spot, implied_move, settings)
    return {
        "long_put": occ_option_for(settings.symbol, trading_date, "P", long_put),
        "short_put": occ_option_for(settings.symbol, trading_date, "P", short_put),
        "short_call": occ_option_for(settings.symbol, trading_date, "C", short_call),
        "long_call": occ_option_for(settings.symbol, trading_date, "C", long_call),
    }


def _bounded_debit(value: Decimal, width: Decimal) -> Decimal:
    return max(Decimal("0"), min(width, value)).quantize(CENT, rounding=ROUND_CEILING)


def simulate_condor(
    trading_date: str,
    underlying_bars: list[PriceBar],
    option_bars: dict[str, list[PriceBar]],
    realized_reference: Decimal | None,
    settings: CondorSettings,
) -> CondorResult:
    if realized_reference is None:
        return CondorResult(trading_date, False, "insufficient realized-move history")
    if realized_reference <= 0:
        return CondorResult(trading_date, False, "realized-move reference is not positive")

    underlying_entry = _at(underlying_bars, settings.entry_time)
    if underlying_entry is None:
        return CondorResult(
            trading_date,
            False,
            "underlying entry bar missing",
            realized_reference=realized_reference,
        )

    day = date.fromisoformat(trading_date)
    atm_call, atm_put = atm_straddle_symbols(day, underlying_entry.open, settings.symbol)
    implied_move = implied_move_at(option_bars, atm_call, atm_put, settings.entry_time)
    if implied_move is None:
        return CondorResult(
            trading_date,
            False,
            "ATM straddle entry marks missing",
            realized_reference=realized_reference,
        )

    richness = (implied_move / realized_reference).quantize(RATIO)
    if richness < settings.richness_multiple:
        return CondorResult(
            trading_date,
            False,
            "implied move is below richness gate",
            realized_reference=realized_reference,
            implied_move_proxy=implied_move,
            richness_ratio=richness,
        )

    strikes = condor_strikes(underlying_entry.open, implied_move, settings)
    names = condor_symbols(day, underlying_entry.open, implied_move, settings)
    legs = {name: option_bars.get(symbol, []) for name, symbol in names.items()}
    entries = {name: _at(bars, settings.entry_time) for name, bars in legs.items()}
    if any(bar is None for bar in entries.values()):
        return CondorResult(
            trading_date,
            False,
            "one or more condor legs lack an exact entry bar",
            short_put_strike=strikes[1],
            short_call_strike=strikes[2],
            realized_reference=realized_reference,
            implied_move_proxy=implied_move,
            richness_ratio=richness,
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
        return CondorResult(
            trading_date,
            False,
            "entry credit or maximum risk failed",
            strikes[1],
            strikes[2],
            realized_reference,
            implied_move,
            richness,
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
            settings.width,
        )
        exit_marks.append((timestamp, debit))

    reason = "hard_close_missing_mark"
    exit_debit = settings.width
    exact = next((item for item in exit_marks if item[0].time() == settings.hard_close), None)
    if exact is not None:
        reason, exit_debit = "hard_close", exact[1]
    elif exit_marks:
        timestamp, debit = exit_marks[-1]
        hard_close_at = datetime.combine(timestamp.date(), settings.hard_close, timestamp.tzinfo)
        if (
            timedelta(0)
            <= hard_close_at - timestamp
            <= timedelta(minutes=settings.max_hard_close_mark_age_minutes)
        ):
            reason, exit_debit = "hard_close_last_mark", debit

    pnl = ((entry_credit - exit_debit) * HUNDRED - settings.fees_per_condor).quantize(CENT)
    return CondorResult(
        trading_date,
        True,
        reason,
        strikes[1],
        strikes[2],
        realized_reference,
        implied_move,
        richness,
        raw_credit,
        entry_credit,
        maximum_risk,
        exit_debit,
        pnl,
    )


def condor_metrics(results: list[CondorResult]) -> dict[str, object]:
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
    skips: dict[str, int] = {}
    exits: dict[str, int] = {}
    for result in results:
        target = exits if result.entered else skips
        target[result.reason] = target.get(result.reason, 0) + 1
    gross_profit = sum(wins, Decimal("0"))
    gross_loss = abs(sum(losses, Decimal("0")))
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
        "average_pnl": (str(Decimal(str(mean(pnls))).quantize(CENT)) if pnls else None),
        "profit_factor": (str((gross_profit / gross_loss).quantize(RATIO)) if gross_loss else None),
        "max_drawdown": str(max_drawdown.quantize(CENT)),
        "worst_trade": str(min(pnls).quantize(CENT)) if pnls else None,
        "exits": exits,
        "skips": skips,
    }


def viable(report: dict[str, dict[str, object]]) -> bool:
    for split, minimum in (("train", 100), ("validation", 30)):
        metrics = report[split]
        stress = report[f"{split}_stress"]
        average = metrics["average_pnl"]
        factor = metrics["profit_factor"]
        stress_average = stress["average_pnl"]
        if int(metrics["trades"]) < minimum:
            return False
        if average is None or Decimal(str(average)) <= 0:
            return False
        if factor is None or Decimal(str(factor)) < Decimal("1.25"):
            return False
        if stress_average is None or Decimal(str(stress_average)) <= 0:
            return False
        if Decimal(str(metrics["max_drawdown"])) < Decimal("-500"):
            return False
    return True


def _settings_payload(settings: CondorSettings) -> dict[str, object]:
    return {key: str(value) for key, value in asdict(settings).items()}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate the preregistered implied-move SPY 0DTE iron condor"
    )
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--oos-start", type=date.fromisoformat, required=True)
    parser.add_argument("--cache-dir", type=Path, default=Path("state/implied-condor-cache"))
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
    base = CondorSettings()
    stress = replace(base, adverse_fill_per_leg=Decimal("0.02"))
    rows: dict[str, list[CondorResult]] = {
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
            atm_symbols = list(atm_straddle_symbols(day, entry.open, base.symbol))
            options = data.option_bars(day, atm_symbols)
            implied = implied_move_at(options, atm_symbols[0], atm_symbols[1], base.entry_time)
            if implied is not None and implied / reference >= base.richness_multiple:
                leg_symbols = sorted(condor_symbols(day, entry.open, implied, base).values())
                options = data.option_bars(day, sorted(set(atm_symbols + leg_symbols)))
            fetched += 1
            if fetched % 25 == 0:
                print(f"[{position}/{len(dates)}] {trading_date}", file=sys.stderr)
        split = "train" if trading_date in splits["train"] else "validation"
        rows[split].append(simulate_condor(trading_date, underlying, options, reference, base))
        rows[f"{split}_stress"].append(
            simulate_condor(trading_date, underlying, options, reference, stress)
        )
        move = session_absolute_move(underlying, base.entry_time)
        if move is not None:
            prior_moves.append(move)

    metrics = {name: condor_metrics(values) for name, values in rows.items()}
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
