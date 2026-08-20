"""Completed-session trend signals for the 0DTE entry gate."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class TrendMode(StrEnum):
    ABOVE = "above"
    CROSSOVER = "crossover"


@dataclass(frozen=True)
class TrendSignal:
    mode: TrendMode
    eligible: bool
    close: Decimal
    moving_average: Decimal
    previous_close: Decimal | None = None
    previous_moving_average: Decimal | None = None


def simple_moving_average(values: list[Decimal], window: int) -> Decimal:
    if window < 2:
        raise ValueError("moving-average window must be at least 2")
    if len(values) < window:
        raise ValueError(
            f"moving-average signal needs {window} completed closes; got {len(values)}"
        )
    selected = values[-window:]
    return sum(selected, Decimal("0")) / Decimal(window)


def trend_signal(
    closes: list[Decimal],
    *,
    window: int = 20,
    mode: TrendMode = TrendMode.ABOVE,
) -> TrendSignal:
    """Evaluate a signal from closes that all predate the entry session.

    ``ABOVE`` permits every session following a close above its contemporaneous
    moving average. ``CROSSOVER`` permits only the first such session after the
    previous close was at or below its own moving average.
    """

    mode = TrendMode(mode)
    current_average = simple_moving_average(closes, window)
    current_close = closes[-1]
    if mode == TrendMode.ABOVE:
        return TrendSignal(
            mode=mode,
            eligible=current_close > current_average,
            close=current_close,
            moving_average=current_average,
        )

    if len(closes) < window + 1:
        raise ValueError(
            f"crossover signal needs {window + 1} completed closes; got {len(closes)}"
        )
    previous_values = closes[:-1]
    previous_average = simple_moving_average(previous_values, window)
    previous_close = previous_values[-1]
    return TrendSignal(
        mode=mode,
        eligible=(
            current_close > current_average
            and previous_close <= previous_average
        ),
        close=current_close,
        moving_average=current_average,
        previous_close=previous_close,
        previous_moving_average=previous_average,
    )
