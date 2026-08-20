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


def test_underlying_allows_research_etfs_and_xsp_paper(config):
    replace(config, symbol="QQQ", signal_symbol="QQQ").validate()
    replace(config, symbol="IWM", signal_symbol="IWM").validate()
    replace(config, symbol="XSP").validate()
    with pytest.raises(ConfigError, match="must be SPY, QQQ, IWM, or XSP"):
        replace(config, symbol="NBIS").validate()


def test_xsp_live_trading_is_blocked(config):
    with pytest.raises(ConfigError, match="XSP only in paper"):
        replace(
            config,
            symbol="XSP",
            paper=False,
            live_confirmed=True,
            options_feed="opra",
            stock_feed="sip",
        ).validate()


def test_live_atm_trading_has_a_separate_research_gate(config):
    with pytest.raises(ConfigError, match="research-blocked"):
        replace(
            config,
            paper=False,
            live_confirmed=True,
            atm_live_confirmed=False,
            options_feed="opra",
            stock_feed="sip",
        ).validate()
    replace(
        config,
        paper=False,
        live_confirmed=True,
        atm_live_confirmed=True,
        options_feed="opra",
        stock_feed="sip",
    ).validate()


def test_absolute_risk_budget_must_be_positive(config):
    with pytest.raises(ConfigError, match="RISK_BUDGET_DOLLARS"):
        replace(config, risk_budget_dollars=Decimal("0")).validate()


def test_trend_configuration_is_validated(config):
    with pytest.raises(ConfigError, match="TREND_WINDOW"):
        replace(config, trend_window=1).validate()
    with pytest.raises(ConfigError, match="TREND_MODE"):
        replace(config, trend_mode="sometimes").validate()
    with pytest.raises(ConfigError, match="SIGNAL_SYMBOL"):
        replace(config, signal_symbol="").validate()


def test_atm_risk_controls_are_validated(config):
    with pytest.raises(ConfigError, match="STRIKE_SELECTION"):
        replace(config, strike_selection="cheap").validate()
    with pytest.raises(ConfigError, match="STOP_DEBIT_MULTIPLE"):
        replace(config, stop_debit_multiple=Decimal("1")).validate()
    with pytest.raises(ConfigError, match="MAX_TOTAL_LOSS_DOLLARS"):
        replace(config, max_total_loss_dollars=Decimal("0")).validate()
    replace(config, take_profit_fraction=None).validate()


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
    assert replace(
        config, risk_budget_dollars=Decimal("100")
    ).minimum_viable_equity() == Decimal("95.00")
    assert replace(
        config,
        dry_run=False,
        shadow_mode=True,
        risk_budget_dollars=Decimal("100"),
    ).minimum_viable_equity() == Decimal("99.00")
