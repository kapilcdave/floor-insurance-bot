"""Locked adaptive-width follow-up to the rejected narrow iron-fly test."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from statistics import mean

from .config import Config
from .directional import PriceBar
from .directional_backtest import HistoricalData, research_splits
from .fifty_credit_research import moving_block_bootstrap
from .iron_fly_research import (
    ACCEPTANCE_RULE,
    DATA_LIMITATION,
    IronFlyResult,
    IronFlySettings,
    center_strike,
    iron_fly_metrics,
    iron_fly_symbols,
    session_absolute_move,
    simulate_iron_fly,
    trailing_realized_reference,
    viable,
)

CANDIDATE_WIDTHS = (
    Decimal("5"),
    Decimal("4"),
    Decimal("3"),
    Decimal("2"),
)


def required_symbols(
    trading_date: date,
    center: Decimal,
    settings: IronFlySettings,
    widths: tuple[Decimal, ...] = CANDIDATE_WIDTHS,
) -> list[str]:
    symbols = {
        symbol
        for width in widths
        for symbol in iron_fly_symbols(
            trading_date, center, replace(settings, wing_width=width)
        ).values()
    }
    return sorted(symbols)


def simulate_adaptive_iron_fly(
    trading_date: str,
    underlying_bars: list[PriceBar],
    option_bars: dict[str, list[PriceBar]],
    realized_reference: Decimal | None,
    settings: IronFlySettings,
    widths: tuple[Decimal, ...] = CANDIDATE_WIDTHS,
) -> IronFlyResult:
    if not widths or any(width <= 0 for width in widths):
        raise ValueError("candidate widths must be positive")
    if tuple(sorted(widths, reverse=True)) != widths:
        raise ValueError("candidate widths must be widest first")

    rejected: IronFlyResult | None = None
    for width in widths:
        result = simulate_iron_fly(
            trading_date,
            underlying_bars,
            option_bars,
            realized_reference,
            replace(settings, wing_width=width),
        )
        result = replace(result, wing_width=width)
        if result.entered:
            return result
        rejected = result
        if result.reason in {
            "insufficient realized-move history",
            "realized-move reference is not positive",
            "underlying entry bar missing",
            "implied move is below richness gate",
        }:
            return result
    assert rejected is not None
    return replace(rejected, reason="no candidate width passed entry and risk gates")


def adaptive_metrics(results: list[IronFlyResult]) -> dict[str, object]:
    metrics = iron_fly_metrics(results)
    traded = [result for result in results if result.entered]
    widths: dict[str, int] = {}
    for result in traded:
        label = str(result.wing_width)
        widths[label] = widths.get(label, 0) + 1
    return {
        **metrics,
        "average_wing_width": (
            str(
                Decimal(str(mean(result.wing_width for result in traded))).quantize(
                    Decimal("0.01")
                )
            )
            if traded
            else None
        ),
        "selected_widths": widths,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate the preregistered adaptive-width SPY 0DTE iron fly"
    )
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--oos-start", type=date.fromisoformat, required=True)
    parser.add_argument(
        "--cache-dir", type=Path, default=Path("state/adaptive-iron-fly-cache")
    )
    parser.add_argument("--report-out", type=Path)
    return parser


def _settings_payload(settings: IronFlySettings) -> dict[str, object]:
    return {key: str(value) for key, value in asdict(settings).items()}


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
        entry = next(
            (bar for bar in underlying if bar.timestamp.time() == base.entry_time), None
        )
        if reference is not None and entry is not None:
            day = date.fromisoformat(trading_date)
            options = data.option_bars(
                day, required_symbols(day, center_strike(entry.open), base)
            )
            fetched += 1
            if fetched % 25 == 0:
                print(f"[{position}/{len(dates)}] {trading_date}", file=sys.stderr)
        split = "train" if trading_date in splits["train"] else "validation"
        rows[split].append(
            simulate_adaptive_iron_fly(
                trading_date, underlying, options, reference, base
            )
        )
        rows[f"{split}_stress"].append(
            simulate_adaptive_iron_fly(
                trading_date, underlying, options, reference, stress
            )
        )
        move = session_absolute_move(underlying, base.entry_time)
        if move is not None:
            prior_moves.append(move)

    metrics = {name: adaptive_metrics(values) for name, values in rows.items()}
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
        "candidate_widths": [str(width) for width in CANDIDATE_WIDTHS],
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
