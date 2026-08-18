from __future__ import annotations

import json
import os
from pathlib import Path

from .models import DailyState


class StateStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self, trading_date: str) -> DailyState:
        if not self.path.exists():
            return DailyState(trading_date=trading_date)
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            state = DailyState.from_dict(data)
        except (OSError, ValueError, TypeError) as exc:
            raise RuntimeError(f"cannot read state file {self.path}: {exc}") from exc
        if state.trading_date != trading_date:
            return DailyState(trading_date=trading_date)
        return state

    def save(self, state: DailyState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(state.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.path)

