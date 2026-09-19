"""Preregistered cross-sectional smile-residual screen for SPY 0DTE butterflies.

Eighteen rejected lines in this project share one failure mode: a gross signal
smaller than the cost of crossing the bid/ask. The
[spread estimator](../../docs/SPREAD_ESTIMATOR.md) has now measured that cost at
roughly six tenths of a cent per contract unit at the money, five times below the
level that kills anything, which removes fill cost as an explanation and leaves
the signal itself under suspicion. The
[parity diagnostic](../../docs/SURFACE_PARITY_DIAGNOSTIC.md) points the same way:
the parity gap measured from trade prints is $0.18 to $0.26, while the first
quote-based look put it near $0.06.

So this screen is built around one question rather than a new signal. Is a
cross-sectional dislocation in the option smile a real, tradable state of the
market, or a single-minute artifact of nonsynchronous prints?

The construction is deliberately leak-free. For every minute, the butterfly value
at each strike offset,

    B_i = P(K_{i-1}) - 2 P(K_i) + P(K_{i+1})

is proportional to the risk-neutral density at that strike and must be positive
under no-arbitrage. A Gaussian smile makes `log B_i` a parabola in the offset, so
fitting `log B_i ~ a + b*i + c*i^2` across that one minute's strikes gives a
residual

    r_i = log B_i - fitted_i

that is positive when the market prices more density at that strike than its own
neighbours imply -- the butterfly is rich -- and negative when it is cheap. The
fit uses only the strikes present at that single timestamp, so no information
crosses sessions and no parameter is learned from the data being graded.

Two controls are run, and the result of this screen is that only one of them
works.

`persist_minutes` requires the dislocation to still be there a minute or three
later. That rules out a single-bar blip, and it is the control most backtests
would stop at. It is not sufficient. A series that lags *consistently* keeps its
residual dislocated for many minutes in a row while never being tradable at the
lagging price, and such a series sails through any persistence filter.

`entry_delay_minutes` is the control that discriminates. The signal is read at
one minute and the order fills at a later one, so the stale print that generated
the signal is no longer available to trade against. A genuine dislocation that
takes minutes to close barely notices a one-minute delay; an edge assembled from
stale prints disappears completely. Both ladders are reported whether or not the
strategy passes, because the gap between them is the actual finding here.

Cache-only. No client, no socket, and the sealed holdout is never read: session
files on or after the boundary are excluded before any file is opened.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, replace
from datetime import date, time
from decimal import Decimal
from pathlib import Path
from statistics import median
from typing import Iterable

from .fifty_credit_research import moving_block_bootstrap
from .spread_estimator import (
    HOLDOUT_START,
    Bar,
    _parse_bar,
    load_underlying,
    parse_symbol,
)

CENT = Decimal("0.01")
HUNDRED = Decimal("100")

# Measured at the money by floor-spread-estimator across 575 sessions: the
# bias-corrected half-spread a mid-fill trader pays on each contract unit.
MEASURED_HALF_SPREAD = Decimal("0.006")

# A butterfly is four contract units: one lower wing, two body, one upper wing.
UNITS_PER_BUTTERFLY = Decimal("4")

ACCEPTANCE_RULE = (
    "At least 150 training and 40 validation trades; positive average P&L and "
    "profit factor at least 1.25 on both splits at the measured fill cost; "
    "positive average P&L on both splits at double that cost; the edge must not "
    "collapse when required persistence rises from one minute to three; and it "
    "must survive filling one minute after the signal instead of at the signal "
    "minute's own print. The sealed holdout is never read."
)

DATA_LIMITATION = (
    "One-minute option trade bars are not synchronized quotes. A butterfly built "
    "from three series that last printed at different moments inside the same "
    "minute shows convexity that never existed simultaneously, and if a series "
    "lags consistently that false convexity persists for minutes, so a persistence "
    "filter does not remove it. Only refusing to fill at the signal minute's own "
    "print does, which is why the entry-delay ladder and not the persistence "
    "ladder carries the weight of this screen."
)


@dataclass(frozen=True)
class SmileSettings:
    symbol: str = "SPY"
    entry_start: time = time(10, 0)
    entry_end: time = time(15, 0)
    minimum_offsets: int = 7
    residual_threshold: Decimal = Decimal("0.25")
    persist_minutes: int = 3
    entry_delay_minutes: int = 0
    hold_minutes: int = 30
    cost_per_unit: Decimal = MEASURED_HALF_SPREAD
    fees_per_butterfly: Decimal = Decimal("0.20")
    maximum_risk_dollars: Decimal = Decimal("100")
    max_entries_per_session: int = 1
    minimum_butterfly_value: Decimal = Decimal("0.02")


@dataclass(frozen=True)
class Trade:
    trading_date: str
    minute: str
    entry_minute: str
    kind: str
    offset: int
    center: Decimal
    residual: Decimal
    direction: str
    entry_value: Decimal
    exit_value: Decimal
    cost: Decimal
    pnl: Decimal


@dataclass(frozen=True)
class SessionPanel:
    """One session's option prices in offset space, indexed by minute."""

    trading_date: str
    atm_strike: Decimal
    # (kind, offset) -> {minute label -> price}
    prices: dict[tuple[str, int], dict[str, Decimal]]
    minutes: tuple[str, ...]


# --------------------------------------------------------------------------
# Panel construction
# --------------------------------------------------------------------------


def build_panel(
    trading_date: str,
    payload: dict[str, list[dict[str, str]]],
    underlying: list[Bar],
    settings: SmileSettings,
) -> SessionPanel | None:
    """Put one session's bars into offset space with no forward filling.

    A missing minute stays missing. Carrying the last print forward would
    manufacture exactly the dislocation this screen is trying to detect.
    """

    atm = _atm_strike(underlying)
    if atm is None:
        return None

    prices: dict[tuple[str, int], dict[str, Decimal]] = {}
    minutes: set[str] = set()

    for symbol, raw_bars in payload.items():
        parsed = parse_symbol(symbol)
        if parsed is None:
            continue
        kind, strike = parsed
        if strike != strike.to_integral_value():
            continue
        offset = int(strike - atm)

        series: dict[str, Decimal] = {}
        for item in raw_bars:
            bar = _parse_bar(item)
            if bar.volume <= 0 or bar.close <= 0:
                continue
            label = bar.timestamp.strftime("%H:%M")
            series[label] = Decimal(repr(round(bar.close, 2)))
            minutes.add(label)
        if series:
            prices[(kind, offset)] = series

    if not prices:
        return None
    return SessionPanel(trading_date, atm, prices, tuple(sorted(minutes)))


def _atm_strike(underlying: list[Bar]) -> Decimal | None:
    """Nearest whole strike to the 10:00 ET underlying close."""

    candidates = [bar for bar in underlying if bar.timestamp.time() <= time(10, 0)]
    if not candidates:
        return None
    spot = Decimal(repr(round(candidates[-1].close, 2)))
    return spot.to_integral_value(rounding="ROUND_HALF_UP")


# --------------------------------------------------------------------------
# The cross-sectional fit
# --------------------------------------------------------------------------


def butterfly_values(
    panel: SessionPanel, kind: str, minute: str
) -> dict[int, Decimal]:
    """Discrete second difference across offsets at one timestamp.

    Only offsets whose three legs all printed in this exact minute are returned,
    so every butterfly here is built from simultaneous prints.
    """

    row = {
        offset: series[minute]
        for (series_kind, offset), series in panel.prices.items()
        if series_kind == kind and minute in series
    }
    values: dict[int, Decimal] = {}
    for offset in sorted(row):
        lower, middle, upper = row.get(offset - 1), row.get(offset), row.get(offset + 1)
        if lower is None or middle is None or upper is None:
            continue
        values[offset] = (lower - Decimal("2") * middle + upper).quantize(CENT)
    return values


def fit_log_parabola(values: dict[int, Decimal]) -> dict[int, Decimal] | None:
    """Fit `log B ~ a + b*i + c*i^2` and return the residual at each offset.

    A Gaussian risk-neutral density is a parabola in log space, so this is the
    smile's own shape rather than an arbitrary smoother. Non-positive butterflies
    are excluded from the fit because their log is undefined; they are convexity
    violations and are counted separately by the caller.
    """

    usable = {offset: value for offset, value in values.items() if value > 0}
    if len(usable) < 4:
        return None

    xs = [float(offset) for offset in sorted(usable)]
    ys = [math.log(float(usable[offset])) for offset in sorted(usable)]

    coefficients = _ols_quadratic(xs, ys)
    if coefficients is None:
        return None
    a, b, c = coefficients

    return {
        offset: Decimal(repr(round(ys[index] - (a + b * xs[index] + c * xs[index] ** 2), 6)))
        for index, offset in enumerate(sorted(usable))
    }


def _ols_quadratic(xs: list[float], ys: list[float]) -> tuple[float, float, float] | None:
    """Least squares on the basis [1, x, x^2] via the 3x3 normal equations.

    Solved with Gaussian elimination and partial pivoting rather than a matrix
    library, since the repository depends only on `requests`.
    """

    powers = [sum(x**degree for x in xs) for degree in range(5)]
    matrix = [
        [powers[0], powers[1], powers[2], sum(ys)],
        [powers[1], powers[2], powers[3], sum(x * y for x, y in zip(xs, ys, strict=True))],
        [powers[2], powers[3], powers[4], sum(x * x * y for x, y in zip(xs, ys, strict=True))],
    ]

    for column in range(3):
        pivot = max(range(column, 3), key=lambda row: abs(matrix[row][column]))
        if abs(matrix[pivot][column]) < 1e-12:
            return None
        matrix[column], matrix[pivot] = matrix[pivot], matrix[column]
        for row in range(3):
            if row == column:
                continue
            factor = matrix[row][column] / matrix[column][column]
            for term in range(column, 4):
                matrix[row][term] -= factor * matrix[column][term]

    return tuple(matrix[index][3] / matrix[index][index] for index in range(3))  # type: ignore[return-value]


def residual_series(
    panel: SessionPanel, kind: str, settings: SmileSettings
) -> tuple[dict[str, dict[int, Decimal]], int, int]:
    """Residuals per minute, plus convexity violations and butterflies examined.

    A non-positive butterfly is a no-arbitrage violation in the printed data. It
    is excluded from the log fit and counted, because its rate is a direct measure
    of how badly nonsynchronous prints distort this cross-section.
    """

    by_minute: dict[str, dict[int, Decimal]] = {}
    violations = 0
    examined = 0
    for minute in panel.minutes:
        values = butterfly_values(panel, kind, minute)
        if len(values) < settings.minimum_offsets:
            continue
        examined += len(values)
        violations += sum(1 for value in values.values() if value <= 0)
        residuals = fit_log_parabola(values)
        if residuals:
            by_minute[minute] = residuals
    return by_minute, violations, examined


# --------------------------------------------------------------------------
# Residual dynamics
# --------------------------------------------------------------------------


def autocorrelation(series: list[float], lag: int) -> float | None:
    if len(series) <= lag + 2:
        return None
    head = series[:-lag]
    tail = series[lag:]
    mean = sum(series) / len(series)
    numerator = sum((a - mean) * (b - mean) for a, b in zip(head, tail, strict=True))
    denominator = sum((value - mean) ** 2 for value in series)
    if denominator <= 0:
        return None
    return numerator / denominator


def half_life(rho: float | None) -> float | None:
    """Minutes for a shock to decay by half under an AR(1) reading of the residual."""

    if rho is None or rho <= 0 or rho >= 1:
        return None
    return -math.log(2.0) / math.log(rho)


def residual_dynamics(
    panels: Iterable[SessionPanel], settings: SmileSettings
) -> dict[str, object]:
    """Measure whether the residual mean-reverts before asking it to pay.

    If the residual is white noise there is nothing to trade regardless of
    threshold, and that is worth knowing on its own.
    """

    lags = (1, 3, 5, 15)
    collected: dict[int, list[float]] = {lag: [] for lag in lags}
    half_lives: list[float] = []
    magnitudes: list[float] = []
    violations = 0
    butterflies = 0
    minutes_fitted = 0

    for panel in panels:
        for kind in ("C", "P"):
            by_minute, kind_violations, kind_total = residual_series(panel, kind, settings)
            violations += kind_violations
            butterflies += kind_total
            minutes_fitted += len(by_minute)
            if len(by_minute) < 20:
                continue

            ordered = sorted(by_minute)
            offsets = {
                offset
                for minute in ordered
                for offset in by_minute[minute]
            }
            for offset in offsets:
                path = [
                    float(by_minute[minute][offset])
                    for minute in ordered
                    if offset in by_minute[minute]
                ]
                if len(path) < 20:
                    continue
                magnitudes.extend(abs(value) for value in path)
                for lag in lags:
                    value = autocorrelation(path, lag)
                    if value is not None:
                        collected[lag].append(value)
                life = half_life(autocorrelation(path, 1))
                if life is not None:
                    half_lives.append(life)

    return {
        "minutes_fitted": minutes_fitted,
        "butterflies_examined": butterflies,
        "convexity_violations": violations,
        "convexity_violation_rate": (
            _round(violations / butterflies) if butterflies else None
        ),
        "median_absolute_residual": _round(median(magnitudes)) if magnitudes else None,
        "autocorrelation": {
            f"lag_{lag}": _round(median(values)) if values else None
            for lag, values in collected.items()
        },
        "median_half_life_minutes": _round(median(half_lives)) if half_lives else None,
        "paths": len(half_lives),
        "reading": _dynamics_reading(collected, half_lives),
    }


def _dynamics_reading(
    collected: dict[int, list[float]], half_lives: list[float]
) -> str:
    lag_one = median(collected[1]) if collected[1] else None
    if lag_one is None:
        return "Too few residual paths to say anything about dynamics."
    if lag_one <= 0:
        return (
            f"Median lag-1 autocorrelation is {lag_one:.3f}, at or below zero. The "
            "residual is not a persistent state that decays; it alternates, which is "
            "the signature of a measurement error that reverses on the next print "
            "rather than of a dislocation that closes. Nothing here is tradable, and "
            "no threshold fixes it."
        )
    life = median(half_lives) if half_lives else None
    lead = f"Median lag-1 autocorrelation is {lag_one:.3f}" + (
        f", a half-life of {life:.2f} minutes. " if life else ". "
    )
    if lag_one < 0.20:
        return lead + (
            "That is negligible persistence: the residual is very nearly white "
            "noise and has decayed before an order could reach the exchange. "
            "There is no dislocation here to trade, so any positive P&L the "
            "simulation reports must come from the prices used rather than from "
            "the signal, and the entry-delay control is what tells them apart."
        )
    return lead + (
        "A residual that decays over minutes rather than instantly is consistent "
        "with a dislocation that closes, which is what the trade simulation tests."
    )


# --------------------------------------------------------------------------
# Trade simulation
# --------------------------------------------------------------------------


def simulate_session(
    panel: SessionPanel, settings: SmileSettings
) -> list[Trade]:
    """Trade persistent residuals, at most `max_entries_per_session` times."""

    trades: list[Trade] = []
    for kind in ("C", "P"):
        by_minute, _, _ = residual_series(panel, kind, settings)
        if not by_minute:
            continue
        ordered = sorted(by_minute)

        for index, minute in enumerate(ordered):
            if len(trades) >= settings.max_entries_per_session:
                break
            if not _within_entry_window(minute, settings):
                continue

            candidate = _persistent_candidate(by_minute, ordered, index, settings)
            if candidate is None:
                continue
            offset, residual = candidate

            # The entry-delay control. The signal is read at `minute`, but the
            # fill happens `entry_delay_minutes` later. A real dislocation that
            # takes minutes to close is barely affected by a one-minute delay; an
            # edge that comes from a stale entry print disappears entirely, since
            # the stale price is exactly what the delay steps past.
            entry_index = index + settings.entry_delay_minutes
            if entry_index >= len(ordered):
                continue
            entry_minute = ordered[entry_index]

            entry = butterfly_values(panel, kind, entry_minute).get(offset)
            if entry is None or entry < settings.minimum_butterfly_value:
                continue

            exit_minute = _minute_after(ordered, entry_index, settings.hold_minutes)
            if exit_minute is None:
                continue
            exit_value = butterfly_values(panel, kind, exit_minute).get(offset)
            if exit_value is None:
                continue

            direction = "buy" if residual < 0 else "sell"
            risk = entry if direction == "buy" else (Decimal("1") - entry)
            if risk * HUNDRED > settings.maximum_risk_dollars:
                continue

            cost = (
                settings.cost_per_unit * UNITS_PER_BUTTERFLY * Decimal("2")
                + settings.fees_per_butterfly
            )
            gross = (exit_value - entry) if direction == "buy" else (entry - exit_value)
            trades.append(
                Trade(
                    trading_date=panel.trading_date,
                    minute=minute,
                    entry_minute=entry_minute,
                    kind=kind,
                    offset=offset,
                    center=panel.atm_strike + offset,
                    residual=residual,
                    direction=direction,
                    entry_value=entry,
                    exit_value=exit_value,
                    cost=cost.quantize(CENT),
                    pnl=(gross * HUNDRED - cost).quantize(CENT),
                )
            )
    return trades


def _within_entry_window(minute: str, settings: SmileSettings) -> bool:
    hour, minutes = (int(part) for part in minute.split(":"))
    moment = time(hour, minutes)
    return settings.entry_start <= moment <= settings.entry_end


def _persistent_candidate(
    by_minute: dict[str, dict[int, Decimal]],
    ordered: list[str],
    index: int,
    settings: SmileSettings,
) -> tuple[int, Decimal] | None:
    """The strongest residual that has held its sign and size long enough."""

    if index + 1 < settings.persist_minutes:
        return None

    window = ordered[index + 1 - settings.persist_minutes : index + 1]
    current = by_minute[ordered[index]]

    best: tuple[int, Decimal] | None = None
    for offset, residual in current.items():
        if abs(residual) < settings.residual_threshold:
            continue
        held = True
        for minute in window:
            earlier = by_minute.get(minute, {}).get(offset)
            if earlier is None or abs(earlier) < settings.residual_threshold:
                held = False
                break
            if (earlier > 0) != (residual > 0):
                held = False
                break
        if not held:
            continue
        if best is None or abs(residual) > abs(best[1]):
            best = (offset, residual)
    return best


def _minute_after(ordered: list[str], index: int, hold: int) -> str | None:
    """The first available minute at least `hold` minutes after the entry."""

    entry_hour, entry_minutes = (int(part) for part in ordered[index].split(":"))
    target = entry_hour * 60 + entry_minutes + hold
    for minute in ordered[index + 1 :]:
        hour, minutes = (int(part) for part in minute.split(":"))
        if hour * 60 + minutes >= target:
            return minute
    return None


# --------------------------------------------------------------------------
# Grading
# --------------------------------------------------------------------------


def grade(trades: list[Trade]) -> dict[str, object]:
    if not trades:
        return {"trades": 0}

    pnls = [trade.pnl for trade in trades]
    wins = [value for value in pnls if value > 0]
    losses = [value for value in pnls if value < 0]
    gains = sum(wins)
    pain = -sum(losses)

    equity = Decimal("0")
    peak = Decimal("0")
    drawdown = Decimal("0")
    for value in pnls:
        equity += value
        peak = max(peak, equity)
        drawdown = min(drawdown, equity - peak)

    return {
        "trades": len(trades),
        "sessions": len({trade.trading_date for trade in trades}),
        "total_pnl": str(sum(pnls)),
        "average_pnl": str((sum(pnls) / len(pnls)).quantize(CENT)),
        "win_rate": str((Decimal(len(wins)) / len(pnls)).quantize(Decimal("0.001"))),
        "profit_factor": str((gains / pain).quantize(Decimal("0.0001"))) if pain > 0 else "inf",
        "max_drawdown": str(drawdown),
        "calls": sum(1 for trade in trades if trade.kind == "C"),
        "puts": sum(1 for trade in trades if trade.kind == "P"),
        "buys": sum(1 for trade in trades if trade.direction == "buy"),
        "sells": sum(1 for trade in trades if trade.direction == "sell"),
    }


def _round(value: float) -> str:
    return f"{value:.4f}"


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------


def session_dates(cache_dir: Path) -> list[str]:
    """Every option session name in the cache, including sealed ones.

    Sealed names are listed so the report can state how many exist rather than
    silently ignoring them. `chronological_splits` then drops them, and their
    files are never opened.
    """

    stamps = []
    for path in sorted(cache_dir.glob("options-*.json")):
        stamp = path.stem.removeprefix("options-")
        try:
            date.fromisoformat(stamp)
        except ValueError:
            continue
        stamps.append(stamp)
    if not stamps:
        raise RuntimeError(f"no option sessions in {cache_dir}")
    return stamps


def load_panels(
    cache_dir: Path, keep: set[str], settings: SmileSettings
) -> list[SessionPanel]:
    underlying = load_underlying(cache_dir, settings.symbol)
    panels: list[SessionPanel] = []
    for stamp in sorted(keep):
        if date.fromisoformat(stamp) >= HOLDOUT_START:
            raise RuntimeError(f"refusing to read sealed session {stamp}")
        path = cache_dir / f"options-{stamp}.json"
        if not path.is_file():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        panel = build_panel(stamp, payload, underlying.get(stamp, []), settings)
        if panel is not None:
            panels.append(panel)
    return panels


PERSISTENCE_LADDER = (1, 3, 5)

# The control that a persistence filter cannot supply. Persistence rules out a
# dislocation that lasts a single bar, but not a series that lags consistently:
# a chronically stale leg keeps the residual dislocated for many minutes while
# never being tradable at the stale price. Delaying the fill by one or two
# minutes steps past exactly that price. A real dislocation that takes minutes to
# close barely notices the delay; an edge made of stale prints vanishes.
ENTRY_DELAY_LADDER = (0, 1, 2)


def chronological_splits(stamps: list[str]) -> dict[str, set[str]]:
    """Split the pre-holdout sessions 75/25 into train and validation.

    `research_splits` cannot be used with an explicit boundary here because it
    requires at least two held-out sessions to be present, and these caches stop
    at `2026-05-22` -- the sealed sessions were never downloaded at all. That is a
    stronger guarantee than declining to read them, so this reproduces the same
    75/25 convention over what exists rather than inventing a boundary.
    """

    available = sorted(stamp for stamp in stamps if date.fromisoformat(stamp) < HOLDOUT_START)
    if len(available) < 10:
        raise RuntimeError("at least 10 pre-holdout sessions are required")

    train_end = max(1, int(len(available) * 0.75))
    return {
        "train": set(available[:train_end]),
        "validation": set(available[train_end:]),
        "sealed_present_in_cache": {
            stamp for stamp in stamps if date.fromisoformat(stamp) >= HOLDOUT_START
        },
    }


def run_research(cache_dir: Path, settings: SmileSettings) -> dict[str, object]:
    stamps = session_dates(cache_dir)
    splits = chronological_splits(stamps)
    train = splits["train"]
    validation = splits["validation"]

    train_panels = load_panels(cache_dir, train, settings)
    validation_panels = load_panels(cache_dir, validation, settings)

    def run_variant(variant: SmileSettings) -> dict[str, object]:
        train_trades = [t for panel in train_panels for t in simulate_session(panel, variant)]
        validation_trades = [
            t for panel in validation_panels for t in simulate_session(panel, variant)
        ]
        stressed = replace(variant, cost_per_unit=variant.cost_per_unit * Decimal("2"))
        return {
            "train": grade(train_trades),
            "validation": grade(validation_trades),
            "train_double_cost": grade(
                [t for panel in train_panels for t in simulate_session(panel, stressed)]
            ),
            "validation_double_cost": grade(
                [t for panel in validation_panels for t in simulate_session(panel, stressed)]
            ),
            "bootstrap_train": moving_block_bootstrap([t.pnl for t in train_trades]),
        }

    ladder: dict[str, object] = {}
    for persist in PERSISTENCE_LADDER:
        ladder[f"persist_{persist}"] = run_variant(replace(settings, persist_minutes=persist))

    delays: dict[str, object] = {}
    for delay in ENTRY_DELAY_LADDER:
        delays[f"delay_{delay}"] = run_variant(replace(settings, entry_delay_minutes=delay))

    dynamics = residual_dynamics(train_panels, settings)
    baseline = ladder[f"persist_{settings.persist_minutes}"]

    return {
        "cache_dir": str(cache_dir),
        "acceptance_rule": ACCEPTANCE_RULE,
        "data_limitation": DATA_LIMITATION,
        "holdout_start": HOLDOUT_START.isoformat(),
        "sessions": {
            "train": len(train_panels),
            "validation": len(validation_panels),
            "sealed_present_in_cache": len(splits["sealed_present_in_cache"]),
        },
        "settings": {
            "residual_threshold": str(settings.residual_threshold),
            "persist_minutes": settings.persist_minutes,
            "entry_delay_minutes": settings.entry_delay_minutes,
            "hold_minutes": settings.hold_minutes,
            "cost_per_unit": str(settings.cost_per_unit),
            "minimum_offsets": settings.minimum_offsets,
        },
        "residual_dynamics": dynamics,
        "persistence_ladder": ladder,
        "entry_delay_ladder": delays,
        "verdict": _verdict(baseline, ladder, delays, dynamics),
    }


def _verdict(
    baseline: dict[str, object],
    ladder: dict[str, object],
    delays: dict[str, object],
    dynamics: dict[str, object],
) -> dict[str, object]:
    train = baseline.get("train", {})
    validation = baseline.get("validation", {})

    reasons: list[str] = []
    if int(train.get("trades", 0) or 0) < 150:
        reasons.append(f"only {train.get('trades', 0)} training trades, 150 required")
    if int(validation.get("trades", 0) or 0) < 40:
        reasons.append(f"only {validation.get('trades', 0)} validation trades, 40 required")
    for name, split in (("train", train), ("validation", validation)):
        if split.get("trades"):
            if Decimal(str(split["average_pnl"])) <= 0:
                reasons.append(f"{name} average P&L {split['average_pnl']} is not positive")
            factor = split["profit_factor"]
            if factor != "inf" and Decimal(str(factor)) < Decimal("1.25"):
                reasons.append(f"{name} profit factor {factor} below 1.25")

    # The entry-delay requirement. This is deliberately part of the pass/fail
    # rule and not a footnote: an edge that needs the signal minute's own print
    # to fill at is not an edge, because that print is already in the past by
    # the time an order could be sent.
    immediate = _average(delays, "delay_0")
    delayed = _average(delays, "delay_1")
    if immediate is not None and delayed is not None and immediate > 0 >= delayed:
        reasons.append(
            f"training average P&L {immediate} filling at the signal minute but "
            f"{delayed} filling one minute later, so the edge lives entirely in "
            "the entry print"
        )

    return {
        "passes": not reasons,
        "failures": reasons,
        "artifact_reading": _artifact_reading(ladder),
        "entry_delay_reading": _delay_reading(delays),
        "dynamics_reading": dynamics.get("reading"),
    }


def _average(ladder: dict[str, object], key: str) -> Decimal | None:
    split = ladder.get(key, {}).get("train", {})  # type: ignore[union-attr]
    if not split.get("trades"):
        return None
    return Decimal(str(split["average_pnl"]))


def _delay_reading(delays: dict[str, object]) -> str:
    """State plainly whether the edge survives not filling at the signal print."""

    immediate = _average(delays, "delay_0")
    one = _average(delays, "delay_1")
    two = _average(delays, "delay_2")
    if immediate is None:
        return "No trades at zero entry delay, so the control cannot be applied."
    parts = f"Training average P&L is {immediate} filling at the signal minute, "
    parts += f"{one} one minute later" if one is not None else "no trades one minute later"
    parts += f", {two} two minutes later. " if two is not None else ". "

    if immediate > 0 and (one is None or one <= 0):
        return parts + (
            "The entire result is the entry print. A one-minute delay is a trivial "
            "handicap for any dislocation that takes minutes to close, so an edge "
            "that dies under it was never reading the market -- it was reading a "
            "stale quote that the delay steps past. This is the decisive failure."
        )
    if immediate > 0 and one > 0:
        decay = (immediate - one) / immediate
        return parts + (
            f"The delay costs {decay:.0%} of the edge rather than all of it, so the "
            "signal is not purely an entry-print artifact."
        )
    return parts + "There is no edge to preserve at any entry delay."


def _artifact_reading(ladder: dict[str, object]) -> str:
    """Compare the one-minute rule against the three-minute rule.

    A strategy that only works without the persistence filter is measuring
    nonsynchronous prints, not the market.
    """

    loose = _average(ladder, "persist_1")
    tight = _average(ladder, "persist_3")
    if loose is None or tight is None:
        return "Not enough trades at one or three minutes of persistence to compare."
    if loose > 0 and tight <= 0:
        return (
            f"Training average P&L is {loose} with no persistence filter and {tight} "
            "once a dislocation must survive three minutes. The apparent edge is a "
            "single-minute print artifact, which is the failure mode this screen was "
            "built to detect."
        )
    if loose > 0 and tight > 0:
        return (
            f"Training average P&L holds from {loose} at one minute to {tight} at "
            "three, so the signal is not purely a single-bar artifact."
        )
    return f"No edge at either persistence setting: {loose} at one minute, {tight} at three."


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="floor-smile-residual-research",
        description="Cross-sectional smile-residual screen for SPY 0DTE butterflies.",
    )
    parser.add_argument(
        "--cache-dir", type=Path, default=Path("state/intraday-surface-butterfly-cache")
    )
    parser.add_argument("--residual-threshold", type=Decimal, default=Decimal("0.25"))
    parser.add_argument("--persist-minutes", type=int, default=3)
    parser.add_argument("--entry-delay-minutes", type=int, default=0)
    parser.add_argument("--hold-minutes", type=int, default=30)
    parser.add_argument("--cost-per-unit", type=Decimal, default=MEASURED_HALF_SPREAD)
    parser.add_argument("--minimum-offsets", type=int, default=7)
    parser.add_argument(
        "--report", type=Path, default=Path("state/smile-residual-report.json")
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = SmileSettings(
        residual_threshold=args.residual_threshold,
        persist_minutes=args.persist_minutes,
        entry_delay_minutes=args.entry_delay_minutes,
        hold_minutes=args.hold_minutes,
        cost_per_unit=args.cost_per_unit,
        minimum_offsets=args.minimum_offsets,
    )

    try:
        report = run_research(args.cache_dir, settings)
    except RuntimeError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    args.report.chmod(0o600)

    print(json.dumps(report["residual_dynamics"], indent=2))
    print(json.dumps(report["entry_delay_ladder"], indent=2))
    print(json.dumps(report["verdict"], indent=2))
    print(f"\nfull report: {args.report}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "ACCEPTANCE_RULE",
    "SessionPanel",
    "SmileSettings",
    "Trade",
    "autocorrelation",
    "build_panel",
    "butterfly_values",
    "fit_log_parabola",
    "grade",
    "half_life",
    "main",
    "residual_dynamics",
    "residual_series",
    "run_research",
    "simulate_session",
]
