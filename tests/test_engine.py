from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from floor_insurance.engine import TradingEngine
from floor_insurance.models import Contract, DailyState, Phase, Quote
from floor_insurance.notify import Notifier
from floor_insurance.state import StateStore

ET = ZoneInfo("America/New_York")


class FakeAlpaca:
    def __init__(self, now: datetime):
        self.now = now
        self.price = Decimal("550")
        self.submissions = []
        self.orders = {}
        self.short_quote = Quote(Decimal("0.60"), Decimal("0.65"), self.now)
        self.long_quote = Quote(Decimal("0.08"), Decimal("0.10"), self.now)

    def clock(self):
        return {"is_open": True, "next_close": self.now.replace(hour=16).isoformat()}

    def calendar_day(self, _date):
        return {"date": "2026-08-18", "open": "09:30", "close": "16:00"}

    def account(self):
        return {"trading_blocked": False, "options_trading_level": 3, "equity": "5000"}

    def latest_underlying_trade(self):
        return self.price, self.now

    def put_contracts(self, _date):
        return [
            Contract("short", Decimal("535"), "2026-08-18"),
            Contract("long", Decimal("534"), "2026-08-18"),
        ]

    def option_quotes(self, _symbols):
        return {
            "short": self.short_quote,
            "long": self.long_quote,
        }

    def submit_spread(self, **kwargs):
        self.submissions.append(kwargs)
        order = {"id": f"order-{len(self.submissions)}", "status": "new", "filled_qty": "0"}
        self.orders[order["id"]] = order
        return order

    def order(self, order_id):
        return self.orders[order_id]

    def cancel_order(self, order_id):
        self.orders[order_id]["status"] = "canceled"

    def order_by_client_id(self, _client_id):
        return None


def engine(config, fake):
    return TradingEngine(
        config,
        fake,
        StateStore(config.state_path),
        Notifier(None, None, 1),
    )


def test_dry_run_finds_valid_trade_but_submits_nothing(config):
    now = datetime(2026, 8, 18, 9, 45, tzinfo=ET)
    fake = FakeAlpaca(now)
    bot = engine(config, fake)
    bot.tick(now)
    state = bot.store.load("2026-08-18")
    assert state.phase == Phase.DONE
    assert state.quantity == 1
    assert fake.submissions == []
    assert "dry run" in state.event_history[-1]["reason"]


def test_emergency_stop_submits_atomic_market_exit(config):
    now = datetime(2026, 8, 18, 10, 0, tzinfo=ET)
    config = replace(config, dry_run=False)
    fake = FakeAlpaca(now)
    fake.price = Decimal("537.90")
    bot = engine(config, fake)
    bot.store.save(
        DailyState(
            "2026-08-18",
            phase=Phase.OPEN,
            short_symbol="short",
            long_symbol="long",
            short_strike="535",
            long_strike="534",
            quantity=1,
            entry_credit="0.50",
            entry_submissions=1,
        )
    )
    bot.tick(now)
    state = bot.store.load("2026-08-18")
    assert state.phase == Phase.EXIT_PENDING
    assert state.exit_reason == "emergency_stop"
    assert fake.submissions[0]["opening"] is False
    assert fake.submissions[0]["price"] is None


def test_live_entry_fill_then_take_profit_limit(config):
    entered = datetime(2026, 8, 18, 9, 45, tzinfo=ET)
    config = replace(config, dry_run=False)
    fake = FakeAlpaca(entered)
    bot = engine(config, fake)
    bot.tick(entered)
    assert bot.store.load("2026-08-18").phase == Phase.ENTRY_PENDING

    fake.orders["order-1"].update(
        status="filled", filled_qty="1", filled_avg_price="-0.50"
    )
    filled_at = entered.replace(minute=46)
    fake.now = filled_at
    bot.tick(filled_at)
    assert bot.store.load("2026-08-18").phase == Phase.OPEN

    managed_at = entered.replace(hour=10, minute=0)
    fake.now = managed_at
    fake.short_quote = Quote(Decimal("0.15"), Decimal("0.30"), managed_at)
    fake.long_quote = Quote(Decimal("0.08"), Decimal("0.10"), managed_at)
    bot.tick(managed_at)
    state = bot.store.load("2026-08-18")
    assert state.phase == Phase.EXIT_PENDING
    assert state.exit_reason == "take_profit"
    assert fake.submissions[-1]["price"] == Decimal("0.25")


def test_filled_stop_counts_loss_and_finishes_at_entry_cap(config):
    now = datetime(2026, 8, 18, 10, 0, tzinfo=ET)
    config = replace(config, dry_run=False)
    fake = FakeAlpaca(now)
    fake.price = Decimal("537.90")
    bot = engine(config, fake)
    bot.store.save(
        DailyState(
            "2026-08-18",
            phase=Phase.OPEN,
            short_symbol="short",
            long_symbol="long",
            short_strike="535",
            long_strike="534",
            quantity=1,
            entry_credit="0.50",
            entry_submissions=1,
        )
    )
    bot.tick(now)
    fake.orders["order-1"].update(
        status="filled", filled_qty="1", filled_avg_price="0.70"
    )
    fake.now = now.replace(minute=1)
    bot.tick(fake.now)
    state = bot.store.load("2026-08-18")
    assert state.phase == Phase.DONE
    assert state.losses == 1
    assert "entry limit" in state.event_history[-1]["reason"]


def test_hard_close_uses_market_order(config):
    now = datetime(2026, 8, 18, 15, 0, tzinfo=ET)
    config = replace(config, dry_run=False)
    fake = FakeAlpaca(now)
    bot = engine(config, fake)
    bot.store.save(
        DailyState(
            "2026-08-18",
            phase=Phase.OPEN,
            short_symbol="short",
            long_symbol="long",
            short_strike="535",
            long_strike="534",
            quantity=1,
            entry_credit="0.50",
            entry_submissions=1,
        )
    )
    bot.tick(now)
    state = bot.store.load("2026-08-18")
    assert state.exit_reason == "hard_close"
    assert fake.submissions[0]["price"] is None


def test_early_close_moves_hard_close_to_noon(config):
    now = datetime(2026, 8, 18, 12, 0, tzinfo=ET)
    config = replace(config, dry_run=False)
    fake = FakeAlpaca(now)
    fake.calendar_day = lambda _date: {
        "date": "2026-08-18",
        "open": "09:30",
        "close": "13:00",
    }
    bot = engine(config, fake)
    bot.store.save(
        DailyState(
            "2026-08-18",
            phase=Phase.OPEN,
            short_symbol="short",
            long_symbol="long",
            short_strike="535",
            long_strike="534",
            quantity=1,
            entry_credit="0.50",
            entry_submissions=1,
        )
    )
    bot.tick(now)
    assert bot.store.load("2026-08-18").exit_reason == "hard_close"
