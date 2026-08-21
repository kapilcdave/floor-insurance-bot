from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from statistics import median
from typing import Any
from zoneinfo import ZoneInfo

import requests

from .config import Config
from .models import Contract, Quote

LOG = logging.getLogger(__name__)


class AlpacaError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def _timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class AlpacaClient:
    """Small REST client covering only the endpoints used by this bot."""

    def __init__(self, config: Config, session: requests.Session | None = None):
        self.config = config
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "APCA-API-KEY-ID": config.api_key,
                "APCA-API-SECRET-KEY": config.api_secret,
                "Accept": "application/json",
                "User-Agent": "floor-insurance-bot/0.1",
            }
        )

    def _request(
        self,
        method: str,
        base: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{base}{path}"
        attempts = 3 if method == "GET" else 1
        for attempt in range(attempts):
            try:
                response = self.session.request(
                    method,
                    url,
                    params=params,
                    json=payload,
                    timeout=self.config.request_timeout_seconds,
                )
                if response.status_code in {429, 500, 502, 503, 504} and attempt + 1 < attempts:
                    time.sleep(2**attempt)
                    continue
                response.raise_for_status()
                return response.json() if response.content else None
            except requests.RequestException as exc:
                if attempt + 1 < attempts:
                    time.sleep(2**attempt)
                    continue
                detail = ""
                if getattr(exc, "response", None) is not None:
                    detail = f": {exc.response.text[:300]}"
                if method == "POST":
                    detail += " (submission outcome may be unknown; reconcile by client_order_id)"
                status = exc.response.status_code if getattr(exc, "response", None) is not None else None
                raise AlpacaError(
                    f"Alpaca {method} {path} failed{detail}", status_code=status
                ) from exc
        raise AssertionError("unreachable")

    def clock(self) -> dict[str, Any]:
        return self._request("GET", self.config.trading_base_url, "/v2/clock")

    def account(self) -> dict[str, Any]:
        return self._request("GET", self.config.trading_base_url, "/v2/account")

    def calendar_day(self, trading_date: str) -> dict[str, Any] | None:
        days = self._request(
            "GET",
            self.config.trading_base_url,
            "/v2/calendar",
            params={"start": trading_date, "end": trading_date},
        )
        return days[0] if days else None

    def daily_closes(
        self, symbol: str, before_date: str, observations: int
    ) -> list[Decimal]:
        """Return adjusted daily closes strictly before ``before_date``.

        The API request may include an in-progress bar for the entry session.
        Filtering by the bar's New York session date here makes the no-lookahead
        boundary explicit rather than dependent on provider response timing.
        """

        if observations < 1:
            raise ValueError("observations must be positive")
        boundary = date.fromisoformat(before_date)
        start = boundary - timedelta(days=max(45, observations * 3))
        params: dict[str, Any] = {
            "timeframe": "1Day",
            "start": start.isoformat(),
            "end": (boundary + timedelta(days=1)).isoformat(),
            "adjustment": "all",
            "feed": self.config.stock_feed,
            "limit": 1000,
            "sort": "asc",
        }
        by_session: dict[date, Decimal] = {}
        while True:
            data = self._request(
                "GET",
                self.config.data_base_url,
                f"/v2/stocks/{symbol}/bars",
                params=params,
            )
            for bar in data.get("bars", []):
                session_date = _timestamp(bar["t"]).astimezone(
                    ZoneInfo("America/New_York")
                ).date()
                if session_date < boundary:
                    by_session[session_date] = Decimal(str(bar["c"]))
            token = data.get("next_page_token")
            if not token:
                break
            params["page_token"] = token
        return [by_session[day] for day in sorted(by_session)][-observations:]

    def latest_underlying_trade(
        self, expiration_date: str | None = None
    ) -> tuple[Decimal, datetime]:
        if self.config.symbol == "XSP":
            if not expiration_date:
                raise AlpacaError("XSP reference price requires an expiration date")
            return self.synthetic_xsp_price(expiration_date)
        data = self._request(
            "GET",
            self.config.data_base_url,
            f"/v2/stocks/{self.config.symbol}/trades/latest",
            params={"feed": self.config.stock_feed},
        )
        trade = data["trade"]
        return Decimal(str(trade["p"])), _timestamp(trade["t"])

    def synthetic_xsp_price(self, expiration_date: str) -> tuple[Decimal, datetime]:
        """Derive a 0DTE XSP reference from executable call/put midpoints.

        Alpaca paper accounts can trade XSP but may not have the index-values
        data grant. At expiry, call-put parity is approximately S = K + C - P.
        Taking the median of the five pairs nearest the money limits the effect
        of one bad indicative quote.
        """

        calls = self._option_chain_quotes(expiration_date, "call")
        puts = self._option_chain_quotes(expiration_date, "put")
        candidates: list[tuple[Decimal, Decimal, datetime]] = []
        for strike in calls.keys() & puts.keys():
            call = calls[strike]
            put = puts[strike]
            call_mid = (call.bid + call.ask) / Decimal("2")
            put_mid = (put.bid + put.ask) / Decimal("2")
            synthetic = strike + call_mid - put_mid
            timestamp = min(call.timestamp, put.timestamp)
            candidates.append((abs(call_mid - put_mid), synthetic, timestamp))
        if not candidates:
            raise AlpacaError("no matched XSP call/put quotes available for parity pricing")
        selected = sorted(candidates, key=lambda item: item[0])[:5]
        price = Decimal(str(median([item[1] for item in selected])))
        observed_at = min(item[2] for item in selected)
        return price.quantize(Decimal("0.001")), observed_at

    def _option_chain_quotes(
        self, expiration_date: str, option_type: str
    ) -> dict[Decimal, Quote]:
        params: dict[str, Any] = {
            "feed": self.config.options_feed,
            "expiration_date": expiration_date,
            "type": option_type,
            "limit": 1000,
        }
        quotes: dict[Decimal, Quote] = {}
        while True:
            data = self._request(
                "GET",
                self.config.data_base_url,
                f"/v1beta1/options/snapshots/{self.config.symbol}",
                params=params,
            )
            for symbol, snapshot in data.get("snapshots", {}).items():
                latest = snapshot.get("latestQuote")
                if not latest:
                    continue
                bid = Decimal(str(latest["bp"]))
                ask = Decimal(str(latest["ap"]))
                if bid < 0 or ask <= 0 or ask < bid:
                    continue
                try:
                    strike = Decimal(symbol[-8:]) / Decimal("1000")
                except Exception:
                    LOG.warning("ignored malformed option symbol %s", symbol)
                    continue
                quote = Quote(bid=bid, ask=ask, timestamp=_timestamp(latest["t"]))
                existing = quotes.get(strike)
                if existing is None or quote.timestamp > existing.timestamp:
                    quotes[strike] = quote
            token = data.get("next_page_token")
            if not token:
                return quotes
            params["page_token"] = token

    def option_contracts(
        self, expiration_date: str, option_type: str
    ) -> list[Contract]:
        if option_type not in {"call", "put"}:
            raise ValueError("option_type must be call or put")
        params: dict[str, Any] = {
            "underlying_symbols": self.config.symbol,
            "expiration_date": expiration_date,
            "type": option_type,
            "status": "active",
            "limit": 1000,
        }
        contracts: list[Contract] = []
        while True:
            data = self._request(
                "GET", self.config.trading_base_url, "/v2/options/contracts", params=params
            )
            for item in data.get("option_contracts", []):
                contracts.append(
                    Contract(
                        symbol=item["symbol"],
                        strike=Decimal(str(item["strike_price"])),
                        expiration_date=item["expiration_date"],
                    )
                )
            token = data.get("next_page_token")
            if not token:
                return contracts
            params["page_token"] = token

    def put_contracts(self, expiration_date: str) -> list[Contract]:
        return self.option_contracts(expiration_date, "put")

    def option_quotes(
        self, symbols: list[str], *, allow_missing: bool = False
    ) -> dict[str, Quote]:
        data = self._request(
            "GET",
            self.config.data_base_url,
            "/v1beta1/options/snapshots",
            params={"symbols": ",".join(symbols), "feed": self.config.options_feed},
        )
        snapshots = data.get("snapshots", data)
        quotes: dict[str, Quote] = {}
        for symbol in symbols:
            latest = snapshots.get(symbol, {}).get("latestQuote")
            if not latest:
                if allow_missing:
                    continue
                raise AlpacaError(f"no option quote returned for {symbol}")
            quotes[symbol] = Quote(
                bid=Decimal(str(latest["bp"])),
                ask=Decimal(str(latest["ap"])),
                timestamp=_timestamp(latest["t"]),
            )
        return quotes

    def submit_spread(
        self,
        *,
        short_symbol: str,
        long_symbol: str,
        quantity: int,
        price: Decimal | None,
        opening: bool,
        client_order_id: str,
    ) -> dict[str, Any]:
        if opening:
            legs = [
                {
                    "symbol": short_symbol,
                    "ratio_qty": "1",
                    "side": "sell",
                    "position_intent": "sell_to_open",
                },
                {
                    "symbol": long_symbol,
                    "ratio_qty": "1",
                    "side": "buy",
                    "position_intent": "buy_to_open",
                },
            ]
        else:
            legs = [
                {
                    "symbol": short_symbol,
                    "ratio_qty": "1",
                    "side": "buy",
                    "position_intent": "buy_to_close",
                },
                {
                    "symbol": long_symbol,
                    "ratio_qty": "1",
                    "side": "sell",
                    "position_intent": "sell_to_close",
                },
            ]
        signed = None if price is None else (-price if opening else price)
        return self.submit_multileg(
            legs=legs,
            quantity=quantity,
            price=signed,
            client_order_id=client_order_id,
        )

    def submit_multileg(
        self,
        *,
        legs: list[dict[str, str]],
        quantity: int,
        price: Decimal | None,
        client_order_id: str,
    ) -> dict[str, Any]:
        """Submit one atomic option strategy.

        Alpaca signs parent prices from the buyer's perspective: positive is a
        net debit and negative is a net credit. ``ratio_qty`` belongs on each
        leg while the parent quantity is the number of complete strategy units.
        """

        if quantity < 1:
            raise ValueError("multi-leg quantity must be positive")
        if not 2 <= len(legs) <= 4:
            raise ValueError("multi-leg orders require two through four legs")
        payload: dict[str, Any] = {
            "order_class": "mleg",
            "qty": str(quantity),
            "type": "limit" if price is not None else "market",
            "time_in_force": "day",
            "client_order_id": client_order_id,
            "legs": legs,
        }
        if price is not None:
            payload["limit_price"] = str(price.quantize(Decimal("0.01")))
        return self._request(
            "POST", self.config.trading_base_url, "/v2/orders", payload=payload
        )

    def order(self, order_id: str) -> dict[str, Any]:
        return self._request(
            "GET", self.config.trading_base_url, f"/v2/orders/{order_id}", params={"nested": "true"}
        )

    def order_by_client_id(self, client_order_id: str) -> dict[str, Any] | None:
        try:
            return self._request(
                "GET",
                self.config.trading_base_url,
                "/v2/orders:by_client_order_id",
                params={"client_order_id": client_order_id, "nested": "true"},
            )
        except AlpacaError as exc:
            if exc.status_code == 404:
                return None
            raise

    def cancel_order(self, order_id: str) -> None:
        self._request("DELETE", self.config.trading_base_url, f"/v2/orders/{order_id}")

    @staticmethod
    def quote_is_fresh(quote: Quote, max_age_seconds: int) -> bool:
        age = datetime.now(timezone.utc) - quote.timestamp.astimezone(timezone.utc)
        return 0 <= age.total_seconds() <= max_age_seconds
