from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import date, time
from decimal import Decimal
from pathlib import Path

from .config import Config
from .directional import DirectionalSettings, SignalModel
from .directional_backtest import HistoricalData, directional_metrics, run_research


def experiment_settings() -> dict[str, DirectionalSettings]:
    baseline = DirectionalSettings()
    noon = replace(baseline, hard_close=time(12, 0))
    return {
        "breakout_1500": baseline,
        "volume_breakout_1200": replace(
            noon, signal_model=SignalModel.OPENING_RANGE_VOLUME
        ),
        "vwap_momentum_1130": replace(
            baseline,
            signal_model=SignalModel.VWAP_MOMENTUM,
            hard_close=time(11, 30),
        ),
        "breakout_1030": replace(baseline, hard_close=time(10, 30)),
        "breakout_1200": noon,
        "vwap_reversion_1130": replace(
            baseline,
            signal_model=SignalModel.VWAP_REVERSION,
            hard_close=time(11, 30),
        ),
        "gap_continuation_1200": replace(
            noon, signal_model=SignalModel.GAP_CONTINUATION
        ),
        "gap_fade_1200": replace(noon, signal_model=SignalModel.GAP_FADE),
    }


def accepted(report: dict[str, dict[str, object]]) -> bool:
    train = report["train"]
    validation = report["validation"]
    return (
        Decimal(str(train["total_pnl"])) > 0
        and Decimal(str(validation["total_pnl"])) > 0
        and Decimal(str(train["profit_factor"] or "0")) > 1
        and Decimal(str(validation["profit_factor"] or "0")) > 1
        and int(validation["trades"]) >= 20
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the fixed directional experiment ledger without revealing OOS"
    )
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--oos-start", type=date.fromisoformat, required=True)
    parser.add_argument("--cache-dir", type=Path, default=Path("state/backtest-cache"))
    return parser


def main() -> int:
    args = _parser().parse_args()
    config = Config.from_env()
    data = HistoricalData(config, args.cache_dir)
    experiments: dict[str, dict[str, object]] = {}
    for name, settings in experiment_settings().items():
        reports, metadata = run_research(
            data,
            args.start,
            args.end,
            settings,
            False,
            args.oos_start,
            False,
        )
        experiments[name] = {
            "settings": {
                "signal_model": settings.signal_model.value,
                "hard_close": settings.hard_close.isoformat(),
                "minimum_reward_risk": str(settings.minimum_reward_risk),
                "minimum_gap_fraction": str(settings.minimum_gap_fraction),
                "minimum_momentum_fraction": str(
                    settings.minimum_momentum_fraction
                ),
                "minimum_volume_ratio": str(settings.minimum_volume_ratio),
            },
            "train": directional_metrics(reports["train"]),
            "validation": directional_metrics(reports["validation"]),
            "oos_option_cache_preexisting": metadata[
                "oos_option_cache_preexisting"
            ],
        }
    selected = [name for name, report in experiments.items() if accepted(report)]
    print(
        json.dumps(
            {
                "acceptance_rule": (
                    "train and validation P&L > 0; train and validation profit "
                    "factor > 1; at least 20 validation trades"
                ),
                "accepted": selected,
                "oos_revealed": False,
                "experiments": experiments,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
