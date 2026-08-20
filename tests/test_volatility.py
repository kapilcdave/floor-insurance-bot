from dataclasses import replace
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from floor_insurance.directional import DirectionalSettings, VixRegime, regime_allows
from floor_insurance.volatility import (
    SERIES_FILES,
    VolatilityDataError,
    VolatilityHistory,
    VolatilitySnapshot,
    parse_history,
)

OHLC_CSV = """DATE,OPEN,HIGH,LOW,CLOSE
01/02/2026,17.240000,17.500000,17.100000,17.400000
01/05/2026,18.190000,18.400000,18.000000,18.300000
bad row
01/06/2026,,,,
01/07/2026,19.000000,19.500000,18.800000,19.250000
"""
CLOSE_ONLY_CSV = """DATE,VVIX
01/02/2026,90.000000
01/05/2026,95.000000
"""


def series(closes: dict[str, str]) -> dict[date, Decimal]:
    return {date.fromisoformat(day): Decimal(value) for day, value in closes.items()}


def aligned(closes: dict[str, str]) -> dict[str, dict[date, Decimal]]:
    """Build a complex whose calendar series all cover the same sessions."""

    parsed = series(closes)
    return {
        "vix": parsed,
        "vix9d": dict(parsed),
        "vix3m": {day: Decimal("20") for day in parsed},
    }


def history(**overrides: dict[date, Decimal]) -> VolatilityHistory:
    base = {
        "vix": series({"2026-01-05": "20", "2026-01-06": "16"}),
        "vix9d": series({"2026-01-05": "22", "2026-01-06": "14"}),
        "vix3m": series({"2026-01-05": "20", "2026-01-06": "20"}),
        "vix1d": series({"2026-01-05": "26", "2026-01-06": "10"}),
        "vvix": series({"2026-01-05": "100", "2026-01-06": "90"}),
    }
    base.update(overrides)
    return VolatilityHistory(base)


def test_parse_history_reads_both_cboe_layouts_and_skips_unusable_rows():
    ohlc = parse_history(OHLC_CSV)
    assert ohlc == {
        date(2026, 1, 2): Decimal("17.400000"),
        date(2026, 1, 5): Decimal("18.300000"),
        date(2026, 1, 7): Decimal("19.250000"),
    }
    assert parse_history(CLOSE_ONLY_CSV)[date(2026, 1, 5)] == Decimal("95.000000")


def test_parse_history_rejects_files_without_a_date_column():
    with pytest.raises(VolatilityDataError):
        parse_history("SYMBOL,CLOSE\nVIX,17.4\n")


def test_parse_history_rejects_empty_and_unusable_files():
    with pytest.raises(VolatilityDataError):
        parse_history("\n \n")
    with pytest.raises(VolatilityDataError):
        parse_history("DATE,CLOSE\n2026-01-02,17.4\n")


def test_parse_history_drops_short_and_non_positive_rows():
    parsed = parse_history(
        "DATE,OPEN,HIGH,LOW,CLOSE\n"
        "01/02/2026\n"
        "01/05/2026,1,1,1,0.000000\n"
        "01/06/2026,1,1,1,-4.000000\n"
        "01/07/2026,1,1,1,19.250000\n"
    )
    assert parsed == {date(2026, 1, 7): Decimal("19.250000")}


def test_snapshot_is_withheld_when_a_series_lacks_the_reference_close():
    sparse = VolatilityHistory({"vix": series({"2026-01-05": "20"})})
    assert sparse.snapshot(date(2026, 1, 5)) is None
    assert sparse.percentile(date(2026, 1, 1)) is None


def test_snapshot_only_uses_closes_strictly_before_the_trading_date():
    snapshot = history().snapshot(date(2026, 1, 6))
    assert snapshot is not None
    assert snapshot.as_of == date(2026, 1, 5)
    assert snapshot.vix == Decimal("20")
    assert snapshot.term_slope == Decimal("1.1000")
    assert snapshot.one_day_ratio == Decimal("1.1818")


def test_snapshot_is_withheld_when_the_last_close_is_stale():
    assert history().snapshot(date(2026, 1, 20)) is None


def test_snapshot_is_withheld_before_any_published_close():
    assert history().snapshot(date(2026, 1, 5)) is None


def test_percentile_requires_a_full_trailing_window():
    assert history().percentile(date(2026, 1, 6), window=252) is None
    ascending = VolatilityHistory(
        aligned({f"2026-01-{day:02d}": str(day) for day in range(1, 11)})
    )
    assert ascending.percentile(date(2026, 1, 10), window=10) == Decimal("1.0000")
    assert ascending.percentile(date(2026, 1, 5), window=5) == Decimal("1.0000")
    descending = VolatilityHistory(
        aligned({f"2026-01-{day:02d}": str(11 - day) for day in range(1, 11)})
    )
    assert descending.percentile(date(2026, 1, 10), window=10) == Decimal("0.1000")


def test_holiday_rows_published_only_by_vix_are_excluded_from_the_calendar():
    complex_ = aligned({"2026-01-02": "17", "2026-01-05": "18", "2026-01-06": "19"})
    # Cboe publishes a VIX row on market holidays; VIX9D and VIX3M do not.
    complex_["vix"][date(2026, 1, 1)] = Decimal("16")
    loaded = VolatilityHistory(complex_)
    assert date(2026, 1, 1) not in loaded.calendar
    assert loaded.calendar == [date(2026, 1, 2), date(2026, 1, 5), date(2026, 1, 6)]
    snapshot = loaded.snapshot(date(2026, 1, 5))
    assert snapshot is not None
    assert snapshot.as_of == date(2026, 1, 2)
    assert snapshot.term_slope is not None


def test_calendar_requires_the_series_to_overlap():
    with pytest.raises(VolatilityDataError):
        VolatilityHistory(
            {
                "vix": series({"2026-01-05": "20"}),
                "vix9d": series({"2026-01-06": "20"}),
            }
        )


def test_missing_optional_series_leaves_derived_ratios_unset():
    partial = VolatilityHistory({"vix": series({"2026-01-05": "20"})})
    snapshot = partial.snapshot(date(2026, 1, 6))
    assert snapshot is not None
    assert snapshot.term_slope is None
    assert snapshot.one_day_ratio is None
    assert snapshot.as_dict()["term_slope"] is None


def test_reference_series_is_mandatory():
    with pytest.raises(VolatilityDataError):
        VolatilityHistory({"vix9d": series({"2026-01-05": "20"})})


def test_load_prefers_the_cache_and_never_touches_the_network(tmp_path):
    class Forbidden:
        def get(self, *args, **kwargs):  # pragma: no cover - must not run
            raise AssertionError("cached volatility history must not be downloaded")

    for name in SERIES_FILES:
        body = CLOSE_ONLY_CSV if name == "vvix" else OHLC_CSV
        (tmp_path / f"cboe-{name}.csv").write_text(body, encoding="utf-8")

    loaded = VolatilityHistory.load(tmp_path, session=Forbidden())
    assert loaded.coverage()["vix"]["sessions"] == 3
    assert loaded.coverage()["vvix"]["last"] == "2026-01-05"


def test_load_refuses_to_invent_history_when_downloads_are_disabled(tmp_path):
    with pytest.raises(VolatilityDataError):
        VolatilityHistory.load(tmp_path, download=False)


def test_download_caches_each_series_with_owner_only_permissions(tmp_path):
    class Recorder:
        def __init__(self):
            self.urls = []

        def get(self, url, timeout=None, headers=None):
            self.urls.append(url)
            body = CLOSE_ONLY_CSV if url.endswith("VVIX_History.csv") else OHLC_CSV
            return SimpleNamespace(
                text=body, raise_for_status=lambda: None, status_code=200
            )

    recorder = Recorder()
    loaded = VolatilityHistory.load(tmp_path, session=recorder)
    assert len(recorder.urls) == len(SERIES_FILES)
    assert loaded.calendar == [date(2026, 1, 2), date(2026, 1, 5), date(2026, 1, 7)]
    for name in SERIES_FILES:
        cache = tmp_path / f"cboe-{name}.csv"
        assert cache.exists()
        assert cache.stat().st_mode & 0o777 == 0o600

    # A second load must be served entirely from the cache.
    replayed = VolatilityHistory.load(tmp_path, session=recorder)
    assert len(recorder.urls) == len(SERIES_FILES)
    assert replayed.calendar == loaded.calendar


def test_any_regime_needs_no_volatility_data():
    assert regime_allows(None, DirectionalSettings()) == (True, "")


def test_every_filter_blocks_a_session_without_volatility_context():
    for regime in VixRegime:
        if regime == VixRegime.ANY:
            continue
        permitted, reason = regime_allows(
            None, DirectionalSettings(vix_regime=regime)
        )
        assert permitted is False
        assert reason


def test_filters_block_when_the_required_series_is_absent():
    snapshot = VolatilitySnapshot(as_of=date(2026, 1, 5), vix=Decimal("20"))
    for regime in (
        VixRegime.LOW_PERCENTILE,
        VixRegime.CONTANGO,
        VixRegime.CHEAP_ONE_DAY,
    ):
        permitted, reason = regime_allows(
            snapshot, DirectionalSettings(vix_regime=regime)
        )
        assert permitted is False
        assert "unavailable" in reason


@pytest.mark.parametrize(
    ("below", "at_or_above", "field", "under", "over"),
    (
        (
            VixRegime.LOW_PERCENTILE,
            VixRegime.HIGH_PERCENTILE,
            "vix_percentile",
            Decimal("0.4"),
            Decimal("0.5"),
        ),
        (
            VixRegime.CONTANGO,
            VixRegime.BACKWARDATION,
            "term_slope",
            Decimal("0.9"),
            Decimal("1"),
        ),
        (
            VixRegime.CHEAP_ONE_DAY,
            VixRegime.RICH_ONE_DAY,
            "one_day_ratio",
            Decimal("0.8"),
            Decimal("1"),
        ),
    ),
)
def test_each_regime_family_partitions_sessions_exactly_once(
    below, at_or_above, field, under, over
):
    for value, expected_half in ((under, below), (over, at_or_above)):
        snapshot = replace(
            VolatilitySnapshot(as_of=date(2026, 1, 5), vix=Decimal("20")),
            **{field: value},
        )
        outcomes = {
            half: regime_allows(snapshot, DirectionalSettings(vix_regime=half))[0]
            for half in (below, at_or_above)
        }
        assert outcomes[expected_half] is True
        assert sum(outcomes.values()) == 1
