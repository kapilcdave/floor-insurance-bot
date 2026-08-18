from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

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

    def latest_underlying_trade(self) -> tuple[Decimal, datetime]:
        data = self._request(
            "GET",
            self.config.data_base_url,
            f"/v2/stocks/{self.config.symbol}/trades/latest",
            params={"feed": self.config.stock_feed},
        )
        trade = data["trade"]
        return Decimal(str(trade["p"])), _timestamp(trade["t"])

    def put_contracts(self, expiration_date: str) -> list[Contract]:
        params: dict[str, Any] = {
            "underlying_symbols": self.config.symbol,
            "expiration_date": expiration_date,
            "type": "put",
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

    def option_quotes(self, symbols: list[str]) -> dict[str, Quote]:
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
        payload: dict[str, Any] = {
            "order_class": "mleg",
            "qty": str(quantity),
            "type": "limit" if price is not None else "market",
            "time_in_force": "day",
            "client_order_id": client_order_id,
            "legs": legs,
        }
        if price is not None:
            signed = -price if opening else price
            payload["limit_price"] = str(signed.quantize(Decimal("0.01")))
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
