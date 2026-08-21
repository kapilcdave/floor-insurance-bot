from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any


class Phase(StrEnum):
    IDLE = "idle"
    ENTRY_PENDING = "entry_pending"
    OPEN = "open"
    EXIT_PENDING = "exit_pending"
    DONE = "done"


@dataclass(frozen=True)
class Contract:
    symbol: str
    strike: Decimal
    expiration_date: str


@dataclass(frozen=True)
class Quote:
    bid: Decimal
    ask: Decimal
    timestamp: datetime


@dataclass
class DailyState:
    trading_date: str
    phase: Phase = Phase.IDLE
    losses: int = 0
    entry_submissions: int = 0
    short_symbol: str | None = None
    long_symbol: str | None = None
    short_strike: str | None = None
    long_strike: str | None = None
    quantity: int = 0
    entry_credit: str | None = None
    entry_limit_credit: str | None = None
    entry_submitted_at: str | None = None
    entry_cancel_requested_at: str | None = None
    active_order_id: str | None = None
    active_client_order_id: str | None = None
    exit_reason: str | None = None
    shadow: bool = False
    entry_filled_at: str | None = None
    entry_underlying: str | None = None
    last_event: str = "fresh day"
    event_history: list[dict[str, Any]] = field(default_factory=list)

    def event(self, name: str, at: datetime, **details: Any) -> None:
        self.last_event = name
        self.event_history.append(
            {"at": at.isoformat(), "event": name, **details}
        )
        self.event_history = self.event_history[-100:]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["phase"] = self.phase.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DailyState":
        copy = dict(data)
        copy["phase"] = Phase(copy.get("phase", Phase.IDLE))
        return cls(**copy)
