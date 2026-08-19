from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from datetime import date, time
from decimal import Decimal
from pathlib import Path

from .config import Config
from .directional import DirectionalSettings, SignalModel, VixRegime
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
        # Round three: the baseline breakout split by prior-close Cboe volatility.
        # Each pair partitions the same sessions, so a favourable half must be
        # read alongside its complement rather than on its own.
        "breakout_1500_low_vix": replace(
            baseline, vix_regime=VixRegime.LOW_PERCENTILE
        ),
        "breakout_1500_high_vix": replace(
            baseline, vix_regime=VixRegime.HIGH_PERCENTILE
        ),
        "breakout_1500_contango": replace(baseline, vix_regime=VixRegime.CONTANGO),
        "breakout_1500_backwardation": replace(
            baseline, vix_regime=VixRegime.BACKWARDATION
        ),
        "breakout_1500_cheap_1d": replace(
            baseline, vix_regime=VixRegime.CHEAP_ONE_DAY
        ),
        "breakout_1500_rich_1d": replace(baseline, vix_regime=VixRegime.RICH_ONE_DAY),
    }


REGIME_PARTITIONS: tuple[tuple[str, str], ...] = (
    ("breakout_1500_low_vix", "breakout_1500_high_vix"),
    ("breakout_1500_contango", "breakout_1500_backwardation"),
    ("breakout_1500_cheap_1d", "breakout_1500_rich_1d"),
)


ACCEPTANCE_RULE = (
    "train and validation P&L > 0; train and validation profit factor > 1; at "
    "least 20 validation trades"
)
PROMOTION_RULE = (
    "declared 2026-08-19 before any path-independent ledger was run: a variant "
    "may only be considered for the sealed holdout if it passes the acceptance "
    "rule under equity-proportional sizing AND again under constant reference "
    "equity, and if its volatility family reconciles exactly against the "
    "unfiltered breakout under constant sizing. Equity-proportional results are "
    "not comparable across variants, because each variant compounds its own "
    "balance and therefore sizes, or skips, boundary days differently."
)


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


def partition_audit(
    experiments: dict[str, dict[str, object]],
) -> list[dict[str, object]]:
    """Show whether each volatility family really splits the baseline sessions.

    Under a path-independent sizing mode the halves must sum exactly to the
    unfiltered breakout trade count and P&L; any residual means some sessions
    lacked the required Cboe series. Under equity-proportional sizing a residual
    is expected, because each variant compounds its own balance and therefore
    sizes, or skips, boundary days differently. That is precisely why a filtered
    result cannot be compared with the unfiltered one under proportional sizing.
    """

    baseline = experiments.get("breakout_1500")
    audit: list[dict[str, object]] = []
    for below, at_or_above in REGIME_PARTITIONS:
        if not (baseline and below in experiments and at_or_above in experiments):
            continue
        rows = []
        for split in ("train", "validation"):
            halves = sum(
                int(experiments[name][split]["trades"])  # type: ignore[index]
                for name in (below, at_or_above)
            )
            halves_pnl = sum(
                Decimal(str(experiments[name][split]["total_pnl"]))  # type: ignore[index]
                for name in (below, at_or_above)
            )
            unfiltered = int(baseline[split]["trades"])  # type: ignore[index]
            unfiltered_pnl = Decimal(str(baseline[split]["total_pnl"]))  # type: ignore[index]
            rows.append(
                {
                    "split": split,
                    "unfiltered_trades": unfiltered,
                    "partition_trades": halves,
                    "unexplained_trades": unfiltered - halves,
                    "unfiltered_pnl": str(unfiltered_pnl),
                    "partition_pnl": str(halves_pnl),
                    "unexplained_pnl": str(unfiltered_pnl - halves_pnl),
                }
            )
        audit.append({"family": [below, at_or_above], "splits": rows})
    return audit


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the fixed directional experiment ledger without revealing OOS"
    )
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--oos-start", type=date.fromisoformat, required=True)
    parser.add_argument(
        "--fixed-contracts",
        type=int,
        help="size every variant identically, ignoring the risk budget; diagnostic only",
    )
    parser.add_argument(
        "--constant-sizing",
        action="store_true",
        help="apply the risk rule to the starting balance so variants stay comparable",
    )
    parser.add_argument("--cache-dir", type=Path, default=Path("state/backtest-cache"))
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.fixed_contracts is not None and args.fixed_contracts < 1:
        raise SystemExit("--fixed-contracts must be at least one")
    config = Config.from_env()
    data = HistoricalData(config, args.cache_dir)
    experiments: dict[str, dict[str, object]] = {}
    ledger = experiment_settings()
    sizing = "equity proportional, path dependent"
    for position, (name, declared) in enumerate(ledger.items(), start=1):
        print(f"[{position}/{len(ledger)}] {name}", file=sys.stderr)
        settings = replace(
            declared,
            fixed_contracts=args.fixed_contracts,
            constant_sizing=args.constant_sizing,
        )
        reports, metadata = run_research(
            data,
            args.start,
            args.end,
            settings,
            False,
            args.oos_start,
            False,
        )
        sizing = str(metadata["sizing"])
        experiments[name] = {
            "settings": {
                "signal_model": settings.signal_model.value,
                "hard_close": settings.hard_close.isoformat(),
                "vix_regime": settings.vix_regime.value,
                "vix_percentile_threshold": str(settings.vix_percentile_threshold),
                "term_slope_threshold": str(settings.term_slope_threshold),
                "one_day_ratio_threshold": str(settings.one_day_ratio_threshold),
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
                "acceptance_rule": ACCEPTANCE_RULE,
                "promotion_rule": PROMOTION_RULE,
                "sizing": sizing,
                "accepted": selected,
                "oos_revealed": False,
                "partition_audit": partition_audit(experiments),
                "experiments": experiments,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
