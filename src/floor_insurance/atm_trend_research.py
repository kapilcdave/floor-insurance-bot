"""Research the declared ATM 20-day-trend credit-spread hypothesis."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, replace
from datetime import date, time
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from pathlib import Path
from statistics import mean

from .config import Config
from .credit_structure import occ_put
from .directional import PriceBar
from .directional_backtest import HistoricalData, research_splits
from .strategy import max_loss_per_contract, stop_close_debit
from .trend import TrendMode, trend_signal

CENT = Decimal("0.01")
HUNDRED = Decimal("100")


@dataclass(frozen=True)
class AtmTrendSettings:
    trend_mode: TrendMode
    stop_debit_multiple: Decimal | None
    take_profit_fraction: Decimal | None
    trend_window: int = 20
    width: Decimal = Decimal("1")
    min_credit: Decimal = Decimal("0.10")
    max_loss_dollars: Decimal = Decimal("100")
    entry_time: time = time(9, 45)
    hard_close: time = time(15, 0)
    slippage_per_side: Decimal = Decimal("0.02")
    fees_per_spread: Decimal = Decimal("0.10")

    @property
    def label(self) -> str:
        stop = (
            "hold"
            if self.stop_debit_multiple is None
            else f"stop{self.stop_debit_multiple}x"
        )
        target = (
            "no_tp"
            if self.take_profit_fraction is None
            else f"tp{self.take_profit_fraction}"
        )
        return f"{self.trend_mode.value}_{stop}_{target}"


@dataclass(frozen=True)
class AtmTrendResult:
    trading_date: str
    eligible: bool
    entered: bool
    reason: str
    credit: Decimal = Decimal("0")
    exit_debit: Decimal = Decimal("0")
    max_loss: Decimal = Decimal("0")
    pnl: Decimal = Decimal("0")


STOP_MULTIPLES: tuple[Decimal | None, ...] = (
    Decimal("1.5"),
    Decimal("2"),
    None,
)
TAKE_PROFITS: tuple[Decimal | None, ...] = (Decimal("0.5"), None)
TREND_MODES = (TrendMode.ABOVE, TrendMode.CROSSOVER)

ACCEPTANCE_RULE = (
    "A candidate must have positive average net P&L and profit factor above one "
    "on both chronological training and validation, with at least 100 training "
    "trades and 30 validation trades. Candidates are ranked by training average "
    "P&L only; validation is a pass/fail gate. The final holdout remains sealed "
    "until one configuration is committed as the candidate."
)


def settings_grid() -> list[AtmTrendSettings]:
    return [
        AtmTrendSettings(mode, stop, target)
        for mode in TREND_MODES
        for stop in STOP_MULTIPLES
        for target in TAKE_PROFITS
    ]


def atm_strikes(spot: Decimal, width: Decimal) -> tuple[Decimal, Decimal]:
    short = spot.to_integral_value(rounding=ROUND_FLOOR)
    return short, short - width


def required_symbols(
    trading_date: date, spot: Decimal, settings: AtmTrendSettings
) -> list[str]:
    short, long = atm_strikes(spot, settings.width)
    return [occ_put(trading_date, short), occ_put(trading_date, long)]


def _at(bars: list[PriceBar], moment: time) -> PriceBar | None:
    return next((bar for bar in bars if bar.timestamp.time() == moment), None)


def simulate_atm_trend(
    trading_date: str,
    prior_closes: list[Decimal],
    spy_bars: list[PriceBar],
    option_bars: dict[str, list[PriceBar]],
    settings: AtmTrendSettings,
) -> AtmTrendResult:
    observations = settings.trend_window + (
        1 if settings.trend_mode == TrendMode.CROSSOVER else 0
    )
    try:
        signal = trend_signal(
            prior_closes[-observations:],
            window=settings.trend_window,
            mode=settings.trend_mode,
        )
    except ValueError as exc:
        return AtmTrendResult(trading_date, False, False, str(exc))
    if not signal.eligible:
        return AtmTrendResult(trading_date, False, False, "trend signal not eligible")

    entry = _at(spy_bars, settings.entry_time)
    if entry is None:
        return AtmTrendResult(trading_date, True, False, "no SPY entry bar")
    day = date.fromisoformat(trading_date)
    short_strike, long_strike = atm_strikes(entry.open, settings.width)
    short_bars = option_bars.get(occ_put(day, short_strike), [])
    long_bars = option_bars.get(occ_put(day, long_strike), [])
    short_entry = _at(short_bars, settings.entry_time)
    long_entry = _at(long_bars, settings.entry_time)
    if short_entry is None or long_entry is None:
        return AtmTrendResult(trading_date, True, False, "one or both entry legs missing")

    credit = (
        short_entry.open - long_entry.open - settings.slippage_per_side
    ).quantize(CENT, rounding=ROUND_FLOOR)
    if credit < settings.min_credit:
        return AtmTrendResult(trading_date, True, False, "credit below minimum", credit)
    if credit >= settings.width:
        return AtmTrendResult(trading_date, True, False, "credit reached spread width", credit)
    maximum_loss = max_loss_per_contract(settings.width, credit)
    if maximum_loss > settings.max_loss_dollars:
        return AtmTrendResult(
            trading_date, True, False, "maximum loss above cap", credit, max_loss=maximum_loss
        )

    short_by_time = {bar.timestamp.time(): bar for bar in short_bars}
    long_by_time = {bar.timestamp.time(): bar for bar in long_bars}
    stop = (
        stop_close_debit(settings.width, credit, settings.stop_debit_multiple)
        if settings.stop_debit_multiple is not None
        else None
    )
    target = (
        (credit * settings.take_profit_fraction).quantize(CENT, rounding=ROUND_CEILING)
        if settings.take_profit_fraction is not None
        else None
    )
    last_debit = credit

    def spread_debit(moment: time) -> Decimal | None:
        short_bar = short_by_time.get(moment)
        long_bar = long_by_time.get(moment)
        if short_bar is None or long_bar is None:
            return None
        raw = short_bar.close - long_bar.close + settings.slippage_per_side
        return max(Decimal("0"), min(settings.width, raw)).quantize(
            CENT, rounding=ROUND_CEILING
        )

    def settle(reason: str, debit: Decimal) -> AtmTrendResult:
        pnl = (
            (credit - debit) * HUNDRED - settings.fees_per_spread
        ).quantize(CENT)
        return AtmTrendResult(
            trading_date,
            True,
            True,
            reason,
            credit,
            debit,
            maximum_loss,
            pnl,
        )

    for bar in spy_bars:
        moment = bar.timestamp.time()
        if moment <= settings.entry_time:
            continue
        if moment > settings.hard_close:
            break
        debit = spread_debit(moment)
        if debit is not None:
            last_debit = debit
        if stop is not None and debit is not None and debit >= stop:
            return settle("spread_stop", debit)
        if target is not None and debit is not None and debit <= target:
            return settle("take_profit", debit)
        if moment == settings.hard_close:
            return settle("hard_close", last_debit)
    return settle("session_ended", last_debit)


def atm_metrics(results: list[AtmTrendResult]) -> dict[str, object]:
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
    gross_profit = sum(wins, Decimal("0"))
    gross_loss = abs(sum(losses, Decimal("0")))
    return {
        "sessions": len(results),
        "eligible_signals": sum(result.eligible for result in results),
        "trades": len(traded),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(pnls), 4) if pnls else None,
        "total_pnl": str(sum(pnls, Decimal("0")).quantize(CENT)),
        "average_pnl": (
            str(Decimal(str(mean(pnls))).quantize(CENT)) if pnls else None
        ),
        "profit_factor": (
            str((gross_profit / gross_loss).quantize(Decimal("0.0001")))
            if gross_loss
            else None
        ),
        "max_drawdown": str(drawdown.quantize(CENT)),
        "spread_stops": sum(result.reason == "spread_stop" for result in traded),
        "take_profits": sum(result.reason == "take_profit" for result in traded),
        "hard_closes": sum(result.reason == "hard_close" for result in traded),
    }


def viable(report: dict[str, dict[str, object]]) -> bool:
    train = report["train"]
    validation = report["validation"]
    if int(train["trades"]) < 100 or int(validation["trades"]) < 30:
        return False
    for split in (train, validation):
        average = split["average_pnl"]
        factor = split["profit_factor"]
        if average is None or Decimal(str(average)) <= 0:
            return False
        if factor is None:
            if int(split["losses"]) == 0 and int(split["wins"]) > 0:
                continue
            return False
        if Decimal(str(factor)) <= 1:
            return False
    return True


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate the declared ATM 20-day-trend spread grid"
    )
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--oos-start", type=date.fromisoformat, required=True)
    parser.add_argument("--slippage-per-side", type=Decimal, default=Decimal("0.02"))
    parser.add_argument("--fees-per-spread", type=Decimal, default=Decimal("0.10"))
    parser.add_argument("--cache-dir", type=Path, default=Path("state/atm-trend-cache"))
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
        for settings in settings_grid()
    ]
    results: dict[str, dict[str, list[AtmTrendResult]]] = {
        settings.label: {"train": [], "validation": []} for settings in grid
    }
    prior_closes: list[Decimal] = []
    fetched = 0
    for position, trading_date in enumerate(dates, start=1):
        bars = sessions[trading_date]
        if trading_date in allowed:
            entry = _at(bars, grid[0].entry_time)
            option_bars: dict[str, list[PriceBar]] = {}
            eligible_any = any(
                simulate_atm_trend(trading_date, prior_closes, bars, {}, settings).eligible
                for settings in grid
            )
            if entry is not None and eligible_any:
                day = date.fromisoformat(trading_date)
                symbols = required_symbols(day, entry.open, grid[0])
                option_bars = data.option_bars(day, symbols)
                fetched += 1
                if fetched % 25 == 0:
                    print(f"[{position}/{len(dates)}] {trading_date}", file=sys.stderr)
            split = "train" if trading_date in splits["train"] else "validation"
            for settings in grid:
                results[settings.label][split].append(
                    simulate_atm_trend(
                        trading_date, prior_closes, bars, option_bars, settings
                    )
                )
        if bars:
            prior_closes.append(bars[-1].close)

    reports: dict[str, dict[str, object]] = {}
    for settings in grid:
        rows = results[settings.label]
        reports[settings.label] = {
            "settings": {
                key: str(value) if value is not None else None
                for key, value in asdict(settings).items()
            },
            "train": atm_metrics(rows["train"]),
            "validation": atm_metrics(rows["validation"]),
        }
    viable_labels = [label for label, report in reports.items() if viable(report)]  # type: ignore[arg-type]
    ranked = sorted(
        viable_labels,
        key=lambda label: Decimal(str(reports[label]["train"]["average_pnl"])),  # type: ignore[index]
        reverse=True,
    )
    locked = sorted(splits["out_of_sample"])
    print(
        json.dumps(
            {
                "acceptance_rule": ACCEPTANCE_RULE,
                "candidate": ranked[0] if ranked else None,
                "viable": ranked,
                "option_sessions_fetched": fetched,
                "oos_revealed": False,
                "oos_start": locked[0],
                "oos_end": locked[-1],
                "oos_option_cache_preexisting": [
                    value
                    for value in locked
                    if (args.cache_dir / f"options-{value}.json").exists()
                ],
                "structures": reports,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
