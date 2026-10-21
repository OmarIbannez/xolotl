"""Bucket choice and window arithmetic (spec §8.2, §5.2 ``agg_bucket``)."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from xolotl.modules.monitoring.aggregates.declarations import (
    AggregateDeclaration,
    AggregateFunction,
    Measure,
    WindowKind,
)
from xolotl.modules.monitoring.aggregates.windows import (
    MEXICO_CITY_ZONE,
    accum_window,
    bucket_kind_for,
    bucket_start_for,
    period_bucket_start,
    window_bounds,
)
from xolotl.modules.monitoring.enums import BucketKind
from xolotl.modules.monitoring.txn.schema import KeyKind
from xolotl.platform.calendar import MEXICO_CITY


def _declared(window_kind: WindowKind, window: str) -> AggregateDeclaration:
    return AggregateDeclaration(
        name="w",
        function=AggregateFunction.SUM,
        measure=Measure.AMOUNT_MINOR,
        unit_key=None,
        key_kind=KeyKind.PARTY,
        window_kind=window_kind,
        window=window,
    )


@pytest.mark.parametrize(
    ("window_kind", "window", "kind"),
    [
        (WindowKind.DURATION, "PT1H", BucketKind.HOUR),
        (WindowKind.DURATION, "P7D", BucketKind.HOUR),
        (WindowKind.DURATION, "P8D", BucketKind.DAY),
        (WindowKind.DURATION, "P6M", BucketKind.DAY),
        (WindowKind.MONTH, "MONTH", BucketKind.MONTH),
        (WindowKind.QUARTER, "QUARTER", BucketKind.QUARTER),
        (WindowKind.ACCUM, "P6M", BucketKind.ACCUM),
    ],
)
def test_bucket_kind_by_window_length(
    window_kind: WindowKind, window: str, kind: BucketKind
):
    assert bucket_kind_for(_declared(window_kind, window)) is kind


@pytest.mark.parametrize("window", ["P7M", "P1Y", "nonsense"])
def test_a_window_past_six_months_has_no_bucket(window: str):
    with pytest.raises(ValueError, match="window"):
        _ = bucket_kind_for(_declared(WindowKind.DURATION, window))


def test_hour_and_day_buckets_are_utc_and_calendar_buckets_are_mexico_city():
    at = datetime(2026, 1, 1, 5, 30, tzinfo=UTC)
    assert bucket_start_for(BucketKind.HOUR, at) == datetime(2026, 1, 1, 5, tzinfo=UTC)
    assert bucket_start_for(BucketKind.DAY, at) == datetime(2026, 1, 1, tzinfo=UTC)
    # 05:30 UTC on 1 January is still 31 December in Mexico City.
    assert bucket_start_for(BucketKind.MONTH, at) == datetime(
        2025, 12, 1, 6, tzinfo=UTC
    )
    assert bucket_start_for(BucketKind.QUARTER, at) == datetime(
        2025, 10, 1, 6, tzinfo=UTC
    )
    assert bucket_start_for(BucketKind.ACCUM, at) == datetime(
        2025, 12, 31, 6, tzinfo=UTC
    )


def test_window_bounds_cover_exactly_the_window():
    at = datetime(2026, 12, 13, 12, 30, tzinfo=UTC)
    assert window_bounds(_declared(WindowKind.DURATION, "P7D"), at=at) == (
        datetime(2026, 12, 6, 12, tzinfo=UTC),
        datetime(2026, 12, 13, 12, tzinfo=UTC),
    )
    # A month window over DAY buckets; 31 March less a month clamps to 28 February.
    assert window_bounds(
        _declared(WindowKind.DURATION, "P1M"), at=datetime(2026, 3, 31, 8, tzinfo=UTC)
    ) == (datetime(2026, 2, 28, tzinfo=UTC), datetime(2026, 3, 31, tzinfo=UTC))
    month = datetime(2026, 12, 1, 6, tzinfo=UTC)
    assert window_bounds(_declared(WindowKind.MONTH, "MONTH"), at=at) == (month, month)


def test_period_bucket_start_and_accum_window():
    assert period_bucket_start(BucketKind.QUARTER, "2027-Q1") == datetime(
        2027, 1, 1, 6, tzinfo=UTC
    )
    assert period_bucket_start(BucketKind.MONTH, "2027-02") == datetime(
        2027, 2, 1, 6, tzinfo=UTC
    )
    assert accum_window(datetime(2026, 7, 14, 18, tzinfo=UTC), "P6M") == (
        datetime(2026, 7, 14, 6, tzinfo=UTC),
        datetime(2027, 1, 14, 6, tzinfo=UTC),
    )


@pytest.mark.parametrize(
    ("kind", "period_key"),
    [
        (BucketKind.MONTH, "2027-Q1"),
        (BucketKind.QUARTER, "2027-02"),
        (BucketKind.DAY, "2027-02-01"),
    ],
)
def test_a_period_key_of_another_kind_is_refused(kind: BucketKind, period_key: str):
    with pytest.raises(ValueError, match="period key"):
        _ = period_bucket_start(kind, period_key)


def test_the_zone_is_the_platform_calendars():
    assert ZoneInfo(MEXICO_CITY) == MEXICO_CITY_ZONE
    assert MEXICO_CITY_ZONE.key == MEXICO_CITY


def test_a_month_bucket_starts_at_mexico_city_midnight():
    at = datetime(2026, 7, 15, 12, tzinfo=UTC)
    assert bucket_start_for(BucketKind.MONTH, at) == datetime(2026, 7, 1, 6, tzinfo=UTC)


def test_an_accumulator_opened_on_a_month_end_clamps_to_the_last_day():
    # 31 August plus six months is 28 February: 2027 has no 29th.
    assert accum_window(datetime(2026, 8, 31, 18, tzinfo=UTC), "P6M") == (
        datetime(2026, 8, 31, 6, tzinfo=UTC),
        datetime(2027, 2, 28, 6, tzinfo=UTC),
    )
