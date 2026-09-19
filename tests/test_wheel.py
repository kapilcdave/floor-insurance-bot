from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from floor_insurance.models import Contract, OptionSnapshot, Quote
from floor_insurance.wheel import (
    WheelAction,
    WheelConfig,
    WheelEngine,
    WheelSkip,
    choose_expiration,
    option_matches_underlying,
    option_root,
    select_contract,
)

ET = ZoneInfo("America/New_York")


def wheel_config(monkeypatch: pytest.MonkeyPatch) -> WheelConfig:
    monkeypatch.setenv("ALPACA_API_KEY", "key")
    monkeypatch.setenv("ALPACA_API_SECRET", "secret")
    monkeypatch.setenv("WHEEL_SYMBOL", "AAPL")
    return replace(
        WheelConfig.from_env(),
        min_annualized_yield=Decimal("0.04"),
    )


def snapshot(
    now: datetime,
    *,
    symbol: str,
    strike: str,
    option_type: str,
    delta: str,
    iv: str = "0.20",
    bid: str = "1.00",
    ask: str = "1.10",
) -> OptionSnapshot:
    return OptionSnapshot(
        symbol=symbol,
        strike=Decimal(strike),
        expiration_date="2026-10-02",
        option_type=option_type,
        quote=Quote(Decimal(bid), Decimal(ask), now),
        delta=Decimal(delta),
        implied_volatility=Decimal(iv),
    )


class FakeAlpaca:
    def __init__(self, now: datetime, *, shares: str = "100", avg_entry: str = "205"):
        self.now = now
        self.shares = shares
        self.avg_entry = avg_entry
        self.qty_available = shares
        self.option_positions = []
        self.orders = []
        self.previous_order = None
        self.submissions = []
        self.iv = "0.20"

    def clock(self):
        return {"is_open": True}

    def account(self):
        return {
            "status": "ACTIVE",
            "trading_blocked": False,
            "options_trading_level": 1,
            "options_buying_power": "30000",
        }

    def asset(self, _symbol):
        return {"tradable": True, "has_options": True}

    def positions(self):
        stock = []
        if Decimal(self.shares) != 0:
            stock = [
                {
                    "asset_class": "us_equity",
                    "symbol": "AAPL",
                    "qty": self.shares,
                    "qty_available": self.qty_available,
                    "avg_entry_price": self.avg_entry,
                }
            ]
        return stock + self.option_positions

    def open_orders(self):
        return self.orders

    def order_by_client_id(self, _client_order_id):
        return self.previous_order

    def latest_stock_trade(self, _symbol):
        return Decimal("200"), self.now

    def option_contracts_between(self, _symbol, option_type, _start, _end):
        side = "C" if option_type == "call" else "P"
        return [
            Contract(f"AAPL261002{side}00200000", Decimal("200"), "2026-10-02")
        ]

    def option_chain_snapshots(self, _symbol, _expiration, option_type):
        if option_type == "call":
            return [
                snapshot(
                    self.now,
                    symbol="AAPL261002C00210000",
                    strike="210",
                    option_type="call",
                    delta="0.24",
                    iv=self.iv,
                ),
                snapshot(
                    self.now,
                    symbol="AAPL261002C00220000",
                    strike="220",
                    option_type="call",
                    delta="0.14",
                    iv=self.iv,
                ),
            ]
        return [
            snapshot(
                self.now,
                symbol="AAPL261002P00190000",
                strike="190",
                option_type="put",
                delta="-0.24",
                iv=self.iv,
            ),
            snapshot(
                self.now,
                symbol="AAPL261002P00180000",
                strike="180",
                option_type="put",
                delta="-0.14",
                iv=self.iv,
            ),
        ]

    def daily_closes(self, _symbol, _before_date, observations):
        pattern = [Decimal("100") + Decimal(index % 3) for index in range(observations)]
        return pattern

    def submit_short_option(self, **kwargs):
        self.submissions.append(kwargs)
        return {"id": "order-1"}


def test_option_root_handles_occ_contracts():
    assert option_root("AAPL261002P00190000") == "AAPL"
    assert option_root("AAPL1261002P00190000") == "AAPL1"
    assert option_matches_underlying("AAPL1261002P00190000", "AAPL") is True
    assert option_matches_underlying("META261002P00190000", "MET") is False
    assert option_root("not-an-option") is None


def test_choose_expiration_uses_nearest_target_dte():
    today = date(2026, 8, 28)
    contracts = [
        Contract("near", Decimal("1"), "2026-09-25"),
        Contract("target", Decimal("1"), "2026-10-02"),
        Contract("far", Decimal("1"), "2026-10-09"),
    ]
    assert choose_expiration(contracts, today, 35) == "2026-10-02"


def test_contract_selector_uses_normal_delta_and_never_calls_below_basis():
    now = datetime(2026, 8, 28, 11, 0, tzinfo=ET)
    snapshots = [
        snapshot(
            now,
            symbol="below-basis",
            strike="202",
            option_type="call",
            delta="0.25",
        ),
        snapshot(
            now,
            symbol="selected",
            strike="210",
            option_type="call",
            delta="0.23",
        ),
    ]

    selected, _ = select_contract(
        snapshots,
        action=WheelAction.COVERED_CALL,
        spot=Decimal("200"),
        cost_basis=Decimal("205"),
        target_delta=Decimal("0.25"),
        min_premium=Decimal("0.10"),
        min_annualized_yield=Decimal("0.04"),
        max_quote_width_fraction=Decimal("0.25"),
        max_quote_age_seconds=30,
        now=now,
        buying_power=Decimal("0"),
    )

    assert selected.symbol == "selected"


def test_known_event_moves_covered_call_deeper_otm(monkeypatch):
    config = replace(
        wheel_config(monkeypatch),
        event_dates=(date(2026, 9, 15),),
    )
    now = datetime(2026, 8, 28, 11, 0, tzinfo=ET)
    fake = FakeAlpaca(now)

    plan = WheelEngine(config, fake).tick(now)

    assert plan.action == WheelAction.COVERED_CALL
    assert plan.strike == Decimal("220")
    assert plan.delta == Decimal("0.14")
    assert plan.cautious is True
    assert "known event" in plan.caution_reasons[0]
    assert fake.submissions == []


def test_high_iv_moves_cash_secured_put_deeper_otm(monkeypatch):
    config = wheel_config(monkeypatch)
    now = datetime(2026, 8, 28, 11, 0, tzinfo=ET)
    fake = FakeAlpaca(now, shares="0")
    fake.iv = "0.80"

    plan = WheelEngine(config, fake).tick(now)

    assert plan.action == WheelAction.CASH_SECURED_PUT
    assert plan.strike == Decimal("180")
    assert plan.delta == Decimal("-0.14")
    assert plan.cautious is True
    assert any("IV/RV" in reason for reason in plan.caution_reasons)


def test_live_mode_submits_only_the_broker_reconciled_contract(monkeypatch):
    config = replace(wheel_config(monkeypatch), dry_run=False)
    now = datetime(2026, 8, 28, 11, 0, tzinfo=ET)
    fake = FakeAlpaca(now)

    plan = WheelEngine(config, fake).tick(now)

    assert plan.order_id == "order-1"
    assert fake.submissions == [
        {
            "symbol": plan.option_symbol,
            "quantity": 1,
            "limit_price": Decimal("1.00"),
            "client_order_id": "wheelhouse-aapl-2026-08-28-call",
        }
    ]


def test_existing_option_position_blocks_a_second_short_option(monkeypatch):
    config = wheel_config(monkeypatch)
    now = datetime(2026, 8, 28, 11, 0, tzinfo=ET)
    fake = FakeAlpaca(now)
    fake.option_positions = [
        {
            "asset_class": "us_option",
            "symbol": "AAPL261002C00210000",
            "qty": "-1",
        }
    ]

    with pytest.raises(WheelSkip, match="existing AAPL option position"):
        WheelEngine(config, fake).tick(now)


def test_incomplete_share_lot_does_not_start_a_put_cycle(monkeypatch):
    config = wheel_config(monkeypatch)
    now = datetime(2026, 8, 28, 11, 0, tzinfo=ET)
    fake = FakeAlpaca(now, shares="50")

    with pytest.raises(WheelSkip, match="incomplete lot"):
        WheelEngine(config, fake).tick(now)


def test_reserved_shares_cannot_back_a_covered_call(monkeypatch):
    config = wheel_config(monkeypatch)
    now = datetime(2026, 8, 28, 11, 0, tzinfo=ET)
    fake = FakeAlpaca(now)
    fake.qty_available = "0"

    with pytest.raises(WheelSkip, match="insufficient covered shares"):
        WheelEngine(config, fake).tick(now)


def test_stale_or_wide_quotes_are_rejected():
    now = datetime(2026, 8, 28, 11, 0, tzinfo=ET)
    bad = snapshot(
        now - timedelta(minutes=5),
        symbol="stale",
        strike="190",
        option_type="put",
        delta="-0.20",
        bid="0.50",
        ask="1.00",
    )
    with pytest.raises(WheelSkip, match="no liquid, fresh"):
        select_contract(
            [bad],
            action=WheelAction.CASH_SECURED_PUT,
            spot=Decimal("200"),
            cost_basis=None,
            target_delta=Decimal("0.25"),
            min_premium=Decimal("0.10"),
            min_annualized_yield=Decimal("0"),
            max_quote_width_fraction=Decimal("0.25"),
            max_quote_age_seconds=30,
            now=now,
            buying_power=Decimal("30000"),
        )


def test_doctor_reads_options_from_the_attributes_list(monkeypatch):
    """Alpaca advertises optionability inside ``attributes``, not as a field.

    A real ``/v2/assets/SOFI`` response returns ``attributes:
    ["fractional_eh_enabled", "has_options", "overnight_tradable"]`` with no
    top-level ``has_options`` key, so a doctor that only looks for the field or
    for ``options_enabled`` reports an optionable stock as having no options.
    """
    config = wheel_config(monkeypatch)
    now = datetime(2026, 8, 28, 11, 0, tzinfo=ET)
    fake = FakeAlpaca(now)
    fake.asset = lambda _symbol: {
        "tradable": True,
        "has_options": None,
        "attributes": ["fractional_eh_enabled", "has_options", "overnight_tradable"],
    }

    assert WheelEngine(config, fake).doctor()["asset_has_options"] is True


def test_doctor_reports_a_stock_without_listed_options(monkeypatch):
    config = wheel_config(monkeypatch)
    now = datetime(2026, 8, 28, 11, 0, tzinfo=ET)
    fake = FakeAlpaca(now)
    fake.asset = lambda _symbol: {"tradable": True, "attributes": ["ptp_no_exception"]}

    assert WheelEngine(config, fake).doctor()["asset_has_options"] is False


@pytest.mark.parametrize("min_dte", ["3", "5", "28"])
def test_weekly_dte_windows_are_accepted(monkeypatch, min_dte):
    monkeypatch.setenv("ALPACA_API_KEY", "key")
    monkeypatch.setenv("ALPACA_API_SECRET", "secret")
    monkeypatch.setenv("WHEEL_SYMBOL", "SOFI")
    monkeypatch.setenv("WHEEL_MIN_DTE", min_dte)
    monkeypatch.setenv("WHEEL_TARGET_DTE", min_dte)
    monkeypatch.setenv("WHEEL_MAX_DTE", "45")

    assert WheelConfig.from_env().min_dte == int(min_dte)


@pytest.mark.parametrize("min_dte", ["0", "1", "2"])
def test_zero_to_two_dte_stays_out_of_reach(monkeypatch, min_dte):
    """A 25-delta option under 3 DTE is mostly gamma and the strike grid is too coarse."""
    monkeypatch.setenv("ALPACA_API_KEY", "key")
    monkeypatch.setenv("ALPACA_API_SECRET", "secret")
    monkeypatch.setenv("WHEEL_SYMBOL", "SOFI")
    monkeypatch.setenv("WHEEL_MIN_DTE", min_dte)
    monkeypatch.setenv("WHEEL_TARGET_DTE", min_dte)
    monkeypatch.setenv("WHEEL_MAX_DTE", "9")

    with pytest.raises(ValueError, match="3 <= min <= target <= max <= 90"):
        WheelConfig.from_env()
