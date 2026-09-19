import math
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from floor_insurance.smile_residual_research import (
    ENTRY_DELAY_LADDER,
    HOLDOUT_START,
    PERSISTENCE_LADDER,
    SessionPanel,
    SmileSettings,
    Trade,
    _delay_reading,
    _dynamics_reading,
    _verdict,
    autocorrelation,
    build_panel,
    butterfly_values,
    chronological_splits,
    fit_log_parabola,
    grade,
    half_life,
    load_panels,
    residual_series,
    simulate_session,
)
from floor_insurance.spread_estimator import Bar

ET = ZoneInfo("America/New_York")


def minute_label(index: int) -> str:
    return (datetime(2025, 6, 2, 10, 0, tzinfo=ET) + timedelta(minutes=index)).strftime("%H:%M")


def panel(
    series: dict[tuple[str, int], dict[str, str]],
    *,
    trading_date: str = "2025-06-02",
    atm: str = "500",
) -> SessionPanel:
    prices = {
        key: {label: Decimal(value) for label, value in points.items()}
        for key, points in series.items()
    }
    minutes = sorted({label for points in prices.values() for label in points})
    return SessionPanel(trading_date, Decimal(atm), prices, tuple(minutes))


# A price curve whose *second* difference in strike is Gaussian, built by
# integrating the density twice rather than by writing down a plausible-looking
# price. That matters: `fit_log_parabola` models `log B` as a parabola, so a test
# smile has to be Gaussian in the butterfly, not in the price.
SMILE_SCALE = 0.30
SMILE_WIDTH = 3.0


def smile_prices(*, dislocate: int | None = None, delta: float = 0.0) -> dict[int, float]:
    prices = {-7: 20.0, -6: 20.0 - 2.4}
    for offset in range(-6, 7):
        density = SMILE_SCALE * math.exp(-(offset**2) / (2 * SMILE_WIDTH**2))
        prices[offset + 1] = density + 2 * prices[offset] - prices[offset - 1]
    if dislocate is not None:
        prices[dislocate] -= delta
    return prices


def smooth_smile(
    label: str, *, dislocate: int | None = None, delta: float = 0.0
) -> dict[tuple[str, int], dict[str, str]]:
    """One minute of a Gaussian call smile, optionally with one strike mispriced."""

    return {
        ("C", offset): {label: f"{price:.6f}"}
        for offset, price in smile_prices(dislocate=dislocate, delta=delta).items()
    }


# --------------------------------------------------------------------------
# Panel construction
# --------------------------------------------------------------------------


def test_build_panel_never_forward_fills_a_missing_minute():
    """A carried-forward print would manufacture the dislocation being hunted."""

    underlying = [Bar(datetime(2025, 6, 2, 10, 0, tzinfo=ET), 500.0, 500.0, 500.0, 500.2, 1e6)]
    payload = {
        "SPY250602C00500000": [
            {"t": "2025-06-02T10:00:00-04:00", "o": "1", "h": "1", "l": "1", "c": "1.00", "v": "10"},
            {"t": "2025-06-02T10:05:00-04:00", "o": "1", "h": "1", "l": "1", "c": "1.20", "v": "10"},
        ]
    }
    built = build_panel("2025-06-02", payload, underlying, SmileSettings())
    assert built is not None
    assert set(built.prices[("C", 0)]) == {"10:00", "10:05"}
    assert built.minutes == ("10:00", "10:05")


def test_build_panel_drops_zero_volume_bars():
    underlying = [Bar(datetime(2025, 6, 2, 10, 0, tzinfo=ET), 500.0, 500.0, 500.0, 500.0, 1e6)]
    payload = {
        "SPY250602C00500000": [
            {"t": "2025-06-02T10:00:00-04:00", "o": "1", "h": "1", "l": "1", "c": "1.00", "v": "0"}
        ]
    }
    assert build_panel("2025-06-02", payload, underlying, SmileSettings()) is None


def test_build_panel_needs_the_underlying_to_locate_the_money():
    assert build_panel("2025-06-02", {}, [], SmileSettings()) is None


# --------------------------------------------------------------------------
# The butterfly and the fit
# --------------------------------------------------------------------------


def test_butterfly_requires_all_three_legs_in_the_same_minute():
    """This is the whole defence against nonsynchronous prints in the fit."""

    built = panel(
        {
            ("C", -1): {"10:00": "1.30", "10:01": "1.30"},
            ("C", 0): {"10:00": "1.00", "10:01": "1.00"},
            ("C", 1): {"10:01": "0.80"},
        }
    )
    assert butterfly_values(built, "C", "10:00") == {}
    assert butterfly_values(built, "C", "10:01") == {0: Decimal("0.10")}


def test_butterfly_reports_a_negative_value_rather_than_hiding_it():
    """A negative butterfly is a no-arbitrage violation; it must be visible."""

    built = panel({("C", -1): {"10:00": "1.00"}, ("C", 0): {"10:00": "1.40"}, ("C", 1): {"10:00": "1.00"}})
    assert butterfly_values(built, "C", "10:00") == {0: Decimal("-0.80")}


def test_the_fit_leaves_almost_no_residual_on_a_gaussian_smile():
    """log B is a parabola for a Gaussian density, so residuals must vanish."""

    built = panel(smooth_smile("10:00"))
    residuals = fit_log_parabola(butterfly_values(built, "C", "10:00"))
    assert residuals is not None
    assert max(abs(value) for value in residuals.values()) < Decimal("0.05")


def test_one_mispriced_strike_produces_a_large_signed_residual():
    """A cheap body makes its own butterfly rich and its neighbours cheap.

    Both the sign and the concentration matter: the dislocated strike must carry
    a larger residual than the two strikes its own misprice also distorts, or the
    screen would trade the wrong butterfly.
    """

    built = panel(smooth_smile("10:00", dislocate=0, delta=0.06))
    residuals = fit_log_parabola(butterfly_values(built, "C", "10:00"))
    assert residuals is not None
    assert residuals[0] > Decimal("0.30")
    assert residuals[-1] < 0 and residuals[1] < 0
    assert abs(residuals[0]) > abs(residuals[-1])


def test_the_fit_refuses_a_cross_section_with_too_few_positive_butterflies():
    assert fit_log_parabola({0: Decimal("0.10"), 1: Decimal("-0.10")}) is None


def test_residual_series_reports_violations_and_the_total_examined():
    """The violation count is only interpretable next to the denominator."""

    # A body priced far above its neighbours drives its own butterfly negative,
    # which is the no-arbitrage violation nonsynchronous prints produce.
    built = panel(smooth_smile("10:00", dislocate=0, delta=-1.00))
    settings = SmileSettings(minimum_offsets=5)
    values = butterfly_values(built, "C", "10:00")
    by_minute, violations, examined = residual_series(built, "C", settings)

    assert examined == len(values)
    assert violations == sum(1 for value in values.values() if value <= 0)
    assert violations > 0
    assert set(by_minute) == {"10:00"}


# --------------------------------------------------------------------------
# Residual dynamics
# --------------------------------------------------------------------------


def test_autocorrelation_of_a_constant_series_is_undefined():
    assert autocorrelation([1.0] * 30, 1) is None


def test_autocorrelation_recovers_a_strong_ar1():
    series = [math.cos(index / 10.0) for index in range(300)]
    rho = autocorrelation(series, 1)
    assert rho is not None and rho > 0.9


def test_half_life_is_undefined_for_a_non_persistent_residual():
    assert half_life(0.0) is None
    assert half_life(-0.3) is None
    assert half_life(1.0) is None


def test_half_life_matches_the_ar1_formula():
    value = half_life(0.5)
    assert value is not None
    assert abs(value - 1.0) < 1e-9


def test_a_sub_minute_half_life_is_called_negligible_not_promising():
    """The first version of this screen described rho = 0.098 as encouraging.

    A lag-1 autocorrelation of about 0.1 is a third of a minute of memory, which
    is gone before an order reaches the exchange. Reading that as "consistent
    with a dislocation that closes" is what let a print artifact look like an
    edge, so the wording is pinned by a test.
    """

    reading = _dynamics_reading({1: [0.098], 3: [0.08]}, [0.34])
    assert "negligible persistence" in reading
    assert "no dislocation here to trade" in reading


def test_a_genuinely_persistent_residual_reads_differently():
    reading = _dynamics_reading({1: [0.55], 3: [0.3]}, [8.0])
    assert "negligible" not in reading
    assert "consistent with a dislocation that closes" in reading


# --------------------------------------------------------------------------
# Persistence and the entry delay
# --------------------------------------------------------------------------


def dislocated_session(minutes: int = 60, *, delta: float = 0.06) -> SessionPanel:
    """A session where offset 0 stays dislocated for the whole morning.

    This is the chronically stale leg that a persistence filter cannot catch: the
    misprice is there every single minute, so it passes any persistence rule while
    the price it offers is never actually available.
    """

    series: dict[tuple[str, int], dict[str, str]] = {}
    for index in range(minutes):
        label = minute_label(index)
        for key, points in smooth_smile(label, dislocate=0, delta=delta).items():
            series.setdefault(key, {}).update(points)
    return panel(series)


def test_a_dislocation_lasting_one_minute_cannot_pass_a_three_minute_filter():
    series: dict[tuple[str, int], dict[str, str]] = {}
    for index in range(60):
        label = minute_label(index)
        delta = 0.06 if index == 10 else 0.0
        for key, points in smooth_smile(label, dislocate=0, delta=delta).items():
            series.setdefault(key, {}).update(points)

    settings = SmileSettings(minimum_offsets=5, persist_minutes=3, hold_minutes=5)
    assert simulate_session(panel(series), settings) == []


def test_a_persistent_dislocation_is_traded_and_the_direction_follows_the_sign():
    """A rich butterfly must be sold, not bought."""

    settings = SmileSettings(minimum_offsets=5, persist_minutes=3, hold_minutes=5)
    trades = simulate_session(dislocated_session(), settings)
    assert len(trades) == 1
    assert trades[0].offset == 0
    assert trades[0].residual > 0
    assert trades[0].direction == "sell"


def test_the_entry_delay_moves_the_fill_off_the_signal_minute():
    """The control has to actually change which print is transacted at."""

    base = SmileSettings(minimum_offsets=5, persist_minutes=3, hold_minutes=5)
    immediate = simulate_session(dislocated_session(), base)[0]
    delayed = simulate_session(
        dislocated_session(), SmileSettings(minimum_offsets=5, persist_minutes=3, hold_minutes=5, entry_delay_minutes=2)
    )[0]

    assert immediate.entry_minute == immediate.minute
    assert delayed.minute == immediate.minute
    assert delayed.entry_minute != delayed.minute
    hour, minute = (int(part) for part in delayed.entry_minute.split(":"))
    signal_hour, signal_minute = (int(part) for part in delayed.minute.split(":"))
    assert (hour * 60 + minute) - (signal_hour * 60 + signal_minute) == 2


def test_the_delay_turns_a_stale_print_from_a_winner_into_a_loss():
    """The finding of this whole screen, reproduced on data built to order.

    Offset 0 shows a rich butterfly for exactly three minutes and then reverts.
    That clears a three-minute persistence filter, and filling at the third
    minute's own print books the full reversion as profit. Filling one minute
    later transacts at the reverted price instead, and all that is left is cost.
    """

    series: dict[tuple[str, int], dict[str, str]] = {}
    for index in range(60):
        label = minute_label(index)
        delta = 0.06 if index < 3 else 0.0
        for key, points in smooth_smile(label, dislocate=0, delta=delta).items():
            series.setdefault(key, {}).update(points)
    session = panel(series)

    base = dict(minimum_offsets=5, persist_minutes=3, hold_minutes=5)
    immediate = simulate_session(session, SmileSettings(**base))
    delayed = simulate_session(session, SmileSettings(**base, entry_delay_minutes=1))

    assert len(immediate) == 1 and len(delayed) == 1
    assert immediate[0].direction == "sell" and immediate[0].pnl > Decimal("10")
    assert delayed[0].entry_value < immediate[0].entry_value
    assert delayed[0].pnl == -delayed[0].cost


def test_a_delay_running_past_the_close_produces_no_trade():
    settings = SmileSettings(
        minimum_offsets=5, persist_minutes=3, hold_minutes=5, entry_delay_minutes=400
    )
    assert simulate_session(dislocated_session(), settings) == []


def test_entries_are_capped_per_session():
    settings = SmileSettings(minimum_offsets=5, persist_minutes=3, hold_minutes=5)
    assert len(simulate_session(dislocated_session(minutes=300), settings)) == 1


def test_entries_outside_the_window_are_skipped():
    settings = SmileSettings(
        minimum_offsets=5, persist_minutes=3, hold_minutes=5, entry_start=time(14, 0)
    )
    assert simulate_session(dislocated_session(), settings) == []


def test_the_cost_charged_is_eight_units_of_half_spread_plus_fees():
    """Four legs, entry and exit: the fill cost cannot be quietly understated."""

    settings = SmileSettings(minimum_offsets=5, persist_minutes=3, hold_minutes=5)
    trade = simulate_session(dislocated_session(), settings)[0]
    expected = settings.cost_per_unit * Decimal("8") + settings.fees_per_butterfly
    assert trade.cost == expected.quantize(Decimal("0.01"))


# --------------------------------------------------------------------------
# Grading and the verdict
# --------------------------------------------------------------------------


def trade(pnl: str) -> Trade:
    return Trade(
        trading_date="2025-06-02",
        minute="10:30",
        entry_minute="10:30",
        kind="C",
        offset=0,
        center=Decimal("500"),
        residual=Decimal("0.4"),
        direction="sell",
        entry_value=Decimal("0.10"),
        exit_value=Decimal("0.08"),
        cost=Decimal("0.25"),
        pnl=Decimal(pnl),
    )


def test_grade_on_no_trades_reports_zero_rather_than_dividing():
    assert grade([]) == {"trades": 0}


def test_grade_computes_profit_factor_and_drawdown():
    graded = grade([trade("10"), trade("-4"), trade("-6"), trade("8")])
    assert graded["profit_factor"] == "1.8000"
    assert graded["max_drawdown"] == "-10"
    assert graded["win_rate"] == "0.500"


def test_grade_reports_an_infinite_profit_factor_as_a_word():
    assert grade([trade("5")])["profit_factor"] == "inf"


def ladder(train_avg: str, validation_avg: str, *, trades: int = 400) -> dict[str, object]:
    split = {
        "trades": trades,
        "average_pnl": train_avg,
        "profit_factor": "3.0000",
    }
    other = {"trades": trades // 3, "average_pnl": validation_avg, "profit_factor": "3.0000"}
    return {"train": split, "validation": other}


def test_the_verdict_fails_a_strategy_whose_edge_dies_under_a_one_minute_delay():
    """This is the finding of the whole screen, so it is asserted directly."""

    baseline = ladder("3.57", "3.30")
    persistence = {"persist_1": ladder("4.53", "5.58"), "persist_3": baseline}
    delays = {"delay_0": baseline, "delay_1": ladder("-0.20", "-0.11"), "delay_2": ladder("-0.32", "-0.59")}

    result = _verdict(baseline, persistence, delays, {"reading": "x"})
    assert result["passes"] is False
    assert any("entry print" in reason for reason in result["failures"])
    assert "decisive failure" in result["entry_delay_reading"]


def test_the_verdict_passes_a_strategy_that_survives_the_delay():
    baseline = ladder("3.00", "2.50")
    persistence = {"persist_1": ladder("3.40", "3.00"), "persist_3": baseline}
    delays = {"delay_0": baseline, "delay_1": ladder("2.40", "2.00"), "delay_2": ladder("2.10", "1.80")}

    result = _verdict(baseline, persistence, delays, {"reading": "x"})
    assert result["passes"] is True
    assert "not purely an entry-print artifact" in result["entry_delay_reading"]


def test_the_verdict_fails_on_too_few_trades():
    baseline = ladder("3.00", "2.50", trades=30)
    delays = {"delay_0": baseline, "delay_1": ladder("2.40", "2.00", trades=30)}
    result = _verdict(baseline, {}, delays, {})
    assert result["passes"] is False
    assert any("training trades" in reason for reason in result["failures"])


def test_the_delay_reading_quantifies_the_decay_rather_than_asserting_robustness():
    delays = {"delay_0": ladder("4.00", "3.00"), "delay_1": ladder("3.00", "2.00"), "delay_2": ladder("2.00", "1.00")}
    assert "25% of the edge" in _delay_reading(delays)


def test_the_delay_reading_says_so_when_the_control_cannot_run():
    assert "cannot be applied" in _delay_reading({})


# --------------------------------------------------------------------------
# Splits and the sealed holdout
# --------------------------------------------------------------------------


def test_splits_are_chronological_with_no_overlap():
    stamps = [f"2025-06-{day:02d}" for day in range(1, 21)]
    splits = chronological_splits(stamps)
    assert len(splits["train"]) == 15
    assert len(splits["validation"]) == 5
    assert not splits["train"] & splits["validation"]
    assert max(splits["train"]) < min(splits["validation"])


def test_splits_exclude_every_sealed_session_and_count_them():
    stamps = [f"2025-06-{day:02d}" for day in range(1, 13)] + ["2026-05-26", "2026-06-01"]
    splits = chronological_splits(stamps)
    assert splits["sealed_present_in_cache"] == {"2026-05-26", "2026-06-01"}
    assert not any(stamp >= "2026-05-26" for stamp in splits["train"] | splits["validation"])


def test_splits_refuse_a_cache_too_small_to_grade():
    with pytest.raises(RuntimeError):
        chronological_splits(["2025-06-01", "2025-06-02"])


def test_load_panels_refuses_a_sealed_session_even_if_asked(tmp_path):
    """Belt and braces: the guard sits at the file-opening boundary too."""

    with pytest.raises(RuntimeError, match="sealed"):
        load_panels(tmp_path, {"2026-06-01"}, SmileSettings())


def test_the_ladders_include_the_settings_that_the_acceptance_rule_names():
    assert 1 in PERSISTENCE_LADDER and 3 in PERSISTENCE_LADDER
    assert ENTRY_DELAY_LADDER[0] == 0 and 1 in ENTRY_DELAY_LADDER
    assert HOLDOUT_START == date(2026, 5, 26)


# --------------------------------------------------------------------------
# Structural guarantee
# --------------------------------------------------------------------------


def test_module_reads_only_the_cache():
    """No client and no socket, so it cannot reach past the sealed boundary."""

    import floor_insurance.smile_residual_research as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    for forbidden in ("AlpacaClient", "import requests", "requests.", "urlopen", "/v1beta1"):
        assert forbidden not in source, forbidden
