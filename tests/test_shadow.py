import json
import stat
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from floor_insurance.shadow import ShadowJournal


def test_journal_serializes_decimals_and_is_private(tmp_path):
    path = tmp_path / "nested" / "shadow.jsonl"
    journal = ShadowJournal(path)
    journal.write(
        "shadow_exit",
        datetime(2026, 8, 18, tzinfo=timezone.utc),
        net_pnl=Decimal("12.34"),
        reason="take_profit",
    )
    item = json.loads(path.read_text())
    assert item["net_pnl"] == "12.34"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert journal.summary()["total_net_pnl"] == "12.34"


def test_summary_rejects_corrupt_journal(tmp_path):
    path = tmp_path / "shadow.jsonl"
    path.write_text("not-json\n")
    with pytest.raises(RuntimeError, match="line 1"):
        ShadowJournal(path).summary()
