"""Evaluate a declared grid of put-credit-spread structures.

The grid is fixed in code. It is a characterisation, not a search: the reported
quantity is the gap between the win rate a structure needs and the win rate it
achieved, so each structure is judged against its own arithmetic. The sealed
out-of-sample window is never fetched.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

from .config import Config
from .credit_structure import (
    CreditSettings,
    credit_metrics,
    occ_put,
    simulate_credit_spread,
    spread_strikes,
)
from .directional_backtest import HistoricalData, research_splits

BUFFERS = (Decimal("5"), Decimal("10"), Decimal("15"))
WIDTHS = (Decimal("1"), Decimal("3"), Decimal("5"))
STOP_BUFFERS = (Decimal("1"), Decimal("3"))
TAKE_PROFITS: tuple[Decimal | None, ...] = (Decimal("0.5"), None)

VIABILITY_RULE = (
    "declared from first principles, not tuned to a result: a structure is only "
    "worth carrying forward if average P&L per contract is positive on both the "
    "training and the validation split, with at least 100 training trades, and "
    "if it stays positive once realistic transaction costs are applied. A "
    "36-session smoke sample had been inspected when this rule was written, so "
    "the rule deliberately requires both splits and a cost run rather than a "
    "single favourable slice."
)
COST_SENSITIVITY_NOTE = (
    "run once with zero costs for the structural upper bound, then again with "
    "--slippage-per-side and --fees-per-spread. Credit spreads this far out of "
    "the money collect single-digit cents, so the cost run is the real test."
)


def structure_grid() -> list[CreditSettings]:
    return [
        CreditSettings(
            buffer_dollars=buffer,
            width=width,
            stop_buffer=stop,
            take_profit_fraction=take_profit,
        )
        for buffer in BUFFERS
        for width in WIDTHS
        for stop in STOP_BUFFERS
        for take_profit in TAKE_PROFITS
    ]


def required_symbols(spot: Decimal, trading_date: date) -> list[str]:
    """Every put symbol the grid could ask for on one session."""

    strikes: set[Decimal] = set()
    for buffer in BUFFERS:
        for width in WIDTHS:
            short_strike, long_strike = spread_strikes(
                spot,
                CreditSettings(
                    buffer_dollars=buffer,
                    width=width,
                    stop_buffer=Decimal("0"),
                    take_profit_fraction=None,
                ),
            )
            strikes.update({short_strike, long_strike})
    return sorted(occ_put(trading_date, strike) for strike in strikes)


def viable(report: dict[str, dict[str, object]]) -> bool:
    train = report["train"]
    validation = report["validation"]
    for split in (train, validation):
        average = split.get("average_pnl_per_contract")
        if average is None or Decimal(str(average)) <= 0:
            return False
    return int(train["trades"]) >= 100


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Characterise put credit spread structures without revealing OOS"
    )
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--oos-start", type=date.fromisoformat, required=True)
    parser.add_argument("--slippage-per-side", type=Decimal, default=Decimal("0"))
    parser.add_argument("--fees-per-spread", type=Decimal, default=Decimal("0"))
    parser.add_argument("--cache-dir", type=Path, default=Path("state/backtest-cache"))
    return parser


def main() -> int:
    args = _parser().parse_args()
    config = Config.from_env()
    data = HistoricalData(config, args.cache_dir)
    sessions = data.stock_sessions(args.start, args.end)
    dates = list(sessions)
    splits = research_splits(dates, args.oos_start)
    allowed = splits["train"] | splits["validation"]

    grid = [
        replace(
            settings,
            slippage_per_side=args.slippage_per_side,
            fees_per_spread=args.fees_per_spread,
        )
        for settings in structure_grid()
    ]
    collected: dict[str, dict[str, list]] = {
        settings.label: {"train": [], "validation": []} for settings in grid
    }

    evaluated = 0
    for position, trading_date in enumerate(dates, start=1):
        if trading_date not in allowed:
            continue
        bars = sessions[trading_date]
        entry = next(
            (
                bar
                for bar in bars
                if bar.timestamp.time() == grid[0].entry_time
            ),
            None,
        )
        if entry is None:
            continue
        day = date.fromisoformat(trading_date)
        option_bars = data.option_bars(day, required_symbols(entry.open, day))
        evaluated += 1
        if evaluated % 25 == 0:
            print(f"[{position}/{len(dates)}] {trading_date}", file=sys.stderr)
        split = "train" if trading_date in splits["train"] else "validation"
        for settings in grid:
            collected[settings.label][split].append(
                simulate_credit_spread(trading_date, bars, option_bars, settings)
            )

    structures: dict[str, dict[str, object]] = {}
    for settings in grid:
        rows = collected[settings.label]
        structures[settings.label] = {
            "settings": {
                "buffer_dollars": str(settings.buffer_dollars),
                "width": str(settings.width),
                "stop_buffer": str(settings.stop_buffer),
                "take_profit_fraction": (
                    str(settings.take_profit_fraction)
                    if settings.take_profit_fraction is not None
                    else None
                ),
                "min_credit_fraction": str(settings.min_credit_fraction),
                "slippage_per_side": str(settings.slippage_per_side),
                "fees_per_spread": str(settings.fees_per_spread),
            },
            "train": credit_metrics(rows["train"], settings),
            "validation": credit_metrics(rows["validation"], settings),
        }

    locked = sorted(splits["out_of_sample"])
    print(
        json.dumps(
            {
                "viability_rule": VIABILITY_RULE,
                "cost_sensitivity": COST_SENSITIVITY_NOTE,
                "viable": [
                    label
                    for label, report in structures.items()
                    if viable(report)  # type: ignore[arg-type]
                ],
                "sessions_evaluated": evaluated,
                "oos_revealed": False,
                "oos_start": locked[0],
                "oos_end": locked[-1],
                "oos_option_cache_preexisting": [
                    value
                    for value in locked
                    if (args.cache_dir / f"options-{value}.json").exists()
                ],
                "structures": structures,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
