"""Zero-order executable-quote recorder for 0DTE no-arbitrage structures.

Every strategy in this project has been accepted or rejected on modeled fill
costs applied to one-minute option *trade* prints. The surface-butterfly family
survives only while adverse fill stays under roughly two to three cents per
contract unit, and nobody has measured whether that is achievable. This module
takes that measurement.

It submits nothing. There is no order-construction code path here at all: the
module never imports a submit helper and holds no broker write capability. It
observes the option ladder at locked times and records, for three families of
structure whose payoff is bounded or deterministic, what the displayed bid and
ask would actually have paid.

- **Butterfly parity.** A call butterfly and a put butterfly on the same three
  strikes have identical expiration payoffs, so their prices must agree. Buying
  the cheap one and selling the expensive one is payoff-neutral: the entry gap
  is the whole P&L. The recorded `executable_edge` is that gap after crossing
  every leg, which is the number that decides the family.
- **Vertical bounds.** A debit vertical must price inside `[0, width]`. Buying
  one for less than nothing, or selling one for more than its width, is
  riskless.
- **Boxes.** A box settles at exactly its width regardless of the underlying.
  Paying less than width, or collecting more, is riskless.

A positive edge means displayed quotes permitted a riskless trade. The expected
finding is that all three distributions sit firmly below zero, which would
establish that the historical print-based gaps are not executable and would
close the surface family. That is a real result either way, obtained without
risking money.

The free `indicative` options feed is not the OPRA book. The report labels every
aggregate with the feed that produced it and refuses to describe indicative
observations as executable.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time as time_module
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime, time, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from statistics import mean, median
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

from .alpaca import AlpacaClient, AlpacaError
from .models import Contract, Quote

ET = ZoneInfo("America/New_York")
CENT = Decimal("0.01")

#: Locked observation slots, matching the intraday surface forward protocol so
#: the two data sets are directly comparable.
SLOT_TIMES: tuple[time, ...] = (
    time(10, 0),
    time(11, 0),
    time(12, 0),
    time(13, 0),
    time(14, 0),
)

CENTER_OFFSETS: tuple[int, ...] = tuple(range(-3, 4))


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)


class ParityRecorderError(RuntimeError):
    """Raised when the recorder cannot run as configured."""


@dataclass(frozen=True)
class ParityConfig:
    """Read-only configuration. Measurement parameters are constants.

    Credentials, feed, and paths come from the environment. The structural
    parameters do not, because a forward measurement whose universe can drift
    between sessions is not a measurement.
    """

    api_key: str
    api_secret: str
    trading_base_url: str
    data_base_url: str
    request_timeout_seconds: int
    symbol: str = "SPY"
    stock_feed: str = "iex"
    options_feed: str = "indicative"
    paper: bool = True
    wing_width: Decimal = Decimal("1")
    center_offsets: tuple[int, ...] = CENTER_OFFSETS
    slot_times: tuple[time, ...] = SLOT_TIMES
    slot_window_seconds: int = 60
    maximum_quote_age_seconds: int = 90
    maximum_leg_quote_width: Decimal = Decimal("0.25")
    poll_seconds: int = 5
    journal_path: Path = Path("state/parity_recorder_events.jsonl")

    @classmethod
    def from_env(cls, *, require_credentials: bool = True) -> ParityConfig:
        options_feed = os.getenv("OPTIONS_FEED", "indicative").strip().lower()
        config = cls(
            api_key=os.getenv("ALPACA_API_KEY", "").strip(),
            api_secret=os.getenv("ALPACA_API_SECRET", "").strip(),
            trading_base_url=os.getenv(
                "ALPACA_TRADING_URL", "https://paper-api.alpaca.markets"
            ).rstrip("/"),
            data_base_url=os.getenv("ALPACA_DATA_URL", "https://data.alpaca.markets").rstrip("/"),
            request_timeout_seconds=_int("REQUEST_TIMEOUT_SECONDS", 10),
            symbol=os.getenv("PARITY_SYMBOL", "SPY").strip().upper(),
            stock_feed=os.getenv("STOCK_FEED", "iex").strip().lower(),
            options_feed=options_feed,
            paper=_bool("ALPACA_PAPER", True),
            maximum_quote_age_seconds=_int(
                "MAX_QUOTE_AGE_SECONDS", 90 if options_feed == "indicative" else 30
            ),
            poll_seconds=_int("PARITY_POLL_SECONDS", 5),
            journal_path=Path(os.getenv("PARITY_LOG_PATH", "state/parity_recorder_events.jsonl")),
        )
        config.validate(require_credentials=require_credentials)
        return config

    def validate(self, *, require_credentials: bool = True) -> None:
        if require_credentials and (not self.api_key or not self.api_secret):
            raise ParityRecorderError("ALPACA_API_KEY and ALPACA_API_SECRET are required")
        if self.options_feed not in {"indicative", "opra"}:
            raise ParityRecorderError("OPTIONS_FEED must be indicative or opra")
        if self.wing_width <= 0:
            raise ParityRecorderError("wing width must be positive")

    @property
    def executable_feed(self) -> bool:
        """True only when the feed is the consolidated book.

        Indicative quotes are usable for plumbing and for order-of-magnitude
        work, but an edge measured on them is not evidence about live fills.
        """

        return self.options_feed == "opra"


def _client_config(config: ParityConfig) -> Any:
    """Adapt to the attribute surface `AlpacaClient` reads.

    `AlpacaClient` takes the main `Config`; the recorder deliberately does not
    construct one, because that object carries order-submission settings and a
    live-trading interlock this module must never touch.
    """

    return SimpleNamespace(
        api_key=config.api_key,
        api_secret=config.api_secret,
        trading_base_url=config.trading_base_url,
        data_base_url=config.data_base_url,
        request_timeout_seconds=config.request_timeout_seconds,
        symbol=config.symbol,
        signal_symbol=config.symbol,
        stock_feed=config.stock_feed,
        options_feed=config.options_feed,
        max_quote_age_seconds=config.maximum_quote_age_seconds,
    )


@dataclass(frozen=True)
class LegQuote:
    symbol: str
    kind: str
    strike: Decimal
    bid: Decimal
    ask: Decimal
    age_seconds: Decimal
    usable: bool
    reason: str

    @property
    def width(self) -> Decimal:
        return (self.ask - self.bid).quantize(CENT)


@dataclass(frozen=True)
class ButterflyParity:
    """One center's call-versus-put butterfly comparison."""

    center: Decimal
    call_buy_cost: Decimal
    call_sell_proceeds: Decimal
    put_buy_cost: Decimal
    put_sell_proceeds: Decimal
    mid_gap: Decimal
    executable_edge: Decimal
    direction: str
    usable: bool

    @property
    def contract_units(self) -> int:
        return 8


@dataclass(frozen=True)
class VerticalBound:
    kind: str
    lower_strike: Decimal
    upper_strike: Decimal
    width: Decimal
    buy_cost: Decimal
    sell_proceeds: Decimal
    executable_edge: Decimal
    direction: str
    usable: bool


@dataclass(frozen=True)
class BoxBound:
    lower_strike: Decimal
    upper_strike: Decimal
    width: Decimal
    buy_cost: Decimal
    sell_proceeds: Decimal
    executable_edge: Decimal
    direction: str
    usable: bool


@dataclass(frozen=True)
class Observation:
    observed_at: datetime
    trading_date: str
    slot: str
    symbol: str
    options_feed: str
    executable_feed: bool
    spot: Decimal
    spot_age_seconds: Decimal
    legs: tuple[LegQuote, ...] = ()
    butterflies: tuple[ButterflyParity, ...] = ()
    verticals: tuple[VerticalBound, ...] = ()
    boxes: tuple[BoxBound, ...] = ()
    reason: str = "recorded"
    skipped: bool = False


def nearest_whole(value: Decimal) -> Decimal:
    return value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def strike_ladder(spot: Decimal, config: ParityConfig) -> list[Decimal]:
    """Return the contiguous strike ladder the three families need.

    Butterflies at centers `base + offset` need one wing beyond each end, so the
    ladder runs one width wider than the center range on both sides. All three
    structure families are then computed from a single quote snapshot.
    """

    base = nearest_whole(spot)
    low = base + Decimal(min(config.center_offsets)) - config.wing_width
    high = base + Decimal(max(config.center_offsets)) + config.wing_width
    strikes: list[Decimal] = []
    strike = low
    while strike <= high:
        strikes.append(strike)
        strike += config.wing_width
    return strikes


def _leg(
    contract: Contract,
    kind: str,
    quote: Quote | None,
    now: datetime,
    config: ParityConfig,
) -> LegQuote:
    if quote is None:
        return LegQuote(
            contract.symbol,
            kind,
            contract.strike,
            Decimal("0"),
            Decimal("0"),
            Decimal("0"),
            False,
            "no quote returned",
        )
    age = Decimal(
        str(
            round(
                (
                    now.astimezone(timezone.utc) - quote.timestamp.astimezone(timezone.utc)
                ).total_seconds(),
                3,
            )
        )
    )
    reason = "usable"
    usable = True
    if quote.bid < 0 or quote.ask <= 0 or quote.bid > quote.ask:
        usable, reason = False, "crossed or empty quote"
    elif quote.ask - quote.bid > config.maximum_leg_quote_width:
        usable, reason = False, "quote wider than the leg limit"
    elif not -2 <= age <= config.maximum_quote_age_seconds:
        usable, reason = False, "quote is stale"
    return LegQuote(
        contract.symbol, kind, contract.strike, quote.bid, quote.ask, age, usable, reason
    )


def fly_buy_cost(lower: LegQuote, middle: LegQuote, upper: LegQuote) -> Decimal:
    """Debit to buy a 1:2:1 butterfly, crossing every leg."""

    return (lower.ask - Decimal("2") * middle.bid + upper.ask).quantize(CENT)


def fly_sell_proceeds(lower: LegQuote, middle: LegQuote, upper: LegQuote) -> Decimal:
    """Credit received for selling a 1:2:1 butterfly, crossing every leg."""

    return (lower.bid - Decimal("2") * middle.ask + upper.bid).quantize(CENT)


def _mid(buy_cost: Decimal, sell_proceeds: Decimal) -> Decimal:
    return ((buy_cost + sell_proceeds) / Decimal("2")).quantize(CENT)


def butterfly_parity(
    center: Decimal, ladder: dict[tuple[str, Decimal], LegQuote], config: ParityConfig
) -> ButterflyParity | None:
    """Compare the same-strike call and put butterflies at one center.

    Their expiration payoffs are identical, so buying the cheap one and selling
    the expensive one is payoff-neutral and the entry gap is the entire result.
    `executable_edge` is that gap after crossing all eight contract units; a
    positive value is a riskless trade at displayed prices.
    """

    strikes = (center - config.wing_width, center, center + config.wing_width)
    try:
        calls = tuple(ladder[("C", strike)] for strike in strikes)
        puts = tuple(ladder[("P", strike)] for strike in strikes)
    except KeyError:
        return None

    call_buy = fly_buy_cost(*calls)
    call_sell = fly_sell_proceeds(*calls)
    put_buy = fly_buy_cost(*puts)
    put_sell = fly_sell_proceeds(*puts)

    buy_call_sell_put = (put_sell - call_buy).quantize(CENT)
    buy_put_sell_call = (call_sell - put_buy).quantize(CENT)
    if buy_call_sell_put >= buy_put_sell_call:
        edge, direction = buy_call_sell_put, "buy_call_fly_sell_put_fly"
    else:
        edge, direction = buy_put_sell_call, "buy_put_fly_sell_call_fly"

    return ButterflyParity(
        center=center,
        call_buy_cost=call_buy,
        call_sell_proceeds=call_sell,
        put_buy_cost=put_buy,
        put_sell_proceeds=put_sell,
        mid_gap=abs(_mid(call_buy, call_sell) - _mid(put_buy, put_sell)).quantize(CENT),
        executable_edge=edge,
        direction=direction,
        usable=all(leg.usable for leg in calls + puts),
    )


def vertical_bounds(
    lower: Decimal, upper: Decimal, ladder: dict[tuple[str, Decimal], LegQuote]
) -> list[VerticalBound]:
    """Check that each debit vertical prices inside `[0, width]`.

    A call vertical is long the lower strike; a put vertical is long the upper.
    Both have payoffs bounded by zero and the strike width, so buying below zero
    or selling above width is riskless.
    """

    width = (upper - lower).quantize(CENT)
    results: list[VerticalBound] = []
    for kind in ("C", "P"):
        try:
            low_leg = ladder[(kind, lower)]
            high_leg = ladder[(kind, upper)]
        except KeyError:
            continue
        long_leg, short_leg = (low_leg, high_leg) if kind == "C" else (high_leg, low_leg)
        buy_cost = (long_leg.ask - short_leg.bid).quantize(CENT)
        sell_proceeds = (long_leg.bid - short_leg.ask).quantize(CENT)
        # Riskless if paid to own a nonnegative payoff, or paid more than the cap.
        underpay = (Decimal("0") - buy_cost).quantize(CENT)
        overcollect = (sell_proceeds - width).quantize(CENT)
        if underpay >= overcollect:
            edge, direction = underpay, "buy_below_zero"
        else:
            edge, direction = overcollect, "sell_above_width"
        results.append(
            VerticalBound(
                kind=kind,
                lower_strike=lower,
                upper_strike=upper,
                width=width,
                buy_cost=buy_cost,
                sell_proceeds=sell_proceeds,
                executable_edge=edge,
                direction=direction,
                usable=low_leg.usable and high_leg.usable,
            )
        )
    return results


def box_bound(
    lower: Decimal, upper: Decimal, ladder: dict[tuple[str, Decimal], LegQuote]
) -> BoxBound | None:
    """Check the box, whose expiration value is exactly its width.

    Long box: long call and short put at the lower strike, short call and long
    put at the upper. It settles at `width` whatever the underlying does, so
    paying less than width -- or collecting more -- is riskless.
    """

    try:
        call_low = ladder[("C", lower)]
        call_high = ladder[("C", upper)]
        put_low = ladder[("P", lower)]
        put_high = ladder[("P", upper)]
    except KeyError:
        return None

    width = (upper - lower).quantize(CENT)
    buy_cost = (call_low.ask - call_high.bid + put_high.ask - put_low.bid).quantize(CENT)
    sell_proceeds = (call_low.bid - call_high.ask + put_high.bid - put_low.ask).quantize(CENT)
    underpay = (width - buy_cost).quantize(CENT)
    overcollect = (sell_proceeds - width).quantize(CENT)
    if underpay >= overcollect:
        edge, direction = underpay, "buy_below_width"
    else:
        edge, direction = overcollect, "sell_above_width"
    return BoxBound(
        lower_strike=lower,
        upper_strike=upper,
        width=width,
        buy_cost=buy_cost,
        sell_proceeds=sell_proceeds,
        executable_edge=edge,
        direction=direction,
        usable=all(leg.usable for leg in (call_low, call_high, put_low, put_high)),
    )


def slot_for(now: datetime, config: ParityConfig) -> str | None:
    """Return the locked slot this instant belongs to, if any."""

    for slot in config.slot_times:
        start = datetime.combine(now.date(), slot, ET)
        delta = (now.astimezone(ET) - start).total_seconds()
        if 0 <= delta < config.slot_window_seconds:
            return slot.strftime("%H:%M")
    return None


def observe(
    client: AlpacaClient,
    config: ParityConfig,
    now: datetime | None = None,
    *,
    slot: str | None = None,
) -> Observation:
    """Take one complete measurement. Reads only; submits nothing."""

    now = (now or datetime.now(ET)).astimezone(ET)
    trading_date = now.date().isoformat()
    label = slot or slot_for(now, config) or "off_slot"

    spot, spot_at = client.latest_underlying_trade(trading_date)
    spot_age = Decimal(
        str(
            round(
                (now.astimezone(timezone.utc) - spot_at.astimezone(timezone.utc)).total_seconds(),
                3,
            )
        )
    )
    base = Observation(
        observed_at=now,
        trading_date=trading_date,
        slot=label,
        symbol=config.symbol,
        options_feed=config.options_feed,
        executable_feed=config.executable_feed,
        spot=spot,
        spot_age_seconds=spot_age,
    )
    if not -2 <= spot_age <= config.maximum_quote_age_seconds:
        return replace_observation(base, reason="underlying trade is stale", skipped=True)

    calls = {c.strike: c for c in client.option_contracts(trading_date, "call")}
    puts = {c.strike: c for c in client.option_contracts(trading_date, "put")}
    wanted = strike_ladder(spot, config)
    contracts: dict[tuple[str, Decimal], Contract] = {}
    for strike in wanted:
        for kind, source in (("C", calls), ("P", puts)):
            if strike in source:
                contracts[(kind, strike)] = source[strike]
    if not contracts:
        return replace_observation(base, reason="no ladder contracts listed", skipped=True)

    symbols = sorted({contract.symbol for contract in contracts.values()})
    quotes = client.option_quotes(symbols, allow_missing=True)
    ladder = {
        key: _leg(contract, key[0], quotes.get(contract.symbol), now, config)
        for key, contract in contracts.items()
    }

    butterflies = tuple(
        parity
        for offset in config.center_offsets
        if (parity := butterfly_parity(nearest_whole(spot) + Decimal(offset), ladder, config))
        is not None
    )
    verticals: list[VerticalBound] = []
    boxes: list[BoxBound] = []
    for lower, upper in zip(wanted, wanted[1:], strict=False):
        verticals.extend(vertical_bounds(lower, upper, ladder))
        if (box := box_bound(lower, upper, ladder)) is not None:
            boxes.append(box)

    return replace_observation(
        base,
        legs=tuple(ladder[key] for key in sorted(ladder, key=lambda item: (item[0], item[1]))),
        butterflies=butterflies,
        verticals=tuple(verticals),
        boxes=tuple(boxes),
        reason="recorded",
    )


def replace_observation(base: Observation, **changes: Any) -> Observation:
    return replace(base, **changes)


def _encode(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (date, time)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {key: _encode(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_encode(item) for item in value]
    return value


def observation_payload(observation: Observation) -> dict[str, Any]:
    return _encode(asdict(observation))


def append_observation(path: Path, observation: Observation) -> None:
    """Append one record. The journal is append-only and never rewritten."""

    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(observation_payload(observation), separators=(",", ":")) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)
    os.chmod(path, 0o600)


@dataclass
class EdgeSummary:
    """Distribution of one structure family's executable edge."""

    label: str
    observations: int = 0
    usable: int = 0
    edges: list[Decimal] = field(default_factory=list)
    positive: int = 0
    best: Decimal | None = None
    mid_gaps: list[Decimal] = field(default_factory=list)

    def add(self, edge: Decimal, usable: bool, mid_gap: Decimal | None = None) -> None:
        self.observations += 1
        if not usable:
            return
        self.usable += 1
        self.edges.append(edge)
        if edge > 0:
            self.positive += 1
        if self.best is None or edge > self.best:
            self.best = edge
        if mid_gap is not None:
            self.mid_gaps.append(mid_gap)

    def payload(self) -> dict[str, Any]:
        if not self.edges:
            return {
                "label": self.label,
                "structures_seen": self.observations,
                "usable": 0,
                "note": "no structure had a complete set of fresh, tight quotes",
            }
        out: dict[str, Any] = {
            "label": self.label,
            "structures_seen": self.observations,
            "usable": self.usable,
            "riskless_at_displayed_quotes": self.positive,
            "median_edge": str(median(self.edges).quantize(CENT)),
            "mean_edge": str(Decimal(str(mean(self.edges))).quantize(CENT)),
            "best_edge": str(self.best.quantize(CENT)) if self.best is not None else None,
            "worst_edge": str(min(self.edges).quantize(CENT)),
        }
        if self.mid_gaps:
            out["median_mid_gap"] = str(median(self.mid_gaps).quantize(CENT))
            out["mean_mid_gap"] = str(Decimal(str(mean(self.mid_gaps))).quantize(CENT))
        return out


def journal_summary(path: Path) -> dict[str, Any]:
    """Summarize the recorded journal without interpreting indicative data as real."""

    if not path.exists():
        return {"journal": str(path), "observations": 0, "note": "journal does not exist yet"}

    families = {
        "butterfly_parity_pair": EdgeSummary("paired call/put butterfly, 8 contract units"),
        "vertical_bound": EdgeSummary("debit vertical outside [0, width]"),
        "box_bound": EdgeSummary("box away from its settlement width"),
    }
    observations = 0
    skipped = 0
    feeds: dict[str, int] = {}
    slots: dict[str, int] = {}
    dates: set[str] = set()
    reasons: dict[str, int] = {}

    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            observations += 1
            feeds[record.get("options_feed", "unknown")] = (
                feeds.get(record.get("options_feed", "unknown"), 0) + 1
            )
            slots[record.get("slot", "unknown")] = slots.get(record.get("slot", "unknown"), 0) + 1
            dates.add(record.get("trading_date", "unknown"))
            if record.get("skipped"):
                skipped += 1
                reasons[record.get("reason", "unknown")] = (
                    reasons.get(record.get("reason", "unknown"), 0) + 1
                )
                continue
            for item in record.get("butterflies", []):
                families["butterfly_parity_pair"].add(
                    Decimal(item["executable_edge"]),
                    bool(item["usable"]),
                    Decimal(item["mid_gap"]),
                )
            for item in record.get("verticals", []):
                families["vertical_bound"].add(
                    Decimal(item["executable_edge"]), bool(item["usable"])
                )
            for item in record.get("boxes", []):
                families["box_bound"].add(Decimal(item["executable_edge"]), bool(item["usable"]))

    executable = set(feeds) == {"opra"}
    return {
        "journal": str(path),
        "observations": observations,
        "skipped": skipped,
        "skip_reasons": reasons,
        "sessions": len(dates),
        "by_feed": feeds,
        "by_slot": slots,
        "measures_executable_quotes": executable,
        "interpretation": (
            "OPRA quotes only: a nonpositive edge distribution is evidence that the "
            "structure is not tradable at displayed prices."
            if executable
            else "Indicative quotes are not the consolidated book. These aggregates test "
            "plumbing and order of magnitude; they are not evidence about live fills."
        ),
        "families": {name: summary.payload() for name, summary in families.items()},
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Record executable 0DTE no-arbitrage quotes. Submits no orders."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("scan", help="take one observation now and print it without journaling")
    once = sub.add_parser("record-once", help="record one observation if inside a locked slot")
    once.add_argument(
        "--force",
        action="store_true",
        help="record even outside a locked slot, labelled off_slot",
    )
    sub.add_parser("record", help="run continuously and record each locked slot once")
    report = sub.add_parser("report", help="summarize the recorded journal")
    report.add_argument("--journal", type=Path)
    return parser


def _print(payload: Any) -> None:
    print(json.dumps(_encode(payload), indent=2))


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    if args.command == "report":
        config = ParityConfig.from_env(require_credentials=False)
        _print(journal_summary(args.journal or config.journal_path))
        return 0

    config = ParityConfig.from_env()
    client = AlpacaClient(_client_config(config))  # type: ignore[arg-type]

    if args.command == "scan":
        _print(observation_payload(observe(client, config)))
        return 0

    if args.command == "record-once":
        now = datetime.now(ET)
        slot = slot_for(now, config)
        if slot is None and not args.force:
            _print({"recorded": False, "reason": "outside every locked slot"})
            return 0
        observation = observe(client, config, now, slot=slot or "off_slot")
        append_observation(config.journal_path, observation)
        _print({"recorded": True, "slot": observation.slot, "reason": observation.reason})
        return 0

    stopping = False

    def stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    done: set[tuple[str, str]] = set()
    print(
        f"recording {config.symbol} 0DTE parity on the {config.options_feed} feed; "
        f"journal {config.journal_path}",
        file=sys.stderr,
    )
    while not stopping:
        now = datetime.now(ET)
        slot = slot_for(now, config)
        key = (now.date().isoformat(), slot or "")
        if slot is not None and key not in done:
            try:
                observation = observe(client, config, now, slot=slot)
                append_observation(config.journal_path, observation)
                done.add(key)
                print(
                    f"{now.isoformat()} slot {slot}: {observation.reason} "
                    f"({len(observation.butterflies)} centers)",
                    file=sys.stderr,
                )
            except AlpacaError as error:
                # A failed slot is recorded as missed, never reconstructed later.
                print(f"{now.isoformat()} slot {slot} failed: {error}", file=sys.stderr)
                done.add(key)
        time_module.sleep(config.poll_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
