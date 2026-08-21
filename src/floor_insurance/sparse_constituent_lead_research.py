"""Locked sparse-IEX follow-up to the constituent lead-lag screen."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, replace
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from .config import Config
from .constituent_lead_research import (
    ACCEPTANCE_RULE,
    CONSTITUENTS,
    DATA_LIMITATION,
    LeadResult,
    LeadSettings,
    block_bootstrap,
    chronological_splits,
    lead_metrics,
    simulate_sparse_lead_session,
    sparse_lead_residual,
    sparse_member_return_history,
    spy_observation_history,
    viable,
)
from .directional_backtest import HistoricalData

MINIMUM_MEMBERS = 8


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate preregistered sparse-IEX constituent lead-lag signal"
    )
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--oos-start", type=date.fromisoformat, required=True)
    parser.add_argument("--oos-end", type=date.fromisoformat, required=True)
    parser.add_argument(
        "--cache-dir", type=Path, default=Path("state/constituent-lead-cache")
    )
    parser.add_argument("--report-out", type=Path)
    return parser


def _settings_payload(settings: LeadSettings) -> dict[str, object]:
    return {key: str(value) for key, value in asdict(settings).items()}


def main() -> int:
    args = _parser().parse_args()
    development_end = args.oos_start - timedelta(days=1)
    data = HistoricalData(Config.from_env(), args.cache_dir)
    base = LeadSettings()
    stress = replace(base, round_trip_cost=Decimal("0.0002"))

    member_returns: dict[str, dict[str, Decimal]] = {}
    for position, symbol in enumerate(CONSTITUENTS, start=1):
        sessions = data.stock_sessions(args.start, development_end, symbol)
        member_returns[symbol] = sparse_member_return_history(sessions, base)
        print(f"[{position}/{len(CONSTITUENTS) + 1}] loaded {symbol}", file=sys.stderr)
        del sessions
    spy_sessions = data.stock_sessions(args.start, development_end, "SPY")
    spy = spy_observation_history(spy_sessions, base)
    dates = sorted(spy)
    print(f"[{len(CONSTITUENTS) + 1}/{len(CONSTITUENTS) + 1}] loaded SPY", file=sys.stderr)
    del spy_sessions

    splits = chronological_splits(dates)
    rows: dict[str, list[LeadResult]] = {
        "train": [],
        "validation": [],
        "train_stress": [],
        "validation_stress": [],
    }
    prior_absolute: list[Decimal] = []
    for trading_date in dates:
        split = "train" if trading_date in splits["train"] else "validation"
        rows[split].append(
            simulate_sparse_lead_session(
                trading_date,
                spy,
                member_returns,
                prior_absolute,
                base,
                MINIMUM_MEMBERS,
            )
        )
        rows[f"{split}_stress"].append(
            simulate_sparse_lead_session(
                trading_date,
                spy,
                member_returns,
                prior_absolute,
                stress,
                MINIMUM_MEMBERS,
            )
        )
        observed = sparse_lead_residual(
            trading_date, spy, member_returns, MINIMUM_MEMBERS
        )
        if observed is not None:
            prior_absolute.append(abs(observed[0]))

    metrics = {name: lead_metrics(values) for name, values in rows.items()}
    combined = [
        result.net_return
        for split in ("train", "validation")
        for result in rows[split]
        if result.signaled
    ]
    report = {
        "acceptance_rule": ACCEPTANCE_RULE,
        "data_limitation": DATA_LIMITATION,
        "sparse_data_rule": (
            "At least 8 of 12 members; first bar by 10:56 and last bar by 10:58 "
            "inside the locked 10:55-10:59 window."
        ),
        "development_passed": viable(metrics),
        "constituents": list(CONSTITUENTS),
        "minimum_members": MINIMUM_MEMBERS,
        "base_settings": _settings_payload(base),
        "stress_settings": _settings_payload(stress),
        "development_start": dates[0],
        "development_end": dates[-1],
        "development_sessions": len(dates),
        "train_sessions": len(splits["train"]),
        "validation_sessions": len(splits["validation"]),
        "oos_revealed": False,
        "oos_downloaded": False,
        "oos_start": args.oos_start.isoformat(),
        "oos_end": args.oos_end.isoformat(),
        "oos_sessions_expected": 60,
        "cache_files": sorted(path.name for path in args.cache_dir.glob("*.json")),
        "metrics": metrics,
        "bootstrap": block_bootstrap(combined),
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
