"""Locked historical simulation for the fixed $0.50 SPY 0DTE spread.

Historical option bars are trade aggregates rather than synchronized NBBO
quotes. This module therefore applies explicit adverse-fill assumptions and is
a rejection screen, not proof that a live multi-leg order was executable.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, time, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from pathlib import Path
from statistics import mean

from .config import Config
from .credit_structure import occ_put_for
from .directional import PriceBar
from .directional_backtest import HistoricalData, research_splits

CENT = Decimal("0.01")
HUNDRED = Decimal("100")
BOOTSTRAP_SEED = 20260820
BOOTSTRAP_PATHS = 10_000
BOOTSTRAP_TRADES = 252
BOOTSTRAP_BLOCK = 5

ACCEPTANCE_RULE = (
    "At least 100 training and 30 validation trades; positive average P&L and "
    "profit factor at least 1.25 on both base splits; maximum drawdown no worse "
    "than -$250 on either base split; and positive average P&L on both stress "
    "splits. The final chronological holdout remains sealed."
)

DATA_LIMITATION = (
    "Alpaca one-minute historical option bars are trade aggregates, not "
    "contemporaneous NBBO quotes. Explicit adverse fills make this a rejection "
    "screen; they cannot prove a $0.50 live multi-leg limit was fillable."
)


@dataclass(frozen=True)
class FiftyCreditSettings:
    symbol: str = "SPY"
    width: Decimal = Decimal("1")
    target_credit: Decimal = Decimal("0.50")
    max_otm_dollars: int = 10
    stop_debit: Decimal = Decimal("0.75")
    entry_time: time = time(10, 0)
    hard_close: time = time(15, 0)
    adverse_fill_per_leg: Decimal = Decimal("0.02")
    fees_per_spread: Decimal = Decimal("0.10")
    stop_delay_bars: int = 0
    max_hard_close_mark_age_minutes: int = 5

    @property
    def minimum_raw_credit(self) -> Decimal:
        return (
            self.target_credit + self.adverse_fill_per_leg * Decimal("2")
        ).quantize(CENT)


@dataclass(frozen=True)
class FiftyCreditResult:
    trading_date: str
    entered: bool
    reason: str
    short_strike: Decimal = Decimal("0")
    long_strike: Decimal = Decimal("0")
    distance_otm: Decimal = Decimal("0")
    raw_entry_credit: Decimal = Decimal("0")
    entry_credit: Decimal = Decimal("0")
    exit_debit: Decimal = Decimal("0")
    pnl: Decimal = Decimal("0")


def _at(bars: list[PriceBar], moment: time) -> PriceBar | None:
    return next((bar for bar in bars if bar.timestamp.time() == moment), None)


def candidate_strikes(
    spot: Decimal, settings: FiftyCreditSettings
) -> list[tuple[Decimal, Decimal]]:
    """Return exact-width candidates ordered from farthest OTM inward."""

    highest = spot.to_integral_value(rounding=ROUND_FLOOR)
    lowest = highest - Decimal(settings.max_otm_dollars)
    return [
        (lowest + Decimal(offset), lowest + Decimal(offset) - settings.width)
        for offset in range(settings.max_otm_dollars + 1)
    ]


def required_symbols(
    trading_date: date, spot: Decimal, settings: FiftyCreditSettings
) -> list[str]:
    symbols = {
        occ_put_for(settings.symbol, trading_date, strike)
        for pair in candidate_strikes(spot, settings)
        for strike in pair
    }
    return sorted(symbols)


def _bounded_debit(value: Decimal, width: Decimal) -> Decimal:
    return max(Decimal("0"), min(width, value)).quantize(
        CENT, rounding=ROUND_CEILING
    )


def simulate_fifty_credit(
    trading_date: str,
    underlying_bars: list[PriceBar],
    option_bars: dict[str, list[PriceBar]],
    settings: FiftyCreditSettings,
) -> FiftyCreditResult:
    entry = _at(underlying_bars, settings.entry_time)
    if entry is None:
        return FiftyCreditResult(trading_date, False, "underlying entry bar missing")

    day = date.fromisoformat(trading_date)
    selected: tuple[Decimal, Decimal, Decimal, list[PriceBar], list[PriceBar]] | None = None
    had_entry_pair = False
    for short_strike, long_strike in candidate_strikes(entry.open, settings):
        short_bars = option_bars.get(
            occ_put_for(settings.symbol, day, short_strike), []
        )
        long_bars = option_bars.get(
            occ_put_for(settings.symbol, day, long_strike), []
        )
        short_entry = _at(short_bars, settings.entry_time)
        long_entry = _at(long_bars, settings.entry_time)
        if short_entry is None or long_entry is None:
            continue
        had_entry_pair = True
        raw_credit = (short_entry.open - long_entry.open).quantize(
            CENT, rounding=ROUND_FLOOR
        )
        if settings.minimum_raw_credit <= raw_credit < settings.width:
            selected = (
                short_strike,
                long_strike,
                raw_credit,
                short_bars,
                long_bars,
            )
            break

    if selected is None:
        reason = (
            "no candidate reached the raw credit threshold"
            if had_entry_pair
            else "no candidate had both entry marks"
        )
        return FiftyCreditResult(trading_date, False, reason)

    short_strike, long_strike, raw_credit, short_bars, long_bars = selected
    short_by_time = {bar.timestamp: bar for bar in short_bars}
    long_by_time = {bar.timestamp: bar for bar in long_bars}
    timestamps = sorted(set(short_by_time) & set(long_by_time))
    exit_slippage = settings.adverse_fill_per_leg * Decimal("2")

    observations: list[tuple[datetime, Decimal, Decimal]] = []
    for timestamp in timestamps:
        moment = timestamp.time()
        if moment <= settings.entry_time or moment > settings.hard_close:
            continue
        short = short_by_time[timestamp]
        long = long_by_time[timestamp]
        close_debit = _bounded_debit(
            short.close - long.close + exit_slippage, settings.width
        )
        adverse_debit = _bounded_debit(
            max(close_debit, short.high - long.low + exit_slippage),
            settings.width,
        )
        observations.append((timestamp, close_debit, adverse_debit))

    def settle(reason: str, debit: Decimal) -> FiftyCreditResult:
        debit = _bounded_debit(debit, settings.width)
        pnl = (
            (settings.target_credit - debit) * HUNDRED
            - settings.fees_per_spread
        ).quantize(CENT)
        return FiftyCreditResult(
            trading_date=trading_date,
            entered=True,
            reason=reason,
            short_strike=short_strike,
            long_strike=long_strike,
            distance_otm=(entry.open - short_strike).quantize(CENT),
            raw_entry_credit=raw_credit,
            entry_credit=settings.target_credit,
            exit_debit=debit,
            pnl=pnl,
        )

    for index, (timestamp, _close_debit, adverse_debit) in enumerate(observations):
        if adverse_debit < settings.stop_debit:
            continue
        exit_index = index + settings.stop_delay_bars
        if exit_index >= len(observations):
            return settle("spread_stop_missing_delayed_mark", settings.width)
        exit_at, _exit_close, exit_adverse = observations[exit_index]
        expected = timestamp + timedelta(minutes=settings.stop_delay_bars)
        if settings.stop_delay_bars and exit_at != expected:
            return settle("spread_stop_stale_delayed_mark", settings.width)
        return settle("spread_stop", max(settings.stop_debit, exit_adverse))

    hard_close_observation = next(
        (item for item in observations if item[0].time() == settings.hard_close),
        None,
    )
    if hard_close_observation is not None:
        return settle("hard_close", hard_close_observation[1])
    if observations:
        timestamp, close_debit, _adverse = observations[-1]
        hard_close_at = datetime.combine(timestamp.date(), settings.hard_close, timestamp.tzinfo)
        age = hard_close_at - timestamp
        if timedelta(0) <= age <= timedelta(
            minutes=settings.max_hard_close_mark_age_minutes
        ):
            return settle("hard_close_last_mark", close_debit)
    return settle("hard_close_missing_mark", settings.width)


def fifty_credit_metrics(results: list[FiftyCreditResult]) -> dict[str, object]:
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
    skips: dict[str, int] = {}
    exits: dict[str, int] = {}
    for result in results:
        target = exits if result.entered else skips
        target[result.reason] = target.get(result.reason, 0) + 1
    return {
        "sessions": len(results),
        "trades": len(traded),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(pnls), 4) if pnls else None,
        "average_raw_credit": (
            str(
                Decimal(str(mean(result.raw_entry_credit for result in traded))).quantize(
                    CENT
                )
            )
            if traded
            else None
        ),
        "average_distance_otm": (
            str(
                Decimal(str(mean(result.distance_otm for result in traded))).quantize(
                    CENT
                )
            )
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
        "worst_trade": str(min(pnls).quantize(CENT)) if pnls else None,
        "exits": exits,
        "skips": skips,
    }


def _percentile(values: list[int], percentile: Decimal) -> int:
    ordered = sorted(values)
    index = int(
        (Decimal(len(ordered) - 1) * percentile).to_integral_value(
            rounding=ROUND_FLOOR
        )
    )
    return ordered[index]


def moving_block_bootstrap(
    pnls: list[Decimal],
    *,
    paths: int = BOOTSTRAP_PATHS,
    trades_per_path: int = BOOTSTRAP_TRADES,
    block_length: int = BOOTSTRAP_BLOCK,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, object]:
    if not pnls:
        return {"paths": 0, "reason": "no trades"}
    if min(paths, trades_per_path, block_length) < 1:
        raise ValueError("bootstrap sizes must be positive")
    cents = [int((pnl * HUNDRED).to_integral_value()) for pnl in pnls]
    randomizer = random.Random(seed)
    totals: list[int] = []
    drawdowns: list[int] = []
    for _ in range(paths):
        path: list[int] = []
        while len(path) < trades_per_path:
            start = randomizer.randrange(len(cents))
            for offset in range(block_length):
                path.append(cents[(start + offset) % len(cents)])
                if len(path) == trades_per_path:
                    break
        cumulative = 0
        peak = 0
        drawdown = 0
        for pnl in path:
            cumulative += pnl
            peak = max(peak, cumulative)
            drawdown = min(drawdown, cumulative - peak)
        totals.append(cumulative)
        drawdowns.append(drawdown)

    def dollars(cents_value: int) -> str:
        return str((Decimal(cents_value) / HUNDRED).quantize(CENT))

    return {
        "seed": seed,
        "paths": paths,
        "trades_per_path": trades_per_path,
        "block_length": block_length,
        "probability_positive": round(sum(value > 0 for value in totals) / paths, 4),
        "annual_pnl_p05": dollars(_percentile(totals, Decimal("0.05"))),
        "annual_pnl_median": dollars(_percentile(totals, Decimal("0.50"))),
        "annual_pnl_p95": dollars(_percentile(totals, Decimal("0.95"))),
        "max_drawdown_p05": dollars(_percentile(drawdowns, Decimal("0.05"))),
        "max_drawdown_median": dollars(_percentile(drawdowns, Decimal("0.50"))),
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
        if factor is None or Decimal(str(factor)) < Decimal("1.25"):
            return False
        if stress_average is None or Decimal(str(stress_average)) <= 0:
            return False
        if Decimal(str(metrics["max_drawdown"])) < Decimal("-250"):
            return False
    return True


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate the preregistered fixed $0.50 SPY 0DTE spread"
    )
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--oos-start", type=date.fromisoformat, required=True)
    parser.add_argument(
        "--cache-dir", type=Path, default=Path("state/fifty-credit-cache")
    )
    parser.add_argument("--report-out", type=Path)
    return parser


def _settings_payload(settings: FiftyCreditSettings) -> dict[str, object]:
    return {
        key: str(value) if value is not None else None
        for key, value in asdict(settings).items()
    }


def main() -> int:
    args = _parser().parse_args()
    config = Config.from_env()
    data = HistoricalData(config, args.cache_dir)
    sessions = data.stock_sessions(args.start, args.end, "SPY")
    dates = list(sessions)
    splits = research_splits(dates, args.oos_start)
    allowed = splits["train"] | splits["validation"]
    base = FiftyCreditSettings()
    stress = replace(
        base,
        adverse_fill_per_leg=Decimal("0.03"),
        stop_delay_bars=1,
    )
    rows: dict[str, list[FiftyCreditResult]] = {
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
        entry = _at(underlying, base.entry_time)
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
            simulate_fifty_credit(trading_date, underlying, options, base)
        )
        rows[f"{split}_stress"].append(
            simulate_fifty_credit(trading_date, underlying, options, stress)
        )

    metrics = {name: fifty_credit_metrics(values) for name, values in rows.items()}
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
        "development_passed": viable(metrics),
        "base_settings": _settings_payload(base),
        "stress_settings": _settings_payload(stress),
        "sessions_evaluated": len(splits["train"] | splits["validation"]),
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
        "bootstrap": moving_block_bootstrap(combined_pnls),
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
