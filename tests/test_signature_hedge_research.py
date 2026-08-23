from __future__ import annotations

import math
import random
from datetime import datetime, time, timezone
from itertools import product

import pytest

from floor_insurance.directional import PriceBar
from floor_insurance.signature_hedge_research import (
    DT,
    GRID_TIMES,
    INTERVALS,
    asian_price_delta,
    build_session,
    fit_lasso_cv,
    hedge_outcome,
    multi_indices,
    norm_cdf,
    session_payoffs,
    signature_positions,
    signature_tables,
    stochastic_indices,
    vanilla_price_delta,
)


def brute_force_signature(
    increments: list[float], gamma: tuple[int, ...], dt: float
) -> float:
    """Iterated left-endpoint sum over strictly increasing index tuples."""

    n = len(increments)
    total = 0.0
    for steps in product(range(n), repeat=len(gamma)):
        if any(steps[position] >= steps[position + 1] for position in range(len(gamma) - 1)):
            continue
        term = 1.0
        for position, letter in enumerate(gamma):
            term *= increments[steps[position]] if letter == 1 else dt
        total += term
    return total


def sample_increments(count: int = 7, seed: int = 11) -> list[float]:
    randomizer = random.Random(seed)
    return [randomizer.uniform(-0.01, 0.01) for _ in range(count)]


def test_signature_recursion_matches_iterated_sums() -> None:
    increments = sample_increments()
    values, _ = signature_tables(increments, 3)
    for gamma in multi_indices(3):
        expected = brute_force_signature(increments, gamma, DT)
        assert values[gamma][len(increments)] == pytest.approx(expected, abs=1e-15)


def test_theorem_one_weights_reproduce_every_coordinate() -> None:
    increments = sample_increments(count=9, seed=5)
    values, weights = signature_tables(increments, 4)
    for gamma in stochastic_indices(4):
        rebuilt = sum(
            weights[gamma][len(increments)][step] * increments[step]
            for step in range(len(increments))
        )
        assert abs(rebuilt - values[gamma][len(increments)]) < 1e-12


def test_pure_time_coordinates_carry_no_position() -> None:
    increments = sample_increments()
    _, weights = signature_tables(increments, 3)
    for gamma in ((2,), (2, 2), (2, 2, 2)):
        assert all(value == 0.0 for value in weights[gamma][len(increments)])


def test_weights_match_paper_example_two_closed_form() -> None:
    """Uniform partition: w[(1,2)][n][l] = (n-1-l)/n and (1,2,2) uses C(.,2)/n^2."""

    n = 8
    increments = sample_increments(count=n, seed=3)
    dt = 1.0 / n
    _, weights = signature_tables(increments, 3, dt=dt)
    for step in range(n):
        assert weights[(1, 2)][n][step] == pytest.approx((n - 1 - step) / n)
        assert weights[(1, 2, 2)][n][step] == pytest.approx(
            math.comb(n - 1 - step, 2) / n**2
        )


def test_price_letter_weight_is_the_parent_signature_value() -> None:
    increments = sample_increments()
    values, weights = signature_tables(increments, 3)
    n = len(increments)
    assert weights[(1, 1)][n][n - 1] == pytest.approx(values[(1,)][n - 1])


def test_lasso_recovers_a_sparse_signal() -> None:
    randomizer = random.Random(7)
    rows = [[randomizer.gauss(0, 1) for _ in range(6)] for _ in range(200)]
    truth = [2.0, 0.0, -1.5, 0.0, 0.0, 0.0]
    targets = [
        0.5 + sum(row[j] * truth[j] for j in range(6)) + randomizer.gauss(0, 0.01)
        for row in rows
    ]
    fit = fit_lasso_cv(rows, targets)
    assert fit.active <= 4
    assert abs(fit.coefficients[0] - 2.0) < 0.2
    assert abs(fit.coefficients[2] + 1.5) < 0.2
    assert abs(fit.intercept - 0.5) < 0.05


def test_lasso_is_deterministic() -> None:
    randomizer = random.Random(19)
    rows = [[randomizer.gauss(0, 1) for _ in range(4)] for _ in range(80)]
    targets = [row[0] - 0.5 * row[3] for row in rows]
    assert fit_lasso_cv(rows, targets) == fit_lasso_cv(rows, targets)


def test_lasso_tolerates_a_constant_column() -> None:
    randomizer = random.Random(23)
    rows = [[randomizer.gauss(0, 1), 1.0] for _ in range(60)]
    targets = [row[0] for row in rows]
    fit = fit_lasso_cv(rows, targets)
    assert fit.coefficients[1] == 0.0


def test_normal_cdf_endpoints() -> None:
    assert norm_cdf(0.0) == pytest.approx(0.5)
    assert norm_cdf(-8.0) < 1e-14
    assert 1.0 - norm_cdf(8.0) < 1e-14


def test_vanilla_delta_bounds_and_parity() -> None:
    call_price, call_delta = vanilla_price_delta(1.0, 0.1, 0.01, "call")
    put_price, put_delta = vanilla_price_delta(1.0, 0.1, 0.01, "put")
    assert 0.45 < call_delta < 0.55
    assert call_delta - put_delta == pytest.approx(1.0)
    assert call_price == pytest.approx(put_price, abs=1e-12)
    _, deep = vanilla_price_delta(1.6, 0.1, 0.01, "call")
    assert deep > 0.999
    _, worthless = vanilla_price_delta(0.5, 0.1, 0.01, "call")
    assert worthless < 1e-6


def test_vanilla_at_expiry_is_the_payoff() -> None:
    assert vanilla_price_delta(1.03, 0.1, 0.0, "call") == (pytest.approx(0.03), 1.0)
    assert vanilla_price_delta(0.97, 0.1, 0.0, "put") == (pytest.approx(0.03), -1.0)


def test_asian_price_collapses_to_the_payoff_at_the_final_grid_point() -> None:
    path = [1.0 + 0.001 * step for step in range(len(GRID_TIMES))]
    price, delta = asian_price_delta(path, len(path) - 1, 0.1, "trading")
    average = math.exp(sum(math.log(value) for value in path) / len(path))
    assert price == pytest.approx(session_payoffs(path)["asian_call"], abs=1e-12)
    assert delta == pytest.approx(average / (len(path) * path[-1]), abs=1e-12)


def test_asian_is_cheaper_and_less_sensitive_than_the_terminal_call() -> None:
    path = [1.0] * len(GRID_TIMES)
    asian, asian_delta = asian_price_delta(path, 0, 0.10, "trading")
    call, call_delta = vanilla_price_delta(1.0, 0.10, 345 / (252 * 390), "call")
    assert 0 < asian < call
    assert 0 < asian_delta < call_delta


def test_asian_delta_exposure_shrinks_as_the_average_locks_in() -> None:
    path = [1.0] * len(GRID_TIMES)
    early = asian_price_delta(path, 2, 0.10, "trading")[1]
    late = asian_price_delta(path, len(path) - 3, 0.10, "trading")[1]
    assert late < early


def test_hedge_outcome_charges_both_ends_and_every_rebalance() -> None:
    session = _flat_session()
    positions = [0.5] * INTERVALS
    wealth, peak, turnover = hedge_outcome(session, 0.1, positions, 0.0)
    assert wealth == pytest.approx(0.1)
    assert peak == 0.5
    assert turnover == pytest.approx(1.0 / (INTERVALS + 1))
    charged, _, _ = hedge_outcome(session, 0.1, positions, 1.0)
    assert charged == pytest.approx(0.1 - 1.0 * 1e-4)


def test_signature_positions_reproduce_the_fitted_prediction() -> None:
    increments = list(sample_increments(count=INTERVALS, seed=13))
    indices = stochastic_indices(3)
    values, weights = signature_tables(increments, 3)
    coefficients = tuple(0.3 * (index + 1) for index in range(len(indices)))
    positions = signature_positions(weights, indices, coefficients)
    gain = sum(positions[step] * increments[step] for step in range(INTERVALS))
    predicted = sum(
        coefficients[position] * values[gamma][INTERVALS]
        for position, gamma in enumerate(indices)
    )
    assert abs(gain - predicted) < 1e-12


def _bar(moment: time, price: float) -> PriceBar:
    return PriceBar(
        timestamp=datetime(2026, 3, 4, moment.hour, moment.minute, tzinfo=timezone.utc),
        open=price,
        high=price,
        low=price,
        close=price,
    )


def _flat_session():
    session = build_session("2026-03-04", [_bar(m, 500.0) for m in GRID_TIMES], 0.08)
    assert session is not None
    return session


def test_build_session_normalizes_by_the_ten_oclock_open() -> None:
    bars = [_bar(moment, 500.0 + index) for index, moment in enumerate(GRID_TIMES)]
    session = build_session("2026-03-04", bars, 0.08)
    assert session is not None
    assert session.spot == 500.0
    assert session.path[0] == 1.0
    assert session.terminal == pytest.approx(523.0 / 500.0)
    assert len(session.increments) == INTERVALS
    assert session.payoffs["put"] == 0.0
    assert session.payoffs["call"] == pytest.approx(23.0 / 500.0)


def test_build_session_rejects_an_incomplete_grid() -> None:
    bars = [_bar(moment, 500.0) for moment in GRID_TIMES[:-1]]
    assert build_session("2026-03-04", bars, 0.08) is None


def test_build_session_ignores_off_grid_bars() -> None:
    bars = [_bar(moment, 500.0) for moment in GRID_TIMES]
    bars.append(_bar(time(10, 7), 999.0))
    session = build_session("2026-03-04", bars, 0.08)
    assert session is not None
    assert all(value == 1.0 for value in session.path)


def test_geometric_average_payoff_is_below_the_terminal_call() -> None:
    path = [1.0 + 0.002 * step for step in range(len(GRID_TIMES))]
    payoffs = session_payoffs(path)
    assert 0 < payoffs["asian_call"] < payoffs["call"]
    assert payoffs["put"] == 0.0


def test_grid_covers_ten_to_fifteen_forty_five_every_quarter_hour() -> None:
    assert GRID_TIMES[0] == time(10, 0)
    assert GRID_TIMES[-1] == time(15, 45)
    assert len(GRID_TIMES) == 24
    assert INTERVALS == 23
