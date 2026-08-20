from datetime import datetime, timezone
from decimal import Decimal

import pytest

from floor_insurance.models import Contract, Quote
from floor_insurance.strategy import (
    StrategySkip,
    executable_close_debit,
    executable_credit,
    max_loss_per_contract,
    select_atm_spread,
    select_spread,
    size_contracts,
    size_contracts_for_budget,
    stop_close_debit,
)


def contract(strike: str) -> Contract:
    return Contract(f"SPY-P-{strike}", Decimal(strike), "2026-08-18")


def quote(bid: str, ask: str) -> Quote:
    return Quote(Decimal(bid), Decimal(ask), datetime.now(timezone.utc))


def test_selects_highest_exact_width_below_buffer_target():
    short, long = select_spread(
        [contract("535"), contract("534"), contract("533")],
        Decimal("550.80"),
        Decimal("15"),
        Decimal("1"),
    )
    assert short.strike == Decimal("535")
    assert long.strike == Decimal("534")


def test_selects_nearest_atm_put_at_or_below_spot():
    short, long = select_atm_spread(
        [contract("551"), contract("550"), contract("549"), contract("548")],
        Decimal("550.80"),
        Decimal("1"),
    )
    assert short.strike == Decimal("550")
    assert long.strike == Decimal("549")


def test_atm_selection_requires_its_exact_protective_leg():
    with pytest.raises(StrategySkip, match="ATM"):
        select_atm_spread(
            [contract("550"), contract("548")],
            Decimal("550.80"),
            Decimal("1"),
        )


def test_executable_prices_use_conservative_sides():
    assert executable_credit(quote("0.60", "0.65"), quote("0.08", "0.10")) == Decimal("0.50")
    assert executable_close_debit(quote("0.20", "0.27"), quote("0.04", "0.06")) == Decimal("0.23")


def test_five_thousand_account_needs_at_least_fifty_cent_credit():
    assert max_loss_per_contract(Decimal("1"), Decimal("0.50")) == Decimal("50.00")
    assert size_contracts(
        Decimal("5000"), Decimal("0.01"), Decimal("1"), Decimal("0.50"), 10
    ) == 1
    with pytest.raises(StrategySkip, match="risk budget"):
        size_contracts(
            Decimal("5000"), Decimal("0.01"), Decimal("1"), Decimal("0.49"), 10
        )


def test_spread_debit_stop_is_capped_at_the_defined_width():
    assert stop_close_debit(Decimal("1"), Decimal("0.40"), Decimal("2")) == Decimal(
        "0.80"
    )
    assert stop_close_debit(Decimal("1"), Decimal("0.60"), Decimal("2")) == Decimal(
        "1.00"
    )


def test_absolute_hundred_dollar_budget_funds_one_xsp_spread():
    assert size_contracts_for_budget(
        Decimal("100"), Decimal("1"), Decimal("0.05"), 1
    ) == 1
    with pytest.raises(StrategySkip, match="risk budget"):
        size_contracts_for_budget(
            Decimal("94.99"), Decimal("1"), Decimal("0.05"), 1
        )


def test_rejects_missing_exact_long_strike():
    with pytest.raises(StrategySkip, match="no exact"):
        select_spread(
            [contract("535"), contract("533")],
            Decimal("550"),
            Decimal("15"),
            Decimal("1"),
        )
