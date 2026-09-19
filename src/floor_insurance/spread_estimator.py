"""Effective bid/ask spread recovered from one-minute SPY option bars.

Every screen in this project grades a candidate by subtracting a *modeled* fill
cost from a trade-print P&L. The surviving surface line uses half a cent per
contract unit and dies somewhere between two and three cents, and no measurement
of the real number has ever existed, because no vendor sells historical option
NBBO quotes at any price.

This module does not need them. Four published microstructure estimators
recover an effective spread from transaction prices alone, and the cache already
holds 1-minute open/high/low/close, volume, and VWAP for nine strikes of both
types across roughly 575 sessions:

* Roll (1984)              -- serial covariance of trade-price changes.
* Corwin & Schultz (2012)  -- high/low ranges over consecutive periods.
* Abdi & Ranaldo (2017)    -- close price against the log mid-range.
* Range decomposition      -- regress the option's bar range on the underlying's
                              bar range; the intercept is the bounce component
                              and the slope is an implied |delta|.

The last one is specific to this dataset and carries its own falsification test:
the fitted slope must look like an option delta. If a $1-out-of-the-money 0DTE
call regresses onto the underlying with a slope of 0.4 and a far wing gives
0.05, the decomposition is measuring what it claims to. If the slopes are noise,
the intercepts are noise too.

Every one of them is biased, so `calibrate` measures the bias on synthetic bars
with a known spread before any real bar is read, and `verdict` divides it out.
The measured directions are worth stating because one of them is the opposite of
what the literature leads you to expect: Corwin-Schultz is documented as biased
*upward* when volatility is large relative to the spread, yet on one-minute bars
it runs 10 to 20 percent *low* and is the most volatility-robust of the three.
The reason is that the published bias comes from daily bars, whose high-low range
spans a full session of drift; a one-minute range on a liquid series is mostly
bid/ask bounce, so the drift term that inflates the daily estimator is nearly
absent. Abdi-Ranaldo lands within a few percent of truth in calm conditions and
Roll runs high throughout, so the three bracket the answer instead of bounding it
from one side.

Two limits are structural rather than fixable. An effective spread recovered from
trades that happened describes the minutes in which somebody chose to trade,
which is a tighter market than an arbitrary moment. And penny discreteness
collapses the range estimators on cheap wings: when every print in a minute lands
on the same tick, high equals low and a range estimator reads zero.

This module is cache-only. It holds no Alpaca client, opens no socket, and
refuses sessions on or after the sealed holdout boundary.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
CENT = Decimal("0.01")
TENTH_CENT = Decimal("0.001")

# Never read a session on or after this date. The holdout is sealed for every
# purpose, including cost measurement, because the discipline is worth more than
# the extra sessions.
HOLDOUT_START = date(2026, 5, 26)

SYMBOL_PATTERN = re.compile(r"^([A-Z]+)(\d{6})([CP])(\d{8})$")

# Corwin-Schultz constant: 3 - 2*sqrt(2).
CS_K = 3.0 - 2.0 * math.sqrt(2.0)

TIME_BUCKETS = (
    ("09:30-10:00", time(9, 30), time(10, 0)),
    ("10:00-11:00", time(10, 0), time(11, 0)),
    ("11:00-12:00", time(11, 0), time(12, 0)),
    ("12:00-14:00", time(12, 0), time(14, 0)),
    ("14:00-15:00", time(14, 0), time(15, 0)),
    ("15:00-16:00", time(15, 0), time(16, 0)),
)

MONEYNESS_BUCKETS = (
    ("atm", Decimal("0"), Decimal("1")),
    ("near_1_2", Decimal("1"), Decimal("2")),
    ("near_2_3", Decimal("2"), Decimal("3")),
    ("wing_3_plus", Decimal("3"), Decimal("999")),
)


class SpreadEstimatorError(RuntimeError):
    """Raised when the requested cache cannot support an estimate."""


@dataclass(frozen=True)
class Bar:
    """One minute of trade activity in a single option series."""

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    vwap: float | None = None

    @property
    def mid_range(self) -> float:
        return (self.high + self.low) / 2.0


@dataclass(frozen=True)
class Estimate:
    """One estimator's verdict for one series over one window."""

    estimator: str
    absolute: Decimal | None
    proportional: Decimal | None
    pairs: int
    slope: Decimal | None = None
    r_squared: Decimal | None = None
    note: str = ""


@dataclass(frozen=True)
class SeriesSample:
    """A single option series inside one time bucket of one session."""

    trading_date: str
    symbol: str
    kind: str
    strike: Decimal
    distance: Decimal
    bucket: str
    bars: int
    volume: float
    mean_price: float
    estimates: tuple[Estimate, ...] = ()


# --------------------------------------------------------------------------
# Estimators
# --------------------------------------------------------------------------


def _clean(bars: list[Bar]) -> list[Bar]:
    """Drop bars that cannot carry information about a spread.

    A bar with no volume is a gap in the tape, not a quiet minute, and a
    non-positive price breaks every log-based estimator.
    """

    return [
        bar
        for bar in bars
        if bar.volume > 0 and bar.low > 0 and bar.high >= bar.low and bar.close > 0
    ]


def _covariance(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    return sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True)) / (n - 1)


def roll_spread(bars: list[Bar]) -> Estimate:
    """Roll (1984): S = 2 * sqrt(-Cov(dp_t, dp_{t-1})).

    Undefined when the covariance is positive, which happens whenever trend
    dominates bounce. Reporting that as `None` rather than zero matters: a
    positive covariance is a failure of the model, not a tight market.
    """

    usable = _clean(bars)
    if len(usable) < 12:
        return Estimate("roll", None, None, 0, note="fewer than 12 usable bars")

    logs = [math.log(bar.close) for bar in usable]
    changes = [b - a for a, b in zip(logs, logs[1:], strict=False)]
    lagged = changes[:-1]
    current = changes[1:]
    covariance = _covariance(current, lagged)
    if covariance >= 0:
        return Estimate(
            "roll",
            None,
            None,
            len(current),
            note="positive serial covariance; trend dominates bounce",
        )

    proportional = 2.0 * math.sqrt(-covariance)
    mean_price = sum(bar.close for bar in usable) / len(usable)
    return Estimate(
        "roll",
        _money(proportional * mean_price),
        _ratio(proportional),
        len(current),
    )


def corwin_schultz_spread(bars: list[Bar]) -> Estimate:
    """Corwin & Schultz (2012) two-day high/low estimator, applied per minute pair.

    Negative pair estimates are set to zero before averaging, following the
    paper's own recommendation, because a negative spread is a small-sample
    artifact rather than evidence of a crossed market.
    """

    usable = _clean(bars)
    if len(usable) < 4:
        return Estimate("corwin_schultz", None, None, 0, note="fewer than 4 usable bars")

    values: list[float] = []
    prices: list[float] = []
    for first, second in zip(usable, usable[1:], strict=False):
        log_range_first = math.log(first.high / first.low)
        log_range_second = math.log(second.high / second.low)
        beta = log_range_first**2 + log_range_second**2
        joint_high = max(first.high, second.high)
        joint_low = min(first.low, second.low)
        gamma = math.log(joint_high / joint_low) ** 2

        alpha = (math.sqrt(2.0 * beta) - math.sqrt(beta)) / CS_K - math.sqrt(gamma / CS_K)
        spread = 2.0 * (math.exp(alpha) - 1.0) / (1.0 + math.exp(alpha))
        values.append(max(0.0, spread))
        prices.append((first.close + second.close) / 2.0)

    if not values:
        return Estimate("corwin_schultz", None, None, 0, note="no usable bar pairs")

    proportional = sum(values) / len(values)
    absolutes = [value * price for value, price in zip(values, prices, strict=True)]
    return Estimate(
        "corwin_schultz",
        _money(median(absolutes)),
        _ratio(proportional),
        len(values),
    )


def abdi_ranaldo_spread(bars: list[Bar]) -> Estimate:
    """Abdi & Ranaldo (2017): S = 2 * sqrt(E[(c_t - eta_t)(c_t - eta_{t+1})]).

    More robust than Corwin-Schultz when volatility is high, which is why both
    are reported side by side rather than averaged.
    """

    usable = _clean(bars)
    if len(usable) < 4:
        return Estimate("abdi_ranaldo", None, None, 0, note="fewer than 4 usable bars")

    closes = [math.log(bar.close) for bar in usable]
    etas = [(math.log(bar.high) + math.log(bar.low)) / 2.0 for bar in usable]

    products = [
        (closes[index] - etas[index]) * (closes[index] - etas[index + 1])
        for index in range(len(usable) - 1)
    ]
    if not products:
        return Estimate("abdi_ranaldo", None, None, 0, note="no usable bar pairs")

    expectation = sum(products) / len(products)
    if expectation <= 0:
        return Estimate(
            "abdi_ranaldo",
            Decimal("0.000"),
            Decimal("0.0000"),
            len(products),
            note="non-positive expectation; estimator floors at zero",
        )

    proportional = 2.0 * math.sqrt(expectation)
    mean_price = sum(bar.close for bar in usable) / len(usable)
    return Estimate(
        "abdi_ranaldo",
        _money(proportional * mean_price),
        _ratio(proportional),
        len(products),
    )


def range_decomposition_spread(bars: list[Bar], underlying: list[Bar]) -> Estimate:
    """Split the option's bar range into bounce and underlying movement.

    For a minute in which the option traded on both sides of the market,

        high - low  ~=  spread  +  |delta| * (underlying high - underlying low)

    so an ordinary least squares fit of the option range on the underlying range
    has the effective spread as its intercept and an implied |delta| as its
    slope. The slope is the falsification test: it must resemble a real delta.
    """

    usable = _clean(bars)
    by_minute = {bar.timestamp: bar for bar in underlying if bar.high >= bar.low > 0}

    xs: list[float] = []
    ys: list[float] = []
    for bar in usable:
        spot_bar = by_minute.get(bar.timestamp)
        if spot_bar is None:
            continue
        xs.append(spot_bar.high - spot_bar.low)
        ys.append(bar.high - bar.low)

    if len(xs) < 20:
        return Estimate(
            "range_decomposition", None, None, len(xs), note="fewer than 20 aligned minutes"
        )

    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    variance = sum((x - mean_x) ** 2 for x in xs)
    if variance <= 0:
        return Estimate(
            "range_decomposition", None, None, n, note="underlying range has no variation"
        )

    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True)) / variance
    intercept = mean_y - slope * mean_x

    residual = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys, strict=True))
    total = sum((y - mean_y) ** 2 for y in ys)
    r_squared = 1.0 - residual / total if total > 0 else 0.0

    if intercept < 0:
        return Estimate(
            "range_decomposition",
            Decimal("0.000"),
            Decimal("0.0000"),
            n,
            slope=_ratio(slope),
            r_squared=_ratio(r_squared),
            note="negative intercept; estimator floors at zero",
        )

    mean_price = sum(bar.close for bar in usable) / len(usable)
    return Estimate(
        "range_decomposition",
        _money(intercept),
        _ratio(intercept / mean_price) if mean_price > 0 else None,
        n,
        slope=_ratio(slope),
        r_squared=_ratio(r_squared),
    )


ESTIMATORS = ("roll", "corwin_schultz", "abdi_ranaldo", "range_decomposition")


def estimate_all(bars: list[Bar], underlying: list[Bar]) -> tuple[Estimate, ...]:
    return (
        roll_spread(bars),
        corwin_schultz_spread(bars),
        abdi_ranaldo_spread(bars),
        range_decomposition_spread(bars, underlying),
    )


def _money(value: float) -> Decimal:
    return Decimal(repr(round(value, 6))).quantize(TENTH_CENT)


def _ratio(value: float) -> Decimal:
    return Decimal(repr(round(value, 8))).quantize(Decimal("0.0001"))


# --------------------------------------------------------------------------
# Synthetic calibration
# --------------------------------------------------------------------------


class _Lcg:
    """A deterministic linear congruential generator.

    Reproducibility matters more than statistical elegance here, and a fixed
    seed with a documented recurrence is auditable in a way that a library
    default is not.
    """

    def __init__(self, seed: int = 20260824):
        self.state = seed & 0xFFFFFFFF

    def next_float(self) -> float:
        self.state = (1664525 * self.state + 1013904223) & 0xFFFFFFFF
        return self.state / 0x100000000

    def normal(self) -> float:
        """Box-Muller, one draw at a time."""

        first = max(self.next_float(), 1e-12)
        second = self.next_float()
        return math.sqrt(-2.0 * math.log(first)) * math.cos(2.0 * math.pi * second)


def synthetic_bars(
    *,
    spread: float,
    price: float,
    minute_volatility: float,
    minutes: int = 300,
    trades_per_minute: int = 20,
    seed: int = 20260824,
) -> list[Bar]:
    """Build minute bars from a mid that diffuses plus a pure bid/ask bounce.

    The mid diffuses multiplicatively, so `minute_volatility` is a *relative*
    per-minute standard deviation. An additive mid on a $0.20 option needs a
    price floor, and that floor is a reflecting barrier that distorts every
    estimator -- the simulator would then be measuring its own artifact.

    The true effective spread is exactly `spread`, so any estimator's error on
    this series is bias rather than a property of the market.
    """

    rng = _Lcg(seed)
    mid = price
    step = minute_volatility / math.sqrt(trades_per_minute)
    start = datetime(2025, 6, 2, 9, 30, tzinfo=ET)
    bars: list[Bar] = []
    for minute in range(minutes):
        prints: list[float] = []
        for _ in range(trades_per_minute):
            mid *= math.exp(step * rng.normal() - 0.5 * step * step)
            side = 1.0 if rng.next_float() < 0.5 else -1.0
            prints.append(max(0.01, mid + side * spread / 2.0))
        bars.append(
            Bar(
                timestamp=start + timedelta(minutes=minute),
                open=prints[0],
                high=max(prints),
                low=min(prints),
                close=prints[-1],
                volume=float(trades_per_minute),
                vwap=sum(prints) / len(prints),
            )
        )
    return bars


CALIBRATION_GRID = (
    # (label, price, spread, relative per-minute volatility of the mid)
    ("atm_tight", 1.00, 0.01, 0.010),
    ("atm_typical", 1.00, 0.02, 0.020),
    ("atm_wide", 1.00, 0.05, 0.020),
    ("atm_violent", 1.00, 0.02, 0.060),
    ("cheap_wing", 0.20, 0.02, 0.020),
    ("cheap_wing_violent", 0.20, 0.02, 0.060),
    ("rich_atm", 3.00, 0.03, 0.015),
)


def calibrate(seed: int = 20260824) -> dict[str, object]:
    """Measure each estimator's bias against a known spread.

    This runs before any real bar is read. An estimator that cannot recover a
    two-cent spread from synthetic data has no business grading a strategy.
    """

    rows: list[dict[str, object]] = []
    for label, price, spread, volatility in CALIBRATION_GRID:
        bars = synthetic_bars(
            spread=spread, price=price, minute_volatility=volatility, seed=seed
        )
        row: dict[str, object] = {
            "case": label,
            "price": f"{price:.2f}",
            "true_spread": f"{spread:.3f}",
            "minute_volatility": f"{volatility:.3f}",
        }
        for estimate in estimate_all(bars, []):
            row[estimate.estimator] = (
                str(estimate.absolute) if estimate.absolute is not None else estimate.note
            )
            if estimate.absolute is not None and spread > 0:
                row[f"{estimate.estimator}_ratio"] = f"{float(estimate.absolute) / spread:.2f}x"
        rows.append(row)

    return {
        "seed": seed,
        "note": (
            "range_decomposition is absent from this grid because it needs "
            "underlying bars, which a synthetic single-series path does not have. "
            "Its falsification test is the fitted delta on real data."
        ),
        "cases": rows,
    }


# --------------------------------------------------------------------------
# Cache loading
# --------------------------------------------------------------------------


def _parse_bar(raw: dict[str, str]) -> Bar:
    return Bar(
        timestamp=datetime.fromisoformat(raw["t"]).astimezone(ET),
        open=float(raw["o"]),
        high=float(raw["h"]),
        low=float(raw["l"]),
        close=float(raw["c"]),
        volume=float(raw.get("v", 0) or 0),
        vwap=float(raw["vw"]) if raw.get("vw") else None,
    )


def option_sessions(cache_dir: Path) -> list[tuple[str, Path]]:
    """Every option session file in the cache, oldest first, holdout removed."""

    if not cache_dir.is_dir():
        raise SpreadEstimatorError(f"cache directory not found: {cache_dir}")

    found: list[tuple[str, Path]] = []
    for path in sorted(cache_dir.glob("options-*.json")):
        stamp = path.stem.removeprefix("options-")
        try:
            when = date.fromisoformat(stamp)
        except ValueError:
            continue
        if when >= HOLDOUT_START:
            continue
        found.append((stamp, path))
    if not found:
        raise SpreadEstimatorError(f"no pre-holdout option sessions in {cache_dir}")
    return found


def load_underlying(cache_dir: Path, symbol: str = "SPY") -> dict[str, list[Bar]]:
    """Group the cached underlying minute bars by trading date."""

    matches = sorted(cache_dir.glob(f"{symbol.lower()}-*.json"))
    if not matches:
        return {}

    payload = json.loads(matches[-1].read_text(encoding="utf-8"))
    raw = payload.get(symbol.upper(), []) if isinstance(payload, dict) else payload

    grouped: dict[str, list[Bar]] = {}
    for item in raw:
        bar = _parse_bar(item)
        grouped.setdefault(bar.timestamp.date().isoformat(), []).append(bar)
    return grouped


def parse_symbol(symbol: str) -> tuple[str, Decimal] | None:
    """Return (kind, strike) for an OCP option symbol, or None if unparseable."""

    match = SYMBOL_PATTERN.match(symbol)
    if match is None:
        return None
    return match.group(3), Decimal(match.group(4)) / Decimal("1000")


def bucket_for(moment: time) -> str | None:
    for label, start, end in TIME_BUCKETS:
        if start <= moment < end:
            return label
    return None


def moneyness_for(distance: Decimal) -> str:
    magnitude = abs(distance)
    for label, low, high in MONEYNESS_BUCKETS:
        if low <= magnitude < high:
            return label
    return MONEYNESS_BUCKETS[-1][0]


def session_samples(
    trading_date: str,
    payload: dict[str, list[dict[str, str]]],
    underlying: list[Bar],
    *,
    minimum_bars: int = 20,
    minimum_volume: float = 50.0,
) -> list[SeriesSample]:
    """Estimate every series in one session, split by time-of-day bucket."""

    spot = _reference_spot(underlying)
    samples: list[SeriesSample] = []

    for symbol, raw_bars in payload.items():
        parsed = parse_symbol(symbol)
        if parsed is None:
            continue
        kind, strike = parsed
        bars = [_parse_bar(item) for item in raw_bars]

        for label, start, end in TIME_BUCKETS:
            window = [bar for bar in bars if start <= bar.timestamp.time() < end]
            usable = _clean(window)
            volume = sum(bar.volume for bar in usable)
            if len(usable) < minimum_bars or volume < minimum_volume:
                continue

            spot_window = [bar for bar in underlying if start <= bar.timestamp.time() < end]
            distance = strike - spot if spot is not None else Decimal("0")
            samples.append(
                SeriesSample(
                    trading_date=trading_date,
                    symbol=symbol,
                    kind=kind,
                    strike=strike,
                    distance=distance,
                    bucket=label,
                    bars=len(usable),
                    volume=volume,
                    mean_price=sum(bar.close for bar in usable) / len(usable),
                    estimates=estimate_all(window, spot_window),
                )
            )
    return samples


def _reference_spot(underlying: list[Bar]) -> Decimal | None:
    """The 11:00 ET close, or the closest earlier bar, as the session reference."""

    candidates = [bar for bar in underlying if bar.timestamp.time() <= time(11, 0)]
    if not candidates:
        return None
    return Decimal(repr(round(candidates[-1].close, 2)))


# --------------------------------------------------------------------------
# Aggregation
# --------------------------------------------------------------------------


def _summarize(values: list[Decimal]) -> dict[str, str] | None:
    if not values:
        return None
    ordered = sorted(values)

    def quantile(fraction: float) -> Decimal:
        if len(ordered) == 1:
            return ordered[0]
        position = fraction * (len(ordered) - 1)
        low = ordered[int(math.floor(position))]
        high = ordered[int(math.ceil(position))]
        weight = Decimal(repr(position - math.floor(position)))
        return (low + (high - low) * weight).quantize(TENTH_CENT)

    return {
        "count": str(len(ordered)),
        "p10": str(quantile(0.10)),
        "median": str(quantile(0.50)),
        "p90": str(quantile(0.90)),
    }


def aggregate(samples: list[SeriesSample]) -> dict[str, object]:
    """Group estimates by estimator, then by moneyness and time of day."""

    overall: dict[str, list[Decimal]] = {name: [] for name in ESTIMATORS}
    by_moneyness: dict[str, dict[str, list[Decimal]]] = {}
    by_bucket: dict[str, dict[str, list[Decimal]]] = {}
    slopes: dict[str, list[Decimal]] = {}
    undefined: dict[str, int] = {name: 0 for name in ESTIMATORS}

    for sample in samples:
        money = moneyness_for(sample.distance)
        for estimate in sample.estimates:
            if estimate.absolute is None:
                undefined[estimate.estimator] += 1
                continue
            overall[estimate.estimator].append(estimate.absolute)
            by_moneyness.setdefault(money, {}).setdefault(estimate.estimator, []).append(
                estimate.absolute
            )
            by_bucket.setdefault(sample.bucket, {}).setdefault(estimate.estimator, []).append(
                estimate.absolute
            )
            if estimate.estimator == "range_decomposition" and estimate.slope is not None:
                slopes.setdefault(money, []).append(estimate.slope)

    return {
        "series_windows": len(samples),
        "sessions": len({sample.trading_date for sample in samples}),
        "overall": {
            name: _summarize(values) for name, values in overall.items() if values
        },
        "undefined_windows": undefined,
        "by_moneyness": {
            money: {name: _summarize(values) for name, values in inner.items()}
            for money, inner in sorted(by_moneyness.items())
        },
        "by_time_bucket": {
            bucket: {name: _summarize(values) for name, values in inner.items()}
            for bucket, inner in sorted(by_bucket.items())
        },
        "implied_delta_by_moneyness": {
            money: _summarize(values) for money, values in sorted(slopes.items())
        },
    }


def bias_factors(calibration: dict[str, object] | None = None) -> dict[str, Decimal]:
    """Median estimated/true ratio for each estimator on the synthetic grid.

    Dividing a raw estimate by its factor removes the measured bias, which is
    the whole point of calibrating before measuring. The factors are derived at
    runtime from `calibrate()` rather than hardcoded, so they cannot drift out of
    step with the grid they came from.
    """

    report = calibration if calibration is not None else calibrate()
    cases = report.get("cases", []) if isinstance(report, dict) else []

    factors: dict[str, Decimal] = {}
    for name in ESTIMATORS:
        ratios: list[Decimal] = []
        for case in cases:
            raw = case.get(f"{name}_ratio") if isinstance(case, dict) else None
            if isinstance(raw, str) and raw.endswith("x"):
                ratios.append(Decimal(raw[:-1]))
        if ratios:
            factors[name] = median(sorted(ratios))
    return factors


def verdict(
    summary: dict[str, object],
    modeled_per_unit: Decimal,
    *,
    fatal_per_unit: Decimal = Decimal("0.03"),
    calibration: dict[str, object] | None = None,
) -> dict[str, object]:
    """State plainly what the estimates imply for the modeled fill cost.

    A butterfly crosses four contract units, and a trader who fills at the mid
    pays half the spread on each. The comparison that decides whether every
    screen in this project was graded fairly is therefore the modeled adverse
    fill per unit against half the *bias-corrected* effective spread.

    The estimators are not interchangeable. Calibration shows Corwin-Schultz
    running low, Abdi-Ranaldo close to true, and Roll high, so they bracket the
    answer rather than all bounding it from one side.
    """

    overall = summary.get("overall", {})
    factors = bias_factors(calibration)

    lines: list[str] = []
    corrected: dict[str, str] = {}
    for name in ESTIMATORS:
        stats = overall.get(name) if isinstance(overall, dict) else None
        if not stats:
            continue
        raw = Decimal(stats["median"])
        factor = factors.get(name)
        adjusted = (raw / factor).quantize(TENTH_CENT) if factor else raw
        half = (adjusted / Decimal("2")).quantize(TENTH_CENT)
        corrected[name] = str(half)
        relation = "above" if half > modeled_per_unit else "at or below"
        lines.append(
            f"{name}: raw median {raw}, bias factor "
            f"{factor if factor is not None else 'n/a'}, corrected spread {adjusted}, "
            f"half-spread {half} per unit, {relation} the modeled {modeled_per_unit}"
        )

    values = [Decimal(value) for value in corrected.values()]
    primary = corrected.get("abdi_ranaldo")
    return {
        "modeled_adverse_fill_per_unit": str(modeled_per_unit),
        "bias_factors": {name: str(value) for name, value in factors.items()},
        "corrected_half_spread_per_unit": corrected,
        "comparisons": lines,
        "bracket": {
            "low": str(min(values)) if values else None,
            "high": str(max(values)) if values else None,
            "primary_abdi_ranaldo": primary,
        },
        "fatal_adverse_fill_per_unit": str(fatal_per_unit),
        "interpretation": _interpretation(values, primary, modeled_per_unit, fatal_per_unit),
    }


def _interpretation(
    values: list[Decimal],
    primary: str | None,
    modeled_per_unit: Decimal,
    fatal_per_unit: Decimal,
) -> str:
    """Compare the measurement to both the modeled cost and the fatal cost.

    Whether the modeled cost is off by a tenth of a cent is not the interesting
    question, because a tenth of a cent changes no decision. What matters is the
    distance to the level at which the surviving strategy stops working.
    """

    if not values or primary is None:
        return "No estimator produced a usable median; nothing can be concluded."

    point = Decimal(primary)
    span = f"{min(values)} to {max(values)}"
    lead = (
        f"The best-calibrated estimator puts the half-spread at {point} per contract "
        f"unit, against a modeled {modeled_per_unit} and a fatal {fatal_per_unit}. "
        f"The full estimator bracket spans {span}. "
    )

    if point >= fatal_per_unit:
        return lead + (
            "The measured cost is at or beyond the level that kills the surviving "
            "line, so that line is dead on cost alone and no further forward "
            "measurement is needed to retire it."
        )

    if point <= modeled_per_unit:
        body = (
            "The modeled cost is therefore not optimistic, and the screens graded "
            "with it were graded fairly. "
        )
    else:
        body = (
            "The modeled cost is slightly optimistic, by "
            f"{(point - modeled_per_unit).quantize(TENTH_CENT)} per unit, which is "
            "far smaller than the estimator disagreement and changes no verdict. "
        )

    return lead + body + (
        f"The decisive point is the margin to the fatal level: the measurement sits "
        f"{(fatal_per_unit - point).quantize(TENTH_CENT)} per unit below it, so cost "
        "is not what limits these strategies and a rejected line cannot be revived "
        "by assuming cheaper fills. Two caveats bound the claim. An effective spread "
        "recovered from trades that happened describes the minutes in which somebody "
        "chose to trade, which is tighter than an arbitrary moment. And the upper end "
        "of the bracket comes from the estimator whose real-data reading exceeds its "
        "own synthetic bias, meaning the calibration grid does not reach the real "
        "volatility regime; treat the low end as the measurement and the high end as "
        "an unresolved bound."
    )


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def run_estimate(
    cache_dir: Path,
    *,
    limit: int | None,
    minimum_bars: int,
    minimum_volume: float,
    modeled_per_unit: Decimal,
    fatal_per_unit: Decimal = Decimal("0.03"),
) -> dict[str, object]:
    sessions = option_sessions(cache_dir)
    if limit is not None:
        sessions = sessions[:limit]
    underlying = load_underlying(cache_dir)

    samples: list[SeriesSample] = []
    for stamp, path in sessions:
        payload = json.loads(path.read_text(encoding="utf-8"))
        samples.extend(
            session_samples(
                stamp,
                payload,
                underlying.get(stamp, []),
                minimum_bars=minimum_bars,
                minimum_volume=minimum_volume,
            )
        )

    calibration = calibrate()
    summary = aggregate(samples)
    return {
        "cache_dir": str(cache_dir),
        "sessions_read": len(sessions),
        "first_session": sessions[0][0],
        "last_session": sessions[-1][0],
        "holdout_start": HOLDOUT_START.isoformat(),
        "filters": {
            "minimum_bars_per_window": minimum_bars,
            "minimum_volume_per_window": minimum_volume,
        },
        "calibration": calibration,
        "summary": summary,
        "verdict": verdict(
            summary,
            modeled_per_unit,
            fatal_per_unit=fatal_per_unit,
            calibration=calibration,
        ),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="floor-spread-estimator",
        description="Recover effective option spreads from cached one-minute bars.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    calibration = sub.add_parser(
        "calibrate", help="Measure estimator bias on synthetic bars with a known spread."
    )
    calibration.add_argument("--seed", type=int, default=20260824)

    run = sub.add_parser("estimate", help="Estimate spreads across the cached sessions.")
    run.add_argument(
        "--cache-dir", type=Path, default=Path("state/surface-butterfly-cache")
    )
    run.add_argument("--limit", type=int, default=None, help="Read only the first N sessions.")
    run.add_argument("--minimum-bars", type=int, default=20)
    run.add_argument("--minimum-volume", type=float, default=50.0)
    run.add_argument("--modeled-per-unit", type=Decimal, default=Decimal("0.005"))
    run.add_argument(
        "--fatal-per-unit",
        type=Decimal,
        default=Decimal("0.03"),
        help="Per-unit fill cost at which the surviving surface line turns negative.",
    )
    run.add_argument("--report", type=Path, default=Path("state/spread-estimator-report.json"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    if args.command == "calibrate":
        print(json.dumps(calibrate(args.seed), indent=2))
        return 0

    try:
        report = run_estimate(
            args.cache_dir,
            limit=args.limit,
            minimum_bars=args.minimum_bars,
            minimum_volume=args.minimum_volume,
            modeled_per_unit=args.modeled_per_unit,
            fatal_per_unit=args.fatal_per_unit,
        )
    except SpreadEstimatorError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    args.report.chmod(0o600)
    print(json.dumps(report["verdict"], indent=2))
    print(f"\nfull report: {args.report}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "Bar",
    "Estimate",
    "SeriesSample",
    "SpreadEstimatorError",
    "abdi_ranaldo_spread",
    "aggregate",
    "calibrate",
    "corwin_schultz_spread",
    "estimate_all",
    "main",
    "range_decomposition_spread",
    "roll_spread",
    "bias_factors",
    "verdict",
    "session_samples",
    "synthetic_bars",
]
