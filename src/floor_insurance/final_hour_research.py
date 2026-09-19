"""Stage 0 upper bound for the preregistered SPY 0DTE final-hour settlement screen.

The protocol is [docs/FINAL_HOUR_SETTLEMENT_RESEARCH.md](../../docs/FINAL_HOUR_SETTLEMENT_RESEARCH.md)
and it was committed before this module existed. Nothing here may add a
structure, move a time, or relax a gate.

Nineteen closed lines in this project share two mechanisms: each selected a trade
by comparing option prints, and each closed the trade at an option mark. The
[smile residual screen](../../docs/SMILE_RESIDUAL_RESEARCH.md) showed that a
selection rule reading one-minute prints selects *for* chronic staleness, and the
[surface butterfly](../../docs/SURFACE_BUTTERFLY_RESEARCH.md) lost its entire
edge to a one-minute fill delay. This screen removes both mechanisms instead of
filtering them:

* **No selection rule.** Entry is unconditional on every session where the legs
  exist. A rule that never chooses cannot cherry-pick a stale print.
* **No exit mark.** A 0DTE payoff at expiration is a deterministic function of
  the settlement price, so the only option price read is the entry. That also
  halves the friction: four contract units are transacted, not eight.
* **VWAP, not the opening print.** The 15:00 one-minute volume-weighted price is
  far harder for one stale print to move than the opening print that decided the
  last two lines.

Stage 0 is the cheap half. It charges no friction at all and asks only whether
any of the six preregistered structures clears three times its own one-way cost
on training. The [ATM trend research](../../docs/ATM_TREND_RESEARCH.md) was
explained entirely by a zero-cost upper bound smaller than its costs, and that
was discovered only after a full simulator had been written. This gate is that
discovery made cheap.

Cache-only. No client, no socket. Session files on or after the sealed boundary
are excluded before any file is opened, and Stage 0 additionally never reads a
session after the training boundary.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import time
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from .smile_residual_research import MEASURED_HALF_SPREAD
from .spread_estimator import (
    HOLDOUT_START,
    Bar,
    SpreadEstimatorError,
    _parse_bar,
    load_underlying,
    option_sessions,
)

ROOT = "SPY"
CENT = Decimal("0.01")
HUNDRED = Decimal("100")

#: Signal minute. The structure is priced from these bars.
ENTRY_TIME = time(15, 0)

#: Strict entry delay used by Stage 1. The cached option bars end at 15:01, so
#: this is the only delayed fill this data can express -- and it is enough,
#: because it is strictly after the minute the structure was priced from.
DELAYED_ENTRY_TIME = time(15, 1)

#: Settlement reference. The underlying's last regular-session minute.
SETTLEMENT_TIME = time(15, 59)

#: Four legs at entry only. Prior lines paid this twice because they exited at a
#: mark; settlement is free.
LEGS_PER_STRUCTURE = Decimal("4")
FEES_PER_STRUCTURE = Decimal("0.20")
ONE_WAY_COST = LEGS_PER_STRUCTURE * MEASURED_HALF_SPREAD * HUNDRED + FEES_PER_STRUCTURE

#: Stage 0 threshold: three times the one-way cost, per the locked protocol.
STAGE_ZERO_THRESHOLD = (ONE_WAY_COST * Decimal("3")).quantize(CENT)

#: One-contract maximum loss ceiling for this account.
MAX_LOSS_LIMIT = Decimal("100")

TRAINING_FRACTION = Decimal("0.75")

STAGE_ZERO_RULE = (
    "For each of the six preregistered structures, compute the per-trade average "
    "with zero friction on the training split only. A structure advances only if "
    "that average is at least $7.80, three times the one-way entry cost. If no "
    "structure clears it, the line closes and nothing further is built. The "
    "sealed holdout is never read, and Stage 0 does not read validation."
)

DATA_LIMITATION = (
    "A one-minute VWAP is still trade-derived, not an executable quote: it is "
    "materially harder for a single stale print to move than an opening print "
    "is, but only the parity recorder can measure an achievable fill. The "
    "underlying bars are IEX rather than consolidated, so the 15:59 close "
    "differs from the OCC closing price that settles the contract, and these "
    "payoffs are kinked at the strikes -- which is what the Stage 1 "
    "pin-sensitivity gate exists to test. The payoff math is cash settlement; "
    "SPY is physically settled, so this is the economic equivalent and not the "
    "operational one."
)


class FinalHourError(RuntimeError):
    """Raised when the cache cannot support the locked protocol."""


@dataclass(frozen=True)
class Leg:
    """One option leg, positioned in dollars relative to the center strike."""

    quantity: int
    kind: str
    offset: Decimal

    def strike(self, center: Decimal) -> Decimal:
        return center + self.offset


@dataclass(frozen=True)
class Structure:
    key: str
    label: str
    legs: tuple[Leg, ...]

    @property
    def strikes_needed(self) -> Decimal:
        return max(abs(leg.offset) for leg in self.legs)


def _iron_fly(width: Decimal, direction: int) -> tuple[Leg, ...]:
    """Body at the center, wings `width` dollars out. direction -1 sells it."""

    return (
        Leg(direction, "C", Decimal("0")),
        Leg(direction, "P", Decimal("0")),
        Leg(-direction, "C", width),
        Leg(-direction, "P", -width),
    )


def _iron_condor(short_offset: Decimal, wing: Decimal, direction: int) -> tuple[Leg, ...]:
    """Shorts `short_offset` from the center, wings a further `wing` out."""

    return (
        Leg(direction, "C", short_offset),
        Leg(direction, "P", -short_offset),
        Leg(-direction, "C", short_offset + wing),
        Leg(-direction, "P", -short_offset - wing),
    )


#: The frozen grid. S5 and S6 are the sign inversions of S1 and S2, so the
#: inversion placebo is part of the preregistered grid rather than something run
#: afterwards -- a losing short structure cannot be flipped into a winner after
#: the fact, because both sides were declared together and both pay the
#: multiplicity penalty in Stage 1.
STRUCTURES: tuple[Structure, ...] = (
    Structure("S1", "short iron fly, $1 wings", _iron_fly(Decimal("1"), -1)),
    Structure("S2", "short iron fly, $2 wings", _iron_fly(Decimal("2"), -1)),
    Structure("S3", "short iron condor, shorts +/-$1, $1 wings", _iron_condor(Decimal("1"), Decimal("1"), -1)),
    Structure("S4", "short iron condor, shorts +/-$2, $1 wings", _iron_condor(Decimal("2"), Decimal("1"), -1)),
    Structure("S5", "long iron fly, $1 wings", _iron_fly(Decimal("1"), 1)),
    Structure("S6", "long iron fly, $2 wings", _iron_fly(Decimal("2"), 1)),
)


@dataclass(frozen=True)
class Trade:
    """One session's unconditional entry in one structure."""

    trading_date: str
    center: Decimal
    settlement: Decimal
    entry_price: Decimal
    maximum_loss: Decimal
    gross: Decimal


def option_symbol(trading_date: str, kind: str, strike: Decimal) -> str:
    """OCC symbol for a SPY contract expiring on `trading_date`."""

    stamp = f"{trading_date[2:4]}{trading_date[5:7]}{trading_date[8:10]}"
    thousandths = int((strike * Decimal("1000")).to_integral_value(rounding=ROUND_HALF_UP))
    return f"{ROOT}{stamp}{kind}{thousandths:08d}"


def center_strike(entry: Bar) -> Decimal:
    """Nearest whole dollar to the 15:00 open, exact halves rounded upward."""

    return Decimal(str(entry.open)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def intrinsic(kind: str, strike: Decimal, settlement: Decimal) -> Decimal:
    if kind == "C":
        return max(Decimal("0"), settlement - strike)
    return max(Decimal("0"), strike - settlement)


def structure_price(
    structure: Structure, center: Decimal, prices: dict[tuple[str, Decimal], Decimal]
) -> Decimal:
    """Signed cost of entering, per share. Negative means a net credit."""

    return sum(
        (Decimal(leg.quantity) * prices[(leg.kind, leg.strike(center))] for leg in structure.legs),
        Decimal("0"),
    )


def structure_value(structure: Structure, center: Decimal, settlement: Decimal) -> Decimal:
    """Signed value held to settlement, per share."""

    return sum(
        (
            Decimal(leg.quantity) * intrinsic(leg.kind, leg.strike(center), settlement)
            for leg in structure.legs
        ),
        Decimal("0"),
    )


def worst_case(structure: Structure, center: Decimal, entry_price: Decimal) -> Decimal:
    """Maximum one-contract loss in dollars, including fees.

    These payoffs are piecewise linear with kinks only at the strikes, so the
    minimum over every settlement price is attained at a strike or outside the
    outermost one. Evaluating those points is exact rather than approximate.
    """

    strikes = [leg.strike(center) for leg in structure.legs]
    probes = [Decimal("0"), *strikes, max(strikes) + Decimal("50")]
    worst = min(structure_value(structure, center, probe) - entry_price for probe in probes)
    return (-worst * HUNDRED + FEES_PER_STRUCTURE).quantize(CENT)


def evaluate(
    structure: Structure,
    trading_date: str,
    center: Decimal,
    settlement: Decimal,
    prices: dict[tuple[str, Decimal], Decimal],
) -> Trade | None:
    """Grade one session, or return None when the risk gate rejects it."""

    if any((leg.kind, leg.strike(center)) not in prices for leg in structure.legs):
        return None
    entry_price = structure_price(structure, center, prices)
    maximum_loss = worst_case(structure, center, entry_price)
    if maximum_loss > MAX_LOSS_LIMIT:
        return None
    gross = ((structure_value(structure, center, settlement) - entry_price) * HUNDRED).quantize(
        CENT
    )
    return Trade(
        trading_date=trading_date,
        center=center,
        settlement=settlement,
        entry_price=entry_price.quantize(CENT),
        maximum_loss=maximum_loss,
        gross=gross,
    )


@dataclass(frozen=True)
class Session:
    """One session's inputs, with every leg price this cache can supply."""

    trading_date: str
    center: Decimal
    settlement: Decimal
    prices: dict[tuple[str, Decimal], Decimal]


def _bar_at(bars: list[Bar], when: time) -> Bar | None:
    for bar in bars:
        if bar.timestamp.time() == when:
            return bar
    return None


def load_sessions(
    cache_dir: Path,
    entry_time: time = ENTRY_TIME,
    fill_time: time | None = None,
) -> tuple[list[Session], dict]:
    """Read every pre-holdout session the cache can price.

    `entry_time` selects the center strike from the underlying. `fill_time`
    prices the legs, and defaults to `entry_time`. Keeping them separate is the
    whole point: setting `fill_time` one minute later is the strict entry delay,
    a fill in a minute strictly after the one that chose the structure. Moving
    both together instead would only establish that the edge is not an artifact
    of the 15:00 clock, which is the weaker claim the
    [parity diagnostic](../../docs/SURFACE_PARITY_DIAGNOSTIC.md) cautions against
    mistaking for this control.

    Only bar presence and strike containment decide inclusion. The skip reasons
    are counted and reported so a coverage failure can never be mistaken for a
    result.
    """

    fill_time = fill_time or entry_time

    try:
        files = option_sessions(cache_dir)
    except SpreadEstimatorError as error:
        raise FinalHourError(str(error)) from error

    underlying = load_underlying(cache_dir, ROOT)
    if not underlying:
        raise FinalHourError(f"no {ROOT} underlying bars in {cache_dir}")

    coverage = {
        "files": len(files),
        "priced": 0,
        "missing_underlying_bar": 0,
        "no_leg_prices": 0,
        "center_from": entry_time.isoformat(timespec="minutes"),
        "priced_at": fill_time.isoformat(timespec="minutes"),
    }
    sessions: list[Session] = []
    for stamp, path in files:
        bars = underlying.get(stamp, [])
        entry = _bar_at(bars, entry_time)
        close = _bar_at(bars, SETTLEMENT_TIME)
        if entry is None or close is None:
            coverage["missing_underlying_bar"] += 1
            continue
        center = center_strike(entry)
        payload = json.loads(path.read_text(encoding="utf-8"))
        prices: dict[tuple[str, Decimal], Decimal] = {}
        for offset in range(-6, 7):
            for kind in ("C", "P"):
                strike = center + Decimal(offset)
                raw = payload.get(option_symbol(stamp, kind, strike))
                if not raw:
                    continue
                bar = _bar_at([_parse_bar(item) for item in raw], fill_time)
                if bar is None or bar.vwap is None:
                    continue
                prices[(kind, strike)] = Decimal(str(bar.vwap))
        if not prices:
            coverage["no_leg_prices"] += 1
            continue
        coverage["priced"] += 1
        sessions.append(
            Session(
                trading_date=stamp,
                center=center,
                settlement=Decimal(str(close.close)),
                prices=prices,
            )
        )
    if not sessions:
        raise FinalHourError(f"no priceable pre-holdout sessions in {cache_dir}")
    return sessions, coverage


def training_boundary(sessions: list[Session]) -> str:
    """Last training date, fixed once from S1 coverage for every structure.

    The split is defined on the sessions where the $1-wide structure is
    complete, so one boundary date applies to all six. Coverage decides the
    boundary; no P&L is consulted.
    """

    complete = [
        session.trading_date
        for session in sessions
        if all((leg.kind, leg.strike(session.center)) in session.prices for leg in STRUCTURES[0].legs)
    ]
    if not complete:
        raise FinalHourError("no session prices a complete $1-wide structure")
    cutoff = int(
        (Decimal(len(complete)) * TRAINING_FRACTION).to_integral_value(rounding=ROUND_HALF_UP)
    )
    return sorted(complete)[max(cutoff - 1, 0)]


def summarize(structure: Structure, trades: list[Trade]) -> dict:
    """Zero-friction summary. No cost is charged anywhere in Stage 0."""

    if not trades:
        return {
            "structure": structure.key,
            "label": structure.label,
            "trades": 0,
            "advances": False,
            "note": "no session priced this structure inside the risk limit",
        }
    gross = [trade.gross for trade in trades]
    average = (sum(gross, Decimal("0")) / Decimal(len(gross))).quantize(CENT)
    wins = sum(1 for value in gross if value > 0)
    return {
        "structure": structure.key,
        "label": structure.label,
        "trades": len(trades),
        "average_gross": str(average),
        "total_gross": str(sum(gross, Decimal("0")).quantize(CENT)),
        "median_gross": str(sorted(gross)[len(gross) // 2]),
        "best": str(max(gross)),
        "worst": str(min(gross)),
        "win_rate": str((Decimal(wins) / Decimal(len(gross))).quantize(Decimal("0.001"))),
        "average_entry_price": str(
            (sum((t.entry_price for t in trades), Decimal("0")) / Decimal(len(trades))).quantize(
                CENT
            )
        ),
        "average_maximum_loss": str(
            (sum((t.maximum_loss for t in trades), Decimal("0")) / Decimal(len(trades))).quantize(
                CENT
            )
        ),
        "advances": average >= STAGE_ZERO_THRESHOLD,
    }


def run_stage_zero(
    cache_dir: Path, entry_time: time = ENTRY_TIME, fill_time: time | None = None
) -> dict:
    sessions, coverage = load_sessions(cache_dir, entry_time, fill_time)
    boundary = training_boundary(sessions)
    training = [session for session in sessions if session.trading_date <= boundary]

    results = []
    for structure in STRUCTURES:
        trades = [
            trade
            for session in training
            if (
                trade := evaluate(
                    structure,
                    session.trading_date,
                    session.center,
                    session.settlement,
                    session.prices,
                )
            )
            is not None
        ]
        results.append(summarize(structure, trades))

    advancing = [row for row in results if row["advances"]]
    advancing.sort(key=lambda row: Decimal(row["average_gross"]), reverse=True)
    return {
        "stage": 0,
        "protocol": "docs/FINAL_HOUR_SETTLEMENT_RESEARCH.md",
        "rule": STAGE_ZERO_RULE,
        "limitation": DATA_LIMITATION,
        "entry_time": entry_time.isoformat(timespec="minutes"),
        "fill_time": (fill_time or entry_time).isoformat(timespec="minutes"),
        "settlement_time": SETTLEMENT_TIME.isoformat(timespec="minutes"),
        "holdout_start": HOLDOUT_START.isoformat(),
        "training_boundary": boundary,
        "measured_half_spread": str(MEASURED_HALF_SPREAD),
        "one_way_cost": str(ONE_WAY_COST),
        "threshold": str(STAGE_ZERO_THRESHOLD),
        "coverage": coverage,
        "training_sessions": len(training),
        "structures": results,
        "advancing": [row["structure"] for row in advancing],
        "candidate": advancing[0]["structure"] if advancing else None,
        "verdict": _verdict(results, advancing),
    }


def _verdict(results: list[dict], advancing: list[dict]) -> str:
    if not advancing:
        best = max(
            (row for row in results if row["trades"]),
            key=lambda row: Decimal(row["average_gross"]),
            default=None,
        )
        detail = (
            f"the best of the six was {best['structure']} at {best['average_gross']} per trade"
            if best
            else "no structure produced a trade"
        )
        return (
            f"No structure cleared the ${STAGE_ZERO_THRESHOLD} zero-friction gate, so "
            f"{detail}. Stage 1 is not authorized and the line closes. Because this is a "
            "zero-friction upper bound, no fill quality, cost assumption, or execution "
            "improvement can rescue it."
        )
    row = advancing[0]
    return (
        f"{row['structure']} clears the gate at {row['average_gross']} per trade over "
        f"{row['trades']} training sessions against a ${STAGE_ZERO_THRESHOLD} threshold. That "
        "is an upper bound with no friction charged, not a result. Stage 1 grades it at "
        "measured cost under the entry delay, pin-sensitivity, and randomized-center gates."
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="floor-final-hour-research",
        description="Stage 0 upper bound for the SPY 0DTE final-hour settlement screen.",
    )
    parser.add_argument("--stage", type=int, default=0)
    parser.add_argument(
        "--cache-dir", type=Path, default=Path("state/intraday-surface-butterfly-cache")
    )
    parser.add_argument(
        "--entry-time",
        type=time.fromisoformat,
        default=ENTRY_TIME,
        help="Minute whose underlying bar chooses the center strike.",
    )
    parser.add_argument(
        "--fill-time",
        type=time.fromisoformat,
        default=None,
        help=(
            "Minute the legs are priced in. Defaults to --entry-time. Setting it to "
            "15:01 is the strict entry delay: the structure is chosen from 15:00 and "
            "paid for in a minute that cannot be the one that chose it."
        ),
    )
    parser.add_argument("--report-out", type=Path, default=Path("state/final-hour-report.json"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.stage != 0:
        print(
            "error: only stage 0 exists. Stage 1 is written after Stage 0 is recorded.",
            file=sys.stderr,
        )
        return 2
    try:
        report = run_stage_zero(args.cache_dir, args.entry_time, args.fill_time)
    except FinalHourError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    args.report_out.parent.mkdir(parents=True, exist_ok=True)
    args.report_out.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"training boundary {report['training_boundary']} ({report['training_sessions']} sessions)")
    print(f"center from {report['entry_time']}, legs priced at {report['fill_time']}")
    print(f"zero-friction gate ${report['threshold']} per trade")
    for row in report["structures"]:
        if not row["trades"]:
            print(f"  {row['structure']}  {row['label']:<44} {row['note']}")
            continue
        mark = "advances" if row["advances"] else "        "
        print(
            f"  {row['structure']}  {row['label']:<44} n={row['trades']:>4} "
            f"avg={row['average_gross']:>9} win={row['win_rate']} {mark}"
        )
    print()
    print(report["verdict"])
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
