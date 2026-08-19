from dataclasses import replace
from decimal import Decimal

import pytest

from floor_insurance.config import ConfigError


def test_live_trading_requires_second_opt_in(config):
    with pytest.raises(ConfigError, match="second opt-in"):
        replace(
            config,
            paper=False,
            live_confirmed=False,
            options_feed="opra",
            stock_feed="sip",
        ).validate()


def test_live_trading_requires_consolidated_feeds(config):
    with pytest.raises(ConfigError, match="OPTIONS_FEED=opra"):
        replace(config, paper=False, live_confirmed=True).validate()


def test_underlying_is_pinned_to_spy(config):
    with pytest.raises(ConfigError, match="must be SPY"):
        replace(config, symbol="NBIS").validate()


def test_dry_run_and_shadow_mode_are_mutually_exclusive(config):
    with pytest.raises(ConfigError, match="cannot both be true"):
        replace(config, dry_run=True, shadow_mode=True).validate()


def test_shadow_mode_needs_positive_modeled_equity(config):
    with pytest.raises(ConfigError, match="shadow equity"):
        replace(config, dry_run=False, shadow_mode=True, shadow_equity=0).validate()


def test_minimum_viable_equity_exposes_the_silent_no_op(config):
    # A $1 spread sold for $0.05 risks $95, so 1% risk needs $9,500.
    assert config.minimum_viable_equity() == Decimal("9500.00")
    assert replace(config, risk_fraction=Decimal("0.05")).minimum_viable_equity() == (
        Decimal("1900.00")
    )
    assert replace(
        config, spread_width=Decimal("5"), min_credit=Decimal("0.40")
    ).minimum_viable_equity() == Decimal("46000.00")
