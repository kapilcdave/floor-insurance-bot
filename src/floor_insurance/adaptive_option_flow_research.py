"""Locked adaptive-width follow-up to opening option-flow research."""

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
from .option_flow_research import (
    ACCEPTANCE_RULE,
    DATA_LIMITATION,
    OptionFlowResult,
    OptionFlowSettings,
    option_flow_metrics,
    simulate_option_flow,
    viable,
)
from .option_flow_research import required_symbols as fixed_required_symbols

CANDIDATE_WIDTHS = (Decimal("3"), Decimal("2"), Decimal("1"))


def required_symbols(
    trading_date: date,
    spot: Decimal,
    settings: OptionFlowSettings,
    widths: tuple[Decimal, ...] = CANDIDATE_WIDTHS,
) -> list[str]:
    return sorted(
        {
            symbol
            for width in widths
            for symbol in fixed_required_symbols(
                trading_date, spot, replace(settings, width=width)
            )
        }
    )


def simulate_adaptive_option_flow(
    trading_date: str,
    underlying_bars: list[PriceBar],
    option_bars: dict[str, list[PriceBar]],
    settings: OptionFlowSettings,
    widths: tuple[Decimal, ...] = CANDIDATE_WIDTHS,
) -> OptionFlowResult:
    if not widths or any(width <= 0 for width in widths):
        raise ValueError("candidate widths must be positive")
    if tuple(sorted(widths, reverse=True)) != widths:
        raise ValueError("candidate widths must be widest first")

    rejected: OptionFlowResult | None = None
    for width in widths:
        result = simulate_option_flow(
            trading_date,
            underlying_bars,
            option_bars,
            replace(settings, width=width),
        )
        result = replace(result, width=width)
        if result.entered:
            return result
        rejected = result
        if result.reason in {
            "underlying entry bar missing",
            "signal volume is below minimum",
            "absolute flow score is below threshold",
        }:
            return result
    assert rejected is not None
    return replace(rejected, reason="no candidate width passed entry and risk gates")


def adaptive_metrics(results: list[OptionFlowResult]) -> dict[str, object]:
    metrics = option_flow_metrics(results)
    traded = [result for result in results if result.entered]
    selected: dict[str, int] = {}
    for result in traded:
        label = str(result.width)
        selected[label] = selected.get(label, 0) + 1
    return {
        **metrics,
        "average_width": (
            str(
                Decimal(str(mean(result.width for result in traded))).quantize(
                    Decimal("0.01")
                )
            )
            if traded
            else None
        ),
        "selected_widths": selected,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate adaptive-width SPY opening option-flow spreads"
    )
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--oos-start", type=date.fromisoformat, required=True)
    parser.add_argument(
        "--cache-dir", type=Path, default=Path("state/adaptive-option-flow-cache")
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
        entry = next(
            (bar for bar in underlying if bar.timestamp.time() == base.entry_time), None
        )
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
            simulate_adaptive_option_flow(
                trading_date, underlying, options, base
            )
        )
        rows[f"{split}_stress"].append(
            simulate_adaptive_option_flow(
                trading_date, underlying, options, stress
            )
        )

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
