"""Tests for the Stage 0 final-hour settlement upper bound.

These pin the payoff arithmetic, the risk gate, and the two guarantees the
protocol makes about data access: no network path exists, and no session on or
after the sealed boundary is read.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from floor_insurance.final_hour_research import (
    MAX_LOSS_LIMIT,
    ONE_WAY_COST,
    STAGE_ZERO_THRESHOLD,
    STRUCTURES,
    FinalHourError,
    Session,
    center_strike,
    evaluate,
    intrinsic,
    load_sessions,
    option_symbol,
    run_stage_zero,
    structure_price,
    structure_value,
    training_boundary,
    worst_case,
)
from floor_insurance.spread_estimator import Bar, _parse_bar

SHORT_FLY_1 = STRUCTURES[0]
SHORT_FLY_2 = STRUCTURES[1]
SHORT_CONDOR_1 = STRUCTURES[2]
LONG_FLY_1 = STRUCTURES[4]
LONG_FLY_2 = STRUCTURES[5]


def prices(center: Decimal, **overrides: str) -> dict[tuple[str, Decimal], Decimal]:
    """A flat leg-price map covering plus or minus six dollars of strikes."""

    book: dict[tuple[str, Decimal], Decimal] = {}
    for offset in range(-6, 7):
        for kind in ("C", "P"):
            book[(kind, center + Decimal(offset))] = Decimal("0.10")
    for key, value in overrides.items():
        kind = key[0].upper()
        offset = Decimal(key[1:].replace("p", "").replace("m", "-"))
        book[(kind, center + offset)] = Decimal(value)
    return book


def test_one_way_cost_is_four_legs_of_measured_spread_plus_fees():
    assert ONE_WAY_COST == Decimal("2.60")
    assert STAGE_ZERO_THRESHOLD == Decimal("7.80")


def test_center_strike_rounds_exact_halves_upward():
    assert center_strike(_bar(600.49)) == Decimal("600")
    assert center_strike(_bar(600.50)) == Decimal("601")
    assert center_strike(_bar(600.51)) == Decimal("601")


def _bar(open_price: float) -> Bar:
    return _parse_bar(
        {
            "t": "2026-05-22T15:00:00-04:00",
            "o": str(open_price),
            "h": str(open_price),
            "l": str(open_price),
            "c": str(open_price),
            "v": "1",
            "vw": str(open_price),
        }
    )


def test_option_symbol_matches_occ_layout():
    assert option_symbol("2026-05-22", "C", Decimal("601")) == "SPY260522C00601000"
    assert option_symbol("2026-05-22", "P", Decimal("599.5")) == "SPY260522P00599500"


def test_intrinsic_is_zero_below_the_strike():
    assert intrinsic("C", Decimal("600"), Decimal("599")) == Decimal("0")
    assert intrinsic("C", Decimal("600"), Decimal("602.5")) == Decimal("2.5")
    assert intrinsic("P", Decimal("600"), Decimal("601")) == Decimal("0")
    assert intrinsic("P", Decimal("600"), Decimal("598.25")) == Decimal("1.75")


def test_short_fly_keeps_the_whole_credit_when_settlement_pins_the_center():
    center = Decimal("600")
    book = prices(center, C0="0.40", P0="0.35", C1="0.10", Pm1="0.08")
    # Sold body 0.75, bought wings 0.18, so the net credit is 0.57 a share.
    assert structure_price(SHORT_FLY_1, center, book) == Decimal("-0.57")
    trade = evaluate(SHORT_FLY_1, "2026-05-22", center, center, book)
    assert trade is not None
    assert trade.gross == Decimal("57.00")


def test_short_fly_loses_the_full_wing_beyond_it():
    center = Decimal("600")
    book = prices(center, C0="0.40", P0="0.35", C1="0.10", Pm1="0.08")
    trade = evaluate(SHORT_FLY_1, "2026-05-22", center, Decimal("604"), book)
    assert trade is not None
    # Width 1 less the 0.57 credit, a 0.43 loss a share.
    assert trade.gross == Decimal("-43.00")
    assert trade.maximum_loss == Decimal("43.20")


def test_settlement_between_the_strikes_is_linear():
    center = Decimal("600")
    book = prices(center, C0="0.40", P0="0.35", C1="0.10", Pm1="0.08")
    trade = evaluate(SHORT_FLY_1, "2026-05-22", center, Decimal("600.30"), book)
    assert trade is not None
    assert trade.gross == Decimal("27.00")


def test_long_structures_are_the_exact_negation_of_the_short_ones():
    center = Decimal("600")
    book = prices(center, C0="0.40", P0="0.35", C1="0.10", Pm1="0.08")
    for settlement in (Decimal("598"), Decimal("600"), Decimal("600.4"), Decimal("603")):
        short = evaluate(SHORT_FLY_1, "d", center, settlement, book)
        long = evaluate(LONG_FLY_1, "d", center, settlement, book)
        assert short is not None and long is not None
        assert short.gross == -long.gross


def test_long_structure_risk_is_only_its_debit():
    center = Decimal("600")
    book = prices(center, C0="0.40", P0="0.35", C1="0.10", Pm1="0.08")
    trade = evaluate(LONG_FLY_1, "d", center, center, book)
    assert trade is not None
    assert trade.entry_price == Decimal("0.57")
    assert trade.maximum_loss == Decimal("57.20")


def test_risk_gate_rejects_a_structure_over_the_account_limit():
    center = Decimal("600")
    # A two-wide fly sold for almost nothing risks close to $200.
    book = prices(center, C0="0.05", P0="0.05", C2="0.02", Pm2="0.02")
    assert worst_case(SHORT_FLY_2, center, structure_price(SHORT_FLY_2, center, book)) > MAX_LOSS_LIMIT
    assert evaluate(SHORT_FLY_2, "d", center, center, book) is None


def test_condor_pays_nothing_extra_inside_its_shorts():
    center = Decimal("600")
    book = prices(center, C1="0.20", Pm1="0.18", C2="0.06", Pm2="0.05")
    trade = evaluate(SHORT_CONDOR_1, "d", center, center, book)
    assert trade is not None
    assert trade.gross == Decimal("27.00")
    # At the short strike the loss has not started yet.
    at_short = evaluate(SHORT_CONDOR_1, "d", center, center + Decimal("1"), book)
    assert at_short is not None
    assert at_short.gross == Decimal("27.00")


def test_missing_leg_prices_skip_the_session():
    center = Decimal("600")
    book = prices(center)
    del book[("C", center + Decimal("1"))]
    assert evaluate(SHORT_FLY_1, "d", center, center, book) is None


def test_wide_structures_need_more_strikes_than_narrow_ones():
    assert SHORT_FLY_1.strikes_needed == Decimal("1")
    assert SHORT_FLY_2.strikes_needed == Decimal("2")
    assert STRUCTURES[3].strikes_needed == Decimal("3")


def test_grid_holds_six_structures_and_the_inversions_pair_up():
    assert len(STRUCTURES) == 6
    assert {s.key for s in STRUCTURES} == {"S1", "S2", "S3", "S4", "S5", "S6"}
    for short, long in ((SHORT_FLY_1, LONG_FLY_1), (SHORT_FLY_2, LONG_FLY_2)):
        assert [leg.quantity for leg in short.legs] == [-leg.quantity for leg in long.legs]


def test_training_boundary_is_fixed_from_coverage_alone():
    sessions = [
        Session(f"2026-01-{day:02d}", Decimal("600"), Decimal("600"), prices(Decimal("600")))
        for day in range(1, 9)
    ]
    # Eight complete sessions, 75% of them is six.
    assert training_boundary(sessions) == "2026-01-06"


def _write_cache(root: Path, stamps: list[str]) -> Path:
    cache = root / "cache"
    cache.mkdir()
    bars = []
    for stamp in stamps:
        for label, price in (("15:00", 600.0), ("15:01", 600.0), ("15:59", 600.0)):
            bars.append(
                {
                    "t": f"{stamp}T{label}:00-04:00",
                    "o": str(price),
                    "h": str(price),
                    "l": str(price),
                    "c": str(price),
                    "v": "10",
                    "vw": str(price),
                }
            )
        payload = {}
        for offset in range(-3, 4):
            for kind in ("C", "P"):
                symbol = option_symbol(stamp, kind, Decimal("600") + Decimal(offset))
                payload[symbol] = [
                    {
                        "t": f"{stamp}T{label}:00-04:00",
                        "o": "0.20",
                        "h": "0.20",
                        "l": "0.20",
                        "c": "0.20",
                        "v": "10",
                        "vw": "0.20",
                    }
                    for label in ("15:00", "15:01")
                ]
        (cache / f"options-{stamp}.json").write_text(json.dumps(payload), encoding="utf-8")
    (cache / "spy-2024-02-01-2026-08-19-iex.json").write_text(
        json.dumps({"SPY": bars}), encoding="utf-8"
    )
    return cache


def test_sealed_sessions_are_never_loaded(tmp_path: Path):
    cache = _write_cache(tmp_path, ["2026-05-20", "2026-05-21", "2026-05-26", "2026-06-01"])
    sessions, coverage = load_sessions(cache)
    stamps = [session.trading_date for session in sessions]
    assert stamps == ["2026-05-20", "2026-05-21"]
    assert coverage["files"] == 2
    assert all(stamp < "2026-05-26" for stamp in stamps)


def test_stage_zero_reports_every_structure_and_reads_no_validation(tmp_path: Path):
    stamps = [f"2026-05-{day:02d}" for day in range(1, 9)]
    cache = _write_cache(tmp_path, stamps)
    report = run_stage_zero(cache)
    assert report["stage"] == 0
    assert report["threshold"] == "7.80"
    assert report["holdout_start"] == "2026-05-26"
    assert len(report["structures"]) == 6
    assert report["training_boundary"] == "2026-05-06"
    assert report["training_sessions"] == 6


def test_missing_underlying_bars_are_counted_not_silently_dropped(tmp_path: Path):
    cache = _write_cache(tmp_path, ["2026-05-20"])
    payload = json.loads((cache / "spy-2024-02-01-2026-08-19-iex.json").read_text())
    payload["SPY"] = [bar for bar in payload["SPY"] if "15:59" not in bar["t"]]
    (cache / "spy-2024-02-01-2026-08-19-iex.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(FinalHourError):
        load_sessions(cache)


def test_empty_cache_directory_is_an_error(tmp_path: Path):
    with pytest.raises(FinalHourError):
        load_sessions(tmp_path / "absent")


def test_structure_value_ignores_prices_entirely():
    """Settlement reads no option price. That is the point of the design."""

    center = Decimal("600")
    assert structure_value(SHORT_FLY_1, center, center) == Decimal("0")
    assert structure_value(SHORT_FLY_1, center, center + Decimal("5")) == Decimal("-1")
    assert structure_value(SHORT_FLY_1, center, center - Decimal("5")) == Decimal("-1")


def test_module_has_no_network_path():
    import floor_insurance.final_hour_research as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    for forbidden in ("AlpacaClient", "import requests", "requests.", "urlopen", "/v1beta1"):
        assert forbidden not in source, forbidden
