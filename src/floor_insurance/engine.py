from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from decimal import ROUND_CEILING, Decimal
from zoneinfo import ZoneInfo

from .alpaca import AlpacaClient, AlpacaError
from .config import Config
from .models import DailyState, Phase
from .notify import Notifier
from .state import StateStore
from .strategy import (
    CENT,
    StrategySkip,
    executable_close_debit,
    executable_credit,
    select_spread,
    size_contracts,
)

LOG = logging.getLogger(__name__)
TERMINAL_FAILURES = {"canceled", "expired", "rejected", "replaced", "suspended"}


def _time(value: str) -> time:
    try:
        return time.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"invalid ET time {value!r}; expected HH:MM") from exc


class TradingEngine:
    def __init__(
        self,
        config: Config,
        alpaca: AlpacaClient,
        store: StateStore,
        notifier: Notifier,
    ):
        self.config = config
        self.alpaca = alpaca
        self.store = store
        self.notifier = notifier
        self.tz = ZoneInfo(config.timezone)

    def tick(self, now: datetime | None = None) -> int:
        now = (now or datetime.now(self.tz)).astimezone(self.tz)
        state = self.store.load(now.date().isoformat())
        if state.phase == Phase.DONE:
            return self.config.poll_seconds_idle

        clock = self.alpaca.clock()
        hard_close = self._hard_close(now, clock)

        if state.phase in {Phase.ENTRY_PENDING, Phase.EXIT_PENDING}:
            self._manage_pending(state, now, hard_close)
        elif state.phase == Phase.OPEN:
            self._manage_open(state, now, hard_close)
        elif now.time() >= hard_close:
            self._finish(state, now, "entry window ended without an open position")
        elif self._entry_allowed(now, clock, hard_close):
            self._enter(state, now)

        self.store.save(state)
        return (
            self.config.poll_seconds_open
            if state.phase in {Phase.OPEN, Phase.ENTRY_PENDING, Phase.EXIT_PENDING}
            else self.config.poll_seconds_idle
        )

    def _hard_close(self, now: datetime, clock: dict) -> time:
        configured = _time(self.config.hard_close_time)
        if not clock.get("is_open"):
            return configured
        close_at = datetime.fromisoformat(clock["next_close"].replace("Z", "+00:00")).astimezone(
            self.tz
        )
        one_hour_before = (close_at - timedelta(hours=1)).time().replace(tzinfo=None)
        return min(configured, one_hour_before)

    def _entry_allowed(self, now: datetime, clock: dict, hard_close: time) -> bool:
        return (
            bool(clock.get("is_open"))
            and _time(self.config.entry_time) <= now.time()
            and now.time() < min(_time(self.config.entry_cutoff_time), hard_close)
        )

    def _enter(self, state: DailyState, now: datetime) -> None:
        account = self.alpaca.account()
        if account.get("trading_blocked"):
            self._finish(state, now, "Alpaca account is trading-blocked")
            return
        level = int(account.get("options_trading_level") or 0)
        if level < 3:
            self._finish(state, now, f"options level {level}; level 3 is required for spreads")
            return

        price, trade_at = self.alpaca.latest_underlying_trade()
        self._require_fresh(trade_at, now, "SPY trade")
        contracts = self.alpaca.put_contracts(now.date().isoformat())
        short, long = select_spread(
            contracts, price, self.config.buffer_dollars, self.config.spread_width
        )
        quotes = self.alpaca.option_quotes([short.symbol, long.symbol])
        self._require_fresh(quotes[short.symbol].timestamp, now, "short option quote")
        self._require_fresh(quotes[long.symbol].timestamp, now, "long option quote")
        credit = executable_credit(quotes[short.symbol], quotes[long.symbol])
        if credit < self.config.min_credit:
            raise StrategySkip(
                f"executable credit ${credit:.2f} is below MIN_CREDIT ${self.config.min_credit:.2f}"
            )
        quantity = size_contracts(
            Decimal(str(account["equity"])),
            self.config.risk_fraction,
            self.config.spread_width,
            credit,
            self.config.max_contracts,
        )

        state.short_symbol = short.symbol
        state.long_symbol = long.symbol
        state.short_strike = str(short.strike)
        state.long_strike = str(long.strike)
        state.quantity = quantity
        state.entry_credit = str(credit)
        state.entry_submissions += 1
        client_id = f"floor-insurance-{state.trading_date}-entry-{state.entry_submissions}"
        state.active_client_order_id = client_id
        state.phase = Phase.ENTRY_PENDING
        state.event(
            "entry prepared",
            now,
            spy=str(price),
            short=str(short.strike),
            long=str(long.strike),
            credit=str(credit),
            quantity=quantity,
        )
        self.store.save(state)

        if self.config.dry_run:
            self._finish(state, now, "dry run: valid entry found; no order submitted")
            self.notifier.send(
                f"DRY RUN: {quantity}x SPY {short.strike}/{long.strike} put spread "
                f"at ${credit} credit; no order sent."
            )
            return

        order = self._submit_or_reconcile(
            state,
            opening=True,
            price=credit,
            client_id=client_id,
        )
        if order:
            state.active_order_id = order["id"]
            state.event("entry submitted", now, order_id=order["id"])
            self.notifier.send(
                f"Entry submitted: {quantity}x SPY {short.strike}/{long.strike} put "
                f"spread, limit ${credit} credit."
            )

    def _submit_or_reconcile(
        self,
        state: DailyState,
        *,
        opening: bool,
        price: Decimal | None,
        client_id: str,
    ) -> dict | None:
        try:
            return self.alpaca.submit_spread(
                short_symbol=state.short_symbol or "",
                long_symbol=state.long_symbol or "",
                quantity=state.quantity,
                price=price,
                opening=opening,
                client_order_id=client_id,
            )
        except AlpacaError:
            LOG.exception("order submission returned an error; reconciling by client ID")
            order = self.alpaca.order_by_client_id(client_id)
            if order:
                return order
            self.notifier.send(
                f"CRITICAL: order result unknown for {client_id}. Bot will not duplicate it; "
                "check Alpaca immediately."
            )
            return None

    def _manage_pending(self, state: DailyState, now: datetime, hard_close: time) -> None:
        order = self._resolve_pending_order(state)
        if not order:
            state.event("pending order not found; manual reconciliation required", now)
            return
        state.active_order_id = order["id"]
        status = order.get("status", "")
        filled_qty = int(Decimal(str(order.get("filled_qty") or "0")))

        if status == "filled" or (status in TERMINAL_FAILURES and filled_qty > 0):
            state.quantity = filled_qty or state.quantity
            if state.phase == Phase.ENTRY_PENDING:
                filled_price = abs(Decimal(str(order.get("filled_avg_price") or state.entry_credit)))
                state.entry_credit = str(filled_price)
                state.phase = Phase.OPEN
                state.active_order_id = None
                state.active_client_order_id = None
                state.event("entry filled", now, credit=str(filled_price), quantity=state.quantity)
                self.notifier.send(
                    f"Entry filled: {state.quantity}x {state.short_strike}/{state.long_strike} "
                    f"for ${filled_price} credit."
                )
            else:
                self._complete_exit(state, now, order)
            return

        if status in TERMINAL_FAILURES:
            if state.phase == Phase.ENTRY_PENDING:
                self._clear_trade(state)
                state.phase = Phase.IDLE
                state.event(f"entry {status}", now)
            else:
                state.phase = Phase.OPEN
                state.active_order_id = None
                state.active_client_order_id = None
                state.event(f"exit {status}; position remains open", now)
                self.notifier.send(f"Exit order {status}; position remains open.")
            return

        if now.time() >= hard_close:
            self.alpaca.cancel_order(order["id"])
            refreshed = self.alpaca.order(order["id"])
            if Decimal(str(refreshed.get("filled_qty") or "0")) > 0:
                self._manage_pending(state, now, hard_close)
            elif state.phase == Phase.ENTRY_PENDING:
                self._clear_trade(state)
                self._finish(state, now, "hard close canceled unfilled entry")
            else:
                state.phase = Phase.OPEN
                state.active_order_id = None
                state.active_client_order_id = None
                self._submit_exit(state, now, "hard_close", price=None)

    def _resolve_pending_order(self, state: DailyState) -> dict | None:
        if state.active_order_id:
            return self.alpaca.order(state.active_order_id)
        if state.active_client_order_id:
            return self.alpaca.order_by_client_id(state.active_client_order_id)
        raise RuntimeError("pending phase has no order identity")

    def _manage_open(self, state: DailyState, now: datetime, hard_close: time) -> None:
        if now.time() >= hard_close:
            self._submit_exit(state, now, "hard_close", price=None)
            return

        price, trade_at = self.alpaca.latest_underlying_trade()
        self._require_fresh(trade_at, now, "SPY trade")
        short_strike = Decimal(state.short_strike or "0")
        if price <= short_strike + self.config.stop_buffer:
            self._submit_exit(state, now, "emergency_stop", price=None)
            return

        if now.time() >= _time(self.config.take_profit_cutoff_time):
            return
        quotes = self.alpaca.option_quotes([state.short_symbol or "", state.long_symbol or ""])
        short_quote = quotes[state.short_symbol or ""]
        long_quote = quotes[state.long_symbol or ""]
        self._require_fresh(short_quote.timestamp, now, "short option quote")
        self._require_fresh(long_quote.timestamp, now, "long option quote")
        close_debit = executable_close_debit(short_quote, long_quote)
        target = (Decimal(state.entry_credit or "0") * self.config.take_profit_fraction).quantize(
            CENT, rounding=ROUND_CEILING
        )
        if close_debit <= target:
            self._submit_exit(state, now, "take_profit", price=target)

    def _submit_exit(
        self, state: DailyState, now: datetime, reason: str, price: Decimal | None
    ) -> None:
        client_id = (
            f"floor-insurance-{state.trading_date}-exit-{reason}-{state.entry_submissions}"
        )
        state.exit_reason = reason
        state.active_client_order_id = client_id
        state.phase = Phase.EXIT_PENDING
        state.event("exit prepared", now, reason=reason, price=str(price) if price else "market")
        self.store.save(state)
        order = self._submit_or_reconcile(
            state, opening=False, price=price, client_id=client_id
        )
        if order:
            state.active_order_id = order["id"]
            state.event("exit submitted", now, reason=reason, order_id=order["id"])
            self.notifier.send(
                f"Exit submitted ({reason}): {state.quantity} spread(s), "
                f"{'market' if price is None else f'limit ${price} debit'}."
            )

    def _complete_exit(self, state: DailyState, now: datetime, order: dict) -> None:
        reason = state.exit_reason or "unknown"
        price = abs(Decimal(str(order.get("filled_avg_price") or "0")))
        state.event("exit filled", now, reason=reason, debit=str(price))
        if reason == "emergency_stop":
            state.losses += 1
            self.notifier.send(
                f"Emergency stop filled at ${price} debit. Daily losses: "
                f"{state.losses}/{self.config.max_daily_losses}."
            )
            self._clear_trade(state)
            if state.losses >= self.config.max_daily_losses:
                self._finish(state, now, "daily loss circuit breaker reached")
            elif now.time() < _time(self.config.entry_cutoff_time):
                state.phase = Phase.IDLE
            else:
                self._finish(state, now, "stop filled after entry cutoff")
        else:
            self.notifier.send(f"Exit filled ({reason}) at ${price} debit. Done for the day.")
            self._clear_trade(state)
            self._finish(state, now, f"{reason} exit filled")

    def _require_fresh(self, observed: datetime, now: datetime, label: str) -> None:
        age = now.astimezone(ZoneInfo("UTC")) - observed.astimezone(ZoneInfo("UTC"))
        if age.total_seconds() < -5 or age.total_seconds() > self.config.max_quote_age_seconds:
            raise StrategySkip(f"{label} is stale ({age.total_seconds():.0f}s old)")

    def _clear_trade(self, state: DailyState) -> None:
        state.short_symbol = None
        state.long_symbol = None
        state.short_strike = None
        state.long_strike = None
        state.quantity = 0
        state.entry_credit = None
        state.active_order_id = None
        state.active_client_order_id = None
        state.exit_reason = None

    def _finish(self, state: DailyState, now: datetime, reason: str) -> None:
        state.phase = Phase.DONE
        state.event("done", now, reason=reason)
        LOG.info("trading day complete: %s", reason)

