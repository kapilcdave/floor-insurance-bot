from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from floor_insurance.models import Contract, Quote
from floor_insurance.parity_recorder import (
    LegQuote,
    ParityConfig,
    ParityRecorderError,
    append_observation,
    box_bound,
    butterfly_parity,
    fly_buy_cost,
    fly_sell_proceeds,
    journal_summary,
    observe,
    slot_for,
    strike_ladder,
    vertical_bounds,
)

ET = ZoneInfo("America/New_York")
DAY = "2026-08-21"
NOW = datetime(2026, 8, 21, 11, 0, 10, tzinfo=ET)


def config(tmp_path: Path, **changes) -> ParityConfig:
    base = ParityConfig(
        api_key="key",
        api_secret="secret",
        trading_base_url="https://paper-api.alpaca.markets",
        data_base_url="https://data.alpaca.markets",
        request_timeout_seconds=10,
        journal_path=tmp_path / "parity.jsonl",
    )
    return replace(base, **changes)


def leg(kind: str, strike: str, bid: str, ask: str, *, usable: bool = True) -> LegQuote:
    return LegQuote(
        symbol=f"SPY-{kind}-{strike}",
        kind=kind,
        strike=Decimal(strike),
        bid=Decimal(bid),
        ask=Decimal(ask),
        age_seconds=Decimal("1"),
        usable=usable,
        reason="usable" if usable else "quote is stale",
    )


def ladder() -> dict[tuple[str, Decimal], LegQuote]:
    """An arbitrage-free ladder with one-cent quotes, spot 100.50.

    Mids satisfy put/call parity exactly (C - P = S - K at zero rates), so both
    butterflies mid at $0.25 and every box mids at its $1 width. Any edge the
    recorder reports against this ladder is pure bid/ask crossing, which is what
    the negative-control tests assert.
    """

    values = {
        ("C", "99"): ("1.79", "1.81"),
        ("C", "100"): ("1.04", "1.06"),
        ("C", "101"): ("0.54", "0.56"),
        ("P", "99"): ("0.29", "0.31"),
        ("P", "100"): ("0.54", "0.56"),
        ("P", "101"): ("1.04", "1.06"),
    }
    return {
        (kind, Decimal(strike)): leg(kind, strike, bid, ask)
        for (kind, strike), (bid, ask) in values.items()
    }


class FakeClient:
    """Read-only stand-in. It exposes no order methods at all."""

    def __init__(self, quotes: dict[str, Quote], spot: Decimal = Decimal("100.10")):
        self.quotes = quotes
        self.spot = spot
        self.quote_requests: list[list[str]] = []

    def latest_underlying_trade(self, _trading_date: str) -> tuple[Decimal, datetime]:
        return self.spot, NOW

    def option_contracts(self, _trading_date: str, option_type: str) -> list[Contract]:
        kind = "C" if option_type == "call" else "P"
        return [
            Contract(f"SPY-{kind}-{strike}", Decimal(str(strike)), DAY) for strike in range(96, 105)
        ]

    def option_quotes(self, symbols: list[str], *, allow_missing: bool = False):
        self.quote_requests.append(symbols)
        return {symbol: self.quotes[symbol] for symbol in symbols if symbol in self.quotes}


def test_butterfly_parity_is_zero_when_both_flies_are_priced_identically():
    """Identical payoffs priced identically must leave no edge, only the spread."""

    parity = butterfly_parity(Decimal("100"), ladder(), config(Path("/tmp")))
    assert parity is not None
    # Parity holds at the mid, so the print-style gap is zero.
    assert parity.mid_gap == Decimal("0.00")
    # Crossing eight contract units costs eight cents and cannot be riskless.
    assert parity.executable_edge == Decimal("-0.08")
    assert parity.usable is True
    assert parity.contract_units == 8


def test_butterfly_parity_detects_a_genuine_executable_gap():
    """A put fly bid above the call fly ask is a riskless pair at displayed prices."""

    # Make the put butterfly expensive on the bid side: raise the wings' bids.
    rich = ladder()
    rich[("P", Decimal("99"))] = leg("P", "99", "0.80", "0.82")
    rich[("P", Decimal("101"))] = leg("P", "101", "1.50", "1.52")
    parity = butterfly_parity(Decimal("100"), rich, config(Path("/tmp")))
    assert parity is not None
    assert parity.executable_edge > 0
    assert parity.direction == "buy_call_fly_sell_put_fly"


def test_butterfly_parity_marks_stale_legs_unusable_without_dropping_them():
    stale = ladder()
    stale[("C", Decimal("100"))] = leg("C", "100", "0.50", "0.52", usable=False)
    parity = butterfly_parity(Decimal("100"), stale, config(Path("/tmp")))
    assert parity is not None
    assert parity.usable is False


def test_butterfly_parity_returns_none_when_a_strike_is_missing():
    partial = ladder()
    del partial[("P", Decimal("101"))]
    assert butterfly_parity(Decimal("100"), partial, config(Path("/tmp"))) is None


def test_fly_costs_cross_every_leg_in_the_adverse_direction():
    lower, middle, upper = (
        leg("C", "99", "1.00", "1.02"),
        leg("C", "100", "0.50", "0.52"),
        leg("C", "101", "0.20", "0.22"),
    )
    # Buying pays both wings' asks and sells the body at its bid.
    assert fly_buy_cost(lower, middle, upper) == Decimal("0.24")
    # Selling collects both wings' bids and buys the body at its ask.
    assert fly_sell_proceeds(lower, middle, upper) == Decimal("0.16")


def test_vertical_bounds_flag_a_credit_above_the_strike_width():
    rich = ladder()
    # A 100/101 call spread that can be sold for more than $1 is riskless.
    rich[("C", Decimal("100"))] = leg("C", "100", "1.60", "1.62")
    bounds = {bound.kind: bound for bound in vertical_bounds(Decimal("100"), Decimal("101"), rich)}
    assert bounds["C"].executable_edge > 0
    assert bounds["C"].direction == "sell_above_width"
    assert bounds["C"].width == Decimal("1.00")


def test_vertical_bounds_are_negative_on_an_ordinary_ladder():
    for bound in vertical_bounds(Decimal("100"), Decimal("101"), ladder()):
        assert bound.executable_edge < 0


def test_box_settles_at_width_so_an_ordinary_ladder_shows_no_edge():
    box = box_bound(Decimal("99"), Decimal("100"), ladder())
    assert box is not None
    assert box.width == Decimal("1.00")
    assert box.executable_edge < 0


def test_box_detects_being_able_to_buy_below_its_settlement_value():
    cheap = ladder()
    # Drop the long call's ask so the whole box can be bought below its $1 width.
    cheap[("C", Decimal("99"))] = leg("C", "99", "1.68", "1.70")
    box = box_bound(Decimal("99"), Decimal("100"), cheap)
    assert box is not None
    assert box.executable_edge > 0
    assert box.direction == "buy_below_width"


def test_strike_ladder_is_contiguous_and_covers_the_outer_wings():
    strikes = strike_ladder(Decimal("100.10"), config(Path("/tmp")))
    assert strikes[0] == Decimal("96")
    assert strikes[-1] == Decimal("104")
    assert len(strikes) == 9
    assert all(b - a == Decimal("1") for a, b in zip(strikes, strikes[1:], strict=False))


@pytest.mark.parametrize(
    "moment,expected",
    [
        (datetime(2026, 8, 21, 10, 0, 0, tzinfo=ET), "10:00"),
        (datetime(2026, 8, 21, 10, 0, 59, tzinfo=ET), "10:00"),
        (datetime(2026, 8, 21, 10, 1, 1, tzinfo=ET), None),
        (datetime(2026, 8, 21, 14, 0, 30, tzinfo=ET), "14:00"),
        (datetime(2026, 8, 21, 9, 59, 59, tzinfo=ET), None),
    ],
)
def test_slot_for_only_accepts_the_first_minute_of_a_locked_hour(moment, expected):
    assert slot_for(moment, config(Path("/tmp"))) == expected


def test_observe_records_every_family_from_one_snapshot(tmp_path):
    quotes = {
        f"SPY-{kind}-{strike}": Quote(Decimal("0.50"), Decimal("0.52"), NOW)
        for kind in ("C", "P")
        for strike in range(96, 105)
    }
    client = FakeClient(quotes)
    observation = observe(client, config(tmp_path), NOW)  # type: ignore[arg-type]

    assert observation.skipped is False
    assert observation.slot == "11:00"
    assert len(observation.butterflies) == 7
    # Eight adjacent pairs on a nine-strike ladder, two kinds of vertical each.
    assert len(observation.boxes) == 8
    assert len(observation.verticals) == 16
    # One snapshot request for the whole ladder.
    assert len(client.quote_requests) == 1
    assert len(client.quote_requests[0]) == 18


def test_observe_skips_and_explains_a_stale_underlying(tmp_path):
    client = FakeClient({})
    stale = datetime(2026, 8, 21, 11, 30, 0, tzinfo=ET)
    observation = observe(client, config(tmp_path), stale, slot="11:00")  # type: ignore[arg-type]
    assert observation.skipped is True
    assert observation.reason == "underlying trade is stale"
    assert observation.butterflies == ()


def test_journal_summary_refuses_to_call_indicative_data_executable(tmp_path):
    quotes = {
        f"SPY-{kind}-{strike}": Quote(Decimal("0.50"), Decimal("0.52"), NOW)
        for kind in ("C", "P")
        for strike in range(96, 105)
    }
    settings = config(tmp_path, options_feed="indicative")
    observation = observe(FakeClient(quotes), settings, NOW)  # type: ignore[arg-type]
    append_observation(settings.journal_path, observation)

    summary = journal_summary(settings.journal_path)
    assert summary["observations"] == 1
    assert summary["measures_executable_quotes"] is False
    assert "not the consolidated book" in summary["interpretation"]
    assert summary["families"]["butterfly_parity_pair"]["usable"] == 7
    assert summary["families"]["butterfly_parity_pair"]["riskless_at_displayed_quotes"] == 0


def test_journal_summary_reports_opra_as_executable(tmp_path):
    quotes = {
        f"SPY-{kind}-{strike}": Quote(Decimal("0.50"), Decimal("0.52"), NOW)
        for kind in ("C", "P")
        for strike in range(96, 105)
    }
    settings = config(tmp_path, options_feed="opra")
    append_observation(
        settings.journal_path,
        observe(FakeClient(quotes), settings, NOW),  # type: ignore[arg-type]
    )
    summary = journal_summary(settings.journal_path)
    assert summary["measures_executable_quotes"] is True
    assert "not tradable at displayed prices" in summary["interpretation"]


def test_journal_summary_handles_a_missing_journal(tmp_path):
    summary = journal_summary(tmp_path / "absent.jsonl")
    assert summary["observations"] == 0


def test_journal_is_append_only_and_private(tmp_path):
    quotes = {
        f"SPY-{kind}-{strike}": Quote(Decimal("0.50"), Decimal("0.52"), NOW)
        for kind in ("C", "P")
        for strike in range(96, 105)
    }
    settings = config(tmp_path)
    for _ in range(3):
        append_observation(settings.journal_path, observe(FakeClient(quotes), settings, NOW))  # type: ignore[arg-type]
    assert len(settings.journal_path.read_text().strip().splitlines()) == 3
    assert oct(settings.journal_path.stat().st_mode)[-3:] == "600"


def test_config_rejects_an_unknown_feed(tmp_path):
    with pytest.raises(ParityRecorderError):
        config(tmp_path, options_feed="yahoo").validate(require_credentials=False)


def test_config_requires_credentials_when_asked():
    with pytest.raises(ParityRecorderError):
        ParityConfig(
            api_key="",
            api_secret="",
            trading_base_url="x",
            data_base_url="y",
            request_timeout_seconds=10,
        ).validate()


def test_module_exposes_no_order_submission_path():
    """The recorder must remain incapable of sending an order."""

    import floor_insurance.parity_recorder as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    for forbidden in ("submit_spread", "submit_multileg", "cancel_order", "/v2/orders"):
        assert forbidden not in source
