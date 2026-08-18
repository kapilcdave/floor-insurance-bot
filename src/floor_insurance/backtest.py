from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict, dataclass
from datetime import datetime, time
from decimal import Decimal
from pathlib import Path
from statistics import mean
from zoneinfo import ZoneInfo

from .models import Quote
from .strategy import StrategySkip, executable_close_debit, executable_credit, size_contracts

ET = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class Row:
    timestamp: datetime
    underlying: Decimal
    short_strike: Decimal
    long_strike: Decimal
    short_bid: Decimal
    short_ask: Decimal
    long_bid: Decimal
    long_ask: Decimal


@dataclass(frozen=True)
class BacktestSettings:
    starting_equity: Decimal = Decimal("5000")
    risk_fraction: Decimal = Decimal("0.01")
    stop_buffer: Decimal = Decimal("3")
    take_profit_fraction: Decimal = Decimal("0.50")
    min_credit: Decimal = Decimal("0.05")
    max_contracts: int = 10
    entry_time: time = time(9, 45)
    take_profit_cutoff: time = time(14, 0)
    hard_close: time = time(15, 0)
    fees_per_spread: Decimal = Decimal("0")


@dataclass(frozen=True)
class Result:
    trading_date: str
    traded: bool
    reason: str
    quantity: int = 0
    entry_credit: Decimal = Decimal("0")
    exit_debit: Decimal = Decimal("0")
    pnl: Decimal = Decimal("0")
    equity_after: Decimal = Decimal("0")


REQUIRED_COLUMNS = {
    "timestamp",
    "underlying",
    "short_strike",
    "long_strike",
    "short_bid",
    "short_ask",
    "long_bid",
    "long_ask",
}


def load_rows(path: Path) -> dict[str, list[Row]]:
    sessions: dict[str, list[Row]] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"CSV is missing columns: {', '.join(sorted(missing))}")
        for raw in reader:
            timestamp = datetime.fromisoformat(raw["timestamp"].replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                raise ValueError("timestamps must include a UTC offset")
            timestamp = timestamp.astimezone(ET)
            row = Row(
                timestamp=timestamp,
                underlying=Decimal(raw["underlying"]),
                short_strike=Decimal(raw["short_strike"]),
                long_strike=Decimal(raw["long_strike"]),
                short_bid=Decimal(raw["short_bid"]),
                short_ask=Decimal(raw["short_ask"]),
                long_bid=Decimal(raw["long_bid"]),
                long_ask=Decimal(raw["long_ask"]),
            )
            sessions.setdefault(timestamp.date().isoformat(), []).append(row)
    for rows in sessions.values():
        rows.sort(key=lambda item: item.timestamp)
    return dict(sorted(sessions.items()))


def _quotes(row: Row) -> tuple[Quote, Quote]:
    return (
        Quote(row.short_bid, row.short_ask, row.timestamp),
        Quote(row.long_bid, row.long_ask, row.timestamp),
    )


def simulate_session(
    trading_date: str,
    rows: list[Row],
    equity: Decimal,
    settings: BacktestSettings,
) -> Result:
    eligible = [row for row in rows if row.timestamp.time() >= settings.entry_time]
    if not eligible:
        return Result(trading_date, False, "no entry-time data", equity_after=equity)
    entry = eligible[0]
    width = entry.short_strike - entry.long_strike
    short_quote, long_quote = _quotes(entry)
    credit = executable_credit(short_quote, long_quote)
    if credit < settings.min_credit:
        return Result(trading_date, False, "credit below minimum", equity_after=equity)
    try:
        quantity = size_contracts(
            equity,
            settings.risk_fraction,
            width,
            credit,
            settings.max_contracts,
        )
    except StrategySkip as exc:
        return Result(trading_date, False, str(exc), equity_after=equity)

    target = credit * settings.take_profit_fraction
    exit_debit = Decimal("0")
    reason = "end_of_data"
    for row in eligible[1:]:
        short_quote, long_quote = _quotes(row)
        debit = executable_close_debit(short_quote, long_quote)
        if row.underlying <= entry.short_strike + settings.stop_buffer:
            exit_debit, reason = debit, "emergency_stop"
            break
        if row.timestamp.time() < settings.take_profit_cutoff and debit <= target:
            exit_debit, reason = debit, "take_profit"
            break
        if row.timestamp.time() >= settings.hard_close:
            exit_debit, reason = debit, "hard_close"
            break
    else:
        if eligible:
            short_quote, long_quote = _quotes(eligible[-1])
            exit_debit = executable_close_debit(short_quote, long_quote)

    pnl = ((credit - exit_debit) * Decimal("100") - settings.fees_per_spread) * quantity
    pnl = pnl.quantize(Decimal("0.01"))
    return Result(
        trading_date,
        True,
        reason,
        quantity,
        credit,
        exit_debit,
        pnl,
        equity + pnl,
    )


def run_backtest(
    sessions: dict[str, list[Row]], settings: BacktestSettings
) -> list[Result]:
    equity = settings.starting_equity
    results: list[Result] = []
    for trading_date, rows in sessions.items():
        result = simulate_session(trading_date, rows, equity, settings)
        equity = result.equity_after
        results.append(result)
    return results


def split_dates(dates: list[str]) -> dict[str, set[str]]:
    if len(dates) < 10:
        raise ValueError("at least 10 sessions are required for chronological splits")
    train_end = max(1, int(len(dates) * 0.60))
    validation_end = max(train_end + 1, int(len(dates) * 0.80))
    return {
        "train": set(dates[:train_end]),
        "validation": set(dates[train_end:validation_end]),
        "out_of_sample": set(dates[validation_end:]),
    }


def metrics(results: list[Result]) -> dict[str, object]:
    traded = [result for result in results if result.traded]
    pnls = [result.pnl for result in traded]
    peak = Decimal("0")
    cumulative = Decimal("0")
    max_drawdown = Decimal("0")
    for pnl in pnls:
        cumulative += pnl
        peak = max(peak, cumulative)
        max_drawdown = min(max_drawdown, cumulative - peak)
    return {
        "sessions": len(results),
        "trades": len(traded),
        "skipped": len(results) - len(traded),
        "wins": sum(pnl > 0 for pnl in pnls),
        "losses": sum(pnl < 0 for pnl in pnls),
        "win_rate": round(sum(pnl > 0 for pnl in pnls) / len(pnls), 4) if pnls else None,
        "total_pnl": str(sum(pnls, Decimal("0"))),
        "average_pnl": str(Decimal(str(mean(pnls))).quantize(Decimal("0.01"))) if pnls else None,
        "max_drawdown": str(max_drawdown.quantize(Decimal("0.01"))),
        "emergency_stops": sum(result.reason == "emergency_stop" for result in traded),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Backtest prepared SPY spread quote data")
    parser.add_argument("csv", type=Path)
    parser.add_argument("--starting-equity", type=Decimal, default=Decimal("5000"))
    parser.add_argument("--fees-per-spread", type=Decimal, default=Decimal("0"))
    parser.add_argument(
        "--reveal-oos",
        action="store_true",
        help="include the locked final 20%%; do this only after parameters are frozen",
    )
    parser.add_argument("--trades-output", type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    sessions = load_rows(args.csv)
    settings = BacktestSettings(
        starting_equity=args.starting_equity, fees_per_spread=args.fees_per_spread
    )
    results = run_backtest(sessions, settings)
    splits = split_dates(list(sessions))
    report = {
        name: metrics([result for result in results if result.trading_date in dates])
        for name, dates in splits.items()
        if name != "out_of_sample" or args.reveal_oos
    }
    print(json.dumps({"settings": {key: str(value) for key, value in asdict(settings).items()}, "results": report}, indent=2))
    if args.trades_output:
        with args.trades_output.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=Result.__dataclass_fields__.keys())
            writer.writeheader()
            writer.writerows(asdict(result) for result in results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

