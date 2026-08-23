"""Preregistered SPY 0DTE Itô-signature replication research.

Tests one narrow claim from arXiv:2608.18120: that a Lasso-fitted linear
combination of discretized Itô-signature coordinates, implemented as the
self-financing strategies of that paper's Theorem 1, replicates an option
payoff more accurately out of sample than a Black-Scholes delta hedge on the
same rebalancing grid.

The protocol is frozen in docs/SIGNATURE_HEDGE_RESEARCH.md. Nothing here
prices or trades an option, and nothing here is connected to the order engine.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
from dataclasses import dataclass
from datetime import date, time
from itertools import product
from pathlib import Path

from .config import Config
from .directional import PriceBar
from .directional_backtest import HistoricalData
from .volatility import VolatilityHistory

GRID_TIMES: tuple[time, ...] = tuple(
    time(10 + (offset * 15) // 60, (offset * 15) % 60) for offset in range(24)
)
INTERVALS = len(GRID_TIMES) - 1
DT = 1.0 / INTERVALS
GRID_MINUTES = 345
PRIMARY_ORDER = 3
SECONDARY_ORDER = 4
PAYOFFS = ("call", "put", "asian_call")
COST_BP_BASE = 1.0
COST_BP_STRESS = 2.0
TRADING_MINUTES_PER_YEAR = 252 * 390
CALENDAR_MINUTES_PER_YEAR = 365 * 24 * 60
CONVENTIONS = ("trading", "calendar")
LAMBDA_STEPS = 40
LAMBDA_MIN_RATIO = 1e-4
CV_FOLDS = 5
BOOTSTRAP_PATHS = 2000
BOOTSTRAP_BLOCK = 10
BOOTSTRAP_SEED = 20260823
THEOREM_TOLERANCE = 1e-9

MIN_TRAIN_SESSIONS = 300
MIN_VALIDATION_SESSIONS = 100
ASIAN_REQUIRED_REDUCTION = 0.10
MAX_ABS_POSITION = 3.0
MAX_MEAN_TURNOVER = 0.5

ACCEPTANCE_RULE = (
    "At least 300 training and 100 validation complete sessions; the Theorem 1 "
    "replication identity within 1e-9 on every session; lower mean absolute "
    "terminal error than the benchmark on both splits for all three payoffs "
    "after 1bp costs; validation mean absolute error reduced by at least 10% "
    "for the Asian payoff and not worsened for the call or the put; still "
    "better on validation under 2bp stress; validation maximum absolute "
    "position no greater than 3 units and mean per-rebalance turnover no "
    "greater than 0.5."
)
DATA_LIMITATION = (
    "This measures replication error, not profit. No option price enters the "
    "signature side, so a favorable result means the payoff is easier to "
    "synthesize, not that money can be made. One-minute SPY trade bars are "
    "not the consolidated tape at those timestamps. VIX1D is an SPX one-day "
    "implied volatility used as a preregistered SPY proxy. SPY minute bars for "
    "the sealed range were already fetched by earlier experiments in this "
    "repository, so the seal is procedural rather than physical."
)


# --------------------------------------------------------------------------
# Signature transform and the Theorem 1 representation
# --------------------------------------------------------------------------


def multi_indices(order: int) -> list[tuple[int, ...]]:
    """All multi-indices up to ``order`` over letters 1 (price) and 2 (time)."""

    indices: list[tuple[int, ...]] = []
    for length in range(1, order + 1):
        indices.extend(product((1, 2), repeat=length))
    return indices


def is_pure_time(gamma: tuple[int, ...]) -> bool:
    return all(letter == 2 for letter in gamma)


def stochastic_indices(order: int) -> list[tuple[int, ...]]:
    """Indices carrying at least one price letter, so tradable and random."""

    return [gamma for gamma in multi_indices(order) if not is_pure_time(gamma)]


def label(gamma: tuple[int, ...]) -> str:
    return "(" + ",".join("S" if letter == 1 else "t" for letter in gamma) + ")"


def signature_tables(
    increments: list[float], order: int, dt: float = DT
) -> tuple[dict[tuple[int, ...], list[float]], dict[tuple[int, ...], list[list[float]]]]:
    """Discretized Itô signatures and their price-increment weight vectors.

    Returns ``(values, weights)`` where ``values[gamma][k]`` is the signature
    coordinate after ``k`` intervals and ``weights[gamma][k][l]`` is the
    Theorem 1 position held over interval ``l``, so that for any index carrying
    a price letter ``values[gamma][k] == sum(weights[gamma][k][l] * dx[l])``.

    Pure-time indices are deterministic on a fixed grid; their weights are zero
    and their value is a cash amount.
    """

    n = len(increments)
    values: dict[tuple[int, ...], list[float]] = {(): [1.0] * (n + 1)}
    weights: dict[tuple[int, ...], list[list[float]]] = {
        (): [[0.0] * n for _ in range(n + 1)]
    }
    for gamma in multi_indices(order):
        parent, letter = gamma[:-1], gamma[-1]
        parent_values, parent_weights = values[parent], weights[parent]
        series = [0.0] * (n + 1)
        weight_rows = [[0.0] * n for _ in range(n + 1)]
        for k in range(1, n + 1):
            if letter == 1:
                series[k] = series[k - 1] + parent_values[k - 1] * increments[k - 1]
                row = list(weight_rows[k - 1])
                row[k - 1] = parent_values[k - 1]
            else:
                series[k] = series[k - 1] + parent_values[k - 1] * dt
                previous, parent_row = weight_rows[k - 1], parent_weights[k - 1]
                row = [previous[step] + dt * parent_row[step] for step in range(n)]
            weight_rows[k] = row
        values[gamma] = series
        weights[gamma] = weight_rows
    return values, weights


# --------------------------------------------------------------------------
# Session extraction
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Session:
    trading_date: str
    spot: float
    path: tuple[float, ...]
    increments: tuple[float, ...]
    sigma: float
    payoffs: dict[str, float]

    @property
    def terminal(self) -> float:
        return self.path[-1]


def _grid_opens(bars: list[PriceBar]) -> list[float] | None:
    by_time = {bar.timestamp.time(): bar for bar in bars}
    opens: list[float] = []
    for moment in GRID_TIMES:
        bar = by_time.get(moment)
        if bar is None or bar.open <= 0:
            return None
        opens.append(float(bar.open))
    return opens


def session_payoffs(path: list[float]) -> dict[str, float]:
    geometric = math.exp(sum(math.log(value) for value in path) / len(path))
    return {
        "call": max(path[-1] - 1.0, 0.0),
        "put": max(1.0 - path[-1], 0.0),
        "asian_call": max(geometric - 1.0, 0.0),
    }


def build_session(
    trading_date: str, bars: list[PriceBar], sigma: float
) -> Session | None:
    opens = _grid_opens(bars)
    if opens is None:
        return None
    spot = opens[0]
    path = [value / spot for value in opens]
    increments = [path[k + 1] - path[k] for k in range(INTERVALS)]
    return Session(
        trading_date=trading_date,
        spot=spot,
        path=tuple(path),
        increments=tuple(increments),
        sigma=sigma,
        payoffs=session_payoffs(path),
    )


# --------------------------------------------------------------------------
# Lasso by coordinate descent on a Gram matrix
# --------------------------------------------------------------------------


def _soft_threshold(value: float, penalty: float) -> float:
    if value > penalty:
        return value - penalty
    if value < -penalty:
        return value + penalty
    return 0.0


def _standardize(rows: list[list[float]]) -> tuple[list[float], list[float]]:
    n, p = len(rows), len(rows[0])
    means = [sum(row[j] for row in rows) / n for j in range(p)]
    scales: list[float] = []
    for j in range(p):
        variance = sum((row[j] - means[j]) ** 2 for row in rows) / n
        scales.append(math.sqrt(variance) if variance > 1e-24 else 0.0)
    return means, scales


def _design(
    rows: list[list[float]], means: list[float], scales: list[float]
) -> list[list[float]]:
    return [
        [
            (row[j] - means[j]) / scales[j] if scales[j] > 0 else 0.0
            for j in range(len(means))
        ]
        for row in rows
    ]


def _gram(design: list[list[float]], targets: list[float]) -> tuple[
    list[list[float]], list[float], float
]:
    n, p = len(design), len(design[0])
    mean_y = sum(targets) / n
    centered = [value - mean_y for value in targets]
    gram = [[0.0] * p for _ in range(p)]
    for j in range(p):
        column_j = [row[j] for row in design]
        for k in range(j, p):
            total = sum(column_j[i] * design[i][k] for i in range(n))
            gram[j][k] = gram[k][j] = total / n
    correlations = [
        sum(design[i][j] * centered[i] for i in range(n)) / n for j in range(p)
    ]
    return gram, correlations, mean_y


def _descend(
    gram: list[list[float]],
    correlations: list[float],
    penalty: float,
    beta: list[float],
    max_sweeps: int = 1000,
    tolerance: float = 1e-11,
) -> list[float]:
    p = len(correlations)
    for _ in range(max_sweeps):
        largest = 0.0
        for j in range(p):
            if gram[j][j] <= 0:
                beta[j] = 0.0
                continue
            partial = correlations[j] - sum(
                gram[j][k] * beta[k] for k in range(p) if k != j
            )
            updated = _soft_threshold(partial, penalty) / gram[j][j]
            shift = updated - beta[j]
            if shift:
                beta[j] = updated
                largest = max(largest, abs(shift))
        if largest < tolerance:
            break
    return beta


def _lambda_grid(correlations: list[float]) -> list[float]:
    top = max((abs(value) for value in correlations), default=0.0)
    if top <= 0:
        return [0.0]
    return [
        top * (LAMBDA_MIN_RATIO ** (step / (LAMBDA_STEPS - 1)))
        for step in range(LAMBDA_STEPS)
    ]


def _folds(count: int, folds: int = CV_FOLDS) -> list[range]:
    edges = [round(count * index / folds) for index in range(folds + 1)]
    return [range(edges[index], edges[index + 1]) for index in range(folds)]


@dataclass(frozen=True)
class LassoFit:
    intercept: float
    coefficients: tuple[float, ...]
    penalty: float
    active: int


def fit_lasso_cv(rows: list[list[float]], targets: list[float]) -> LassoFit:
    """Lasso with a penalty chosen by contiguous-block cross-validation."""

    means, scales = _standardize(rows)
    design = _design(rows, means, scales)
    gram, correlations, mean_y = _gram(design, targets)
    grid = _lambda_grid(correlations)

    errors = [0.0] * len(grid)
    counts = [0] * len(grid)
    for held in _folds(len(rows)):
        keep = [index for index in range(len(rows)) if index not in held]
        if not keep or not held:
            continue
        fold_rows = [rows[index] for index in keep]
        fold_targets = [targets[index] for index in keep]
        fold_means, fold_scales = _standardize(fold_rows)
        fold_design = _design(fold_rows, fold_means, fold_scales)
        fold_gram, fold_correlations, fold_mean = _gram(fold_design, fold_targets)
        beta = [0.0] * len(correlations)
        for position, penalty in enumerate(grid):
            beta = _descend(fold_gram, fold_correlations, penalty, beta)
            total = 0.0
            for index in held:
                row = rows[index]
                predicted = fold_mean + sum(
                    beta[j] * (row[j] - fold_means[j]) / fold_scales[j]
                    for j in range(len(beta))
                    if fold_scales[j] > 0
                )
                total += (targets[index] - predicted) ** 2
            errors[position] += total
            counts[position] += len(held)

    best = min(
        range(len(grid)),
        key=lambda position: (
            errors[position] / counts[position] if counts[position] else math.inf
        ),
    )
    beta = [0.0] * len(correlations)
    for penalty in grid[: best + 1]:
        beta = _descend(gram, correlations, penalty, beta)
    coefficients = [
        beta[j] / scales[j] if scales[j] > 0 else 0.0 for j in range(len(beta))
    ]
    intercept = mean_y - sum(
        coefficients[j] * means[j] for j in range(len(coefficients))
    )
    return LassoFit(
        intercept=intercept,
        coefficients=tuple(coefficients),
        penalty=grid[best],
        active=sum(1 for value in coefficients if value != 0.0),
    )


# --------------------------------------------------------------------------
# Benchmark hedges
# --------------------------------------------------------------------------


def norm_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def _tau(index: int, convention: str) -> float:
    remaining = GRID_MINUTES * (1.0 - index * DT)
    divisor = (
        TRADING_MINUTES_PER_YEAR
        if convention == "trading"
        else CALENDAR_MINUTES_PER_YEAR
    )
    return remaining / divisor


def vanilla_price_delta(
    spot: float, sigma: float, tau: float, kind: str
) -> tuple[float, float]:
    """Black-Scholes price and delta with strike 1, ``r = q = 0``."""

    if tau <= 0 or sigma <= 0:
        if kind == "call":
            return max(spot - 1.0, 0.0), 1.0 if spot > 1.0 else 0.0
        return max(1.0 - spot, 0.0), -1.0 if spot < 1.0 else 0.0
    deviation = sigma * math.sqrt(tau)
    d1 = (math.log(spot) + 0.5 * deviation * deviation) / deviation
    d2 = d1 - deviation
    if kind == "call":
        return spot * norm_cdf(d1) - norm_cdf(d2), norm_cdf(d1)
    return norm_cdf(-d2) - spot * norm_cdf(-d1), norm_cdf(d1) - 1.0


def asian_price_delta(
    path: list[float], index: int, sigma: float, convention: str
) -> tuple[float, float]:
    """Exact discrete geometric-average call price and delta under GBM.

    The average runs over all grid points, so at grid ``index`` the log average
    is normal with mean ``m`` and variance ``v ** 2`` below. The price follows
    from lognormal expectation and the delta from ``dPrice/dm * dm/dSpot``,
    using ``A * n(d1) == K * n(d2)``.
    """

    total = len(path)
    spot = path[index]
    times = [_tau(0, convention) - _tau(step, convention) for step in range(total)]
    known = sum(math.log(path[step]) for step in range(index + 1))
    future = list(range(index + 1, total))
    drift = sum(-0.5 * sigma * sigma * (times[step] - times[index]) for step in future)
    mean = (known + len(future) * math.log(spot) + drift) / total
    variance = 0.0
    for first in future:
        for second in future:
            variance += min(times[first], times[second]) - times[index]
    variance *= sigma * sigma / (total * total)
    exposure = (len(future) + 1) / total
    if variance <= 0:
        average = math.exp(mean)
        return max(average - 1.0, 0.0), (
            exposure * average / spot if average > 1.0 else 0.0
        )
    deviation = math.sqrt(variance)
    scale = math.exp(mean + 0.5 * variance)
    d1 = (mean + variance) / deviation
    d2 = d1 - deviation
    price = scale * norm_cdf(d1) - norm_cdf(d2)
    return price, exposure * scale * norm_cdf(d1) / spot


def benchmark_positions(
    session: Session, payoff: str, convention: str
) -> tuple[float, list[float]]:
    path = list(session.path)
    positions: list[float] = []
    initial = 0.0
    for index in range(INTERVALS):
        if payoff == "asian_call":
            price, delta = asian_price_delta(path, index, session.sigma, convention)
        else:
            price, delta = vanilla_price_delta(
                path[index], session.sigma, _tau(index, convention), payoff
            )
        if index == 0:
            initial = price
        positions.append(delta)
    return initial, positions


# --------------------------------------------------------------------------
# Wealth, cost, and error accounting
# --------------------------------------------------------------------------


def hedge_outcome(
    session: Session, cash: float, positions: list[float], cost_bp: float
) -> tuple[float, float, float]:
    """Return ``(terminal wealth net of cost, max position, mean turnover)``."""

    path = list(session.path)
    gain = sum(
        positions[index] * session.increments[index] for index in range(INTERVALS)
    )
    traded = abs(positions[0]) * path[0]
    turnover = abs(positions[0])
    for index in range(1, INTERVALS):
        shift = abs(positions[index] - positions[index - 1])
        traded += shift * path[index]
        turnover += shift
    traded += abs(positions[-1]) * path[-1]
    turnover += abs(positions[-1])
    cost = traded * cost_bp / 10_000.0
    return (
        cash + gain - cost,
        max(abs(value) for value in positions),
        turnover / (INTERVALS + 1),
    )


def signature_positions(
    weights: dict[tuple[int, ...], list[list[float]]],
    indices: list[tuple[int, ...]],
    coefficients: tuple[float, ...],
) -> list[float]:
    return [
        sum(
            coefficients[position] * weights[gamma][INTERVALS][interval]
            for position, gamma in enumerate(indices)
        )
        for interval in range(INTERVALS)
    ]


# --------------------------------------------------------------------------
# Statistics
# --------------------------------------------------------------------------


def sign_test(differences: list[float]) -> dict[str, object]:
    nonzero = [value for value in differences if value != 0.0]
    if not nonzero:
        return {"comparisons": 0, "p_value": None}
    wins = sum(1 for value in nonzero if value < 0)
    count = len(nonzero)
    tail = min(wins, count - wins)
    cumulative = sum(math.comb(count, index) for index in range(tail + 1))
    p_value = min(1.0, 2.0 * cumulative / (2.0**count))
    return {
        "comparisons": count,
        "signature_better": wins,
        "p_value": round(p_value, 6),
    }


def paired_block_bootstrap(differences: list[float]) -> dict[str, object]:
    if len(differences) < 30:
        return {"paths": 0, "reason": "fewer than 30 paired sessions"}
    randomizer = random.Random(BOOTSTRAP_SEED)
    count = len(differences)
    favorable = 0
    means: list[float] = []
    for _ in range(BOOTSTRAP_PATHS):
        drawn: list[float] = []
        while len(drawn) < count:
            start = randomizer.randrange(count)
            for offset in range(BOOTSTRAP_BLOCK):
                drawn.append(differences[(start + offset) % count])
                if len(drawn) == count:
                    break
        average = sum(drawn) / count
        means.append(average)
        favorable += average < 0
    means.sort()
    return {
        "seed": BOOTSTRAP_SEED,
        "paths": BOOTSTRAP_PATHS,
        "block_length": BOOTSTRAP_BLOCK,
        "probability_signature_better": round(favorable / BOOTSTRAP_PATHS, 4),
        "mean_difference_p05": round(means[int(0.05 * BOOTSTRAP_PATHS)] * 1000, 4),
        "mean_difference_p95": round(means[int(0.95 * BOOTSTRAP_PATHS)] * 1000, 4),
    }


# --------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------


def _milli(value: float) -> float:
    return round(value * 1000, 4)


def evaluate(
    sessions: dict[str, list[Session]],
    order: int,
) -> dict[str, object]:
    indices = stochastic_indices(order)
    tables = {
        split: [
            signature_tables(list(session.increments), order)
            for session in sessions[split]
        ]
        for split in ("train", "validation")
    }
    features = {
        split: [
            [values[gamma][INTERVALS] for gamma in indices]
            for values, _ in tables[split]
        ]
        for split in ("train", "validation")
    }

    theorem_error = 0.0
    for split in ("train", "validation"):
        for position, (values, weights) in enumerate(tables[split]):
            increments = sessions[split][position].increments
            for gamma in indices:
                rebuilt = sum(
                    weights[gamma][INTERVALS][interval] * increments[interval]
                    for interval in range(INTERVALS)
                )
                theorem_error = max(
                    theorem_error, abs(rebuilt - values[gamma][INTERVALS])
                )

    payoff_results: dict[str, object] = {}
    for payoff in PAYOFFS:
        targets = [session.payoffs[payoff] for session in sessions["train"]]
        fit = fit_lasso_cv(features["train"], targets)
        loadings = sorted(
            (
                {"coordinate": label(gamma), "coefficient": round(value, 6)}
                for gamma, value in zip(indices, fit.coefficients, strict=True)
                if value != 0.0
            ),
            key=lambda item: -abs(item["coefficient"]),
        )

        benchmark_cash_fit: dict[str, float] = {}
        for convention in CONVENTIONS:
            residuals = []
            for session in sessions["train"]:
                cash, positions = benchmark_positions(session, payoff, convention)
                wealth, _, _ = hedge_outcome(session, 0.0, positions, COST_BP_BASE)
                residuals.append(session.payoffs[payoff] - wealth)
            benchmark_cash_fit[convention] = sum(residuals) / len(residuals)

        static_cash = sum(targets) / len(targets)

        splits: dict[str, object] = {}
        for split in ("train", "validation"):
            rows = sessions[split]
            signature: dict[str, list[float]] = {}
            statistics: dict[str, list[float]] = {"max_position": [], "turnover": []}
            for cost_bp, tag in ((COST_BP_BASE, "base"), (COST_BP_STRESS, "stress")):
                errors = []
                for position, session in enumerate(rows):
                    _, weights = tables[split][position]
                    positions = signature_positions(weights, indices, fit.coefficients)
                    wealth, peak, turnover = hedge_outcome(
                        session, fit.intercept, positions, cost_bp
                    )
                    errors.append(session.payoffs[payoff] - wealth)
                    if tag == "base":
                        statistics["max_position"].append(peak)
                        statistics["turnover"].append(turnover)
                signature[tag] = errors

            benchmark: dict[str, list[float]] = {}
            benchmark_fitted: dict[str, list[float]] = {}
            benchmark_stats: dict[str, dict[str, float]] = {}
            for convention in CONVENTIONS:
                for cost_bp, tag in ((COST_BP_BASE, "base"), (COST_BP_STRESS, "stress")):
                    errors = []
                    fitted = []
                    peaks = []
                    turnovers = []
                    for session in rows:
                        cash, positions = benchmark_positions(
                            session, payoff, convention
                        )
                        wealth, peak, turnover = hedge_outcome(
                            session, cash, positions, cost_bp
                        )
                        errors.append(session.payoffs[payoff] - wealth)
                        fitted.append(
                            session.payoffs[payoff]
                            - (wealth - cash + benchmark_cash_fit[convention])
                        )
                        peaks.append(peak)
                        turnovers.append(turnover)
                    benchmark[f"{convention}_{tag}"] = errors
                    benchmark_fitted[f"{convention}_{tag}"] = fitted
                    if tag == "base":
                        benchmark_stats[convention] = {
                            "max_position": max(peaks),
                            "mean_turnover": sum(turnovers) / len(turnovers),
                        }

            def mae(values: list[float]) -> float:
                return sum(abs(value) for value in values) / len(values)

            best_base = min(CONVENTIONS, key=lambda name: mae(benchmark[f"{name}_base"]))
            reference = benchmark[f"{best_base}_base"]
            differences = [
                abs(signature["base"][index]) - abs(reference[index])
                for index in range(len(rows))
            ]
            splits[split] = {
                "sessions": len(rows),
                "unhedged_mae": _milli(
                    mae([session.payoffs[payoff] - static_cash for session in rows])
                ),
                "signature_mae": _milli(mae(signature["base"])),
                "signature_rmse": _milli(
                    math.sqrt(sum(value**2 for value in signature["base"]) / len(rows))
                ),
                "signature_mae_stress": _milli(mae(signature["stress"])),
                "benchmark_convention_credited": best_base,
                "benchmark_mae": _milli(mae(reference)),
                "benchmark_mae_trading": _milli(mae(benchmark["trading_base"])),
                "benchmark_mae_calendar": _milli(mae(benchmark["calendar_base"])),
                "benchmark_rmse": _milli(
                    math.sqrt(sum(value**2 for value in reference) / len(rows))
                ),
                "benchmark_mae_stress": _milli(mae(benchmark[f"{best_base}_stress"])),
                "benchmark_mae_fitted_cash": _milli(
                    min(
                        mae(benchmark_fitted[f"{name}_base"]) for name in CONVENTIONS
                    )
                ),
                "mae_reduction": round(
                    1.0 - mae(signature["base"]) / mae(reference), 4
                )
                if mae(reference) > 0
                else None,
                "mae_reduction_vs_fitted_cash": round(
                    1.0
                    - mae(signature["base"])
                    / min(mae(benchmark_fitted[f"{name}_base"]) for name in CONVENTIONS),
                    4,
                ),
                "win_rate": round(
                    sum(1 for value in differences if value < 0) / len(rows), 4
                ),
                "signature_max_position": round(max(statistics["max_position"]), 4),
                "signature_mean_turnover": round(
                    sum(statistics["turnover"]) / len(rows), 4
                ),
                "benchmark_max_position": round(
                    benchmark_stats[best_base]["max_position"], 4
                ),
                "benchmark_mean_turnover": round(
                    benchmark_stats[best_base]["mean_turnover"], 4
                ),
                "sign_test": sign_test(differences),
                "bootstrap": paired_block_bootstrap(differences),
            }

        payoff_results[payoff] = {
            "penalty": round(fit.penalty, 8),
            "initial_cash": _milli(fit.intercept),
            "active_coordinates": fit.active,
            "coordinates_available": len(indices),
            "top_loadings": loadings[:10],
            "splits": splits,
        }

    return {
        "order": order,
        "theorem_1_max_identity_error": theorem_error,
        "theorem_1_holds": theorem_error < THEOREM_TOLERANCE,
        "payoffs": payoff_results,
    }


def gate_report(primary: dict[str, object]) -> dict[str, object]:
    payoffs = primary["payoffs"]
    call = payoffs["call"]["splits"]
    put = payoffs["put"]["splits"]
    asian = payoffs["asian_call"]["splits"]
    checks: dict[str, bool] = {
        "train_sessions": call["train"]["sessions"] >= MIN_TRAIN_SESSIONS,
        "validation_sessions": (
            call["validation"]["sessions"] >= MIN_VALIDATION_SESSIONS
        ),
        "theorem_1_identity": bool(primary["theorem_1_holds"]),
    }
    for name, result in (("call", call), ("put", put), ("asian", asian)):
        for split in ("train", "validation"):
            checks[f"{name}_{split}_beats_benchmark"] = (
                result[split]["signature_mae"] < result[split]["benchmark_mae"]
            )
    checks["asian_validation_reduction"] = (
        asian["validation"]["mae_reduction"] or 0.0
    ) >= ASIAN_REQUIRED_REDUCTION
    checks["stress_validation"] = all(
        result["validation"]["signature_mae_stress"]
        < result["validation"]["benchmark_mae_stress"]
        for result in (call, put, asian)
    )
    checks["position_cap"] = all(
        result["validation"]["signature_max_position"] <= MAX_ABS_POSITION
        for result in (call, put, asian)
    )
    checks["turnover_cap"] = all(
        result["validation"]["signature_mean_turnover"] <= MAX_MEAN_TURNOVER
        for result in (call, put, asian)
    )
    return {"checks": checks, "passed": all(checks.values())}


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate the preregistered SPY Itô-signature replication test"
    )
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--oos-start", type=date.fromisoformat, required=True)
    parser.add_argument(
        "--cache-dir", type=Path, default=Path("state/signature-hedge-cache")
    )
    parser.add_argument("--report-out", type=Path)
    return parser


def development_splits(dates: list[str], oos_start: date) -> dict[str, set[str]]:
    """Chronological 75/25 split of the development window.

    This deliberately does not use ``research_splits``. That helper derives the
    holdout from dates already fetched, which would require downloading the
    sealed bars in order to name them. Here the underlying bars *are* the
    strategy data, so the sealed range is never requested at all and the split
    is computed from the development sessions only. Any date at or beyond the
    holdout boundary is a fetch error, not a held-out sample.
    """

    if any(date.fromisoformat(value) >= oos_start for value in dates):
        raise SystemExit(
            "sealed sessions were fetched; the holdout boundary was violated"
        )
    if len(dates) < 10:
        raise SystemExit("at least 10 development sessions are required")
    train_end = max(1, int(len(dates) * 0.75))
    return {"train": set(dates[:train_end]), "validation": set(dates[train_end:])}


def main() -> int:
    args = _parser().parse_args()
    if args.end >= args.oos_start:
        raise SystemExit(
            "the development end date must precede the sealed holdout start"
        )
    config = Config.from_env()
    data = HistoricalData(config, args.cache_dir)
    volatility = VolatilityHistory.load(args.cache_dir)
    bars = data.stock_sessions(args.start, args.end, "SPY")
    dates = sorted(bars)
    splits = development_splits(dates, args.oos_start)

    sessions: dict[str, list[Session]] = {"train": [], "validation": []}
    excluded: list[dict[str, str]] = []
    for trading_date in dates:
        split = "train" if trading_date in splits["train"] else "validation"
        day = date.fromisoformat(trading_date)
        previous = volatility.previous_session(day)
        reading = (
            volatility.series.get("vix1d", {}).get(previous)
            if previous is not None
            else None
        )
        if reading is None:
            excluded.append({"date": trading_date, "reason": "no prior VIX1D close"})
            continue
        session = build_session(trading_date, bars[trading_date], float(reading) / 100.0)
        if session is None:
            excluded.append({"date": trading_date, "reason": "incomplete 15-minute grid"})
            continue
        sessions[split].append(session)

    print(
        f"complete sessions: train={len(sessions['train'])} "
        f"validation={len(sessions['validation'])} excluded={len(excluded)}",
        file=sys.stderr,
    )
    primary = evaluate(sessions, PRIMARY_ORDER)
    secondary = evaluate(sessions, SECONDARY_ORDER)
    report = {
        "acceptance_rule": ACCEPTANCE_RULE,
        "data_limitation": DATA_LIMITATION,
        "grid": {
            "times": [moment.strftime("%H:%M") for moment in GRID_TIMES],
            "intervals": INTERVALS,
        },
        "cost_basis_points": {"base": COST_BP_BASE, "stress": COST_BP_STRESS},
        "sessions": {
            "train": len(sessions["train"]),
            "validation": len(sessions["validation"]),
            "excluded": excluded,
        },
        "oos_revealed": False,
        "seal": {
            "requested_end": args.end.isoformat(),
            "oos_start": args.oos_start.isoformat(),
            "last_development_session": dates[-1],
            "oos_sessions_fetched": 0,
            "cache_files": sorted(
                path.name for path in args.cache_dir.glob("spy-*.json")
            ),
            "note": (
                "The sealed range was never requested, so no holdout bar exists "
                "in this cache. SPY minute bars for that range do exist in other "
                "experiments' caches in this repository, so the seal is "
                "procedural rather than physical."
            ),
        },
        "primary": primary,
        "secondary": secondary,
        "gates": gate_report(primary),
    }
    report["development_passed"] = report["gates"]["passed"]
    encoded = json.dumps(report, indent=2) + "\n"
    if args.report_out:
        args.report_out.parent.mkdir(parents=True, exist_ok=True)
        args.report_out.write_text(encoded, encoding="utf-8")
        os.chmod(args.report_out, 0o600)
    print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
