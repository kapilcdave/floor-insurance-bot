"""Preregistered SPY 0DTE local-surface butterfly rejection screen."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, time, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from pathlib import Path
from statistics import mean

from .config import Config
from .directional import PriceBar
from .directional_backtest import HistoricalData, research_splits
from .fifty_credit_research import moving_block_bootstrap
from .iron_fly_research import center_strike, occ_option_for

CENT = Decimal("0.01")
HUNDRED = Decimal("100")
RATIO = Decimal("0.0001")

CENTER_OFFSETS = tuple(range(-3, 4))

ACCEPTANCE_RULE = (
    "At least 100 training and 30 validation trades; positive average P&L and "
    "profit factor at least 1.25 on both base splits; maximum drawdown no worse "
    "than -$500 on either base split; positive average P&L on both stress "
    "splits; and at least ten call and ten put butterflies in both base splits. "
    "The final chronological holdout remains sealed."
)

DATA_LIMITATION = (
    "Alpaca Basic one-minute option trade bars are not synchronized NBBO quotes. "
    "Nonsynchronous trades can create false convexity and parity gaps, so this "
    "cost-stressed simulation is a rejection screen rather than proof of an "
    "executable surface arbitrage."
)


@dataclass(frozen=True)
class SurfaceSettings:
    symbol: str = "SPY"
    wing_width: Decimal = Decimal("1")
    center_offsets: tuple[int, ...] = CENTER_OFFSETS
    entry_time: time = time(11, 0)
    exit_time: time = time(12, 0)
    minimum_parity_gap: Decimal = Decimal("0.08")
    maximum_entry_debit: Decimal = Decimal("0.10")
    adverse_fill_per_unit: Decimal = Decimal("0.005")
    fees_per_butterfly: Decimal = Decimal("0.20")
    maximum_risk_dollars: Decimal = Decimal("100")
    max_exit_mark_age_minutes: int = 5


@dataclass(frozen=True)
class SurfaceCandidate:
    center: Decimal
    kind: str
    parity_gap: Decimal
    raw_entry_debit: Decimal
    entry_debit: Decimal
    maximum_risk: Decimal
    symbols: tuple[str, str, str]


@dataclass(frozen=True)
class SurfaceResult:
    trading_date: str
    entered: bool
    reason: str
    kind: str = "none"
    center: Decimal = Decimal("0")
    parity_gap: Decimal = Decimal("0")
    raw_entry_debit: Decimal = Decimal("0")
    entry_debit: Decimal = Decimal("0")
    maximum_risk: Decimal = Decimal("0")
    exit_credit: Decimal = Decimal("0")
    pnl: Decimal = Decimal("0")


def _at(bars: list[PriceBar], moment: time) -> PriceBar | None:
    return next((bar for bar in bars if bar.timestamp.time() == moment), None)


def butterfly_contracts(
    trading_date: date, center: Decimal, kind: str, settings: SurfaceSettings
) -> tuple[str, str, str]:
    option_type = kind.upper()
    if option_type not in {"C", "P"}:
        raise ValueError("kind must be C or P")
    return tuple(
        occ_option_for(settings.symbol, trading_date, option_type, strike)
        for strike in (
            center - settings.wing_width,
            center,
            center + settings.wing_width,
        )
    )


def required_symbols(trading_date: date, spot: Decimal, settings: SurfaceSettings) -> list[str]:
    base = center_strike(spot)
    return sorted(
        {
            symbol
            for offset in settings.center_offsets
            for kind in ("C", "P")
            for symbol in butterfly_contracts(trading_date, base + Decimal(offset), kind, settings)
        }
    )


def _raw_butterfly_value(lower: PriceBar, middle: PriceBar, upper: PriceBar) -> Decimal:
    return (lower.open - Decimal("2") * middle.open + upper.open).quantize(CENT)


def select_surface_candidate(
    trading_date: date,
    spot: Decimal,
    option_bars: dict[str, list[PriceBar]],
    settings: SurfaceSettings,
) -> SurfaceCandidate | None:
    base = center_strike(spot)
    candidates: list[SurfaceCandidate] = []
    for offset in settings.center_offsets:
        center = base + Decimal(offset)
        contracts = {
            kind: butterfly_contracts(trading_date, center, kind, settings) for kind in ("C", "P")
        }
        entries: dict[str, tuple[PriceBar, PriceBar, PriceBar]] = {}
        for kind, symbols in contracts.items():
            values = tuple(
                _at(option_bars.get(symbol, []), settings.entry_time) for symbol in symbols
            )
            if all(value is not None for value in values):
                entries[kind] = values  # type: ignore[assignment]
        if set(entries) != {"C", "P"}:
            continue
        raw = {kind: _raw_butterfly_value(*values) for kind, values in entries.items()}
        gap = abs(raw["C"] - raw["P"]).quantize(CENT)
        if gap < settings.minimum_parity_gap:
            continue
        kind = "C" if raw["C"] <= raw["P"] else "P"
        modeled = raw[kind] + settings.adverse_fill_per_unit * Decimal("4")
        entry_debit = max(Decimal("0"), modeled).quantize(CENT, rounding=ROUND_CEILING)
        maximum_risk = (entry_debit * HUNDRED + settings.fees_per_butterfly).quantize(CENT)
        if (
            entry_debit > settings.maximum_entry_debit
            or maximum_risk > settings.maximum_risk_dollars
        ):
            continue
        candidates.append(
            SurfaceCandidate(
                center,
                kind,
                gap,
                raw[kind],
                entry_debit,
                maximum_risk,
                contracts[kind],
            )
        )
    if not candidates:
        return None
    return sorted(
        candidates,
        key=lambda item: (
            -item.parity_gap,
            item.entry_debit,
            abs(item.center - spot),
            item.center,
            item.kind,
        ),
    )[0]


def simulate_surface_butterfly(
    trading_date: str,
    underlying_bars: list[PriceBar],
    option_bars: dict[str, list[PriceBar]],
    settings: SurfaceSettings,
) -> SurfaceResult:
    entry = _at(underlying_bars, settings.entry_time)
    if entry is None:
        return SurfaceResult(trading_date, False, "underlying entry bar missing")
    day = date.fromisoformat(trading_date)
    candidate = select_surface_candidate(day, entry.open, option_bars, settings)
    if candidate is None:
        return SurfaceResult(
            trading_date, False, "no candidate passed parity, debit, and data gates"
        )

    lower_bars, middle_bars, upper_bars = (
        option_bars.get(symbol, []) for symbol in candidate.symbols
    )
    by_timestamp = [
        {bar.timestamp: bar for bar in bars} for bars in (lower_bars, middle_bars, upper_bars)
    ]
    synchronized = sorted(set(by_timestamp[0]) & set(by_timestamp[1]) & set(by_timestamp[2]))
    marks: list[tuple[datetime, Decimal]] = []
    for timestamp in synchronized:
        if timestamp.time() <= settings.entry_time or timestamp.time() > settings.exit_time:
            continue
        raw_credit = (
            by_timestamp[0][timestamp].close
            - Decimal("2") * by_timestamp[1][timestamp].close
            + by_timestamp[2][timestamp].close
            - settings.adverse_fill_per_unit * Decimal("4")
        )
        credit = max(Decimal("0"), min(settings.wing_width, raw_credit)).quantize(
            CENT, rounding=ROUND_FLOOR
        )
        marks.append((timestamp, credit))

    reason = "exit_missing_mark"
    exit_credit = Decimal("0")
    exact = next((item for item in marks if item[0].time() == settings.exit_time), None)
    if exact is not None:
        reason, exit_credit = "timed_exit", exact[1]
    elif marks:
        timestamp, credit = marks[-1]
        exit_at = datetime.combine(timestamp.date(), settings.exit_time, timestamp.tzinfo)
        if (
            timedelta(0)
            <= exit_at - timestamp
            <= timedelta(minutes=settings.max_exit_mark_age_minutes)
        ):
            reason, exit_credit = "timed_exit_last_mark", credit

    pnl = ((exit_credit - candidate.entry_debit) * HUNDRED - settings.fees_per_butterfly).quantize(
        CENT
    )
    return SurfaceResult(
        trading_date,
        True,
        reason,
        candidate.kind,
        candidate.center,
        candidate.parity_gap,
        candidate.raw_entry_debit,
        candidate.entry_debit,
        candidate.maximum_risk,
        exit_credit,
        pnl,
    )


def surface_metrics(results: list[SurfaceResult]) -> dict[str, object]:
    traded = [result for result in results if result.entered]
    pnls = [result.pnl for result in traded]
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [pnl for pnl in pnls if pnl < 0]
    cumulative = Decimal("0")
    peak = Decimal("0")
    drawdown = Decimal("0")
    for pnl in pnls:
        cumulative += pnl
        peak = max(peak, cumulative)
        drawdown = min(drawdown, cumulative - peak)
    skips: dict[str, int] = {}
    exits: dict[str, int] = {}
    for result in results:
        target = exits if result.entered else skips
        target[result.reason] = target.get(result.reason, 0) + 1
    gross_profit = sum(wins, Decimal("0"))
    gross_loss = abs(sum(losses, Decimal("0")))
    return {
        "sessions": len(results),
        "trades": len(traded),
        "calls": sum(result.kind == "C" for result in traded),
        "puts": sum(result.kind == "P" for result in traded),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(pnls), 4) if pnls else None,
        "average_parity_gap": (
            str(Decimal(str(mean(result.parity_gap for result in traded))).quantize(CENT))
            if traded
            else None
        ),
        "average_entry_debit": (
            str(Decimal(str(mean(result.entry_debit for result in traded))).quantize(CENT))
            if traded
            else None
        ),
        "average_maximum_risk": (
            str(Decimal(str(mean(result.maximum_risk for result in traded))).quantize(CENT))
            if traded
            else None
        ),
        "total_pnl": str(sum(pnls, Decimal("0")).quantize(CENT)),
        "average_pnl": (str(Decimal(str(mean(pnls))).quantize(CENT)) if pnls else None),
        "profit_factor": (str((gross_profit / gross_loss).quantize(RATIO)) if gross_loss else None),
        "max_drawdown": str(drawdown.quantize(CENT)),
        "worst_trade": str(min(pnls).quantize(CENT)) if pnls else None,
        "exits": exits,
        "skips": skips,
    }


def viable(report: dict[str, dict[str, object]]) -> bool:
    for split, minimum in (("train", 100), ("validation", 30)):
        metrics = report[split]
        stress = report[f"{split}_stress"]
        if int(metrics["trades"]) < minimum:
            return False
        if int(metrics["calls"]) < 10 or int(metrics["puts"]) < 10:
            return False
        if metrics["average_pnl"] is None or Decimal(str(metrics["average_pnl"])) <= 0:
            return False
        if metrics["profit_factor"] is None or Decimal(str(metrics["profit_factor"])) < Decimal(
            "1.25"
        ):
            return False
        if stress["average_pnl"] is None or Decimal(str(stress["average_pnl"])) <= 0:
            return False
        if Decimal(str(metrics["max_drawdown"])) < Decimal("-500"):
            return False
    return True


def _settings_payload(settings: SurfaceSettings) -> dict[str, object]:
    return {key: str(value) for key, value in asdict(settings).items()}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate the preregistered SPY 0DTE surface butterfly"
    )
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--oos-start", type=date.fromisoformat, required=True)
    parser.add_argument("--cache-dir", type=Path, default=Path("state/surface-butterfly-cache"))
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
    rows: dict[str, list[SurfaceResult]] = {
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
        options: dict[str, list[PriceBar]] = {}
        entry = _at(bars, base.entry_time)
        if entry is not None:
            day = date.fromisoformat(trading_date)
            options = data.option_bars(day, required_symbols(day, entry.open, base))
            fetched += 1
            if fetched % 25 == 0:
                print(f"[{position}/{len(dates)}] {trading_date}", file=sys.stderr)
        split = "train" if trading_date in splits["train"] else "validation"
        rows[split].append(simulate_surface_butterfly(trading_date, bars, options, base))
        rows[f"{split}_stress"].append(
            simulate_surface_butterfly(trading_date, bars, options, stress)
        )

    metrics = {name: surface_metrics(values) for name, values in rows.items()}
    combined_pnls = [
        result.pnl for split in ("train", "validation") for result in rows[split] if result.entered
    ]
    locked = sorted(splits["out_of_sample"])
    report = {
        "acceptance_rule": ACCEPTANCE_RULE,
        "data_limitation": DATA_LIMITATION,
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
