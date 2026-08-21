"""Paper-only forward execution probe for the local-surface butterfly."""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import time as time_module
from dataclasses import asdict, dataclass, field
from datetime import datetime, time, timezone
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .alpaca import AlpacaClient, AlpacaError
from .models import Contract, Quote
from .notify import Notifier
from .probe import PaperProbeJournal
from .surface_butterfly_research import CENTER_OFFSETS

LOG = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")
CENT = Decimal("0.01")
TERMINAL_FAILURES = {"canceled", "expired", "rejected", "replaced", "suspended"}


class SurfaceProbeError(RuntimeError):
    """Raised when the forward probe cannot safely proceed."""


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise SurfaceProbeError(f"{name} must be true or false")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise SurfaceProbeError(f"{name} must be an integer") from exc


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


@dataclass(frozen=True)
class SurfaceProbeConfig:
    """Narrow configuration accepted by the paper-only runner.

    Signal and execution parameters are intentionally constants rather than
    environment-tunable fields. Changing one requires a code and protocol
    revision, which prevents accidental forward-test drift.
    """

    api_key: str
    api_secret: str
    trading_base_url: str
    data_base_url: str
    request_timeout_seconds: int
    symbol: str = "SPY"
    stock_feed: str = "iex"
    options_feed: str = "indicative"
    paper: bool = True
    live_confirmed: bool = False
    probe_confirmed: bool = False
    entry_time: time = time(11, 0)
    entry_cutoff: time = time(11, 5)
    exit_time: time = time(12, 0)
    hard_close_time: time = time(15, 0)
    wing_width: Decimal = Decimal("1")
    center_offsets: tuple[int, ...] = CENTER_OFFSETS
    minimum_parity_gap: Decimal = Decimal("0.08")
    maximum_entry_debit: Decimal = Decimal("0.10")
    minimum_signed_limit: Decimal = Decimal("-0.10")
    maximum_leg_quote_width: Decimal = Decimal("0.10")
    maximum_quote_age_seconds: int = 90
    entry_timeout_seconds: int = 60
    poll_seconds: int = 5
    state_path: Path = Path("state/surface_butterfly_probe_state.json")
    journal_path: Path = Path("state/surface_butterfly_probe_events.jsonl")
    telegram_token: str | None = None
    telegram_chat_id: str | None = None

    @classmethod
    def from_env(cls, *, require_credentials: bool = True) -> SurfaceProbeConfig:
        api_key = os.getenv("ALPACA_API_KEY", "").strip()
        api_secret = os.getenv("ALPACA_API_SECRET", "").strip()
        config = cls(
            api_key=api_key,
            api_secret=api_secret,
            trading_base_url=os.getenv(
                "ALPACA_TRADING_URL", "https://paper-api.alpaca.markets"
            ).rstrip("/"),
            data_base_url=os.getenv(
                "ALPACA_DATA_URL", "https://data.alpaca.markets"
            ).rstrip("/"),
            request_timeout_seconds=_int("REQUEST_TIMEOUT_SECONDS", 10),
            stock_feed=os.getenv("STOCK_FEED", "iex").strip().lower(),
            options_feed=os.getenv("OPTIONS_FEED", "indicative").strip().lower(),
            paper=_bool("ALPACA_PAPER", True),
            live_confirmed=_bool("LIVE_TRADING_CONFIRMED", False),
            probe_confirmed=_bool("SURFACE_BUTTERFLY_PAPER_PROBE", False),
            poll_seconds=_int("SURFACE_PROBE_POLL_SECONDS", 5),
            state_path=Path(
                os.getenv(
                    "SURFACE_PROBE_STATE_PATH",
                    "state/surface_butterfly_probe_state.json",
                )
            ),
            journal_path=Path(
                os.getenv(
                    "SURFACE_PROBE_LOG_PATH",
                    "state/surface_butterfly_probe_events.jsonl",
                )
            ),
            telegram_token=os.getenv("TELEGRAM_BOT_TOKEN") or None,
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID") or None,
        )
        config.validate(require_credentials=require_credentials)
        return config

    def validate(self, *, require_credentials: bool = True) -> None:
        if require_credentials and (not self.api_key or not self.api_secret):
            raise SurfaceProbeError("ALPACA_API_KEY and ALPACA_API_SECRET are required")
        if (
            not self.paper
            or self.trading_base_url != "https://paper-api.alpaca.markets"
        ):
            raise SurfaceProbeError(
                "surface butterfly probe is paper-only and requires "
                "https://paper-api.alpaca.markets"
            )
        if self.data_base_url != "https://data.alpaca.markets":
            raise SurfaceProbeError(
                "surface butterfly probe requires https://data.alpaca.markets"
            )
        if self.live_confirmed:
            raise SurfaceProbeError(
                "LIVE_TRADING_CONFIRMED must remain false for the surface probe"
            )
        if self.stock_feed not in {"iex", "sip"}:
            raise SurfaceProbeError("STOCK_FEED must be iex or sip")
        if self.options_feed not in {"indicative", "opra"}:
            raise SurfaceProbeError("OPTIONS_FEED must be indicative or opra")
        if min(self.request_timeout_seconds, self.poll_seconds) < 1:
            raise SurfaceProbeError("timeouts and polling interval must be positive")
        if self.state_path == self.journal_path:
            raise SurfaceProbeError("surface probe state and journal paths must differ")

    def authorize_orders(self) -> None:
        self.validate()
        if not self.probe_confirmed:
            raise SurfaceProbeError(
                "order submission blocked: set SURFACE_BUTTERFLY_PAPER_PROBE=true "
                "with paper credentials"
            )


@dataclass(frozen=True)
class ButterflyCandidate:
    center: Decimal
    kind: str
    parity_gap: Decimal
    call_debit: Decimal
    put_debit: Decimal
    displayed_debit: Decimal
    limit_price: Decimal
    symbols: tuple[str, str, str]
    quotes: dict[str, Quote]


@dataclass(frozen=True)
class ScanResult:
    observed_at: datetime
    underlying: Decimal
    underlying_at: datetime
    in_entry_window: bool
    candidate_count: int
    candidate: ButterflyCandidate | None
    reason: str
    diagnostics: tuple[dict[str, Any], ...] = ()


def _nearest_whole(value: Decimal) -> Decimal:
    return value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def _fresh_and_tight(quote: Quote, now: datetime, config: SurfaceProbeConfig) -> bool:
    age = now.astimezone(timezone.utc) - quote.timestamp.astimezone(timezone.utc)
    width = quote.ask - quote.bid
    return (
        Decimal("0") <= quote.bid <= quote.ask
        and quote.ask > 0
        and width <= config.maximum_leg_quote_width
        and -2 <= age.total_seconds() <= config.maximum_quote_age_seconds
    )


def _buy_debit(quotes: tuple[Quote, Quote, Quote]) -> Decimal:
    lower, middle, upper = quotes
    return lower.ask - Decimal("2") * middle.bid + upper.ask


def _close_signed_price(quotes: tuple[Quote, Quote, Quote]) -> Decimal:
    """Return signed close cost: buys minus sells, so credits are negative."""

    lower, middle, upper = quotes
    return Decimal("2") * middle.ask - lower.bid - upper.bid


def _contract_triplet(
    contracts: dict[Decimal, Contract], center: Decimal, width: Decimal
) -> tuple[Contract, Contract, Contract] | None:
    strikes = (center - width, center, center + width)
    if not all(strike in contracts for strike in strikes):
        return None
    return tuple(contracts[strike] for strike in strikes)  # type: ignore[return-value]


def select_candidate(
    *,
    spot: Decimal,
    calls: list[Contract],
    puts: list[Contract],
    quotes: dict[str, Quote],
    now: datetime,
    config: SurfaceProbeConfig,
    diagnostics: list[dict[str, Any]] | None = None,
) -> tuple[ButterflyCandidate | None, int]:
    by_kind = {
        "C": {contract.strike: contract for contract in calls},
        "P": {contract.strike: contract for contract in puts},
    }
    base = _nearest_whole(spot)
    passed: list[ButterflyCandidate] = []
    evaluated = 0
    for offset in config.center_offsets:
        center = base + Decimal(offset)
        triplets = {
            kind: _contract_triplet(values, center, config.wing_width)
            for kind, values in by_kind.items()
        }
        if any(value is None for value in triplets.values()):
            if diagnostics is not None:
                diagnostics.append({"center": center, "result": "missing_contract"})
            continue
        symbols = {
            kind: tuple(contract.symbol for contract in triplet or ())
            for kind, triplet in triplets.items()
        }
        six = [symbol for values in symbols.values() for symbol in values]
        if not all(
            symbol in quotes and _fresh_and_tight(quotes[symbol], now, config)
            for symbol in six
        ):
            if diagnostics is not None:
                diagnostics.append({"center": center, "result": "quote_gate"})
            continue
        evaluated += 1
        debit = {
            kind: _buy_debit(tuple(quotes[symbol] for symbol in values))
            for kind, values in symbols.items()
        }
        gap = abs(debit["C"] - debit["P"])
        if gap < config.minimum_parity_gap:
            if diagnostics is not None:
                diagnostics.append(
                    {
                        "center": center,
                        "result": "gap_below_minimum",
                        "call_debit": debit["C"],
                        "put_debit": debit["P"],
                        "parity_gap": gap,
                    }
                )
            continue
        kind = "C" if debit["C"] <= debit["P"] else "P"
        limit_price = debit[kind].quantize(CENT, rounding=ROUND_CEILING)
        if not config.minimum_signed_limit <= limit_price <= config.maximum_entry_debit:
            if diagnostics is not None:
                diagnostics.append(
                    {
                        "center": center,
                        "result": "limit_outside_range",
                        "kind": kind,
                        "call_debit": debit["C"],
                        "put_debit": debit["P"],
                        "parity_gap": gap,
                        "limit_price": limit_price,
                    }
                )
            continue
        selected_symbols = symbols[kind]
        passed.append(
            ButterflyCandidate(
                center=center,
                kind=kind,
                parity_gap=gap,
                call_debit=debit["C"],
                put_debit=debit["P"],
                displayed_debit=debit[kind],
                limit_price=limit_price,
                symbols=selected_symbols,
                quotes={symbol: quotes[symbol] for symbol in six},
            )
        )
        if diagnostics is not None:
            diagnostics.append(
                {
                    "center": center,
                    "result": "passed",
                    "kind": kind,
                    "call_debit": debit["C"],
                    "put_debit": debit["P"],
                    "parity_gap": gap,
                    "limit_price": limit_price,
                }
            )
    if not passed:
        return None, evaluated
    return (
        sorted(
            passed,
            key=lambda item: (
                -item.parity_gap,
                item.limit_price,
                abs(item.center - spot),
                item.center,
                item.kind,
            ),
        )[0],
        evaluated,
    )


def scan_surface(
    client: AlpacaClient,
    config: SurfaceProbeConfig,
    now: datetime | None = None,
) -> ScanResult:
    now = (now or datetime.now(ET)).astimezone(ET)
    trading_date = now.date().isoformat()
    spot, spot_at = client.latest_underlying_trade(trading_date)
    spot_age = now.astimezone(timezone.utc) - spot_at.astimezone(timezone.utc)
    in_window = config.entry_time <= now.time() <= config.entry_cutoff
    if not -2 <= spot_age.total_seconds() <= config.maximum_quote_age_seconds:
        return ScanResult(
            now,
            spot,
            spot_at,
            in_window,
            0,
            None,
            "underlying trade is stale",
        )
    calls = client.option_contracts(trading_date, "call")
    puts = client.option_contracts(trading_date, "put")
    by_kind = {
        "C": {contract.strike: contract for contract in calls},
        "P": {contract.strike: contract for contract in puts},
    }
    base = _nearest_whole(spot)
    symbols = sorted(
        {
            contract.symbol
            for offset in config.center_offsets
            for values in by_kind.values()
            for contract in (
                _contract_triplet(
                    values, base + Decimal(offset), config.wing_width
                )
                or ()
            )
        }
    )
    quotes = client.option_quotes(symbols, allow_missing=True) if symbols else {}
    diagnostics: list[dict[str, Any]] = []
    candidate, evaluated = select_candidate(
        spot=spot,
        calls=calls,
        puts=puts,
        quotes=quotes,
        now=now,
        config=config,
        diagnostics=diagnostics,
    )
    reason = "candidate selected" if candidate else "no candidate passed quote, gap, and debit gates"
    return ScanResult(
        now,
        spot,
        spot_at,
        in_window,
        evaluated,
        candidate,
        reason,
        tuple(diagnostics),
    )


def _entry_legs(symbols: tuple[str, str, str]) -> list[dict[str, str]]:
    return [
        {
            "symbol": symbols[0],
            "ratio_qty": "1",
            "side": "buy",
            "position_intent": "buy_to_open",
        },
        {
            "symbol": symbols[1],
            "ratio_qty": "2",
            "side": "sell",
            "position_intent": "sell_to_open",
        },
        {
            "symbol": symbols[2],
            "ratio_qty": "1",
            "side": "buy",
            "position_intent": "buy_to_open",
        },
    ]


def _exit_legs(symbols: tuple[str, str, str]) -> list[dict[str, str]]:
    return [
        {
            "symbol": symbols[0],
            "ratio_qty": "1",
            "side": "sell",
            "position_intent": "sell_to_close",
        },
        {
            "symbol": symbols[1],
            "ratio_qty": "2",
            "side": "buy",
            "position_intent": "buy_to_close",
        },
        {
            "symbol": symbols[2],
            "ratio_qty": "1",
            "side": "sell",
            "position_intent": "sell_to_close",
        },
    ]


@dataclass
class SurfaceProbeState:
    trading_date: str
    phase: str = "idle"
    candidate: dict[str, Any] | None = None
    symbols: list[str] = field(default_factory=list)
    entry_client_id: str | None = None
    entry_order_id: str | None = None
    entry_submitted_at: str | None = None
    entry_cancel_requested_at: str | None = None
    entry_filled_at: str | None = None
    entry_signed_price: str | None = None
    exit_client_id: str | None = None
    exit_order_id: str | None = None
    exit_submitted_at: str | None = None
    exit_reason: str | None = None
    last_event: str = "fresh day"


class SurfaceStateStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self, trading_date: str) -> SurfaceProbeState:
        if not self.path.exists():
            return SurfaceProbeState(trading_date)
        try:
            state = SurfaceProbeState(**json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, TypeError, ValueError) as exc:
            raise SurfaceProbeError(f"cannot read surface probe state: {exc}") from exc
        if state.trading_date != trading_date:
            if state.phase in {"entry_pending", "open", "exit_pending"}:
                raise SurfaceProbeError(
                    f"surface state from {state.trading_date} is still {state.phase}; "
                    "reconcile the paper account manually"
                )
            return SurfaceProbeState(trading_date)
        return state

    def save(self, state: SurfaceProbeState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(asdict(state), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.path)


class SurfaceProbeRunner:
    def __init__(
        self,
        config: SurfaceProbeConfig,
        client: AlpacaClient,
        notifier: Notifier | None = None,
    ):
        config.authorize_orders()
        self.config = config
        self.client = client
        self.notifier = notifier or Notifier(
            config.telegram_token,
            config.telegram_chat_id,
            config.request_timeout_seconds,
        )
        self.store = SurfaceStateStore(config.state_path)
        self.journal = PaperProbeJournal(config.journal_path)

    def _note(self, event: str, now: datetime, **details: Any) -> None:
        self.journal.write(event, now, **details)

    def _submit_or_reconcile(
        self,
        *,
        legs: list[dict[str, str]],
        price: Decimal | None,
        client_id: str,
    ) -> dict[str, Any] | None:
        try:
            return self.client.submit_multileg(
                legs=legs,
                quantity=1,
                price=price,
                client_order_id=client_id,
            )
        except AlpacaError as exc:
            if exc.status_code is not None and 400 <= exc.status_code < 500:
                raise SurfaceProbeError(f"paper order rejected: {exc}") from exc
            LOG.exception("surface order result uncertain; reconciling by client ID")
            return self.client.order_by_client_id(client_id)

    @staticmethod
    def _seconds_since(value: str | None, now: datetime) -> float:
        if not value:
            return 0
        return (now - datetime.fromisoformat(value).astimezone(ET)).total_seconds()

    def tick(self, now: datetime | None = None) -> int:
        now = (now or datetime.now(ET)).astimezone(ET)
        state = self.store.load(now.date().isoformat())
        if state.phase == "entry_pending":
            self._manage_entry(state, now)
        elif state.phase == "open":
            if now.time() >= self.config.exit_time:
                self._submit_exit(state, now, "noon_markout")
        elif state.phase == "exit_pending":
            self._manage_exit(state, now)
        elif state.phase == "idle":
            clock = self.client.clock()
            if not clock.get("is_open"):
                return self.config.poll_seconds
            if now.time() < self.config.entry_time:
                return self.config.poll_seconds
            if now.time() > self.config.entry_cutoff:
                state.phase = "done"
                state.last_event = "entry window missed"
                self.store.save(state)
                self._note("surface_probe_window_missed", now)
            else:
                self._enter(state, now)
        self.store.save(state)
        return self.config.poll_seconds

    def _enter(self, state: SurfaceProbeState, now: datetime) -> None:
        result = scan_surface(self.client, self.config, now)
        candidate = result.candidate
        if candidate is None:
            state.phase = "done"
            state.last_event = result.reason
            self._note(
                "surface_probe_no_candidate",
                now,
                underlying=result.underlying,
                evaluated_centers=result.candidate_count,
                reason=result.reason,
            )
            return
        details = candidate_details(candidate)
        self._note(
            "surface_probe_candidate",
            now,
            underlying=result.underlying,
            underlying_at=result.underlying_at,
            **details,
        )
        state.candidate = _json_value(details)
        state.symbols = list(candidate.symbols)
        state.entry_client_id = f"surface-{state.trading_date.replace('-', '')}-entry"
        state.entry_submitted_at = now.isoformat()
        state.phase = "entry_pending"
        state.last_event = "entry submission prepared"
        self.store.save(state)
        order = self._submit_or_reconcile(
            legs=_entry_legs(candidate.symbols),
            price=candidate.limit_price,
            client_id=state.entry_client_id,
        )
        if order is None:
            state.last_event = "entry outcome unknown; manual reconciliation required"
            self.notifier.send(
                f"CRITICAL: paper butterfly entry {state.entry_client_id} has unknown status."
            )
            return
        state.entry_order_id = str(order["id"])
        state.last_event = "entry submitted"
        self._note(
            "surface_probe_submitted",
            now,
            order_id=state.entry_order_id,
            client_order_id=state.entry_client_id,
            limit_price=candidate.limit_price,
            symbols=candidate.symbols,
        )
        self.notifier.send(
            f"PAPER surface butterfly submitted: SPY {candidate.center} {candidate.kind}, "
            f"limit {candidate.limit_price}."
        )

    def _pending_order(self, order_id: str | None, client_id: str | None) -> dict[str, Any] | None:
        if order_id:
            return self.client.order(order_id)
        if client_id:
            return self.client.order_by_client_id(client_id)
        return None

    def _manage_entry(self, state: SurfaceProbeState, now: datetime) -> None:
        order = self._pending_order(state.entry_order_id, state.entry_client_id)
        if order is None:
            state.last_event = "entry order not found; manual reconciliation required"
            return
        state.entry_order_id = str(order["id"])
        status = str(order.get("status", ""))
        filled_qty = Decimal(str(order.get("filled_qty") or "0"))
        if status == "filled" or (status in TERMINAL_FAILURES and filled_qty > 0):
            signed_price = Decimal(str(order.get("filled_avg_price") or "0"))
            state.entry_signed_price = str(signed_price)
            state.entry_filled_at = now.isoformat()
            state.phase = "open"
            state.last_event = "entry filled"
            seconds = self._seconds_since(state.entry_submitted_at, now)
            self._note(
                "surface_probe_filled",
                now,
                order_id=state.entry_order_id,
                signed_entry_price=signed_price,
                seconds_to_fill=seconds,
                cancel_was_requested=bool(state.entry_cancel_requested_at),
            )
            self.notifier.send(
                f"PAPER butterfly filled at signed price {signed_price}; scheduled exit 12:00 ET."
            )
            return
        if status in TERMINAL_FAILURES:
            state.phase = "done"
            state.last_event = f"entry {status} unfilled"
            self._note(
                "surface_probe_unfilled",
                now,
                order_id=state.entry_order_id,
                status=status,
                seconds_working=self._seconds_since(state.entry_submitted_at, now),
            )
            return
        elapsed = self._seconds_since(state.entry_submitted_at, now)
        if elapsed >= self.config.entry_timeout_seconds and not state.entry_cancel_requested_at:
            self.client.cancel_order(state.entry_order_id)
            state.entry_cancel_requested_at = now.isoformat()
            state.last_event = "entry cancel requested"
            self._note(
                "surface_probe_cancel_requested",
                now,
                order_id=state.entry_order_id,
                seconds_working=elapsed,
            )

    def _submit_exit(self, state: SurfaceProbeState, now: datetime, reason: str) -> None:
        if len(state.symbols) != 3:
            raise SurfaceProbeError("open butterfly state does not contain three symbols")
        symbols = tuple(state.symbols)
        mark: Decimal | None = None
        try:
            quotes = self.client.option_quotes(list(symbols))
            triplet = tuple(quotes[symbol] for symbol in symbols)
            if all(_fresh_and_tight(quote, now, self.config) for quote in triplet):
                mark = _close_signed_price(triplet)  # type: ignore[arg-type]
        except AlpacaError:
            LOG.exception("could not obtain noon executable close mark")
        self._note(
            "surface_probe_markout",
            now,
            signed_entry_price=state.entry_signed_price,
            signed_close_mark=mark,
            reason=reason,
        )
        state.exit_client_id = f"surface-{state.trading_date.replace('-', '')}-exit"
        state.exit_submitted_at = now.isoformat()
        state.exit_reason = reason
        state.phase = "exit_pending"
        state.last_event = "exit submission prepared"
        self.store.save(state)
        order = self._submit_or_reconcile(
            legs=_exit_legs(symbols),
            price=None,
            client_id=state.exit_client_id,
        )
        if order is None:
            state.last_event = "exit outcome unknown; manual reconciliation required"
            self.notifier.send(
                f"CRITICAL: paper butterfly exit {state.exit_client_id} has unknown status."
            )
            return
        state.exit_order_id = str(order["id"])
        state.last_event = "exit submitted"
        self._note(
            "surface_probe_exit_submitted",
            now,
            order_id=state.exit_order_id,
            client_order_id=state.exit_client_id,
            reason=reason,
        )
        self.notifier.send("PAPER surface butterfly noon exit submitted.")

    def _manage_exit(self, state: SurfaceProbeState, now: datetime) -> None:
        order = self._pending_order(state.exit_order_id, state.exit_client_id)
        if order is None:
            state.last_event = "exit order not found; manual reconciliation required"
            return
        state.exit_order_id = str(order["id"])
        status = str(order.get("status", ""))
        filled_qty = Decimal(str(order.get("filled_qty") or "0"))
        if status == "filled" or (status in TERMINAL_FAILURES and filled_qty > 0):
            exit_signed = Decimal(str(order.get("filled_avg_price") or "0"))
            entry_signed = Decimal(state.entry_signed_price or "0")
            gross_pnl = (-exit_signed - entry_signed) * Decimal("100")
            state.phase = "done"
            state.last_event = "exit filled"
            self._note(
                "surface_probe_exit_filled",
                now,
                order_id=state.exit_order_id,
                signed_entry_price=entry_signed,
                signed_exit_price=exit_signed,
                gross_pnl=gross_pnl.quantize(CENT),
                seconds_to_exit=self._seconds_since(state.exit_submitted_at, now),
                reason=state.exit_reason,
            )
            self.notifier.send(f"PAPER butterfly closed; gross P&L ${gross_pnl:.2f}.")
            return
        if status in TERMINAL_FAILURES:
            state.phase = "open"
            state.exit_order_id = None
            state.exit_client_id = None
            state.last_event = f"exit {status}; retry required"
            self._note("surface_probe_exit_failed", now, status=status)
            self.notifier.send(f"PAPER butterfly exit {status}; retrying next tick.")
        elif now.time() >= self.config.hard_close_time:
            self.notifier.send(
                "CRITICAL: PAPER butterfly exit still pending at the 15:00 ET safety boundary."
            )


def candidate_details(candidate: ButterflyCandidate) -> dict[str, Any]:
    return {
        "center": candidate.center,
        "kind": candidate.kind,
        "parity_gap": candidate.parity_gap,
        "call_debit": candidate.call_debit,
        "put_debit": candidate.put_debit,
        "displayed_debit": candidate.displayed_debit,
        "limit_price": candidate.limit_price,
        "symbols": candidate.symbols,
        "quotes": {
            symbol: {
                "bid": quote.bid,
                "ask": quote.ask,
                "timestamp": quote.timestamp,
            }
            for symbol, quote in candidate.quotes.items()
        },
    }


def scan_report(result: ScanResult) -> dict[str, Any]:
    return _json_value(
        {
            "observed_at": result.observed_at,
            "underlying": result.underlying,
            "underlying_at": result.underlying_at,
            "in_entry_window": result.in_entry_window,
            "evaluated_centers": result.candidate_count,
            "reason": result.reason,
            "center_diagnostics": result.diagnostics,
            "candidate": candidate_details(result.candidate) if result.candidate else None,
            "counts_toward_forward_test": bool(
                result.in_entry_window and result.candidate is not None
            ),
        }
    )


def journal_summary(path: Path) -> dict[str, Any]:
    counts: dict[str, int] = {}
    pnls: list[Decimal] = []
    if path.exists():
        with path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                try:
                    item = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise SurfaceProbeError(
                        f"invalid surface journal line {line_number}: {exc}"
                    ) from exc
                event = str(item.get("event", "unknown"))
                counts[event] = counts.get(event, 0) + 1
                if event == "surface_probe_exit_filled":
                    pnls.append(Decimal(str(item["gross_pnl"])))
    return {
        "journal": str(path),
        "events": counts,
        "candidates": counts.get("surface_probe_candidate", 0),
        "submitted": counts.get("surface_probe_submitted", 0),
        "filled": counts.get("surface_probe_filled", 0),
        "unfilled": counts.get("surface_probe_unfilled", 0),
        "exits": counts.get("surface_probe_exit_filled", 0),
        "gross_pnl": str(sum(pnls, Decimal("0")).quantize(CENT)),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SPY surface butterfly paper probe")
    parser.add_argument(
        "command",
        nargs="?",
        choices=("run", "once", "scan", "doctor", "state", "report"),
        default="run",
    )
    return parser


def main() -> int:
    logging.basicConfig(
        level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    args = _parser().parse_args()
    try:
        config = SurfaceProbeConfig.from_env(
            require_credentials=args.command not in {"state", "report"}
        )
        if args.command == "report":
            print(json.dumps(journal_summary(config.journal_path), indent=2))
            return 0
        if args.command == "state":
            state = SurfaceStateStore(config.state_path).load(
                datetime.now(ET).date().isoformat()
            )
            print(json.dumps(asdict(state), indent=2))
            return 0
        client = AlpacaClient(config)  # type: ignore[arg-type]
        if args.command == "scan":
            print(json.dumps(scan_report(scan_surface(client, config)), indent=2))
            return 0
        account = client.account()
        if args.command == "doctor":
            clock = client.clock()
            print(
                json.dumps(
                    {
                        "paper_endpoint": config.trading_base_url,
                        "probe_confirmed": config.probe_confirmed,
                        "account_status": account.get("status"),
                        "trading_blocked": account.get("trading_blocked"),
                        "options_trading_level": account.get("options_trading_level"),
                        "options_buying_power": account.get("options_buying_power"),
                        "market_open": clock.get("is_open"),
                        "next_open": clock.get("next_open"),
                        "next_close": clock.get("next_close"),
                        "options_feed": config.options_feed,
                        "stock_feed": config.stock_feed,
                        "state_path": str(config.state_path),
                        "journal_path": str(config.journal_path),
                        "telegram_configured": bool(
                            config.telegram_token and config.telegram_chat_id
                        ),
                    },
                    indent=2,
                )
            )
            eligible = (
                not account.get("trading_blocked")
                and int(account.get("options_trading_level") or 0) >= 3
            )
            return 0 if eligible else 1
        runner = SurfaceProbeRunner(config, client)
        if args.command == "once":
            runner.tick()
            return 0

        stopping = False

        def stop(_signum: int, _frame: object) -> None:
            nonlocal stopping
            stopping = True

        signal.signal(signal.SIGINT, stop)
        signal.signal(signal.SIGTERM, stop)
        LOG.info("surface butterfly paper probe started")
        while not stopping:
            delay = runner.tick()
            for _ in range(delay):
                if stopping:
                    break
                time_module.sleep(1)
        return 0
    except (AlpacaError, SurfaceProbeError, ValueError, OSError) as exc:
        LOG.error("surface probe error: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
