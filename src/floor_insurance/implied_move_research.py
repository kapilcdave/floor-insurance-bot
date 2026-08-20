"""Predeclared SPY 0DTE implied-move credit-spread research.

This module is deliberately disconnected from the trading engine. Historical
Alpaca option bars contain trades rather than NBBO quotes, so the results are a
cost-stressed rejection screen, not evidence that an order was executable.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, replace
from datetime import date, time
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from pathlib import Path
from statistics import mean

from .config import Config
from .credit_structure import occ_put_for
from .directional import PriceBar
from .directional_backtest import HistoricalData, research_splits
from .strategy import max_loss_per_contract, stop_close_debit

CENT = Decimal("0.01")
HUNDRED = Decimal("100")

MOVE_MULTIPLES = (Decimal("1"), Decimal("1.25"))
TAKE_PROFITS: tuple[Decimal | None, ...] = (
    Decimal("0.5"),
    Decimal("0.75"),
    None,
)

ACCEPTANCE_RULE = (
    "A candidate must have at least 100 training and 30 validation trades; "
    "positive average net P&L and profit factor above 1.15 on both splits; "
    "maximum drawdown no worse than five $100 risk units on either split; and "
    "positive average P&L on both splits when adverse fill is increased from "
    "$0.02 to $0.03 per leg on entry and exit. Validation is pass/fail and the "
    "final chronological holdout remains sealed."
)

DATA_LIMITATION = (
    "Alpaca historical option bars are one-minute trade aggregates, not "
    "contemporaneous NBBO quotes. Per-leg adverse-fill charges make this a "
    "rejection screen; only forward OPRA quote collection can test executable "
    "fills."
)


@dataclass(frozen=True)
class ImpliedMoveSettings:
    move_multiple: Decimal
    take_profit_fraction: Decimal | None
    stop_debit_multiple: Decimal = Decimal("2")
    width: Decimal = Decimal("1")
    min_credit: Decimal = Decimal("0.15")
    max_loss_dollars: Decimal = Decimal("100")
    entry_time: time = time(10, 0)
    hard_close: time = time(15, 0)
    slippage_per_leg: Decimal = Decimal("0.02")
    fees_per_spread: Decimal = Decimal("0.10")
    symbol: str = "SPY"

    @property
    def label(self) -> str:
        target = (
            "hold"
            if self.take_profit_fraction is None
            else f"tp{self.take_profit_fraction}"
        )
        return f"move{self.move_multiple}_{target}_stop{self.stop_debit_multiple}x"


@dataclass(frozen=True)
class ImpliedMoveResult:
    trading_date: str
    entered: bool
    reason: str
    implied_move: Decimal = Decimal("0")
    short_strike: Decimal = Decimal("0")
    long_strike: Decimal = Decimal("0")
    credit: Decimal = Decimal("0")
    exit_debit: Decimal = Decimal("0")
    max_loss: Decimal = Decimal("0")
    pnl: Decimal = Decimal("0")


def settings_grid() -> list[ImpliedMoveSettings]:
    return [
        ImpliedMoveSettings(multiple, target)
        for multiple in MOVE_MULTIPLES
        for target in TAKE_PROFITS
    ]


def occ_call_for(symbol: str, expiration: date, strike: Decimal) -> str:
    return f"{symbol}{expiration:%y%m%d}C{int(strike * Decimal('1000')):08d}"


def _at(bars: list[PriceBar], moment: time) -> PriceBar | None:
    return next((bar for bar in bars if bar.timestamp.time() == moment), None)


def atm_straddle_symbols(
    trading_date: date, spot: Decimal, symbol: str = "SPY"
) -> tuple[str, str]:
    strike = spot.to_integral_value(rounding=ROUND_FLOOR)
    return (
        occ_call_for(symbol, trading_date, strike),
        occ_put_for(symbol, trading_date, strike),
    )


def implied_move_at(
    option_bars: dict[str, list[PriceBar]],
    call_symbol: str,
    put_symbol: str,
    moment: time,
) -> Decimal | None:
    call = _at(option_bars.get(call_symbol, []), moment)
    put = _at(option_bars.get(put_symbol, []), moment)
    if call is None or put is None:
        return None
    value = call.open + put.open
    return value.quantize(CENT) if value > 0 else None


def spread_strikes(
    spot: Decimal,
    implied_move: Decimal,
    move_multiple: Decimal,
    width: Decimal,
) -> tuple[Decimal, Decimal]:
    short = (spot - implied_move * move_multiple).to_integral_value(
        rounding=ROUND_FLOOR
    )
    return short, short - width


def required_spread_symbols(
    trading_date: date,
    spot: Decimal,
    implied_move: Decimal,
    settings: list[ImpliedMoveSettings],
) -> list[str]:
    symbols: set[str] = set()
    for item in settings:
        short, long = spread_strikes(
            spot, implied_move, item.move_multiple, item.width
        )
        symbols.add(occ_put_for(item.symbol, trading_date, short))
        symbols.add(occ_put_for(item.symbol, trading_date, long))
    return sorted(symbols)


def simulate_implied_move_spread(
    trading_date: str,
    underlying_bars: list[PriceBar],
    option_bars: dict[str, list[PriceBar]],
    settings: ImpliedMoveSettings,
) -> ImpliedMoveResult:
    entry = _at(underlying_bars, settings.entry_time)
    if entry is None:
        return ImpliedMoveResult(trading_date, False, "underlying entry bar missing")
    day = date.fromisoformat(trading_date)
    call_symbol, put_symbol = atm_straddle_symbols(day, entry.open, settings.symbol)
    implied_move = implied_move_at(
        option_bars, call_symbol, put_symbol, settings.entry_time
    )
    if implied_move is None:
        return ImpliedMoveResult(trading_date, False, "ATM straddle entry marks missing")

    short_strike, long_strike = spread_strikes(
        entry.open, implied_move, settings.move_multiple, settings.width
    )
    short_symbol = occ_put_for(settings.symbol, day, short_strike)
    long_symbol = occ_put_for(settings.symbol, day, long_strike)
    short_bars = option_bars.get(short_symbol, [])
    long_bars = option_bars.get(long_symbol, [])
    short_entry = _at(short_bars, settings.entry_time)
    long_entry = _at(long_bars, settings.entry_time)
    if short_entry is None or long_entry is None:
        return ImpliedMoveResult(
            trading_date,
            False,
            "one or both spread entry marks missing",
            implied_move,
            short_strike,
            long_strike,
        )

    transaction_slippage = settings.slippage_per_leg * Decimal("2")
    credit = (short_entry.open - long_entry.open - transaction_slippage).quantize(
        CENT, rounding=ROUND_FLOOR
    )
    if credit < settings.min_credit:
        return ImpliedMoveResult(
            trading_date,
            False,
            "credit below minimum",
            implied_move,
            short_strike,
            long_strike,
            credit,
        )
    if credit >= settings.width:
        return ImpliedMoveResult(
            trading_date,
            False,
            "credit reached spread width",
            implied_move,
            short_strike,
            long_strike,
            credit,
        )
    maximum_loss = max_loss_per_contract(settings.width, credit)
    if maximum_loss > settings.max_loss_dollars:
        return ImpliedMoveResult(
            trading_date,
            False,
            "maximum loss above cap",
            implied_move,
            short_strike,
            long_strike,
            credit,
            max_loss=maximum_loss,
        )

    short_by_time = {bar.timestamp.time(): bar for bar in short_bars}
    long_by_time = {bar.timestamp.time(): bar for bar in long_bars}
    stop = stop_close_debit(
        settings.width, credit, settings.stop_debit_multiple
    )
    target = (
        (credit * settings.take_profit_fraction).quantize(
            CENT, rounding=ROUND_CEILING
        )
        if settings.take_profit_fraction is not None
        else None
    )
    last_debit = credit

    def spread_debit(moment: time) -> Decimal | None:
        short = short_by_time.get(moment)
        long = long_by_time.get(moment)
        if short is None or long is None:
            return None
        raw = short.close - long.close + transaction_slippage
        return max(Decimal("0"), min(settings.width, raw)).quantize(
            CENT, rounding=ROUND_CEILING
        )

    def settle(reason: str, debit: Decimal) -> ImpliedMoveResult:
        pnl = ((credit - debit) * HUNDRED - settings.fees_per_spread).quantize(
            CENT
        )
        return ImpliedMoveResult(
            trading_date,
            True,
            reason,
            implied_move,
            short_strike,
            long_strike,
            credit,
            debit,
            maximum_loss,
            pnl,
        )

    for bar in underlying_bars:
        moment = bar.timestamp.time()
        if moment <= settings.entry_time:
            continue
        if moment > settings.hard_close:
            break
        debit = spread_debit(moment)
        if debit is not None:
            last_debit = debit
        if debit is not None and debit >= stop:
            return settle("spread_stop", debit)
        if target is not None and debit is not None and debit <= target:
            return settle("take_profit", debit)
        if moment == settings.hard_close:
            return settle("hard_close", last_debit)
    return settle("session_ended", last_debit)


def implied_move_metrics(results: list[ImpliedMoveResult]) -> dict[str, object]:
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
    skipped: dict[str, int] = {}
    for result in results:
        if not result.entered:
            skipped[result.reason] = skipped.get(result.reason, 0) + 1
    return {
        "sessions": len(results),
        "trades": len(traded),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(pnls), 4) if pnls else None,
        "average_credit": (
            str(Decimal(str(mean(result.credit for result in traded))).quantize(CENT))
            if traded
            else None
        ),
        "total_pnl": str(sum(pnls, Decimal("0")).quantize(CENT)),
        "average_pnl": (
            str(Decimal(str(mean(pnls))).quantize(CENT)) if pnls else None
        ),
        "profit_factor": (
            str((gross_profit / gross_loss).quantize(Decimal("0.0001")))
            if gross_loss
            else None
        ),
        "max_drawdown": str(max_drawdown.quantize(CENT)),
        "spread_stops": sum(result.reason == "spread_stop" for result in traded),
        "take_profits": sum(result.reason == "take_profit" for result in traded),
        "hard_closes": sum(result.reason == "hard_close" for result in traded),
        "skips": skipped,
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
        if factor is None or Decimal(str(factor)) <= Decimal("1.15"):
            return False
        if stress_average is None or Decimal(str(stress_average)) <= 0:
            return False
        if Decimal(str(metrics["max_drawdown"])) < Decimal("-500"):
            return False
    return True


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate six predeclared SPY implied-move credit spreads"
    )
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--oos-start", type=date.fromisoformat, required=True)
    parser.add_argument(
        "--cache-dir", type=Path, default=Path("state/implied-move-cache")
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    config = Config.from_env()
    data = HistoricalData(config, args.cache_dir)
    sessions = data.stock_sessions(args.start, args.end, "SPY")
    dates = list(sessions)
    splits = research_splits(dates, args.oos_start)
    allowed = splits["train"] | splits["validation"]
    grid = settings_grid()
    stressed = [replace(item, slippage_per_leg=Decimal("0.03")) for item in grid]
    rows: dict[str, dict[str, list[ImpliedMoveResult]]] = {
        item.label: {
            "train": [],
            "validation": [],
            "train_stress": [],
            "validation_stress": [],
        }
        for item in grid
    }
    fetched = 0
    for position, trading_date in enumerate(dates, start=1):
        if trading_date not in allowed:
            continue
        bars = sessions[trading_date]
        entry = _at(bars, grid[0].entry_time)
        option_bars: dict[str, list[PriceBar]] = {}
        if entry is not None:
            day = date.fromisoformat(trading_date)
            call_symbol, put_symbol = atm_straddle_symbols(day, entry.open)
            option_bars.update(data.option_bars(day, [call_symbol, put_symbol]))
            implied_move = implied_move_at(
                option_bars, call_symbol, put_symbol, grid[0].entry_time
            )
            if implied_move is not None:
                symbols = required_spread_symbols(
                    day, entry.open, implied_move, grid
                )
                option_bars.update(data.option_bars(day, symbols))
            fetched += 1
            if fetched % 25 == 0:
                print(f"[{position}/{len(dates)}] {trading_date}", file=sys.stderr)
        split = "train" if trading_date in splits["train"] else "validation"
        for settings, stress_settings in zip(grid, stressed, strict=True):
            rows[settings.label][split].append(
                simulate_implied_move_spread(
                    trading_date, bars, option_bars, settings
                )
            )
            rows[settings.label][f"{split}_stress"].append(
                simulate_implied_move_spread(
                    trading_date, bars, option_bars, stress_settings
                )
            )

    reports: dict[str, dict[str, object]] = {}
    for settings in grid:
        report_rows = rows[settings.label]
        reports[settings.label] = {
            "settings": {
                key: str(value) if value is not None else None
                for key, value in asdict(settings).items()
            },
            **{
                split: implied_move_metrics(report_rows[split])
                for split in (
                    "train",
                    "validation",
                    "train_stress",
                    "validation_stress",
                )
            },
        }
    viable_labels = [label for label, report in reports.items() if viable(report)]  # type: ignore[arg-type]
    ranked = sorted(
        viable_labels,
        key=lambda label: Decimal(str(reports[label]["train"]["average_pnl"])),  # type: ignore[index]
        reverse=True,
    )
    locked = sorted(splits["out_of_sample"])
    print(
        json.dumps(
            {
                "acceptance_rule": ACCEPTANCE_RULE,
                "data_limitation": DATA_LIMITATION,
                "candidate": ranked[0] if ranked else None,
                "viable": ranked,
                "option_sessions_fetched": fetched,
                "oos_revealed": False,
                "oos_start": locked[0],
                "oos_end": locked[-1],
                "oos_option_cache_preexisting": [
                    value
                    for value in locked
                    if (args.cache_dir / f"options-{value}.json").exists()
                ],
                "structures": reports,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
