from __future__ import annotations

import math
import os
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_FLOOR, Decimal
from enum import StrEnum
from statistics import stdev
from typing import Any
from zoneinfo import ZoneInfo

from .alpaca import AlpacaClient
from .models import Contract, OptionSnapshot

CENT = Decimal("0.01")
OPTION_MULTIPLIER = Decimal("100")
OCC_SYMBOL = re.compile(r"^([A-Z][A-Z0-9]{0,5})(\d{6})([CP])(\d{8})$")


class WheelSkip(RuntimeError):
    """A safe reason for Wheelhouse not to submit an order."""


class WheelAction(StrEnum):
    COVERED_CALL = "covered_call"
    CASH_SECURED_PUT = "cash_secured_put"


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _decimal(name: str, default: str) -> Decimal:
    try:
        return Decimal(os.getenv(name, default))
    except Exception as exc:
        raise ValueError(f"{name} must be a decimal number") from exc


def _event_dates(name: str = "WHEEL_EVENT_DATES") -> tuple[date, ...]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return ()
    try:
        return tuple(sorted({date.fromisoformat(value.strip()) for value in raw.split(",")}))
    except ValueError as exc:
        raise ValueError(f"{name} must be comma-separated YYYY-MM-DD dates") from exc


@dataclass(frozen=True)
class WheelConfig:
    api_key: str
    api_secret: str
    trading_base_url: str
    data_base_url: str
    request_timeout_seconds: int
    stock_feed: str
    options_feed: str
    symbol: str
    paper: bool
    dry_run: bool
    live_confirmed: bool
    min_dte: int
    max_dte: int
    target_dte: int
    target_delta: Decimal
    cautious_delta: Decimal
    iv_rv_ratio: Decimal
    realized_vol_window: int
    event_dates: tuple[date, ...]
    min_premium: Decimal
    min_annualized_yield: Decimal
    max_quote_width_fraction: Decimal
    max_contracts: int
    max_quote_age_seconds: int
    poll_seconds: int
    timezone: str = "America/New_York"

    @classmethod
    def from_env(cls) -> WheelConfig:
        paper = _bool("ALPACA_PAPER", True)
        trading_default = (
            "https://paper-api.alpaca.markets"
            if paper
            else "https://api.alpaca.markets"
        )
        config = cls(
            api_key=os.getenv("ALPACA_API_KEY", "").strip(),
            api_secret=os.getenv("ALPACA_API_SECRET", "").strip(),
            trading_base_url=os.getenv("ALPACA_TRADING_URL", trading_default).rstrip("/"),
            data_base_url=os.getenv(
                "ALPACA_DATA_URL", "https://data.alpaca.markets"
            ).rstrip("/"),
            request_timeout_seconds=_int("REQUEST_TIMEOUT_SECONDS", 10),
            stock_feed=os.getenv("STOCK_FEED", "iex" if paper else "sip").lower(),
            options_feed=os.getenv("OPTIONS_FEED", "indicative").lower(),
            symbol=os.getenv("WHEEL_SYMBOL", "").strip().upper(),
            paper=paper,
            dry_run=_bool("DRY_RUN", True),
            live_confirmed=_bool("WHEEL_LIVE_CONFIRMED", False),
            min_dte=_int("WHEEL_MIN_DTE", 28),
            max_dte=_int("WHEEL_MAX_DTE", 45),
            target_dte=_int("WHEEL_TARGET_DTE", 35),
            target_delta=_decimal("WHEEL_TARGET_DELTA", "0.25"),
            cautious_delta=_decimal("WHEEL_CAUTIOUS_DELTA", "0.15"),
            iv_rv_ratio=_decimal("WHEEL_IV_RV_RATIO", "1.5"),
            realized_vol_window=_int("WHEEL_REALIZED_VOL_WINDOW", 20),
            event_dates=_event_dates(),
            min_premium=_decimal("WHEEL_MIN_PREMIUM", "0.10"),
            min_annualized_yield=_decimal("WHEEL_MIN_ANNUALIZED_YIELD", "0.08"),
            max_quote_width_fraction=_decimal("WHEEL_MAX_QUOTE_WIDTH_FRACTION", "0.25"),
            max_contracts=_int("WHEEL_MAX_CONTRACTS", 1),
            max_quote_age_seconds=_int(
                "MAX_QUOTE_AGE_SECONDS", 90 if paper else 30
            ),
            poll_seconds=_int("WHEEL_POLL_SECONDS", 900),
        )
        config.validate()
        return config

    def validate(self) -> None:
        if not self.api_key or not self.api_secret:
            raise ValueError("ALPACA_API_KEY and ALPACA_API_SECRET are required")
        if not re.fullmatch(r"[A-Z][A-Z0-9.]{0,9}", self.symbol):
            raise ValueError("WHEEL_SYMBOL is required and must be a stock ticker")
        if self.stock_feed not in {"iex", "sip"}:
            raise ValueError("STOCK_FEED must be iex or sip")
        if self.options_feed not in {"indicative", "opra"}:
            raise ValueError("OPTIONS_FEED must be indicative or opra")
        if not self.paper:
            if not self.live_confirmed:
                raise ValueError(
                    "live wheel blocked: set WHEEL_LIVE_CONFIRMED=true as a second opt-in"
                )
            if self.stock_feed != "sip" or self.options_feed != "opra":
                raise ValueError("live wheel requires STOCK_FEED=sip and OPTIONS_FEED=opra")
        # The floor is 3 so a weekly wheel can reach the nearest Friday. Below 3 a
        # 25-delta option is mostly gamma and the strike grid is too coarse to
        # express the target delta at all, so 0-2 DTE stays out of reach.
        if not (3 <= self.min_dte <= self.target_dte <= self.max_dte <= 90):
            raise ValueError("wheel DTE settings must order 3 <= min <= target <= max <= 90")
        if not (Decimal("0") < self.cautious_delta <= self.target_delta < Decimal("0.5")):
            raise ValueError("wheel deltas must order 0 < cautious <= target < 0.5")
        if self.iv_rv_ratio <= 1:
            raise ValueError("WHEEL_IV_RV_RATIO must be greater than one")
        if self.realized_vol_window < 10:
            raise ValueError("WHEEL_REALIZED_VOL_WINDOW must be at least 10")
        if self.min_premium <= 0 or self.min_annualized_yield < 0:
            raise ValueError("wheel premium must be positive and yield cannot be negative")
        if not (Decimal("0") < self.max_quote_width_fraction <= Decimal("1")):
            raise ValueError("WHEEL_MAX_QUOTE_WIDTH_FRACTION must be in (0, 1]")
        if min(
            self.max_contracts,
            self.max_quote_age_seconds,
            self.request_timeout_seconds,
            self.poll_seconds,
        ) < 1:
            raise ValueError("wheel caps, timeouts, and polling must be positive")


@dataclass(frozen=True)
class WheelPlan:
    action: WheelAction
    symbol: str
    option_symbol: str
    expiration_date: str
    strike: Decimal
    quantity: int
    limit_price: Decimal
    spot: Decimal
    delta: Decimal
    implied_volatility: Decimal
    realized_volatility: Decimal
    iv_rv_ratio: Decimal
    annualized_yield: Decimal
    cautious: bool
    caution_reasons: tuple[str, ...]
    dry_run: bool
    order_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["action"] = self.action.value
        for key, value in tuple(data.items()):
            if isinstance(value, Decimal):
                data[key] = str(value)
        return data


def option_root(symbol: str) -> str | None:
    match = OCC_SYMBOL.fullmatch(symbol)
    return match.group(1) if match else None


def option_matches_underlying(option_symbol: str, underlying: str) -> bool:
    root = option_root(option_symbol)
    if root is None:
        return False
    suffix = root.removeprefix(underlying)
    return root == underlying or bool(suffix and suffix.isdigit())


def realized_volatility(closes: list[Decimal]) -> Decimal:
    if len(closes) < 3 or any(value <= 0 for value in closes):
        raise WheelSkip("not enough valid daily closes to estimate realized volatility")
    returns = [
        math.log(float(right / left))
        for left, right in zip(closes, closes[1:], strict=False)
    ]
    if len(set(returns)) == 1:
        return Decimal("0")
    return Decimal(str(stdev(returns) * math.sqrt(252)))


def choose_expiration(
    contracts: list[Contract], today: date, target_dte: int
) -> str:
    expirations = sorted({date.fromisoformat(contract.expiration_date) for contract in contracts})
    if not expirations:
        raise WheelSkip("no option expiration is available in the configured DTE window")
    selected = min(expirations, key=lambda expiry: (abs((expiry - today).days - target_dte), expiry))
    return selected.isoformat()


def quote_is_fresh(snapshot: OptionSnapshot, now: datetime, max_age_seconds: int) -> bool:
    age = now.astimezone(timezone.utc) - snapshot.quote.timestamp.astimezone(timezone.utc)
    return 0 <= age.total_seconds() <= max_age_seconds


def annualized_premium_yield(
    action: WheelAction,
    premium: Decimal,
    strike: Decimal,
    spot: Decimal,
    dte: int,
) -> Decimal:
    capital = strike if action == WheelAction.CASH_SECURED_PUT else spot
    return premium / capital * Decimal(365) / Decimal(dte)


def select_contract(
    snapshots: list[OptionSnapshot],
    *,
    action: WheelAction,
    spot: Decimal,
    cost_basis: Decimal | None,
    target_delta: Decimal,
    min_premium: Decimal,
    min_annualized_yield: Decimal,
    max_quote_width_fraction: Decimal,
    max_quote_age_seconds: int,
    now: datetime,
    buying_power: Decimal,
) -> tuple[OptionSnapshot, Decimal]:
    candidates: list[tuple[Decimal, Decimal, OptionSnapshot]] = []
    for snapshot in snapshots:
        if not quote_is_fresh(snapshot, now, max_quote_age_seconds):
            continue
        bid = snapshot.quote.bid.quantize(CENT, rounding=ROUND_FLOOR)
        midpoint = (snapshot.quote.bid + snapshot.quote.ask) / Decimal("2")
        if bid < min_premium or midpoint <= 0:
            continue
        if (snapshot.quote.ask - snapshot.quote.bid) / midpoint > max_quote_width_fraction:
            continue
        absolute_delta = abs(snapshot.delta)
        if absolute_delta <= 0 or absolute_delta > target_delta:
            continue
        if action == WheelAction.COVERED_CALL:
            floor = max(spot, cost_basis or spot)
            if snapshot.option_type != "call" or snapshot.strike < floor:
                continue
        else:
            if snapshot.option_type != "put" or snapshot.strike > spot:
                continue
            if snapshot.strike * OPTION_MULTIPLIER > buying_power:
                continue
        expiry = date.fromisoformat(snapshot.expiration_date)
        dte = (expiry - now.date()).days
        if dte < 1:
            continue
        premium_yield = annualized_premium_yield(
            action, bid, snapshot.strike, spot, dte
        )
        if premium_yield < min_annualized_yield:
            continue
        candidates.append((target_delta - absolute_delta, -premium_yield, snapshot))
    if not candidates:
        raise WheelSkip(
            "no liquid, fresh, affordable contract meets the delta, premium, and yield rules"
        )
    selected = min(candidates, key=lambda item: (item[0], item[1], item[2].strike))[2]
    dte = (date.fromisoformat(selected.expiration_date) - now.date()).days
    premium_yield = annualized_premium_yield(
        action,
        selected.quote.bid.quantize(CENT, rounding=ROUND_FLOOR),
        selected.strike,
        spot,
        dte,
    )
    return selected, premium_yield


class WheelEngine:
    """A broker-reconciled wheel: shares sell calls; no shares sell secured puts."""

    def __init__(self, config: WheelConfig, alpaca: AlpacaClient):
        self.config = config
        self.alpaca = alpaca
        self.tz = ZoneInfo(config.timezone)

    def doctor(self) -> dict[str, Any]:
        account = self.alpaca.account()
        asset = self.alpaca.asset(self.config.symbol)
        return {
            "name": "Wheelhouse",
            "symbol": self.config.symbol,
            "paper": self.config.paper,
            "dry_run": self.config.dry_run,
            "account_status": account.get("status"),
            "trading_blocked": bool(account.get("trading_blocked")),
            "options_trading_level": int(account.get("options_trading_level") or 0),
            "options_buying_power": str(account.get("options_buying_power") or "0"),
            "asset_tradable": bool(asset.get("tradable")),
            "asset_has_options": bool(
                asset.get("has_options")
                or {"has_options", "options_enabled"} & set(asset.get("attributes") or [])
            ),
            "stock_feed": self.config.stock_feed,
            "options_feed": self.config.options_feed,
            "dte_window": [self.config.min_dte, self.config.max_dte],
            "normal_delta": str(self.config.target_delta),
            "cautious_delta": str(self.config.cautious_delta),
            "known_event_dates": [value.isoformat() for value in self.config.event_dates],
        }

    def tick(self, now: datetime | None = None) -> WheelPlan:
        now = (now or datetime.now(self.tz)).astimezone(self.tz)
        clock = self.alpaca.clock()
        if not clock.get("is_open"):
            raise WheelSkip("market is closed")

        account = self.alpaca.account()
        if account.get("trading_blocked"):
            raise WheelSkip("Alpaca account is trading-blocked")
        if int(account.get("options_trading_level") or 0) < 1:
            raise WheelSkip("options trading level 1 or higher is required")

        positions = self.alpaca.positions()
        stock_position, option_positions = self._positions_for_symbol(positions)
        if option_positions:
            symbols = ", ".join(sorted(str(position["symbol"]) for position in option_positions))
            raise WheelSkip(f"existing {self.config.symbol} option position found: {symbols}")
        if self._has_open_order(self.alpaca.open_orders()):
            raise WheelSkip(f"an open {self.config.symbol} option order already exists")

        shares = Decimal(str(stock_position.get("qty", "0"))) if stock_position else Decimal("0")
        available_shares = (
            Decimal(str(stock_position.get("qty_available", shares)))
            if stock_position
            else Decimal("0")
        )
        if shares < 0:
            raise WheelSkip("short stock is not compatible with the wheel")
        if shares >= OPTION_MULTIPLIER:
            action = WheelAction.COVERED_CALL
            option_type = "call"
            cost_basis = Decimal(str(stock_position.get("avg_entry_price") or "0"))
        elif shares == 0:
            action = WheelAction.CASH_SECURED_PUT
            option_type = "put"
            cost_basis = None
        else:
            raise WheelSkip(
                f"{shares} shares is an incomplete lot; Wheelhouse will not add assignment risk"
            )

        client_order_id = (
            f"wheelhouse-{self.config.symbol.lower()}-{now.date().isoformat()}-{option_type}"
        )
        previous = self.alpaca.order_by_client_id(client_order_id)
        if previous is not None:
            raise WheelSkip(
                f"today's {action.value} was already attempted (status={previous.get('status')})"
            )

        spot, observed_at = self.alpaca.latest_stock_trade(self.config.symbol)
        age = now.astimezone(timezone.utc) - observed_at.astimezone(timezone.utc)
        if not 0 <= age.total_seconds() <= self.config.max_quote_age_seconds:
            raise WheelSkip(f"{self.config.symbol} stock price is stale")

        earliest = now.date() + timedelta(days=self.config.min_dte)
        latest = now.date() + timedelta(days=self.config.max_dte)
        contracts = self.alpaca.option_contracts_between(
            self.config.symbol,
            option_type,
            earliest.isoformat(),
            latest.isoformat(),
        )
        expiration = choose_expiration(contracts, now.date(), self.config.target_dte)
        snapshots = self.alpaca.option_chain_snapshots(
            self.config.symbol, expiration, option_type
        )
        if not snapshots:
            raise WheelSkip("option chain has no quoted contracts with Greeks and IV")

        closes = self.alpaca.daily_closes(
            self.config.symbol,
            now.date().isoformat(),
            self.config.realized_vol_window + 1,
        )
        realized = realized_volatility(closes)
        atm = min(snapshots, key=lambda snapshot: abs(snapshot.strike - spot))
        iv_ratio = (
            atm.implied_volatility / realized if realized > 0 else Decimal("Infinity")
        )
        expiry_date = date.fromisoformat(expiration)
        crossed_events = tuple(
            value.isoformat()
            for value in self.config.event_dates
            if now.date() <= value <= expiry_date
        )
        caution_reasons: list[str] = []
        if crossed_events:
            caution_reasons.append("known event before expiration: " + ", ".join(crossed_events))
        if iv_ratio >= self.config.iv_rv_ratio:
            caution_reasons.append(
                f"ATM IV/RV {iv_ratio:.2f} >= {self.config.iv_rv_ratio:.2f}"
            )
        target_delta = (
            self.config.cautious_delta if caution_reasons else self.config.target_delta
        )
        buying_power = Decimal(str(account.get("options_buying_power") or "0"))
        selected, premium_yield = select_contract(
            snapshots,
            action=action,
            spot=spot,
            cost_basis=cost_basis,
            target_delta=target_delta,
            min_premium=self.config.min_premium,
            min_annualized_yield=self.config.min_annualized_yield,
            max_quote_width_fraction=self.config.max_quote_width_fraction,
            max_quote_age_seconds=self.config.max_quote_age_seconds,
            now=now,
            buying_power=buying_power,
        )
        if action == WheelAction.COVERED_CALL:
            quantity = min(
                int(available_shares // OPTION_MULTIPLIER), self.config.max_contracts
            )
        else:
            quantity = min(
                int(buying_power // (selected.strike * OPTION_MULTIPLIER)),
                self.config.max_contracts,
            )
        if quantity < 1:
            raise WheelSkip("insufficient covered shares or cash collateral for one contract")

        limit_price = selected.quote.bid.quantize(CENT, rounding=ROUND_FLOOR)
        order_id = None
        if not self.config.dry_run:
            order = self.alpaca.submit_short_option(
                symbol=selected.symbol,
                quantity=quantity,
                limit_price=limit_price,
                client_order_id=client_order_id,
            )
            order_id = str(order.get("id")) if order.get("id") else None
        return WheelPlan(
            action=action,
            symbol=self.config.symbol,
            option_symbol=selected.symbol,
            expiration_date=expiration,
            strike=selected.strike,
            quantity=quantity,
            limit_price=limit_price,
            spot=spot,
            delta=selected.delta,
            implied_volatility=selected.implied_volatility,
            realized_volatility=realized,
            iv_rv_ratio=iv_ratio,
            annualized_yield=premium_yield,
            cautious=bool(caution_reasons),
            caution_reasons=tuple(caution_reasons),
            dry_run=self.config.dry_run,
            order_id=order_id,
        )

    def _positions_for_symbol(
        self, positions: list[dict[str, Any]]
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        stock = next(
            (
                position
                for position in positions
                if position.get("asset_class") == "us_equity"
                and position.get("symbol") == self.config.symbol
            ),
            None,
        )
        options = [
            position
            for position in positions
            if position.get("asset_class") == "us_option"
            and option_matches_underlying(
                str(position.get("symbol", "")), self.config.symbol
            )
        ]
        return stock, options

    def _has_open_order(self, orders: list[dict[str, Any]]) -> bool:
        for order in orders:
            client_id = str(order.get("client_order_id", ""))
            if client_id.startswith(f"wheelhouse-{self.config.symbol.lower()}-"):
                return True
            if option_matches_underlying(
                str(order.get("symbol", "")), self.config.symbol
            ):
                return True
        return False
