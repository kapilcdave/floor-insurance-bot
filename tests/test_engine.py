from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from floor_insurance.engine import TradingEngine
from floor_insurance.models import Contract, DailyState, Phase, Quote
from floor_insurance.notify import Notifier
from floor_insurance.state import StateStore
from floor_insurance.strategy import StrategySkip

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

    def daily_closes(self, _symbol, _before_date, _observations):
        return [Decimal("100")] * 20 + [Decimal("110")]

    def latest_underlying_trade(self, _expiration_date=None):
        return self.price, self.now

    def put_contracts(self, _date):
        return [
            Contract("short", Decimal("550"), "2026-08-18"),
            Contract("long", Decimal("549"), "2026-08-18"),
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


class ProbeAlpaca(FakeAlpaca):
    def __init__(self, now: datetime):
        super().__init__(now)
        self.canceled = []
        self.quotes = {
            "SPY-P-546": Quote(Decimal("0.03"), Decimal("0.05"), now),
            "SPY-P-547": Quote(Decimal("0.25"), Decimal("0.27"), now),
            "SPY-P-548": Quote(Decimal("0.58"), Decimal("0.60"), now),
            "SPY-P-549": Quote(Decimal("0.80"), Decimal("0.82"), now),
            "SPY-P-550": Quote(Decimal("1.10"), Decimal("1.12"), now),
        }

    def daily_closes(self, *_args):
        raise AssertionError("paper probe must not evaluate a directional signal")

    def put_contracts(self, _date):
        return [
            Contract(f"SPY-P-{strike}", Decimal(str(strike)), "2026-08-18")
            for strike in range(546, 551)
        ]

    def option_quotes(self, symbols, *, allow_missing=False):
        return {symbol: self.quotes[symbol] for symbol in symbols}

    def cancel_order(self, order_id):
        self.canceled.append(order_id)
        super().cancel_order(order_id)


def engine(config, fake):
    return TradingEngine(
        config,
        fake,
        StateStore(config.state_path),
        Notifier(None, None, 1),
    )


def probe_config(config):
    return replace(
        config,
        paper_probe_mode=True,
        dry_run=False,
        shadow_mode=False,
        symbol="SPY",
        signal_symbol="SPY",
        strike_selection="credit_target",
        spread_width=Decimal("1"),
        min_credit=Decimal("0.30"),
        risk_budget_dollars=Decimal("100"),
        max_total_loss_dollars=Decimal("100"),
        max_contracts=1,
        max_daily_entries=1,
        take_profit_fraction=None,
        probe_max_otm_dollars=Decimal("3"),
        max_leg_quote_width=Decimal("0.10"),
        entry_fill_timeout_seconds=60,
        paper_probe_log_path=config.state_path.with_name("probe.jsonl"),
    )


def test_paper_probe_submits_fixed_limit_and_cancels_without_chasing(config):
    entered = datetime(2026, 8, 18, 10, 0, tzinfo=ET)
    config = probe_config(config)
    fake = ProbeAlpaca(entered)
    bot = engine(config, fake)

    bot.tick(entered)

    state = bot.store.load("2026-08-18")
    assert state.phase == Phase.ENTRY_PENDING
    assert state.short_strike == "548"
    assert state.long_strike == "547"
    assert fake.submissions[0]["price"] == Decimal("0.30")
    assert fake.submissions[0]["opening"] is True
    assert fake.submissions[0]["quantity"] == 1

    fake.now = entered + timedelta(seconds=59)
    bot.tick(fake.now)
    assert fake.canceled == []

    fake.now = entered + timedelta(seconds=60)
    bot.tick(fake.now)
    state = bot.store.load("2026-08-18")
    assert fake.canceled == ["order-1"]
    assert state.phase == Phase.DONE
    assert len(fake.submissions) == 1
    report = bot.probe_journal.summary()
    assert report["submitted"] == 1
    assert report["filled"] == 0
    assert report["unfilled"] == 1


def test_paper_probe_records_actual_broker_fill(config):
    entered = datetime(2026, 8, 18, 10, 0, tzinfo=ET)
    config = probe_config(config)
    fake = ProbeAlpaca(entered)
    bot = engine(config, fake)
    bot.tick(entered)
    fake.orders["order-1"].update(
        status="filled", filled_qty="1", filled_avg_price="-0.32"
    )

    fake.now = entered + timedelta(seconds=20)
    bot.tick(fake.now)

    state = bot.store.load("2026-08-18")
    assert state.phase == Phase.OPEN
    assert state.entry_credit == "0.32"
    report = bot.probe_journal.summary()
    assert report["submitted"] == 1
    assert report["filled"] == 1
    assert report["unfilled"] == 0


def test_paper_probe_skips_when_no_spread_reaches_target(config):
    entered = datetime(2026, 8, 18, 10, 0, tzinfo=ET)
    config = probe_config(config)
    fake = ProbeAlpaca(entered)
    fake.quotes = {
        symbol: Quote(Decimal("0.01"), Decimal("0.02"), entered)
        for symbol in fake.quotes
    }
    bot = engine(config, fake)

    with pytest.raises(StrategySkip, match=r"at least \$0.30"):
        bot.tick(entered)

    assert fake.submissions == []


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


def test_entry_is_skipped_when_previous_close_is_not_above_average(config):
    now = datetime(2026, 8, 18, 9, 45, tzinfo=ET)
    fake = FakeAlpaca(now)
    fake.daily_closes = lambda *_args: [Decimal("100")] * 20
    bot = engine(config, fake)

    bot.tick(now)

    state = bot.store.load("2026-08-18")
    assert state.phase == Phase.DONE
    assert state.event_history[-2]["event"] == "trend signal evaluated"
    assert state.event_history[-2]["eligible"] is False
    assert "not eligible" in state.event_history[-1]["reason"]
    assert fake.submissions == []


def test_crossover_mode_uses_one_extra_completed_close(config):
    now = datetime(2026, 8, 18, 9, 45, tzinfo=ET)
    fake = FakeAlpaca(now)
    observations = []

    def daily_closes(_symbol, _before_date, requested):
        observations.append(requested)
        return [Decimal("100")] * 20 + [Decimal("110")]

    fake.daily_closes = daily_closes
    bot = engine(replace(config, trend_mode="crossover"), fake)

    bot.tick(now)

    assert observations == [21]
    state = bot.store.load("2026-08-18")
    assert state.event_history[0]["mode"] == "crossover"
    assert state.event_history[0]["eligible"] is True


def test_emergency_stop_submits_atomic_market_exit(config):
    now = datetime(2026, 8, 18, 10, 0, tzinfo=ET)
    config = replace(config, dry_run=False)
    fake = FakeAlpaca(now)
    fake.short_quote = Quote(Decimal("1.00"), Decimal("1.05"), now)
    fake.long_quote = Quote(Decimal("0.00"), Decimal("0.05"), now)
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


def test_disabled_take_profit_holds_until_stop_or_hard_close(config):
    now = datetime(2026, 8, 18, 10, 0, tzinfo=ET)
    config = replace(config, dry_run=False, take_profit_fraction=None)
    fake = FakeAlpaca(now)
    fake.short_quote = Quote(Decimal("0.15"), Decimal("0.30"), now)
    fake.long_quote = Quote(Decimal("0.08"), Decimal("0.10"), now)
    bot = engine(config, fake)
    bot.store.save(
        DailyState(
            "2026-08-18",
            phase=Phase.OPEN,
            short_symbol="short",
            long_symbol="long",
            short_strike="550",
            long_strike="549",
            quantity=1,
            entry_credit="0.50",
            entry_submissions=1,
        )
    )

    bot.tick(now)

    assert bot.store.load("2026-08-18").phase == Phase.OPEN
    assert fake.submissions == []


def test_filled_stop_counts_loss_and_finishes_at_entry_cap(config):
    now = datetime(2026, 8, 18, 10, 0, tzinfo=ET)
    config = replace(config, dry_run=False)
    fake = FakeAlpaca(now)
    fake.short_quote = Quote(Decimal("1.00"), Decimal("1.05"), now)
    fake.long_quote = Quote(Decimal("0.00"), Decimal("0.05"), now)
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


def test_shadow_mode_tracks_virtual_trade_without_submitting_order(config):
    entered = datetime(2026, 8, 18, 9, 45, tzinfo=ET)
    config = replace(
        config,
        dry_run=False,
        shadow_mode=True,
        shadow_equity=Decimal("5000"),
        shadow_log_path=config.state_path.with_name("shadow.jsonl"),
    )
    fake = FakeAlpaca(entered)
    bot = engine(config, fake)
    bot.tick(entered)
    state = bot.store.load("2026-08-18")
    assert state.phase == Phase.OPEN
    assert state.shadow is True
    assert state.active_order_id is None
    assert state.active_client_order_id is None
    assert fake.submissions == []

    managed_at = entered.replace(hour=10, minute=0)
    fake.now = managed_at
    fake.short_quote = Quote(Decimal("0.15"), Decimal("0.30"), managed_at)
    fake.long_quote = Quote(Decimal("0.08"), Decimal("0.10"), managed_at)
    bot.tick(managed_at)

    state = bot.store.load("2026-08-18")
    assert state.phase == Phase.DONE
    assert state.event_history[-2]["event"] == "shadow exit filled"
    assert state.event_history[-2]["net_pnl"] == "28.00"
    assert fake.submissions == []
    summary = bot.shadow_journal.summary()
    assert summary["trades"] == 1
    assert summary["entries"] == 1
    assert summary["observations"] == 1
    assert summary["entries_without_exit"] == 0
    assert summary["wins"] == 1
    assert summary["total_net_pnl"] == "28.00"


def test_absolute_risk_budget_sizes_one_spread(config):
    entered = datetime(2026, 8, 18, 9, 45, tzinfo=ET)
    config = replace(
        config,
        dry_run=False,
        shadow_mode=True,
        symbol="XSP",
        risk_budget_dollars=Decimal("100"),
        shadow_equity=Decimal("100"),
        max_contracts=1,
        shadow_log_path=config.state_path.with_name("xsp-shadow.jsonl"),
    )
    fake = FakeAlpaca(entered)
    fake.short_quote = Quote(Decimal("0.08"), Decimal("0.09"), entered)
    fake.long_quote = Quote(Decimal("0.02"), Decimal("0.03"), entered)

    bot = engine(config, fake)
    bot.tick(entered)

    state = bot.store.load("2026-08-18")
    assert state.phase == Phase.OPEN
    assert state.quantity == 1
    assert state.event_history[-1]["max_loss_per_contract"] == "95.00"


def test_shadow_mode_accepts_penny_credit_and_small_timestamp_skew(config):
    entered = datetime(2026, 8, 18, 9, 45, tzinfo=ET)
    config = replace(
        config,
        dry_run=False,
        shadow_mode=True,
        max_quote_age_seconds=90,
        shadow_log_path=config.state_path.with_name("shadow-penny.jsonl"),
    )
    fake = FakeAlpaca(entered)
    fake.now = entered + timedelta(seconds=65)
    fake.short_quote = Quote(
        Decimal("0.03"), Decimal("0.04"), entered - timedelta(seconds=37)
    )
    fake.long_quote = Quote(
        Decimal("0"), Decimal("0.01"), entered - timedelta(seconds=34)
    )

    bot = engine(config, fake)
    bot.tick(entered)

    state = bot.store.load("2026-08-18")
    assert state.phase == Phase.OPEN
    assert state.shadow is True
    assert state.entry_credit == "0.02"
    assert fake.submissions == []


def test_timestamp_beyond_configured_skew_is_rejected(config):
    entered = datetime(2026, 8, 18, 9, 45, tzinfo=ET)
    config = replace(config, max_quote_age_seconds=90)
    fake = FakeAlpaca(entered)
    fake.now = entered + timedelta(seconds=91)

    with pytest.raises(StrategySkip, match="ahead of the bot clock"):
        engine(config, fake).tick(entered)


def test_shadow_stop_has_priority_and_applies_modeled_fees(config):
    entered = datetime(2026, 8, 18, 9, 45, tzinfo=ET)
    config = replace(
        config,
        dry_run=False,
        shadow_mode=True,
        shadow_equity=Decimal("5000"),
        shadow_fees_per_spread=Decimal("0.06"),
        shadow_log_path=config.state_path.with_name("shadow-stop.jsonl"),
    )
    fake = FakeAlpaca(entered)
    bot = engine(config, fake)
    bot.tick(entered)

    stopped_at = entered.replace(hour=10, minute=0)
    fake.now = stopped_at
    fake.short_quote = Quote(Decimal("0.95"), Decimal("1.08"), stopped_at)
    fake.long_quote = Quote(Decimal("0.08"), Decimal("0.08"), stopped_at)
    bot.tick(stopped_at)

    state = bot.store.load("2026-08-18")
    assert state.phase == Phase.DONE
    assert state.losses == 1
    summary = bot.shadow_journal.summary()
    assert summary["exit_reasons"] == {"emergency_stop": 1}
    assert summary["total_net_pnl"] == "-50.06"
    assert fake.submissions == []
