from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from decimal import ROUND_CEILING, Decimal
from zoneinfo import ZoneInfo

from .alpaca import AlpacaClient, AlpacaError
from .config import Config
from .models import DailyState, Phase
from .notify import Notifier
from .shadow import ShadowJournal
from .state import StateStore
from .strategy import (
    CENT,
    StrategySkip,
    executable_close_debit,
    executable_credit,
    max_loss_per_contract,
    select_spread,
    size_contracts,
)

LOG = logging.getLogger(__name__)
TERMINAL_FAILURES = {"canceled", "expired", "rejected", "replaced", "suspended"}


class SubmissionRejected(RuntimeError):
    """The broker definitively rejected an order before acceptance."""


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
        shadow_journal: ShadowJournal | None = None,
    ):
        self.config = config
        self.alpaca = alpaca
        self.store = store
        self.notifier = notifier
        self.shadow_journal = shadow_journal or ShadowJournal(config.shadow_log_path)
        self.tz = ZoneInfo(config.timezone)
        self._calendar_cache: dict[str, dict | None] = {}

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
            if state.entry_submissions >= self.config.max_daily_entries:
                self._finish(state, now, "daily entry limit reached")
            else:
                self._enter(state, now)

        self.store.save(state)
        return (
            self.config.poll_seconds_open
            if state.phase in {Phase.OPEN, Phase.ENTRY_PENDING, Phase.EXIT_PENDING}
            else self.config.poll_seconds_idle
        )

    def _hard_close(self, now: datetime, clock: dict) -> time:
        configured = _time(self.config.hard_close_time)
        trading_date = now.date().isoformat()
        if trading_date not in self._calendar_cache:
            self._calendar_cache[trading_date] = self.alpaca.calendar_day(trading_date)
        session = self._calendar_cache[trading_date]
        if session:
            close_value = str(session["close"])
            if "T" in close_value:
                close_at = datetime.fromisoformat(close_value.replace("Z", "+00:00")).astimezone(
                    self.tz
                )
            else:
                close_at = datetime.combine(now.date(), _time(close_value), self.tz)
        elif clock.get("is_open"):
            close_at = datetime.fromisoformat(
                clock["next_close"].replace("Z", "+00:00")
            ).astimezone(self.tz)
        else:
            return configured
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
        if account.get("trading_blocked") and not self.config.shadow_mode:
            self._finish(state, now, "Alpaca account is trading-blocked")
            return
        level = int(account.get("options_trading_level") or 0)
        if level < 3 and not self.config.shadow_mode:
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
        min_credit = (
            self.config.shadow_min_credit
            if self.config.shadow_mode
            else self.config.min_credit
        )
        if credit < min_credit:
            threshold_name = (
                "SHADOW_MIN_CREDIT" if self.config.shadow_mode else "MIN_CREDIT"
            )
            raise StrategySkip(
                f"executable credit ${credit:.2f} is below "
                f"{threshold_name} ${min_credit:.2f}"
            )
        sizing_equity = (
            self.config.shadow_equity
            if self.config.shadow_mode
            else Decimal(str(account["equity"]))
        )
        quantity = size_contracts(
            sizing_equity,
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
        state.event(
            "entry prepared",
            now,
            spy=str(price),
            short=str(short.strike),
            long=str(long.strike),
            credit=str(credit),
            quantity=quantity,
        )

        if self.config.shadow_mode:
            state.phase = Phase.OPEN
            state.shadow = True
            state.entry_filled_at = now.isoformat()
            state.entry_underlying = str(price)
            state.active_client_order_id = None
            per_contract_risk = max_loss_per_contract(
                self.config.spread_width, credit
            )
            state.event(
                "shadow entry filled",
                now,
                credit=str(credit),
                quantity=quantity,
                max_loss_per_contract=str(per_contract_risk),
            )
            self.store.save(state)
            self.shadow_journal.write(
                "shadow_entry",
                now,
                trading_date=state.trading_date,
                feed=self.config.options_feed,
                modeled_equity=sizing_equity,
                risk_fraction=self.config.risk_fraction,
                underlying=price,
                short_symbol=short.symbol,
                short_strike=short.strike,
                short_bid=quotes[short.symbol].bid,
                short_ask=quotes[short.symbol].ask,
                long_symbol=long.symbol,
                long_strike=long.strike,
                long_bid=quotes[long.symbol].bid,
                long_ask=quotes[long.symbol].ask,
                entry_credit=credit,
                quantity=quantity,
                max_loss_per_contract=per_contract_risk,
            )
            self.notifier.send(
                f"SHADOW entry: {quantity}x SPY {short.strike}/{long.strike} "
                f"at ${credit} modeled credit; no order sent."
            )
            return

        if self.config.dry_run:
            self._finish(state, now, "dry run: valid entry found; no order submitted")
            self.notifier.send(
                f"DRY RUN: {quantity}x SPY {short.strike}/{long.strike} put spread "
                f"at ${credit} credit; no order sent."
            )
            return

        client_id = f"floor-insurance-{state.trading_date}-entry-{state.entry_submissions}"
        state.active_client_order_id = client_id
        state.phase = Phase.ENTRY_PENDING
        self.store.save(state)
        try:
            order = self._submit_or_reconcile(
                state,
                opening=True,
                price=credit,
                client_id=client_id,
            )
        except SubmissionRejected as exc:
            self._clear_trade(state)
            self._finish(state, now, f"entry rejected: {exc}")
            self.notifier.send(f"Entry rejected by Alpaca: {exc}")
            return
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
        except AlpacaError as exc:
            if exc.status_code is not None and 400 <= exc.status_code < 500 and exc.status_code not in {408, 429}:
                raise SubmissionRejected(str(exc)) from exc
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
        if state.shadow:
            self._manage_shadow_open(state, now, hard_close)
            return
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

    def _manage_shadow_open(
        self, state: DailyState, now: datetime, hard_close: time
    ) -> None:
        price, trade_at = self.alpaca.latest_underlying_trade()
        self._require_fresh(trade_at, now, "SPY trade")
        quotes = self.alpaca.option_quotes(
            [state.short_symbol or "", state.long_symbol or ""]
        )
        short_quote = quotes[state.short_symbol or ""]
        long_quote = quotes[state.long_symbol or ""]
        self._require_fresh(short_quote.timestamp, now, "short option quote")
        self._require_fresh(long_quote.timestamp, now, "long option quote")
        close_debit = executable_close_debit(short_quote, long_quote)
        target = (
            Decimal(state.entry_credit or "0") * self.config.take_profit_fraction
        ).quantize(CENT, rounding=ROUND_CEILING)
        stop_level = Decimal(state.short_strike or "0") + self.config.stop_buffer

        self.shadow_journal.write(
            "shadow_observation",
            now,
            trading_date=state.trading_date,
            underlying=price,
            stop_level=stop_level,
            short_symbol=state.short_symbol,
            short_bid=short_quote.bid,
            short_ask=short_quote.ask,
            long_symbol=state.long_symbol,
            long_bid=long_quote.bid,
            long_ask=long_quote.ask,
            executable_close_debit=close_debit,
            take_profit_target=target,
        )

        if now.time() >= hard_close:
            self._complete_shadow_exit(state, now, "hard_close", close_debit, price)
        elif price <= stop_level:
            self._complete_shadow_exit(
                state, now, "emergency_stop", close_debit, price
            )
        elif (
            now.time() < _time(self.config.take_profit_cutoff_time)
            and close_debit <= target
        ):
            self._complete_shadow_exit(state, now, "take_profit", close_debit, price)

    def _complete_shadow_exit(
        self,
        state: DailyState,
        now: datetime,
        reason: str,
        exit_debit: Decimal,
        underlying: Decimal,
    ) -> None:
        entry_credit = Decimal(state.entry_credit or "0")
        fees = self.config.shadow_fees_per_spread * state.quantity
        gross_pnl = (entry_credit - exit_debit) * Decimal("100") * state.quantity
        net_pnl = (gross_pnl - fees).quantize(CENT)
        self.shadow_journal.write(
            "shadow_exit",
            now,
            trading_date=state.trading_date,
            entered_at=state.entry_filled_at,
            entry_underlying=state.entry_underlying,
            exit_underlying=underlying,
            short_symbol=state.short_symbol,
            short_strike=state.short_strike,
            long_symbol=state.long_symbol,
            long_strike=state.long_strike,
            quantity=state.quantity,
            entry_credit=entry_credit,
            exit_debit=exit_debit,
            gross_pnl=gross_pnl.quantize(CENT),
            modeled_fees=fees.quantize(CENT),
            net_pnl=net_pnl,
            reason=reason,
            feed=self.config.options_feed,
        )
        state.event(
            "shadow exit filled",
            now,
            reason=reason,
            debit=str(exit_debit),
            net_pnl=str(net_pnl),
        )
        self.notifier.send(
            f"SHADOW exit ({reason}): ${exit_debit} debit, modeled net P&L "
            f"${net_pnl}; no order sent."
        )
        if reason == "emergency_stop":
            state.losses += 1
            self._clear_trade(state)
            if state.losses >= self.config.max_daily_losses:
                self._finish(state, now, "shadow daily loss circuit breaker reached")
            elif (
                state.entry_submissions < self.config.max_daily_entries
                and now.time() < _time(self.config.entry_cutoff_time)
            ):
                state.phase = Phase.IDLE
            else:
                self._finish(
                    state, now, "shadow stop; daily entry limit or cutoff reached"
                )
        else:
            self._clear_trade(state)
            self._finish(state, now, f"shadow {reason} exit")

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
        try:
            order = self._submit_or_reconcile(
                state, opening=False, price=price, client_id=client_id
            )
        except SubmissionRejected as exc:
            state.phase = Phase.OPEN
            state.active_order_id = None
            state.active_client_order_id = None
            state.event("exit rejected; position remains open", now, reason=reason)
            self.notifier.send(
                f"CRITICAL: Alpaca rejected the {reason} exit; position remains open: {exc}"
            )
            return
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
            elif (
                state.entry_submissions < self.config.max_daily_entries
                and now.time() < _time(self.config.entry_cutoff_time)
            ):
                state.phase = Phase.IDLE
            else:
                self._finish(state, now, "stop filled; daily entry limit or cutoff reached")
        else:
            self.notifier.send(f"Exit filled ({reason}) at ${price} debit. Done for the day.")
            self._clear_trade(state)
            self._finish(state, now, f"{reason} exit filled")

    def _require_fresh(self, observed: datetime, now: datetime, label: str) -> None:
        age = now.astimezone(ZoneInfo("UTC")) - observed.astimezone(ZoneInfo("UTC"))
        age_seconds = age.total_seconds()
        max_age = self.config.max_quote_age_seconds
        if abs(age_seconds) > max_age:
            if age_seconds < 0:
                raise StrategySkip(
                    f"{label} timestamp is {abs(age_seconds):.0f}s ahead of the bot clock"
                )
            raise StrategySkip(f"{label} is stale ({age_seconds:.0f}s old)")

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
        state.shadow = False
        state.entry_filled_at = None
        state.entry_underlying = None

    def _finish(self, state: DailyState, now: datetime, reason: str) -> None:
        state.phase = Phase.DONE
        state.event("done", now, reason=reason)
        LOG.info("trading day complete: %s", reason)
