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

