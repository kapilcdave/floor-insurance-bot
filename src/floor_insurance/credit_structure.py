"""Structural evaluation of the floor-insurance put credit spread.

This module does not search for a signal. It asks a narrower question the
strategy has never been asked: given the credit the market actually pays for a
put spread a fixed distance below spot, what win rate would that structure need
in order to break even, and what win rate did it actually achieve?

Every structure is evaluated on the same sessions. Results are reported as the
gap between the required and the realised win rate, so a structure fails on its
own arithmetic rather than on a comparison with its neighbours.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from statistics import mean

from .directional import PriceBar

CENT = Decimal("0.01")
HUNDRED = Decimal("100")
RATIO = Decimal("0.0001")


@dataclass(frozen=True)
class CreditSettings:
    """One fully specified spread structure."""

    buffer_dollars: Decimal
    width: Decimal
    stop_buffer: Decimal
    take_profit_fraction: Decimal | None
    min_credit_fraction: Decimal = Decimal("0.05")
    entry_time: time = time(9, 45)
    hard_close: time = time(15, 0)
    slippage_per_side: Decimal = Decimal("0")
    fees_per_spread: Decimal = Decimal("0")

    @property
    def label(self) -> str:
        stop = f"stop{self.stop_buffer}"
        target = (
            "hold"
            if self.take_profit_fraction is None
            else f"tp{self.take_profit_fraction}"
        )
        return f"buf{self.buffer_dollars}_w{self.width}_{stop}_{target}"


@dataclass(frozen=True)
class CreditResult:
    trading_date: str
    entered: bool
    reason: str
    short_strike: Decimal = Decimal("0")
    long_strike: Decimal = Decimal("0")
    credit: Decimal = Decimal("0")
    exit_debit: Decimal = Decimal("0")
    pnl_per_contract: Decimal = Decimal("0")


def max_loss_per_contract(width: Decimal, credit: Decimal) -> Decimal:
    return ((width - credit) * HUNDRED).quantize(CENT)


def break_even_win_rate(
    width: Decimal, credit: Decimal, take_profit_fraction: Decimal | None
) -> Decimal | None:
    """Win rate needed for zero expectancy if every loss is the maximum loss.

    This is the optimistic reading of the requirement: the emergency stop is
    meant to cut losses well short of the spread width, so the realised
    requirement sits below this number. It is still the right first screen,
    because a structure that cannot clear it even with a perfect stop has no
    room to pay for slippage, fees, or a single gap through the stop.
    """

    if credit <= 0 or credit >= width:
        return None
    win = credit * HUNDRED
    if take_profit_fraction is not None:
        win = win * take_profit_fraction
    loss = max_loss_per_contract(width, credit)
    if win <= 0:
        return None
    return (loss / (loss + win)).quantize(RATIO)


def minimum_equity(
    width: Decimal, credit: Decimal, risk_fraction: Decimal
) -> Decimal:
    """Smallest balance that can fund one contract under the risk rule."""

    if risk_fraction <= 0:
        raise ValueError("risk fraction must be positive")
    return (max_loss_per_contract(width, credit) / risk_fraction).quantize(CENT)


def occ_put_for(symbol: str, expiration: date, strike: Decimal) -> str:
    return f"{symbol}{expiration:%y%m%d}P{int(strike * Decimal('1000')):08d}"


def occ_put(expiration: date, strike: Decimal) -> str:
    return occ_put_for("SPY", expiration, strike)


def spread_strikes(spot: Decimal, settings: CreditSettings) -> tuple[Decimal, Decimal]:
    short_strike = (spot - settings.buffer_dollars).to_integral_value(
        rounding=ROUND_FLOOR
    )
    return short_strike, short_strike - settings.width


def _at(bars: list[PriceBar], moment: time) -> PriceBar | None:
    return next((bar for bar in bars if bar.timestamp.time() == moment), None)


def simulate_credit_spread(
    trading_date: str,
    spy_bars: list[PriceBar],
    option_bars: dict[str, list[PriceBar]],
    settings: CreditSettings,
) -> CreditResult:
    """Sell one put spread at the entry minute and manage it to a conclusion.

    The stop is evaluated against each bar's low rather than its close, because
    a 15-second poll loop would see an intrabar breach. Exits are priced from
    the option bars at the minute the trigger fired.
    """

    entry = _at(spy_bars, settings.entry_time)
    if entry is None:
        return CreditResult(trading_date, False, "no SPY bar at the entry minute")
    short_strike, long_strike = spread_strikes(entry.open, settings)
    expiration = date.fromisoformat(trading_date)
    short_bars = option_bars.get(occ_put(expiration, short_strike), [])
    long_bars = option_bars.get(occ_put(expiration, long_strike), [])
    short_entry = _at(short_bars, settings.entry_time)
    long_entry = _at(long_bars, settings.entry_time)
    if short_entry is None or long_entry is None:
        return CreditResult(
            trading_date, False, "one or both legs did not trade at the entry minute"
        )

    credit = (
        short_entry.open - long_entry.open - settings.slippage_per_side
    ).quantize(CENT, rounding=ROUND_FLOOR)
    minimum = (settings.width * settings.min_credit_fraction).quantize(CENT)
    if credit < minimum:
        return CreditResult(
            trading_date,
            False,
            f"credit {credit} is below {minimum}",
            short_strike,
            long_strike,
            credit,
        )
    if credit >= settings.width:
        return CreditResult(
            trading_date,
            False,
            "credit is not below the spread width",
            short_strike,
            long_strike,
            credit,
        )

    short_by_time = {bar.timestamp.time(): bar for bar in short_bars}
    long_by_time = {bar.timestamp.time(): bar for bar in long_bars}
    stop_level = short_strike + settings.stop_buffer
    target = (
        (credit * settings.take_profit_fraction).quantize(CENT, rounding=ROUND_CEILING)
        if settings.take_profit_fraction is not None
        else None
    )
    last_value = credit

    def spread_value(moment: time) -> Decimal | None:
        short_bar = short_by_time.get(moment)
        long_bar = long_by_time.get(moment)
        if short_bar is None or long_bar is None:
            return None
        raw = short_bar.close - long_bar.close + settings.slippage_per_side
        return max(Decimal("0"), min(settings.width, raw)).quantize(
            CENT, rounding=ROUND_CEILING
        )

    def settle(reason: str, debit: Decimal) -> CreditResult:
        pnl = ((credit - debit) * HUNDRED - settings.fees_per_spread).quantize(CENT)
        return CreditResult(
            trading_date,
            True,
            reason,
            short_strike,
            long_strike,
            credit,
            debit,
            pnl,
        )

    for bar in spy_bars:
        moment = bar.timestamp.time()
        if moment <= settings.entry_time:
            continue
        if moment > settings.hard_close:
            break
        value = spread_value(moment)
        if value is not None:
            last_value = value
        if bar.low <= stop_level:
            return settle("emergency_stop", last_value)
        if target is not None and value is not None and value <= target:
            return settle("take_profit", value)
        if moment == settings.hard_close:
            return settle("hard_close", last_value)
    return settle("session_ended", last_value)


def credit_metrics(
    results: list[CreditResult], settings: CreditSettings
) -> dict[str, object]:
    traded = [result for result in results if result.entered]
    pnls = [result.pnl_per_contract for result in traded]
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [pnl for pnl in pnls if pnl < 0]
    credits = [result.credit for result in traded]
    median_credit = sorted(credits)[len(credits) // 2] if credits else Decimal("0")
    structural = break_even_win_rate(
        settings.width, median_credit, settings.take_profit_fraction
    )
    realised = (
        (Decimal(len(wins)) / Decimal(len(pnls))).quantize(RATIO) if pnls else None
    )
    average_win = Decimal(str(mean(wins))).quantize(CENT) if wins else None
    average_loss = (
        abs(Decimal(str(mean(losses)))).quantize(CENT) if losses else None
    )
    # The break-even implied by the losses that actually occurred, rather than
    # by the theoretical maximum loss. This is the number that decides the
    # structure once the emergency stop is doing its job.
    observed_break_even = (
        (average_loss / (average_loss + average_win)).quantize(RATIO)
        if average_win and average_loss
        else None
    )
    return {
        "sessions": len(results),
        "trades": len(traded),
        "entry_rate": (
            str((Decimal(len(traded)) / Decimal(len(results))).quantize(RATIO))
            if results
            else None
        ),
        "median_credit": str(median_credit),
        "credit_over_width": (
            str((median_credit / settings.width).quantize(RATIO))
            if settings.width
            else None
        ),
        "stops": sum(result.reason == "emergency_stop" for result in traded),
        "take_profits": sum(result.reason == "take_profit" for result in traded),
        "held_to_close": sum(
            result.reason in {"hard_close", "session_ended"} for result in traded
        ),
        "wins": len(wins),
        "losses": len(losses),
        "realised_win_rate": str(realised) if realised is not None else None,
        "break_even_win_rate_if_losses_were_maximal": (
            str(structural) if structural is not None else None
        ),
        "average_win": str(average_win) if average_win is not None else None,
        "average_loss": str(average_loss) if average_loss is not None else None,
        "loss_to_win_ratio": (
            str((average_loss / average_win).quantize(RATIO))
            if average_win and average_loss
            else None
        ),
        "observed_break_even_win_rate": (
            str(observed_break_even) if observed_break_even is not None else None
        ),
        "win_rate_margin": (
            str((realised - observed_break_even).quantize(RATIO))
            if realised is not None and observed_break_even is not None
            else None
        ),
        "total_pnl_per_contract": str(sum(pnls, Decimal("0")).quantize(CENT)),
        "average_pnl_per_contract": (
            str(Decimal(str(mean(pnls))).quantize(CENT)) if pnls else None
        ),
        "worst_loss": str(min(pnls).quantize(CENT)) if pnls else None,
        "minimum_equity_at_one_percent": (
            str(minimum_equity(settings.width, median_credit, Decimal("0.01")))
            if median_credit > 0
            else None
        ),
    }
