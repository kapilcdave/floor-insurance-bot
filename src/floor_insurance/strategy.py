from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from .models import Contract, Quote

CENT = Decimal("0.01")
MULTIPLIER = Decimal("100")


class StrategySkip(RuntimeError):
    """A safe, expected reason not to place a trade."""


def select_spread(
    contracts: list[Contract],
    underlying_price: Decimal,
    buffer_dollars: Decimal,
    width: Decimal,
) -> tuple[Contract, Contract]:
    target = (underlying_price - buffer_dollars).to_integral_value(
        rounding=ROUND_FLOOR
    )
    by_strike = {contract.strike: contract for contract in contracts}
    eligible = sorted((strike for strike in by_strike if strike <= target), reverse=True)
    for short_strike in eligible:
        long_contract = by_strike.get(short_strike - width)
        if long_contract:
            return by_strike[short_strike], long_contract
    raise StrategySkip(
        f"no exact {width}-wide put spread at or below target strike {target}"
    )


def executable_credit(short_quote: Quote, long_quote: Quote) -> Decimal:
    value = short_quote.bid - long_quote.ask
    return max(Decimal("0"), value.quantize(CENT, rounding=ROUND_FLOOR))


def executable_close_debit(short_quote: Quote, long_quote: Quote) -> Decimal:
    value = short_quote.ask - long_quote.bid
    return max(CENT, value.quantize(CENT, rounding=ROUND_CEILING))


def max_loss_per_contract(width: Decimal, credit: Decimal) -> Decimal:
    if credit <= 0 or credit >= width:
        raise StrategySkip("credit must be greater than zero and less than width")
    return ((width - credit) * MULTIPLIER).quantize(CENT)


def size_contracts(
    equity: Decimal,
    risk_fraction: Decimal,
    width: Decimal,
    credit: Decimal,
    cap: int,
) -> int:
    if equity <= 0:
        raise StrategySkip("account equity is not positive")
    return size_contracts_for_budget(equity * risk_fraction, width, credit, cap)


def size_contracts_for_budget(
    risk_budget: Decimal,
    width: Decimal,
    credit: Decimal,
    cap: int,
) -> int:
    if risk_budget <= 0:
        raise StrategySkip("risk budget is not positive")
    per_contract = max_loss_per_contract(width, credit)
    quantity = int((risk_budget / per_contract).to_integral_value(rounding=ROUND_FLOOR))
    if quantity < 1:
        raise StrategySkip(
            f"risk budget ${risk_budget:.2f} is below ${per_contract:.2f} max loss per spread"
        )
    return min(quantity, cap)
