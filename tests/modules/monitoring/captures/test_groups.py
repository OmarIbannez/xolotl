"""Accumulation groups of ``AVI_XVI_A`` over real processing (plan 4b B10).

One MXN 4,000 crypto operation a month is 34.10 UMA at 117.31 pesos; five of them
and a December one of MXN 5,000 (42.62 UMA) reach 213.11 UMA, past 210.
"""

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import Engine
from sqlalchemy.orm import sessionmaker

from tests.helpers import platform_ctx
from tests.modules.monitoring.captures.support import (
    CaptureStage,
    add_relationship,
    crypto_item,
    events,
    external_ids,
    feed,
    groups,
    local,
)
from xolotl.modules.monitoring.aggregates.windows import MEXICO_CITY_ZONE
from xolotl.modules.monitoring.captures.enums import (
    CaptureGroupStatus,
    CaptureKind,
    ReportableEventStatus,
)
from xolotl.platform.db import tenant_session
from xolotl.platform.enums import Confidence
from xolotl.platform.units import UnitValue, upsert_value

CODE = "AVI_XVI_A"
MONTHLY = ((7, 15), (8, 14), (9, 15), (10, 15), (11, 18))


def six_months(stage: CaptureStage, *, december: tuple[int, str] = (9, "5000.00")):
    """Feed the five monthly operations and the December one that crosses.

    Returns:
        The rows' ids, in order.

    """
    _ = add_relationship(stage, "crypto-1", product="CRYPTO")
    day, amount = december
    items = [
        crypto_item(f"avi-{month:02d}", local(month, mday), "4000.00")
        for month, mday in MONTHLY
    ]
    items.append(crypto_item("avi-12", local(12, day), amount))
    return feed(stage, items)


def test_the_whole_group_is_captured_on_crossing(stage: CaptureStage):
    ids = six_months(stage)

    found = events(stage, CODE)
    (group,) = groups(stage)
    assert [event.transaction_id for event in found] == ids
    assert [event.period_key for event in found] == [
        "2026-07",
        "2026-08",
        "2026-09",
        "2026-10",
        "2026-11",
        "2026-12",
    ]
    assert {event.capture_group_id for event in found} == {group.id}
    assert all(event.capture_kind is CaptureKind.ACCUMULATION for event in found)
    assert all(event.status is ReportableEventStatus.CAPTURED for event in found)
    assert not any(event.late for event in found)
    assert group.status is CaptureGroupStatus.CROSSED
    assert group.threshold_crossed_at == local(12, 9)
    assert group.window_start == datetime(
        2026, 7, 15, tzinfo=MEXICO_CITY_ZONE
    ).astimezone(UTC)
    assert group.window_end == datetime(
        2027, 1, 15, tzinfo=MEXICO_CITY_ZONE
    ).astimezone(UTC)
    assert group.period_key == "2026-07"
    assert group.accumulated_minor == 2_500_000
    assert group.accumulated_unit.quantize(Decimal("0.01")) == Decimal("213.11")


def test_nothing_is_captured_below_the_threshold(stage: CaptureStage):
    _ = six_months(stage, december=(9, "1000.00"))

    assert events(stage, CODE) == []
    (group,) = groups(stage)
    assert group.status is CaptureGroupStatus.OPEN
    assert group.threshold_crossed_at is None


def test_later_operations_of_a_crossed_group_are_captured_as_they_arrive(
    stage: CaptureStage,
):
    group_ids = six_months(stage)
    (later,) = feed(stage, [crypto_item("avi-12b", local(12, 10), "100.00")])

    found = events(stage, CODE)
    (group,) = groups(stage)
    assert [event.transaction_id for event in found] == [*group_ids, later]
    assert found[-1].capture_group_id == group.id
    assert found[-1].period_key == "2026-12"
    assert group.threshold_crossed_at == local(12, 9)


def _load_fix(admin_engine: Engine, day: date, value: str) -> None:
    owner = sessionmaker(bind=admin_engine, expire_on_commit=False)
    with tenant_session(platform_ctx(), factory=owner) as s:
        _ = upsert_value(
            s,
            UnitValue(
                unit_code="USD_MXN_FIX",
                valid_from=day,
                valid_to=day,
                value=Decimal(value),
                quote_ccy="MXN",
                source="fixture",
                confidence=Confidence.VERIFIED,
            ),
            fetched_at=datetime(2027, 1, 4, 18, tzinfo=UTC),
        )


def test_a_window_that_elapses_stays_open_and_a_new_group_starts(
    stage: CaptureStage, admin_engine: Engine
):
    _load_fix(admin_engine, date(2027, 1, 4), "18.5000")
    _ = add_relationship(stage, "crypto-1", product="CRYPTO")
    later = datetime(2027, 1, 5, 10, tzinfo=MEXICO_CITY_ZONE).astimezone(UTC)

    _ = feed(
        stage,
        [
            crypto_item("avi-07", local(7, 2), "4000.00"),
            crypto_item("avi-01", later, "4000.00"),
        ],
    )

    first, second = groups(stage)
    assert first.status is CaptureGroupStatus.OPEN
    assert first.window_end == datetime(2027, 1, 2, tzinfo=MEXICO_CITY_ZONE).astimezone(
        UTC
    )
    assert second.status is CaptureGroupStatus.OPEN
    assert second.window_start == datetime(
        2027, 1, 5, tzinfo=MEXICO_CITY_ZONE
    ).astimezone(UTC)
    assert second.accumulated_minor == 400_000
    assert events(stage, CODE) == []


def test_an_operation_at_the_threshold_is_captured_alone(stage: CaptureStage):
    _ = add_relationship(stage, "crypto-1", product="CRYPTO")
    # 210 UMA exactly: 117.31 x 210.
    (row,) = feed(stage, [crypto_item("avi-big", local(12, 9), "24635.10")])

    (event,) = events(stage, CODE)
    assert event.transaction_id == row
    assert event.capture_group_id is None
    assert external_ids(stage)[event.transaction_id] == "avi-big"
    assert groups(stage) == []
