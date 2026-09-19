"""Cache-only placebo diagnostics for the SPY 0DTE surface-butterfly result.

This reads ONLY the existing surface-butterfly cache. It never constructs an
Alpaca client and never issues a network request, so the sealed holdout cannot
be touched. Sessions on or after the holdout boundary are dropped even if a
cache file happens to exist.

The locked result buys the cheaper of two same-strike butterflies when their
prices disagree by at least $0.08. The disagreement is measured from six
one-minute *trade* prints, many of which have a volume of one contract. A stale
print makes one butterfly look phantom-cheap, and the selection rule takes the
largest disagreement -- i.e. it maximises exposure to the stalest quote in the
universe. These variants separate that artifact from a real convergence edge.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from pathlib import Path
from statistics import mean
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
CENT = Decimal("0.01")
HUNDRED = Decimal("100")
RATIO = Decimal("0.0001")

WING = Decimal("1")
CENTER_OFFSETS = tuple(range(-3, 4))
ENTRY_TIME = time(11, 0)
EXIT_TIME = time(12, 0)
HOLD_MINUTES = 60
MIN_GAP = Decimal("0.08")
MAX_DEBIT = Decimal("0.10")
ADVERSE = Decimal("0.005")
FEES = Decimal("0.20")
MAX_EXIT_MARK_AGE = 5


@dataclass(frozen=True)
class Bar:
    timestamp: datetime
    open: Decimal
    close: Decimal
    volume: Decimal


def load_bars(path: Path) -> dict[str, list[Bar]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {
        symbol: [
            Bar(
                datetime.fromisoformat(item["t"]).astimezone(ET),
                Decimal(item["o"]),
                Decimal(item["c"]),
                Decimal(item["v"]),
            )
            for item in items
        ]
        for symbol, items in raw.items()
    }


def occ(expiration: date, kind: str, strike: Decimal) -> str:
    return f"SPY{expiration:%y%m%d}{kind}{int(strike * 1000):08d}"


def at(bars: list[Bar], moment: time) -> Bar | None:
    return next((bar for bar in bars if bar.timestamp.time() == moment), None)


def legs(expiration: date, center: Decimal, kind: str) -> tuple[str, str, str]:
    return (
        occ(expiration, kind, center - WING),
        occ(expiration, kind, center),
        occ(expiration, kind, center + WING),
    )


@dataclass(frozen=True)
class Trade:
    kind: str
    center: Decimal
    gap: Decimal
    entry_debit: Decimal
    signal_debit: Decimal
    min_leg_volume: Decimal
    pnl: Decimal


def _raw(lower: Bar, middle: Bar, upper: Bar) -> Decimal:
    return (lower.open - Decimal("2") * middle.open + upper.open).quantize(CENT)


def shift(moment: time, minutes: int) -> time:
    return (datetime.combine(date(2000, 1, 1), moment) + timedelta(minutes=minutes)).time()


def simulate(
    trading_date: str,
    spot: Decimal,
    option_bars: dict[str, list[Bar]],
    *,
    variant: str,
    min_volume: Decimal = Decimal("0"),
    entry_time: time = ENTRY_TIME,
    fill_delay_minutes: int = 0,
    hold_minutes: int = HOLD_MINUTES,
    stats: dict[str, int] | None = None,
) -> Trade | None:
    """Return the single trade the variant would take, or None.

    `entry_time` is when the signal is read. `fill_delay_minutes` moves only the
    fill, so the structure selected at `entry_time` is transacted at a later
    print.
    """

    base = spot.to_integral_value(rounding=ROUND_HALF_UP)
    candidates: list[tuple[Decimal, Decimal, Decimal, Decimal, str, tuple[str, str, str]]] = []
    for offset in CENTER_OFFSETS:
        center = base + Decimal(offset)
        contracts = {kind: legs(trading_date_to_date(trading_date), center, kind) for kind in "CP"}
        entries: dict[str, tuple[Bar, Bar, Bar]] = {}
        for kind, symbols in contracts.items():
            values = tuple(at(option_bars.get(symbol, []), entry_time) for symbol in symbols)
            if all(value is not None for value in values):
                entries[kind] = values  # type: ignore[assignment]
        if set(entries) != {"C", "P"}:
            continue

        volumes = [bar.volume for values in entries.values() for bar in values]
        if min(volumes) < min_volume:
            continue

        raw = {kind: _raw(*values) for kind, values in entries.items()}
        gap = abs(raw["C"] - raw["P"]).quantize(CENT)

        if variant == "placebo_nogap":
            if gap >= MIN_GAP:
                continue
        elif gap < MIN_GAP:
            continue

        cheap = "C" if raw["C"] <= raw["P"] else "P"
        kind = {"inverted": "P" if cheap == "C" else "C"}.get(variant, cheap)

        modeled = raw[kind] + ADVERSE * Decimal("4")
        debit = max(Decimal("0"), modeled).quantize(CENT, rounding=ROUND_CEILING)
        risk = (debit * HUNDRED + FEES).quantize(CENT)
        if debit > MAX_DEBIT or risk > Decimal("100"):
            continue
        candidates.append(
            (gap, debit, abs(center - spot), center, min(volumes), kind, contracts[kind])
        )

    if not candidates:
        return None

    if variant == "placebo_nogap":
        # No inconsistency exists to rank by; take the cheapest, nearest center.
        chosen = sorted(candidates, key=lambda c: (c[1], c[2], c[5]))[0]
    else:
        chosen = sorted(candidates, key=lambda c: (-c[0], c[1], c[2], c[5]))[0]

    gap, signal_debit, _, center, min_volume_seen, kind, symbols = chosen

    fill_time = shift(entry_time, fill_delay_minutes)
    debit = signal_debit
    if fill_delay_minutes:
        marks = tuple(at(option_bars.get(symbol, []), fill_time) for symbol in symbols)
        if any(mark is None for mark in marks):
            # No synchronized print to fill against. Reported rather than
            # silently dropped, since a delay that quietly discards half the
            # sample would be its own selection effect.
            if stats is not None:
                stats["signals_without_a_delayed_fill"] = (
                    stats.get("signals_without_a_delayed_fill", 0) + 1
                )
            return None
        modeled = _raw(*marks) + ADVERSE * Decimal("4")  # type: ignore[misc]
        debit = max(Decimal("0"), modeled).quantize(CENT, rounding=ROUND_CEILING)

    exit_credit = exit_mark(option_bars, symbols, fill_time, shift(entry_time, hold_minutes))
    pnl = ((exit_credit - debit) * HUNDRED - FEES).quantize(CENT)
    return Trade(kind, center, gap, debit, signal_debit, min_volume_seen, pnl)


def trading_date_to_date(value: str) -> date:
    return date.fromisoformat(value)


def exit_mark(
    option_bars: dict[str, list[Bar]],
    symbols: tuple[str, str, str],
    entry_time: time,
    exit_time: time = EXIT_TIME,
) -> Decimal:
    series = [{bar.timestamp: bar for bar in option_bars.get(symbol, [])} for symbol in symbols]
    synchronized = sorted(set(series[0]) & set(series[1]) & set(series[2]))
    marks: list[tuple[datetime, Decimal]] = []
    for timestamp in synchronized:
        if timestamp.time() <= entry_time or timestamp.time() > exit_time:
            continue
        raw = (
            series[0][timestamp].close
            - Decimal("2") * series[1][timestamp].close
            + series[2][timestamp].close
            - ADVERSE * Decimal("4")
        )
        marks.append((timestamp, max(Decimal("0"), min(WING, raw)).quantize(CENT, ROUND_FLOOR)))

    exact = next((item for item in marks if item[0].time() == exit_time), None)
    if exact is not None:
        return exact[1]
    if marks:
        timestamp, credit = marks[-1]
        exit_at = datetime.combine(timestamp.date(), exit_time, timestamp.tzinfo)
        if timedelta(0) <= exit_at - timestamp <= timedelta(minutes=MAX_EXIT_MARK_AGE):
            return credit
    return Decimal("0")


def metrics(trades: list[Trade]) -> dict[str, object]:
    pnls = [trade.pnl for trade in trades]
    if not pnls:
        return {"trades": 0}
    wins = [value for value in pnls if value > 0]
    losses = [value for value in pnls if value < 0]
    gross_loss = abs(sum(losses, Decimal("0")))
    return {
        "trades": len(pnls),
        "win_rate": round(len(wins) / len(pnls), 4),
        "avg_pnl": str(Decimal(str(mean(pnls))).quantize(CENT)),
        "total_pnl": str(sum(pnls, Decimal("0")).quantize(CENT)),
        "profit_factor": (
            str((sum(wins, Decimal("0")) / gross_loss).quantize(RATIO)) if gross_loss else "inf"
        ),
        "avg_gap": str(Decimal(str(mean(t.gap for t in trades))).quantize(CENT)),
        "avg_debit": str(Decimal(str(mean(t.entry_debit for t in trades))).quantize(CENT)),
        "avg_signal_debit": str(Decimal(str(mean(t.signal_debit for t in trades))).quantize(CENT)),
        "avg_fill_slippage": str(
            Decimal(str(mean(t.entry_debit - t.signal_debit for t in trades))).quantize(CENT)
        ),
        # How often the price actually paid would have failed the strategy's own
        # MAX_DEBIT screen. A high number means the rule only ever qualifies a
        # trade because the price it inspects is not the price it gets.
        "share_paid_above_max_debit": round(
            sum(1 for t in trades if t.entry_debit > MAX_DEBIT) / len(trades), 4
        ),
        "median_min_leg_volume": str(sorted(t.min_leg_volume for t in trades)[len(trades) // 2]),
    }


def volume_buckets(trades: list[Trade]) -> dict[str, object]:
    edges = [
        (Decimal("0"), Decimal("2")),
        (Decimal("2"), Decimal("10")),
        (Decimal("10"), Decimal("50")),
        (Decimal("50"), Decimal("1e9")),
    ]
    out: dict[str, object] = {}
    for low, high in edges:
        bucket = [t for t in trades if low <= t.min_leg_volume < high]
        label = f"min_leg_volume [{low},{high if high < Decimal('1e9') else 'inf'})"
        out[label] = (
            {
                "trades": len(bucket),
                "avg_pnl": str(Decimal(str(mean(t.pnl for t in bucket))).quantize(CENT)),
                "avg_gap": str(Decimal(str(mean(t.gap for t in bucket))).quantize(CENT)),
            }
            if bucket
            else {"trades": 0}
        )
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, default=Path("state/surface-butterfly-cache"))
    parser.add_argument("--stock-cache", default="spy-2024-02-01-2026-08-19-iex.json")
    parser.add_argument("--oos-start", type=date.fromisoformat, default=date(2026, 5, 26))
    parser.add_argument(
        "--entry-time",
        type=lambda value: time.fromisoformat(value),
        default=ENTRY_TIME,
        help="signal time; use with --cache-dir to test the intraday variant's slots",
    )
    parser.add_argument("--report-out", type=Path)
    args = parser.parse_args()

    stock = load_bars(args.cache_dir / args.stock_cache)["SPY"]
    sessions: dict[str, list[Bar]] = {}
    for bar in stock:
        if time(9, 30) <= bar.timestamp.time() <= time(16, 0):
            sessions.setdefault(bar.timestamp.date().isoformat(), []).append(bar)

    dates = sorted(sessions)
    oos_index = next(
        (i for i, value in enumerate(dates) if date.fromisoformat(value) >= args.oos_start),
        len(dates),
    )
    train_end = max(1, int(oos_index * 0.75))
    splits = {"train": dates[:train_end], "validation": dates[train_end:oos_index]}

    variants = {
        "control": {},
        "inverted": {"variant": "inverted"},
        "placebo_nogap": {"variant": "placebo_nogap"},
        "liquid_min10": {"min_volume": Decimal("10")},
        "liquid_min25": {"min_volume": Decimal("25")},
        "entry_shift_1min": {"entry_time": shift(args.entry_time, 1)},
        "entry_delay_1min": {"fill_delay_minutes": 1},
        "entry_delay_2min": {"fill_delay_minutes": 2},
    }

    report: dict[str, object] = {
        "note": "cache-only diagnostic; no network access; holdout sessions excluded",
        "oos_start": args.oos_start.isoformat(),
        "cache_dir": str(args.cache_dir),
        "entry_time": args.entry_time.isoformat(),
        "sessions": {name: len(values) for name, values in splits.items()},
        "variants": {},
    }

    cache: dict[str, dict[str, list[Bar]]] = {}
    for name, kwargs in variants.items():
        per_split: dict[str, object] = {}
        for split, values in splits.items():
            trades: list[Trade] = []
            stats: dict[str, int] = {}
            for trading_date in values:
                path = args.cache_dir / f"options-{trading_date}.json"
                if not path.exists():
                    continue
                if trading_date not in cache:
                    cache[trading_date] = load_bars(path)
                entry = at(sessions[trading_date], kwargs.get("entry_time", args.entry_time))
                if entry is None:
                    continue
                trade = simulate(
                    trading_date,
                    entry.open,
                    cache[trading_date],
                    variant=str(kwargs.get("variant", "control")),
                    min_volume=Decimal(str(kwargs.get("min_volume", 0))),
                    entry_time=kwargs.get("entry_time", args.entry_time),
                    fill_delay_minutes=int(kwargs.get("fill_delay_minutes", 0)),
                    stats=stats,
                )
                if trade is not None:
                    trades.append(trade)
            per_split[split] = {**metrics(trades), **stats}
            if name == "control":
                per_split[f"{split}_volume_buckets"] = volume_buckets(trades)
        report["variants"][name] = per_split  # type: ignore[index]

    encoded = json.dumps(report, indent=2) + "\n"
    if args.report_out:
        args.report_out.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
