from __future__ import annotations

import json
import os
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


class PaperProbeJournal:
    """Append-only ledger for actual Alpaca paper-order outcomes."""

    def __init__(self, path: Path):
        self.path = path

    def write(self, event: str, observed_at: datetime, **details: Any) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"event": event, "observed_at": observed_at, **details}
        encoded = (json.dumps(_json_value(payload), sort_keys=True) + "\n").encode()
        descriptor = os.open(
            self.path,
            os.O_APPEND | os.O_CREAT | os.O_WRONLY,
            0o600,
        )
        try:
            remaining = memoryview(encoded)
            while remaining:
                written = os.write(descriptor, remaining)
                remaining = remaining[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.chmod(self.path, 0o600)

    def summary(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        exits: list[dict[str, Any]] = []
        if self.path.exists():
            with self.path.open(encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise RuntimeError(
                            f"invalid paper probe journal line {line_number}: {exc}"
                        ) from exc
                    event = str(item.get("event", "unknown"))
                    counts[event] = counts.get(event, 0) + 1
                    if event == "probe_exit_filled":
                        exits.append(item)
        pnls = [Decimal(str(item["gross_pnl"])) for item in exits]
        total = sum(pnls, Decimal("0"))
        return {
            "journal": str(self.path),
            "events": counts,
            "submitted": counts.get("probe_submitted", 0),
            "filled": counts.get("probe_filled", 0),
            "unfilled": counts.get("probe_unfilled", 0),
            "exits": len(exits),
            "gross_pnl": str(total.quantize(Decimal("0.01"))),
        }
