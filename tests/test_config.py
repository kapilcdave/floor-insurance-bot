from dataclasses import replace

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
