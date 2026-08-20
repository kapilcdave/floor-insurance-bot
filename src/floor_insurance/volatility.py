"""Cboe daily volatility-index history.

This module supplies the only research input that is not derived from the SPY
price path: the published Cboe volatility complex. Snapshots are always taken
from the most recent close *strictly before* the trading date, so a signal that
consults them cannot see the session it is trading.
"""

from __future__ import annotations

import csv
import io
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import requests

CBOE_BASE = "https://cdn.cboe.com/api/global/us_indices/daily_prices"
SERIES_FILES = {
    "vix1d": "VIX1D_History.csv",
    "vix9d": "VIX9D_History.csv",
    "vix": "VIX_History.csv",
    "vix3m": "VIX3M_History.csv",
    "vvix": "VVIX_History.csv",
}
REFERENCE_SERIES = "vix"
CALENDAR_SERIES = ("vix", "vix9d", "vix3m")
PERCENTILE_WINDOW = 252
MAX_STALENESS_DAYS = 5
RATIO = Decimal("0.0001")


class VolatilityDataError(RuntimeError):
    """Raised when volatility history is absent or unusable."""


@dataclass(frozen=True)
class VolatilitySnapshot:
    """Prior-session volatility state available before the open."""

    as_of: date
    vix: Decimal
    vix1d: Decimal | None = None
    vix9d: Decimal | None = None
    vix3m: Decimal | None = None
    vvix: Decimal | None = None
    vix_percentile: Decimal | None = None
    term_slope: Decimal | None = None
    one_day_ratio: Decimal | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {
            "as_of": self.as_of.isoformat(),
            "vix": str(self.vix),
            "vix1d": str(self.vix1d) if self.vix1d is not None else None,
            "vix9d": str(self.vix9d) if self.vix9d is not None else None,
            "vix3m": str(self.vix3m) if self.vix3m is not None else None,
            "vvix": str(self.vvix) if self.vvix is not None else None,
            "vix_percentile": (
                str(self.vix_percentile) if self.vix_percentile is not None else None
            ),
            "term_slope": str(self.term_slope) if self.term_slope is not None else None,
            "one_day_ratio": (
                str(self.one_day_ratio) if self.one_day_ratio is not None else None
            ),
        }


def parse_history(text: str) -> dict[date, Decimal]:
    """Parse a Cboe daily-price CSV into closes keyed by session date.

    Cboe publishes either ``DATE,OPEN,HIGH,LOW,CLOSE`` or ``DATE,<INDEX>``; the
    final column is the close in both layouts.
    """

    reader = csv.reader(io.StringIO(text))
    rows = [row for row in reader if row and row[0].strip()]
    if not rows:
        raise VolatilityDataError("volatility history is empty")
    header = [cell.strip().upper() for cell in rows[0]]
    if header[0] != "DATE":
        raise VolatilityDataError("volatility history is missing a DATE column")
    closes: dict[date, Decimal] = {}
    for row in rows[1:]:
        if len(row) < 2:
            continue
        raw_date = row[0].strip()
        raw_close = row[-1].strip()
        if not raw_close:
            continue
        try:
            session = datetime.strptime(raw_date, "%m/%d/%Y").date()
            close = Decimal(raw_close)
        except (ValueError, ArithmeticError):
            continue
        if close <= 0:
            continue
        closes[session] = close
    if not closes:
        raise VolatilityDataError("volatility history contained no usable closes")
    return closes


class VolatilityHistory:
    """Cached, offline-reproducible view of the Cboe volatility complex."""

    def __init__(self, series: dict[str, dict[date, Decimal]]):
        if REFERENCE_SERIES not in series:
            raise VolatilityDataError(f"the {REFERENCE_SERIES} series is required")
        self.series = series
        self._calendar = self._build_calendar(series)

    @staticmethod
    def _build_calendar(series: dict[str, dict[date, Decimal]]) -> list[date]:
        """Sessions on which the whole long-history complex published a close.

        ``VIX_History.csv`` carries rows on market holidays while VIX9D and
        VIX3M do not, so the raw VIX dates are not a trading calendar. Taking
        the intersection keeps every snapshot internally consistent and stops
        holiday rows from padding the trailing percentile window.
        """

        present = [series[name] for name in CALENDAR_SERIES if name in series]
        common = set(present[0])
        for closes in present[1:]:
            common &= set(closes)
        if not common:
            raise VolatilityDataError(
                "the volatility series share no common session dates"
            )
        return sorted(common)

    @property
    def calendar(self) -> list[date]:
        return self._calendar

    @classmethod
    def load(
        cls,
        cache_dir: Path,
        *,
        download: bool = True,
        session: requests.Session | None = None,
        timeout: int = 30,
    ) -> "VolatilityHistory":
        client = session or requests
        loaded: dict[str, dict[date, Decimal]] = {}
        for name, filename in SERIES_FILES.items():
            cache = cache_dir / f"cboe-{name}.csv"
            if cache.exists():
                text = cache.read_text(encoding="utf-8")
            elif download:
                response = client.get(
                    f"{CBOE_BASE}/{filename}",
                    timeout=timeout,
                    headers={"User-Agent": "floor-insurance-bot/0.4"},
                )
                response.raise_for_status()
                text = response.text
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(text, encoding="utf-8")
                cache.chmod(0o600)
            elif name == REFERENCE_SERIES:
                raise VolatilityDataError(
                    f"{cache} is absent and downloads are disabled"
                )
            else:
                continue
            loaded[name] = parse_history(text)
        return cls(loaded)

    def previous_session(self, trading_date: date) -> date | None:
        index = bisect_right(self._calendar, trading_date - timedelta(days=1))
        if index == 0:
            return None
        return self._calendar[index - 1]

    def percentile(self, as_of: date, window: int = PERCENTILE_WINDOW) -> Decimal | None:
        index = bisect_right(self._calendar, as_of)
        if index == 0:
            return None
        history = self._calendar[max(0, index - window) : index]
        if len(history) < window:
            return None
        closes = [self.series[REFERENCE_SERIES][day] for day in history]
        current = closes[-1]
        rank = sum(1 for value in closes if value <= current)
        return (Decimal(rank) / Decimal(len(closes))).quantize(RATIO)

    def snapshot(
        self,
        trading_date: date,
        *,
        max_staleness_days: int = MAX_STALENESS_DAYS,
        percentile_window: int = PERCENTILE_WINDOW,
    ) -> VolatilitySnapshot | None:
        as_of = self.previous_session(trading_date)
        if as_of is None:
            return None
        if (trading_date - as_of).days > max_staleness_days:
            return None
        vix = self.series[REFERENCE_SERIES][as_of]
        values = {
            name: self.series.get(name, {}).get(as_of)
            for name in SERIES_FILES
            if name != REFERENCE_SERIES
        }
        vix9d = values.get("vix9d")
        vix3m = values.get("vix3m")
        vix1d = values.get("vix1d")
        term_slope = (
            (vix9d / vix3m).quantize(RATIO)
            if vix9d is not None and vix3m is not None and vix3m > 0
            else None
        )
        one_day_ratio = (
            (vix1d / vix9d).quantize(RATIO)
            if vix1d is not None and vix9d is not None and vix9d > 0
            else None
        )
        return VolatilitySnapshot(
            as_of=as_of,
            vix=vix,
            vix1d=vix1d,
            vix9d=vix9d,
            vix3m=vix3m,
            vvix=values.get("vvix"),
            vix_percentile=self.percentile(as_of, percentile_window),
            term_slope=term_slope,
            one_day_ratio=one_day_ratio,
        )

    def coverage(self) -> dict[str, dict[str, str | int]]:
        return {
            name: {
                "sessions": len(closes),
                "first": min(closes).isoformat(),
                "last": max(closes).isoformat(),
            }
            for name, closes in sorted(self.series.items())
        }
