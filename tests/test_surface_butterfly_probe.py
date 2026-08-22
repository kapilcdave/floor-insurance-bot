from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from floor_insurance.models import Contract, Quote
from floor_insurance.surface_butterfly_probe import (
    SurfaceProbeConfig,
    SurfaceProbeError,
    SurfaceProbeRunner,
    journal_summary,
    scan_report,
    scan_surface,
    select_candidate,
)

ET = ZoneInfo("America/New_York")
DAY = "2026-08-21"


def probe_config(tmp_path: Path, **changes) -> SurfaceProbeConfig:
    config = SurfaceProbeConfig(
        api_key="paper-key",
        api_secret="paper-secret",
        trading_base_url="https://paper-api.alpaca.markets",
        data_base_url="https://data.alpaca.markets",
        request_timeout_seconds=10,
        probe_confirmed=True,
        state_path=tmp_path / "state.json",
        journal_path=tmp_path / "events.jsonl",
    )
    return replace(config, **changes)


def contracts(kind: str) -> list[Contract]:
    return [
        Contract(f"SPY-{kind}-{strike}", Decimal(str(strike)), DAY)
        for strike in (99, 100, 101)
    ]


def candidate_quotes(now: datetime) -> dict[str, Quote]:
    values = {
        "SPY-C-99": ("0.50", "0.52"),
        "SPY-C-100": ("0.40", "0.42"),
        "SPY-C-101": ("0.30", "0.32"),
        "SPY-P-99": ("0.40", "0.42"),
        "SPY-P-100": ("0.30", "0.32"),
        "SPY-P-101": ("0.36", "0.38"),
    }
    return {
        symbol: Quote(Decimal(bid), Decimal(ask), now)
        for symbol, (bid, ask) in values.items()
    }


class FakeClient:
    def __init__(self, now: datetime):
        self.now = now
        self.quotes = candidate_quotes(now)
        self.orders: dict[str, dict] = {}
        self.submissions: list[dict] = []
        self.canceled: list[str] = []

    def clock(self):
        return {"is_open": True}

    def latest_underlying_trade(self, _trading_date):
        return Decimal("100.20"), self.now

    def option_contracts(self, _trading_date, option_type):
        return contracts("C" if option_type == "call" else "P")

    def option_quotes(self, symbols, *, allow_missing=False):
        return {symbol: self.quotes[symbol] for symbol in symbols if symbol in self.quotes}

    def submit_multileg(self, **kwargs):
        self.submissions.append(kwargs)
        order = {
            "id": f"order-{len(self.submissions)}",
            "status": "new",
            "filled_qty": "0",
        }
        self.orders[order["id"]] = order
        return order

    def order(self, order_id):
        return self.orders[order_id]

    def order_by_client_id(self, client_id):
        return next(
            (
                order
                for order, submission in zip(
                    self.orders.values(), self.submissions, strict=True
                )
                if submission["client_order_id"] == client_id
            ),
            None,
        )

    def cancel_order(self, order_id):
        self.canceled.append(order_id)
        self.orders[order_id]["status"] = "canceled"


def test_selects_cheaper_executable_call_butterfly(tmp_path):
    now = datetime(2026, 8, 21, 11, 0, tzinfo=ET)
    config = probe_config(tmp_path)

    candidate, evaluated = select_candidate(
        spot=Decimal("100.20"),
        calls=contracts("C"),
        puts=contracts("P"),
        quotes=candidate_quotes(now),
        now=now,
        config=config,
    )

    assert evaluated == 1
    assert candidate is not None
    assert candidate.kind == "C"
    assert candidate.center == Decimal("100")
    assert candidate.call_debit == Decimal("0.04")
    assert candidate.put_debit == Decimal("0.20")
    assert candidate.parity_gap == Decimal("0.16")
    assert candidate.limit_price == Decimal("0.04")


def test_stale_or_wide_leg_prevents_candidate(tmp_path):
    now = datetime(2026, 8, 21, 11, 0, tzinfo=ET)
    config = probe_config(tmp_path)
    quotes = candidate_quotes(now)
    quotes["SPY-C-99"] = Quote(
        Decimal("0.20"), Decimal("0.52"), now - timedelta(seconds=91)
    )

    candidate, evaluated = select_candidate(
        spot=Decimal("100.20"),
        calls=contracts("C"),
        puts=contracts("P"),
        quotes=quotes,
        now=now,
        config=config,
    )

    assert candidate is None
    assert evaluated == 0


def test_scan_outside_window_is_read_only_and_not_forward_evidence(tmp_path):
    now = datetime(2026, 8, 21, 14, 20, tzinfo=ET)
    client = FakeClient(now)

    result = scan_surface(client, probe_config(tmp_path), now)
    report = scan_report(result)

    assert result.candidate is not None
    assert report["in_entry_window"] is False
    assert report["counts_toward_forward_test"] is False
    assert report["center_diagnostics"][0]["result"] in {
        "missing_contract",
        "passed",
    }
    assert client.submissions == []


def test_runner_submits_once_and_cancels_unchanged_limit(tmp_path):
    entered = datetime(2026, 8, 21, 11, 0, tzinfo=ET)
    client = FakeClient(entered)
    runner = SurfaceProbeRunner(probe_config(tmp_path), client)

    runner.tick(entered)

    assert len(client.submissions) == 1
    submission = client.submissions[0]
    assert submission["quantity"] == 1
    assert submission["price"] == Decimal("0.04")
    assert [leg["ratio_qty"] for leg in submission["legs"]] == ["1", "2", "1"]

    client.now = entered + timedelta(seconds=60)
    runner.tick(client.now)
    assert client.canceled == ["order-1"]

    client.now += timedelta(seconds=5)
    runner.tick(client.now)
    assert runner.store.load(DAY).phase == "done"
    summary = journal_summary(runner.config.journal_path)
    assert summary["submitted"] == 1
    assert summary["filled"] == 0
    assert summary["unfilled"] == 1


def test_runner_records_fill_noon_markout_exit_and_signed_pnl(tmp_path):
    entered = datetime(2026, 8, 21, 11, 0, tzinfo=ET)
    client = FakeClient(entered)
    runner = SurfaceProbeRunner(probe_config(tmp_path), client)
    runner.tick(entered)
    client.orders["order-1"].update(
        status="filled", filled_qty="1", filled_avg_price="0.04"
    )

    client.now = entered + timedelta(seconds=5)
    client.quotes = candidate_quotes(client.now)
    runner.tick(client.now)
    assert runner.store.load(DAY).phase == "open"

    client.now = entered.replace(hour=12)
    client.quotes = candidate_quotes(client.now)
    runner.tick(client.now)
    assert len(client.submissions) == 2
    assert client.submissions[1]["price"] is None
    assert [leg["position_intent"] for leg in client.submissions[1]["legs"]] == [
        "sell_to_close",
        "buy_to_close",
        "sell_to_close",
    ]
    client.orders["order-2"].update(
        status="filled", filled_qty="1", filled_avg_price="-0.10"
    )

    client.now += timedelta(seconds=5)
    runner.tick(client.now)

    assert runner.store.load(DAY).phase == "done"
    summary = journal_summary(runner.config.journal_path)
    assert summary["filled"] == 1
    assert summary["exits"] == 1
    assert summary["gross_pnl"] == "6.00"
    assert summary["modeled_fees"] == "0.20"
    assert summary["modeled_net_pnl"] == "5.80"
    assert summary["modeled_average_pnl"] == "5.80"
    assert summary["average_adverse_fill_per_unit"] == "0.0000"


def test_mechanics_probe_can_enter_outside_locked_window_and_labels_events(tmp_path):
    entered = datetime(2026, 8, 21, 13, 0, tzinfo=ET)
    client = FakeClient(entered)
    config = probe_config(tmp_path).as_mechanics_only()
    config = replace(
        config,
        state_path=tmp_path / "mechanics-state.json",
        journal_path=tmp_path / "mechanics-events.jsonl",
    )
    runner = SurfaceProbeRunner(config, client)

    runner.tick(entered)
    client.orders["order-1"].update(
        status="filled", filled_qty="1", filled_avg_price="0.04"
    )
    client.now = entered + timedelta(seconds=5)
    client.quotes = candidate_quotes(client.now)
    runner.tick(client.now)

    state = runner.store.load(DAY)
    assert state.phase == "open"
    assert state.scheduled_exit_at == "2026-08-21T14:00:05-04:00"
    events = config.journal_path.read_text(encoding="utf-8")
    assert '"cohort": "mechanics_only"' in events


def test_mechanics_probe_refuses_closed_market(tmp_path):
    now = datetime(2026, 8, 21, 16, 30, tzinfo=ET)
    client = FakeClient(now)
    client.clock = lambda: {"is_open": False}
    config = probe_config(tmp_path).as_mechanics_only()
    runner = SurfaceProbeRunner(config, client)

    with pytest.raises(SurfaceProbeError, match="open regular options session"):
        runner.tick(now)


def test_runner_refuses_live_or_unconfirmed_configuration(tmp_path):
    config = probe_config(tmp_path)
    with pytest.raises(SurfaceProbeError, match="paper-only"):
        replace(
            config,
            paper=False,
            trading_base_url="https://api.alpaca.markets",
        ).validate()
    with pytest.raises(SurfaceProbeError, match="submission blocked"):
        replace(config, probe_confirmed=False).authorize_orders()


def intraday_config(tmp_path: Path) -> SurfaceProbeConfig:
    config = probe_config(tmp_path).as_intraday_forward()
    return replace(
        config,
        state_path=tmp_path / "intraday-state.json",
        journal_path=tmp_path / "intraday-events.jsonl",
    )


def test_intraday_forward_waits_after_no_signal_then_takes_next_hour(tmp_path):
    ten = datetime(2026, 8, 21, 10, 0, tzinfo=ET)
    client = FakeClient(ten)
    no_gap = candidate_quotes(ten)
    for suffix in ("99", "100", "101"):
        no_gap[f"SPY-P-{suffix}"] = no_gap[f"SPY-C-{suffix}"]
    client.quotes = no_gap
    runner = SurfaceProbeRunner(intraday_config(tmp_path), client)

    runner.tick(ten)

    state = runner.store.load(DAY)
    assert state.phase == "idle"
    assert state.attempted_slots == ["10:00"]
    assert client.submissions == []

    eleven = ten.replace(hour=11)
    client.now = eleven
    client.quotes = candidate_quotes(eleven)
    runner.tick(eleven)

    state = runner.store.load(DAY)
    assert state.phase == "entry_pending"
    assert state.signal_slot == "11:00"
    assert state.attempted_slots == ["10:00", "11:00"]
    assert len(client.submissions) == 1
    assert client.submissions[0]["client_order_id"].startswith("surface-intraday-")
    assert '"cohort": "intraday_forward"' in runner.config.journal_path.read_text()


def test_intraday_forward_records_missed_slot_without_backfill(tmp_path):
    now = datetime(2026, 8, 21, 10, 30, tzinfo=ET)
    client = FakeClient(now)
    runner = SurfaceProbeRunner(intraday_config(tmp_path), client)

    runner.tick(now)

    state = runner.store.load(DAY)
    assert state.phase == "idle"
    assert state.attempted_slots == ["10:00"]
    assert client.submissions == []


def test_intraday_forward_fill_exits_one_hour_later(tmp_path):
    entered = datetime(2026, 8, 21, 10, 0, tzinfo=ET)
    client = FakeClient(entered)
    runner = SurfaceProbeRunner(intraday_config(tmp_path), client)
    runner.tick(entered)
    client.orders["order-1"].update(
        status="filled", filled_qty="1", filled_avg_price="0.05"
    )

    filled = entered + timedelta(seconds=5)
    client.now = filled
    client.quotes = candidate_quotes(filled)
    runner.tick(filled)
    state = runner.store.load(DAY)
    assert state.scheduled_exit_at == "2026-08-21T11:00:05-04:00"

    exit_at = filled + timedelta(hours=1)
    client.now = exit_at
    client.quotes = candidate_quotes(exit_at)
    runner.tick(exit_at)

    assert len(client.submissions) == 2
    assert client.submissions[-1]["price"] is None
