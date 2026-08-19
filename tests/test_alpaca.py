from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

from floor_insurance.alpaca import AlpacaClient


class Response:
    status_code = 200
    content = b"{}"

    def __init__(self, data):
        self.data = data
        self.text = ""

    def raise_for_status(self):
        return None

    def json(self):
        return self.data


class Session:
    def __init__(self):
        self.headers = {}
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return Response({"id": "order-1"})


class ChainSession(Session):
    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        option_type = kwargs["params"]["type"]
        side = "C" if option_type == "call" else "P"
        values = {
            "call": {
                "00769000": ("2.00", "2.20"),
                "00770000": ("1.40", "1.60"),
                "00771000": ("0.90", "1.10"),
            },
            "put": {
                "00769000": ("0.30", "0.50"),
                "00770000": ("0.70", "0.90"),
                "00771000": ("1.20", "1.40"),
            },
        }
        snapshots = {}
        for strike, (bid, ask) in values[option_type].items():
            snapshots[f"XSP260819{side}{strike}"] = {
                "latestQuote": {
                    "bp": bid,
                    "ap": ask,
                    "t": "2026-08-19T15:45:00Z",
                }
            }
        return Response({"snapshots": snapshots, "next_page_token": None})


def test_credit_spread_is_atomic_and_credit_price_is_negative(config):
    session = Session()
    client = AlpacaClient(config, session=session)
    client.submit_spread(
        short_symbol="SPY260818P00535000",
        long_symbol="SPY260818P00534000",
        quantity=1,
        price=Decimal("0.50"),
        opening=True,
        client_order_id="floor-insurance-test",
    )
    payload = session.calls[0][2]["json"]
    assert payload["order_class"] == "mleg"
    assert payload["limit_price"] == "-0.50"
    assert "side" not in payload
    assert [leg["position_intent"] for leg in payload["legs"]] == [
        "sell_to_open",
        "buy_to_open",
    ]


def test_market_exit_omits_limit_price(config):
    session = Session()
    client = AlpacaClient(config, session=session)
    client.submit_spread(
        short_symbol="short",
        long_symbol="long",
        quantity=1,
        price=None,
        opening=False,
        client_order_id="floor-insurance-exit",
    )
    payload = session.calls[0][2]["json"]
    assert payload["type"] == "market"
    assert "limit_price" not in payload


def test_xsp_reference_uses_median_call_put_parity(config):
    session = ChainSession()
    client = AlpacaClient(replace(config, symbol="XSP"), session=session)

    price, observed_at = client.latest_underlying_trade("2026-08-19")

    # Synthetic values are 770.70, 770.70, and 770.70.
    assert price == Decimal("770.700")
    assert observed_at == datetime(2026, 8, 19, 15, 45, tzinfo=timezone.utc)
    assert {call[2]["params"]["type"] for call in session.calls} == {"call", "put"}
