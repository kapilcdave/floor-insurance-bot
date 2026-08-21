"""Locked deployment-compatible 15-minute-delayed option-flow research."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from datetime import date, time
from decimal import Decimal
from pathlib import Path

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
    required_symbols,
    simulate_option_flow,
    viable,
)


def delayed_settings(*, stress: bool = False) -> OptionFlowSettings:
    return OptionFlowSettings(
        signal_end=time(10, 0),
        entry_time=time(10, 15),
        adverse_fill_per_leg=Decimal("0.03" if stress else "0.02"),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate deployment-compatible delayed SPY option flow"
    )
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--oos-start", type=date.fromisoformat, required=True)
    parser.add_argument(
        "--cache-dir", type=Path, default=Path("state/delayed-option-flow-cache")
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
    base = delayed_settings()
    stress = delayed_settings(stress=True)
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
        "deployment_reason": (
            "The 09:30-09:59 signal is held fixed; entry waits until 10:15 so "
            "15-minute-delayed free option trades are observable."
        ),
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
