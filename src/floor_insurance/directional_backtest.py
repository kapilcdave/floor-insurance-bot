from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import asdict, replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from statistics import mean
from typing import Any
from zoneinfo import ZoneInfo

from .alpaca import AlpacaClient
from .config import Config
from .directional import (
    DirectionalResult,
    DirectionalSettings,
    PriceBar,
    SignalModel,
    VixRegime,
    candidate_pairs,
    opening_range_signal,
    regime_allows,
    select_debit_spread,
    simulate_debit_spread,
)
from .volatility import VolatilityHistory

ET = ZoneInfo("America/New_York")
CENT = Decimal("0.01")


def _bar(raw: dict[str, Any]) -> PriceBar:
    return PriceBar(
        timestamp=datetime.fromisoformat(raw["t"].replace("Z", "+00:00")).astimezone(ET),
        open=Decimal(str(raw["o"])),
        high=Decimal(str(raw["h"])),
        low=Decimal(str(raw["l"])),
        close=Decimal(str(raw["c"])),
        volume=Decimal(str(raw.get("v", "0"))),
        vwap=Decimal(str(raw["vw"])) if raw.get("vw") is not None else None,
    )


def _raw_bar(bar: PriceBar) -> dict[str, str]:
    return {
        "t": bar.timestamp.isoformat(),
        "o": str(bar.open),
        "h": str(bar.high),
        "l": str(bar.low),
        "c": str(bar.close),
        "v": str(bar.volume),
        "vw": str(bar.vwap) if bar.vwap is not None else "",
    }


def _cached_bars(path: Path) -> dict[str, list[PriceBar]] | None:
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {
        symbol: [
            PriceBar(
                timestamp=datetime.fromisoformat(item["t"]).astimezone(ET),
                open=Decimal(item["o"]),
                high=Decimal(item["h"]),
                low=Decimal(item["l"]),
                close=Decimal(item["c"]),
                volume=Decimal(item["v"]),
                vwap=Decimal(item["vw"]) if item["vw"] else None,
            )
            for item in items
        ]
        for symbol, items in raw.items()
    }


def _store_bars(path: Path, bars: dict[str, list[PriceBar]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        symbol: [_raw_bar(bar) for bar in values] for symbol, values in bars.items()
    }
    path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    path.chmod(0o600)


class HistoricalData:
    def __init__(self, config: Config, cache_dir: Path):
        self.config = config
        self.api = AlpacaClient(config)
        self.cache_dir = cache_dir
        self._volatility: VolatilityHistory | None = None

    def volatility(self) -> VolatilityHistory:
        """Load the Cboe volatility complex once, from cache when present."""

        if self._volatility is None:
            self._volatility = VolatilityHistory.load(self.cache_dir)
        return self._volatility

    def stock_sessions(self, start: date, end: date) -> dict[str, list[PriceBar]]:
        cache = self.cache_dir / f"spy-{start}-{end}-{self.config.stock_feed}.json"
        cached = _cached_bars(cache)
        if cached is not None:
            bars = cached.get("SPY", [])
        else:
            start_at = datetime.combine(start, time(9, 30), ET).astimezone(ZoneInfo("UTC"))
            end_at = datetime.combine(end, time(16, 1), ET).astimezone(ZoneInfo("UTC"))
            params: dict[str, Any] = {
                "symbols": "SPY",
                "timeframe": "1Min",
                "start": start_at.isoformat(),
                "end": end_at.isoformat(),
                "feed": self.config.stock_feed,
                "limit": 10000,
                "sort": "asc",
            }
            bars = []
            while True:
                data = self.api._request(
                    "GET", self.config.data_base_url, "/v2/stocks/bars", params=params
                )
                raw = data.get("bars", {})
                page = raw.get("SPY", []) if isinstance(raw, dict) else raw
                bars.extend(_bar(item) for item in page)
                token = data.get("next_page_token")
                if not token:
                    break
                params["page_token"] = token
            _store_bars(cache, {"SPY": bars})
        sessions: dict[str, list[PriceBar]] = {}
        for bar in bars:
            if time(9, 30) <= bar.timestamp.time() <= time(16, 0):
                sessions.setdefault(bar.timestamp.date().isoformat(), []).append(bar)
        return dict(sorted(sessions.items()))

    def option_bars(
        self, trading_date: date, symbols: list[str]
    ) -> dict[str, list[PriceBar]]:
        """Fetch option bars for a session, merging into the per-day cache.

        Symbols that returned no bars are cached as empty lists. Without that,
        a strike that simply never traded is missing from the cache forever and
        every later run refetches the whole day.
        """

        cache = self.cache_dir / f"options-{trading_date}.json"
        cached = _cached_bars(cache) or {}
        if set(symbols).issubset(cached):
            return {symbol: cached[symbol] for symbol in symbols}

        start_at = datetime.combine(trading_date, time(9, 45), ET).astimezone(
            ZoneInfo("UTC")
        )
        end_at = datetime.combine(trading_date, time(15, 1), ET).astimezone(
            ZoneInfo("UTC")
        )
        params: dict[str, Any] = {
            "symbols": ",".join(symbols),
            "timeframe": "1Min",
            "start": start_at.isoformat(),
            "end": end_at.isoformat(),
            "limit": 10000,
            "sort": "asc",
        }
        bars: dict[str, list[PriceBar]] = {}
        while True:
            data = self.api._request(
                "GET", self.config.data_base_url, "/v1beta1/options/bars", params=params
            )
            for symbol, values in data.get("bars", {}).items():
                bars.setdefault(symbol, []).extend(_bar(item) for item in values)
            token = data.get("next_page_token")
            if not token:
                break
            params["page_token"] = token
        merged = dict(cached)
        for symbol in symbols:
            merged[symbol] = bars.get(symbol, [])
        _store_bars(cache, merged)
        return {symbol: merged[symbol] for symbol in symbols}


def directional_metrics(results: list[DirectionalResult]) -> dict[str, object]:
    traded = [result for result in results if result.traded]
    pnls = [result.pnl for result in traded]
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [pnl for pnl in pnls if pnl < 0]
    cumulative = Decimal("0")
    peak = Decimal("0")
    max_drawdown = Decimal("0")
    for pnl in pnls:
        cumulative += pnl
        peak = max(peak, cumulative)
        max_drawdown = min(max_drawdown, cumulative - peak)
    gross_profit = sum(wins, Decimal("0"))
    gross_loss = abs(sum(losses, Decimal("0")))
    return {
        "sessions": len(results),
        "signals": sum(result.signal != "none" for result in results),
        "trades": len(traded),
        "calls": sum(result.signal == "call" and result.traded for result in results),
        "puts": sum(result.signal == "put" and result.traded for result in results),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(pnls), 4) if pnls else None,
        "target_hits": sum(result.reason == "two_r_target" for result in traded),
        "total_pnl": str(sum(pnls, Decimal("0")).quantize(CENT)),
        "average_pnl": (
            str(Decimal(str(mean(pnls))).quantize(CENT)) if pnls else None
        ),
        "average_r": (
            str(
                Decimal(str(mean(result.r_multiple for result in traded))).quantize(
                    Decimal("0.0001")
                )
            )
            if traded
            else None
        ),
        "profit_factor": (
            str((gross_profit / gross_loss).quantize(Decimal("0.0001")))
            if gross_loss
            else None
        ),
        "max_drawdown": str(max_drawdown.quantize(CENT)),
    }


def research_splits(
    dates: list[str], oos_start: date | None
) -> dict[str, set[str]]:
    if len(dates) < 10:
        raise ValueError("at least 10 sessions are required for chronological splits")
    if oos_start is None:
        train_end = max(1, int(len(dates) * 0.60))
        validation_end = max(train_end + 1, int(len(dates) * 0.80))
        return {
            "train": set(dates[:train_end]),
            "validation": set(dates[train_end:validation_end]),
            "out_of_sample": set(dates[validation_end:]),
        }

    oos_index = next(
        (index for index, value in enumerate(dates) if date.fromisoformat(value) >= oos_start),
        len(dates),
    )
    if oos_index < 8 or len(dates) - oos_index < 2:
        raise ValueError("explicit OOS boundary needs at least 8 prior and 2 held-out sessions")
    train_end = max(1, int(oos_index * 0.75))
    return {
        "train": set(dates[:train_end]),
        "validation": set(dates[train_end:oos_index]),
        "out_of_sample": set(dates[oos_index:]),
    }


def run_research(
    data: HistoricalData,
    start: date,
    end: date,
    settings: DirectionalSettings,
    reveal_oos: bool,
    oos_start: date | None = None,
    progress: bool = True,
) -> tuple[dict[str, list[DirectionalResult]], dict[str, object]]:
    sessions = data.stock_sessions(start, end)
    dates = list(sessions)
    splits = research_splits(dates, oos_start)
    allowed = splits["train"] | splits["validation"]
    if reveal_oos:
        allowed |= splits["out_of_sample"]

    equity = settings.starting_equity
    all_results: list[DirectionalResult] = []
    volatility = (
        data.volatility() if settings.vix_regime != VixRegime.ANY else None
    )
    for index, trading_date in enumerate(dates, start=1):
        if trading_date not in allowed:
            continue
        bars = sessions[trading_date]
        previous_bars = sessions[dates[index - 2]] if index > 1 else []
        previous_close = previous_bars[-1].close if previous_bars else None
        signal = opening_range_signal(bars, settings, previous_close)
        if signal is None:
            result = DirectionalResult(
                trading_date,
                "none",
                False,
                "no confirmed opening-range breakout",
                equity_after=equity,
            )
        else:
            day = date.fromisoformat(trading_date)
            snapshot = volatility.snapshot(day) if volatility is not None else None
            permitted, blocked = regime_allows(snapshot, settings)
            if not permitted:
                all_results.append(
                    DirectionalResult(
                        trading_date,
                        signal.direction.value,
                        False,
                        f"volatility regime filter: {blocked}",
                        equity_after=equity,
                    )
                )
                continue
            pairs = candidate_pairs(day, signal, settings)
            symbols = sorted({symbol for pair in pairs for symbol in pair[:2]})
            if progress:
                print(
                    f"[{index}/{len(dates)}] {trading_date} {signal.direction.value}",
                    file=sys.stderr,
                )
            option_bars = data.option_bars(day, symbols)
            entry_bars = {
                symbol: next(
                    (
                        bar
                        for bar in values
                        if bar.timestamp.time() == settings.entry_time
                    ),
                    None,
                )
                for symbol, values in option_bars.items()
            }
            spread = select_debit_spread(
                signal,
                pairs,
                {symbol: bar for symbol, bar in entry_bars.items() if bar is not None},
                settings,
            )
            if spread is None:
                result = DirectionalResult(
                    trading_date,
                    signal.direction.value,
                    False,
                    "no candidate met 1:2 reward/risk with available entry bars",
                    equity_after=equity,
                )
            else:
                inferred_close = bars[-1].timestamp + timedelta(minutes=1, hours=-1)
                day_settings = replace(
                    settings,
                    hard_close=min(settings.hard_close, inferred_close.time()),
                )
                result = simulate_debit_spread(
                    trading_date, signal, spread, option_bars, equity, day_settings
                )
        equity = result.equity_after
        all_results.append(result)

    reports = {
        name: [result for result in all_results if result.trading_date in split]
        for name, split in splits.items()
        if name != "out_of_sample" or reveal_oos
    }
    locked_dates = sorted(splits["out_of_sample"])
    preexisting_oos_cache = [
        value
        for value in locked_dates
        if (data.cache_dir / f"options-{value}.json").exists()
    ]
    metadata = {
        "data_source": (
            f"Alpaca {data.config.stock_feed} SPY bars and entitlement-default "
            "historical option trade bars"
        ),
        "fill_model": "synchronized option-bar prices with modeled round-trip slippage",
        "historical_quotes_available": False,
        "signal_model": settings.signal_model.value,
        "sizing": (
            f"fixed {settings.fixed_contracts} contract(s), path independent, "
            "risk budget ignored"
            if settings.fixed_contracts is not None
            else "constant reference equity, path independent"
            if settings.constant_sizing
            else "equity proportional, path dependent"
        ),
        "vix_regime": settings.vix_regime.value,
        "volatility_source": (
            "Cboe published daily index closes, prior session only"
            if volatility is not None
            else "not used"
        ),
        "volatility_coverage": (
            volatility.coverage() if volatility is not None else None
        ),
        "volatility_calendar_sessions": (
            len(volatility.calendar) if volatility is not None else None
        ),
        "oos_revealed": reveal_oos,
        "oos_boundary": str(oos_start) if oos_start else "automatic final 20%",
        "oos_sessions": len(locked_dates),
        "oos_start": locked_dates[0],
        "oos_end": locked_dates[-1],
        "oos_option_cache_preexisting": preexisting_oos_cache,
    }
    return reports, metadata


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Research 0DTE directional SPY debit spreads using Alpaca bars"
    )
    yesterday = datetime.now(ET).date() - timedelta(days=1)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, default=yesterday)
    parser.add_argument("--starting-equity", type=Decimal, default=Decimal("5000"))
    parser.add_argument("--risk-fraction", type=Decimal, default=Decimal("0.02"))
    parser.add_argument("--width", type=Decimal, default=Decimal("3"))
    parser.add_argument("--minimum-reward-risk", type=Decimal, default=Decimal("2"))
    parser.add_argument("--slippage-per-side", type=Decimal, default=Decimal("0.05"))
    parser.add_argument("--fees-per-spread", type=Decimal, default=Decimal("0.10"))
    parser.add_argument("--maximum-contracts", type=int, default=25)
    parser.add_argument(
        "--fixed-contracts",
        type=int,
        help="size every trade identically, ignoring the risk budget; diagnostic only",
    )
    parser.add_argument(
        "--constant-sizing",
        action="store_true",
        help="apply the risk rule to the starting balance so variants stay comparable",
    )
    parser.add_argument(
        "--signal-model",
        type=SignalModel,
        choices=list(SignalModel),
        default=SignalModel.OPENING_RANGE,
    )
    parser.add_argument("--hard-close", type=time.fromisoformat, default=time(15, 0))
    parser.add_argument(
        "--vix-regime",
        type=VixRegime,
        choices=list(VixRegime),
        default=VixRegime.ANY,
        help="prior-close Cboe volatility filter applied before any option data is read",
    )
    parser.add_argument("--minimum-volume-ratio", type=Decimal, default=Decimal("1"))
    parser.add_argument(
        "--minimum-momentum-fraction", type=Decimal, default=Decimal("0.0015")
    )
    parser.add_argument("--minimum-gap-fraction", type=Decimal, default=Decimal("0.002"))
    parser.add_argument(
        "--oos-start",
        type=date.fromisoformat,
        help="explicit first held-out date; recommended after exploratory API probes",
    )
    parser.add_argument("--cache-dir", type=Path, default=Path("state/backtest-cache"))
    parser.add_argument("--trades-output", type=Path)
    parser.add_argument(
        "--reveal-oos",
        action="store_true",
        help="fetch and reveal the held-out dates only after parameters are frozen",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.start >= args.end:
        raise SystemExit("--start must be earlier than --end")
    settings = DirectionalSettings(
        starting_equity=args.starting_equity,
        risk_fraction=args.risk_fraction,
        width=args.width,
        minimum_reward_risk=args.minimum_reward_risk,
        slippage_per_side=args.slippage_per_side,
        fees_per_spread=args.fees_per_spread,
        maximum_contracts=args.maximum_contracts,
        fixed_contracts=args.fixed_contracts,
        constant_sizing=args.constant_sizing,
        signal_model=args.signal_model,
        hard_close=args.hard_close,
        vix_regime=args.vix_regime,
        minimum_volume_ratio=args.minimum_volume_ratio,
        minimum_momentum_fraction=args.minimum_momentum_fraction,
        minimum_gap_fraction=args.minimum_gap_fraction,
    )
    if not (Decimal("0") < settings.risk_fraction <= Decimal("0.05")):
        raise SystemExit("--risk-fraction must be greater than zero and at most 0.05")
    if min(settings.width, settings.minimum_reward_risk) <= 0:
        raise SystemExit("spread width and reward/risk must be positive")
    if settings.fixed_contracts is not None and settings.fixed_contracts < 1:
        raise SystemExit("--fixed-contracts must be at least one")
    if min(
        settings.minimum_volume_ratio,
        settings.minimum_momentum_fraction,
        settings.minimum_gap_fraction,
    ) <= 0:
        raise SystemExit("volume ratio, momentum fraction, and gap fraction must be positive")
    config = Config.from_env()
    reports, metadata = run_research(
        HistoricalData(config, args.cache_dir),
        args.start,
        args.end,
        settings,
        args.reveal_oos,
        args.oos_start,
    )
    output = {
        "warning": (
            "Research approximation only: Alpaca does not expose historical option "
            "quotes on this endpoint, so these are not executable-fill results."
        ),
        "settings": {key: str(value) for key, value in asdict(settings).items()},
        "metadata": metadata,
        "results": {name: directional_metrics(values) for name, values in reports.items()},
    }
    print(json.dumps(output, indent=2))
    if args.trades_output:
        args.trades_output.parent.mkdir(parents=True, exist_ok=True)
        with args.trades_output.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=DirectionalResult.__dataclass_fields__.keys()
            )
            writer.writeheader()
            for values in reports.values():
                writer.writerows(asdict(result) for result in values)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
