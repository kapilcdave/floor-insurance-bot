from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from enum import StrEnum

from .volatility import VolatilitySnapshot

CENT = Decimal("0.01")
HUNDRED = Decimal("100")


class Direction(StrEnum):
    CALL = "call"
    PUT = "put"


class SignalModel(StrEnum):
    OPENING_RANGE = "opening_range"
    OPENING_RANGE_VOLUME = "opening_range_volume"
    VWAP_MOMENTUM = "vwap_momentum"
    VWAP_REVERSION = "vwap_reversion"
    GAP_CONTINUATION = "gap_continuation"
    GAP_FADE = "gap_fade"


class VixRegime(StrEnum):
    """Prior-close Cboe volatility filters. Each pair partitions the sessions."""

    ANY = "any"
    LOW_PERCENTILE = "low_percentile"
    HIGH_PERCENTILE = "high_percentile"
    CONTANGO = "contango"
    BACKWARDATION = "backwardation"
    CHEAP_ONE_DAY = "cheap_one_day"
    RICH_ONE_DAY = "rich_one_day"


@dataclass(frozen=True)
class PriceBar:
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal = Decimal("0")
    vwap: Decimal | None = None


@dataclass(frozen=True)
class DirectionalSignal:
    direction: Direction
    timestamp: datetime
    underlying: Decimal
    opening_range_high: Decimal
    opening_range_low: Decimal
    session_vwap: Decimal


@dataclass(frozen=True)
class DebitSpread:
    direction: Direction
    long_symbol: str
    short_symbol: str
    long_strike: Decimal
    short_strike: Decimal
    width: Decimal
    entry_debit: Decimal
    max_profit: Decimal
    reward_risk: Decimal


@dataclass(frozen=True)
class DirectionalSettings:
    starting_equity: Decimal = Decimal("5000")
    risk_fraction: Decimal = Decimal("0.02")
    width: Decimal = Decimal("3")
    minimum_reward_risk: Decimal = Decimal("2")
    slippage_per_side: Decimal = Decimal("0.05")
    fees_per_spread: Decimal = Decimal("0.10")
    maximum_contracts: int = 25
    fixed_contracts: int | None = None
    constant_sizing: bool = False
    market_open: time = time(9, 30)
    entry_time: time = time(9, 45)
    hard_close: time = time(15, 0)
    opening_range_minutes: int = 5
    candidate_radius: int = 2
    signal_model: SignalModel = SignalModel.OPENING_RANGE
    minimum_volume_ratio: Decimal = Decimal("1")
    minimum_momentum_fraction: Decimal = Decimal("0.0015")
    minimum_gap_fraction: Decimal = Decimal("0.002")
    vix_regime: VixRegime = VixRegime.ANY
    vix_percentile_threshold: Decimal = Decimal("0.5")
    term_slope_threshold: Decimal = Decimal("1")
    one_day_ratio_threshold: Decimal = Decimal("1")


@dataclass(frozen=True)
class DirectionalResult:
    trading_date: str
    signal: str
    traded: bool
    reason: str
    quantity: int = 0
    long_symbol: str = ""
    short_symbol: str = ""
    entry_debit: Decimal = Decimal("0")
    exit_credit: Decimal = Decimal("0")
    pnl: Decimal = Decimal("0")
    r_multiple: Decimal = Decimal("0")
    equity_after: Decimal = Decimal("0")


def opening_range_signal(
    bars: list[PriceBar],
    settings: DirectionalSettings,
    previous_close: Decimal | None = None,
) -> DirectionalSignal | None:
    context = [
        bar
        for bar in bars
        if settings.market_open <= bar.timestamp.time() < settings.entry_time
    ]
    if len(context) < 15:
        return None
    context = context[-15:]
    opening_range = context[: settings.opening_range_minutes]
    if len(opening_range) < settings.opening_range_minutes:
        return None

    volume = sum((bar.volume for bar in context), Decimal("0"))
    if volume > 0:
        session_vwap = sum(
            ((bar.vwap or bar.close) * bar.volume for bar in context), Decimal("0")
        ) / volume
    else:
        session_vwap = sum((bar.close for bar in context), Decimal("0")) / Decimal(
            len(context)
        )

    last = context[-1]
    opening_high = max(bar.high for bar in opening_range)
    opening_low = min(bar.low for bar in opening_range)
    breakout_call = last.close > opening_high and last.close > session_vwap
    breakout_put = last.close < opening_low and last.close < session_vwap

    if settings.signal_model in {
        SignalModel.GAP_CONTINUATION,
        SignalModel.GAP_FADE,
    }:
        if previous_close is None or previous_close <= 0:
            return None
        opening_price = context[0].open
        gap = (opening_price - previous_close) / previous_close
        if abs(gap) < settings.minimum_gap_fraction:
            return None
        if settings.signal_model == SignalModel.GAP_CONTINUATION:
            if gap > 0 and last.close > opening_price and last.close > session_vwap:
                direction = Direction.CALL
            elif gap < 0 and last.close < opening_price and last.close < session_vwap:
                direction = Direction.PUT
            else:
                return None
        elif gap > 0 and last.close < opening_price and last.close < session_vwap:
            direction = Direction.PUT
        elif gap < 0 and last.close > opening_price and last.close > session_vwap:
            direction = Direction.CALL
        else:
            return None
        return DirectionalSignal(
            direction=direction,
            timestamp=last.timestamp,
            underlying=last.close,
            opening_range_high=opening_high,
            opening_range_low=opening_low,
            session_vwap=session_vwap,
        )

    if settings.signal_model == SignalModel.OPENING_RANGE_VOLUME:
        comparison = context[5:10]
        confirmation = context[10:15]
        comparison_volume = sum((bar.volume for bar in comparison), Decimal("0"))
        confirmation_volume = sum((bar.volume for bar in confirmation), Decimal("0"))
        if comparison_volume <= 0:
            return None
        volume_ratio = confirmation_volume / comparison_volume
        if volume_ratio < settings.minimum_volume_ratio:
            return None

    if settings.signal_model in {
        SignalModel.VWAP_MOMENTUM,
        SignalModel.VWAP_REVERSION,
    }:
        reference = context[4].close
        momentum = (last.close - reference) / reference
        vwap_deviation = (last.close - session_vwap) / session_vwap
        recent = context[-3:]
        call_confirmed = all(bar.close > session_vwap for bar in recent)
        put_confirmed = all(bar.close < session_vwap for bar in recent)
        if settings.signal_model == SignalModel.VWAP_REVERSION:
            if (
                vwap_deviation >= settings.minimum_momentum_fraction
                and breakout_call
            ):
                direction = Direction.PUT
            elif (
                vwap_deviation <= -settings.minimum_momentum_fraction
                and breakout_put
            ):
                direction = Direction.CALL
            else:
                return None
        elif momentum >= settings.minimum_momentum_fraction and call_confirmed:
            direction = Direction.CALL
        elif momentum <= -settings.minimum_momentum_fraction and put_confirmed:
            direction = Direction.PUT
        else:
            return None
    elif breakout_call:
        direction = Direction.CALL
    elif breakout_put:
        direction = Direction.PUT
    else:
        return None
    return DirectionalSignal(
        direction=direction,
        timestamp=last.timestamp,
        underlying=last.close,
        opening_range_high=opening_high,
        opening_range_low=opening_low,
        session_vwap=session_vwap,
    )


def regime_allows(
    snapshot: VolatilitySnapshot | None, settings: DirectionalSettings
) -> tuple[bool, str]:
    """Apply the pre-declared prior-close volatility filter.

    Each regime pair splits on one threshold, so the two halves of a family are
    disjoint and exhaustive over the sessions that have the required data. A
    session without the required series is never traded rather than silently
    defaulting to the permissive branch.
    """

    regime = settings.vix_regime
    if regime == VixRegime.ANY:
        return True, ""
    if snapshot is None:
        return False, "no prior-session volatility close available"

    if regime in {VixRegime.LOW_PERCENTILE, VixRegime.HIGH_PERCENTILE}:
        value = snapshot.vix_percentile
        threshold = settings.vix_percentile_threshold
        label = "vix percentile"
    elif regime in {VixRegime.CONTANGO, VixRegime.BACKWARDATION}:
        value = snapshot.term_slope
        threshold = settings.term_slope_threshold
        label = "vix9d/vix3m"
    else:
        value = snapshot.one_day_ratio
        threshold = settings.one_day_ratio_threshold
        label = "vix1d/vix9d"

    if value is None:
        return False, f"{label} unavailable at {snapshot.as_of.isoformat()} close"
    wants_below = regime in {
        VixRegime.LOW_PERCENTILE,
        VixRegime.CONTANGO,
        VixRegime.CHEAP_ONE_DAY,
    }
    if wants_below:
        if value < threshold:
            return True, ""
        return False, f"{label} {value} is not below {threshold}"
    if value >= threshold:
        return True, ""
    return False, f"{label} {value} is below {threshold}"


def occ_symbol(expiration: date, direction: Direction, strike: Decimal) -> str:
    strike_thousandths = int(strike * Decimal("1000"))
    kind = "C" if direction == Direction.CALL else "P"
    return f"SPY{expiration:%y%m%d}{kind}{strike_thousandths:08d}"


def candidate_pairs(
    expiration: date,
    signal: DirectionalSignal,
    settings: DirectionalSettings,
) -> list[tuple[str, str, Decimal, Decimal]]:
    center = signal.underlying.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    pairs: list[tuple[str, str, Decimal, Decimal]] = []
    for offset in range(-settings.candidate_radius, settings.candidate_radius + 1):
        long_strike = center + Decimal(offset)
        short_strike = (
            long_strike + settings.width
            if signal.direction == Direction.CALL
            else long_strike - settings.width
        )
        pairs.append(
            (
                occ_symbol(expiration, signal.direction, long_strike),
                occ_symbol(expiration, signal.direction, short_strike),
                long_strike,
                short_strike,
            )
        )
    return pairs


def select_debit_spread(
    signal: DirectionalSignal,
    pairs: list[tuple[str, str, Decimal, Decimal]],
    entry_bars: dict[str, PriceBar],
    settings: DirectionalSettings,
) -> DebitSpread | None:
    eligible: list[DebitSpread] = []
    for long_symbol, short_symbol, long_strike, short_strike in pairs:
        long_bar = entry_bars.get(long_symbol)
        short_bar = entry_bars.get(short_symbol)
        if not long_bar or not short_bar:
            continue
        debit = (long_bar.open - short_bar.open + settings.slippage_per_side).quantize(
            CENT
        )
        if debit <= 0 or debit >= settings.width:
            continue
        max_profit = settings.width - debit
        reward_risk = max_profit / debit
        if reward_risk < settings.minimum_reward_risk:
            continue
        eligible.append(
            DebitSpread(
                direction=signal.direction,
                long_symbol=long_symbol,
                short_symbol=short_symbol,
                long_strike=long_strike,
                short_strike=short_strike,
                width=settings.width,
                entry_debit=debit,
                max_profit=max_profit,
                reward_risk=reward_risk,
            )
        )
    if not eligible:
        return None
    target_debit = settings.width / (settings.minimum_reward_risk + Decimal("1"))
    return min(eligible, key=lambda spread: abs(spread.entry_debit - target_debit))


def size_debit_spreads(
    equity: Decimal, spread: DebitSpread, settings: DirectionalSettings
) -> int:
    """Size a position, optionally removing dependence on the running equity.

    Equity-proportional sizing makes two variants incomparable: a filter that
    avoids losses keeps a larger balance, so it can afford days the unfiltered
    path had to skip. Two controls are available.

    ``constant_sizing`` keeps the risk rule intact but applies it to the
    starting balance, so every variant sees the same day set and the same
    contract count. This is the faithful control.

    ``fixed_contracts`` additionally ignores the risk budget. It answers a
    different question and takes premium the 2% cap would forbid, so it is a
    diagnostic rather than a tradable configuration.
    """

    if settings.fixed_contracts is not None:
        return min(settings.fixed_contracts, settings.maximum_contracts)
    reference = settings.starting_equity if settings.constant_sizing else equity
    risk_budget = reference * settings.risk_fraction
    risk_per_spread = spread.entry_debit * HUNDRED + settings.fees_per_spread
    quantity = int((risk_budget / risk_per_spread).to_integral_value(rounding=ROUND_FLOOR))
    return min(quantity, settings.maximum_contracts)


def simulate_debit_spread(
    trading_date: str,
    signal: DirectionalSignal,
    spread: DebitSpread,
    option_bars: dict[str, list[PriceBar]],
    equity: Decimal,
    settings: DirectionalSettings,
) -> DirectionalResult:
    quantity = size_debit_spreads(equity, spread, settings)
    if quantity < 1:
        return DirectionalResult(
            trading_date,
            signal.direction.value,
            False,
            "risk budget is below one spread debit",
            equity_after=equity,
        )

    long_by_time = {bar.timestamp: bar for bar in option_bars[spread.long_symbol]}
    short_by_time = {bar.timestamp: bar for bar in option_bars[spread.short_symbol]}
    timestamps = sorted(set(long_by_time) & set(short_by_time))
    target_credit = spread.entry_debit * (
        Decimal("1") + settings.minimum_reward_risk
    )
    exit_credit = Decimal("0")
    reason = "missing_hard_close_data"
    observed_exit = False
    for timestamp in timestamps:
        if timestamp.time() <= settings.entry_time:
            continue
        credit = (
            long_by_time[timestamp].close
            - short_by_time[timestamp].close
            - settings.slippage_per_side
        )
        credit = max(Decimal("0"), min(spread.width, credit)).quantize(CENT)
        if credit >= target_credit:
            exit_credit = target_credit.quantize(CENT)
            reason = "two_r_target"
            observed_exit = True
            break
        if timestamp.time() >= settings.hard_close:
            exit_credit = credit
            reason = "hard_close"
            observed_exit = True
            break

    if not observed_exit:
        exit_credit = Decimal("0")

    pnl = (
        (exit_credit - spread.entry_debit) * HUNDRED - settings.fees_per_spread
    ) * quantity
    pnl = pnl.quantize(CENT)
    initial_risk = spread.entry_debit * HUNDRED * quantity
    r_multiple = (pnl / initial_risk).quantize(Decimal("0.0001"))
    return DirectionalResult(
        trading_date=trading_date,
        signal=signal.direction.value,
        traded=True,
        reason=reason,
        quantity=quantity,
        long_symbol=spread.long_symbol,
        short_symbol=spread.short_symbol,
        entry_debit=spread.entry_debit,
        exit_credit=exit_credit,
        pnl=pnl,
        r_multiple=r_multiple,
        equity_after=equity + pnl,
    )
