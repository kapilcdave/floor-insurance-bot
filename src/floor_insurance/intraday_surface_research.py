"""Preregistered intraday time-grid test of the surface butterfly."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

from .config import Config
from .directional import PriceBar
from .directional_backtest import HistoricalData, research_splits
from .fifty_credit_research import moving_block_bootstrap
from .surface_butterfly_research import (
    ACCEPTANCE_RULE,
    DATA_LIMITATION,
    SurfaceResult,
    SurfaceSettings,
    _at,
    required_symbols,
    simulate_surface_butterfly,
    surface_metrics,
    viable,
)

ENTRY_TIMES = (time(10), time(11), time(12), time(13), time(14))


@dataclass(frozen=True)
class IntradaySurfaceResult:
    trading_date: str
    entry_time: str
    result: SurfaceResult


def settings_for_entry(
    base: SurfaceSettings, entry_time: time
) -> SurfaceSettings:
    anchor = datetime.combine(date(2000, 1, 1), entry_time)
    exit_time = (anchor + timedelta(hours=1)).time()
    return replace(base, entry_time=entry_time, exit_time=exit_time)


def required_intraday_symbols(
    trading_date: date,
    underlying_bars: list[PriceBar],
    base: SurfaceSettings,
) -> list[str]:
    symbols: set[str] = set()
    for entry_time in ENTRY_TIMES:
        entry = _at(underlying_bars, entry_time)
        if entry is None:
            continue
        settings = settings_for_entry(base, entry_time)
        symbols.update(required_symbols(trading_date, entry.open, settings))
    return sorted(symbols)


def simulate_intraday_surface(
    trading_date: str,
    underlying_bars: list[PriceBar],
    option_bars: dict[str, list[PriceBar]],
    base: SurfaceSettings,
) -> IntradaySurfaceResult:
    for entry_time in ENTRY_TIMES:
        settings = settings_for_entry(base, entry_time)
        result = simulate_surface_butterfly(
            trading_date,
            underlying_bars,
            option_bars,
            settings,
        )
        if result.entered:
            return IntradaySurfaceResult(
                trading_date,
                entry_time.strftime("%H:%M"),
                result,
            )
    return IntradaySurfaceResult(
        trading_date,
        "none",
        SurfaceResult(
            trading_date,
            False,
            "no candidate passed any locked intraday scan",
        ),
    )


def intraday_metrics(results: list[IntradaySurfaceResult]) -> dict[str, object]:
    metrics = surface_metrics([item.result for item in results])
    entries_by_time = {
        value: sum(item.entry_time == value and item.result.entered for item in results)
        for value in (entry.strftime("%H:%M") for entry in ENTRY_TIMES)
    }
    metrics["entries_by_time"] = entries_by_time
    return metrics


def _settings_payload(settings: SurfaceSettings) -> dict[str, object]:
    return {key: str(value) for key, value in asdict(settings).items()}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate the preregistered intraday SPY surface butterfly"
    )
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--oos-start", type=date.fromisoformat, required=True)
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("state/intraday-surface-butterfly-cache"),
    )
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
    base = SurfaceSettings()
    stress = replace(base, adverse_fill_per_unit=Decimal("0.01"))
    rows: dict[str, list[IntradaySurfaceResult]] = {
        "train": [],
        "validation": [],
        "train_stress": [],
        "validation_stress": [],
    }
    fetched = 0
    for position, trading_date in enumerate(dates, start=1):
        if trading_date not in allowed:
            continue
        bars = sessions[trading_date]
        day = date.fromisoformat(trading_date)
        symbols = required_intraday_symbols(day, bars, base)
        options = data.option_bars(day, symbols) if symbols else {}
        fetched += 1
        if fetched % 10 == 0:
            print(f"[{position}/{len(dates)}] {trading_date}", file=sys.stderr)
        split = "train" if trading_date in splits["train"] else "validation"
        rows[split].append(
            simulate_intraday_surface(trading_date, bars, options, base)
        )
        rows[f"{split}_stress"].append(
            simulate_intraday_surface(trading_date, bars, options, stress)
        )

    metrics = {name: intraday_metrics(values) for name, values in rows.items()}
    combined_pnls = [
        item.result.pnl
        for split in ("train", "validation")
        for item in rows[split]
        if item.result.entered
    ]
    locked = sorted(splits["out_of_sample"])
    report = {
        "acceptance_rule": ACCEPTANCE_RULE,
        "data_limitation": DATA_LIMITATION,
        "development_passed": viable(metrics),
        "entry_times": [value.strftime("%H:%M") for value in ENTRY_TIMES],
        "selection": "first qualifying scan only; maximum one trade per session",
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
