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


class ShadowJournal:
    """Append-only JSONL journal of every quote sample and virtual fill."""

    def __init__(self, path: Path):
        self.path = path

    def write(self, event: str, observed_at: datetime, **details: Any) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "event": event,
            "observed_at": observed_at,
            **details,
        }
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
        exits: list[dict[str, Any]] = []
        entries = 0
        observations = 0
        skips = 0
        errors = 0
        if self.path.exists():
            with self.path.open(encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise RuntimeError(
                            f"invalid shadow journal line {line_number}: {exc}"
                        ) from exc
                    if item.get("event") == "shadow_entry":
                        entries += 1
                    elif item.get("event") == "shadow_observation":
                        observations += 1
                    elif item.get("event") == "shadow_skip":
                        skips += 1
                    elif item.get("event") == "shadow_error":
                        errors += 1
                    elif item.get("event") == "shadow_exit":
                        exits.append(item)
        pnls = [Decimal(item["net_pnl"]) for item in exits]
        reasons: dict[str, int] = {}
        for item in exits:
            reason = str(item.get("reason", "unknown"))
            reasons[reason] = reasons.get(reason, 0) + 1
        total = sum(pnls, Decimal("0"))
        return {
            "journal": str(self.path),
            "entries": entries,
            "observations": observations,
            "skips": skips,
            "errors": errors,
            "trades": len(exits),
            "entries_without_exit": max(0, entries - len(exits)),
            "wins": sum(pnl > 0 for pnl in pnls),
            "losses": sum(pnl < 0 for pnl in pnls),
            "flat": sum(pnl == 0 for pnl in pnls),
            "win_rate": (
                round(sum(pnl > 0 for pnl in pnls) / len(pnls), 4)
                if pnls
                else None
            ),
            "total_net_pnl": str(total.quantize(Decimal("0.01"))),
            "average_net_pnl": (
                str((total / len(pnls)).quantize(Decimal("0.01"))) if pnls else None
            ),
            "exit_reasons": reasons,
            "first_exit": exits[0]["observed_at"] if exits else None,
            "last_exit": exits[-1]["observed_at"] if exits else None,
        }
