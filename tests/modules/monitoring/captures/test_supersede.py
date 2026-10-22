"""Late operations: supersession, reopened periods and plain late events (B10).

The group of ``test_groups``: five monthly MXN 4,000 crypto operations (34.10 UMA
each), then a MXN 5,000 one on 11 December that crosses 210 UMA. A MXN 5,000
operation dated 10 December and sent on 12 December crosses a day earlier.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import update

from tests.helpers import system_ctx
from tests.modules.monitoring.captures.support import (
    CaptureStage,
    add_relationship,
    crypto_item,
    events,
    feed,
    groups,
    local,
    outbox,
    periods,
)
from tests.modules.monitoring.captures.test_groups import MONTHLY
from tests.modules.monitoring.support import make_item
from xolotl.modules.monitoring.aggregates.windows import MEXICO_CITY_ZONE
from xolotl.modules.monitoring.captures.enums import (
    CaptureGroupStatus,
    CapturePeriodStatus,
    ReportableEventStatus,
)
from xolotl.modules.monitoring.captures.models import CapturePeriod, ReportableEvent
from xolotl.modules.monitoring.events import (
    CAPTURE_PERIOD_CHANGED,
    REPORTABLE_EVENT_CAPTURED,
    REPORTABLE_EVENT_SUPERSEDED,
)
from xolotl.modules.monitoring.txn.models import Transaction
from xolotl.platform.db import tenant_session

CODE = "AVI_XVI_A"
SENT_LATE = local(12, 12)


def crossed_on_the_11th(stage: CaptureStage) -> list[UUID]:
    _ = add_relationship(stage, "crypto-1", product="CRYPTO")
    items = [
        crypto_item(f"avi-{month:02d}", local(month, mday), "4000.00")
        for month, mday in MONTHLY
    ]
    items.append(crypto_item("avi-12", local(12, 11), "5000.00"))
    return feed(stage, items)


def send_late(stage: CaptureStage) -> UUID:
    (row,) = feed(
        stage,
        [crypto_item("avi-late", local(12, 10), "5000.00")],
        received=SENT_LATE,
    )
    return row


def test_a_late_operation_supersedes_the_group(stage: CaptureStage):
    ids = crossed_on_the_11th(stage)
    (before,) = groups(stage)
    old = {event.id: event for event in events(stage, CODE)}
    assert before.threshold_crossed_at == local(12, 11)

    late = send_late(stage)

    with tenant_session(system_ctx(stage.tenant_id)) as s:
        row = s.get_one(Transaction, (stage.tenant_id, local(12, 10), late))
        assert row.late
    superseded, rebuilt = groups(stage)
    assert superseded.id == before.id
    assert superseded.status is CaptureGroupStatus.SUPERSEDED
    assert superseded.superseded_by_id == rebuilt.id
    assert rebuilt.status is CaptureGroupStatus.CROSSED
    assert rebuilt.threshold_crossed_at == local(12, 10)
    assert rebuilt.window_start == before.window_start
    assert rebuilt.accumulated_minor == before.accumulated_minor + 500_000
    found = events(stage, CODE)
    assert [event.transaction_id for event in found] == [*ids[:5], late, ids[5]]
    assert {event.capture_group_id for event in found} == {rebuilt.id}
    assert all(event.status is ReportableEventStatus.CAPTURED for event in found)
    assert all(event.late for event in found)
    # Each event of the old group is re-captured in place, once.
    assert {event.id for event in found} == {
        *old,
        *(e.id for e in found if e.transaction_id == late),
    }
    notices = outbox(stage, REPORTABLE_EVENT_SUPERSEDED)
    assert sorted(str(n.data["reportable_event_id"]) for n in notices) == sorted(
        str(event_id) for event_id in old
    )
    assert all(
        n.data["superseded_by_id"] == n.data["reportable_event_id"] for n in notices
    )
    captured = outbox(stage, REPORTABLE_EVENT_CAPTURED)
    assert len(captured) == 6 + 1 + 6


def test_a_late_operation_after_the_close_reopens_the_period(stage: CaptureStage):
    _ = crossed_on_the_11th(stage)
    closed_at = datetime(2026, 12, 1, 10, tzinfo=UTC)
    with tenant_session(system_ctx(stage.tenant_id)) as s:
        _ = s.execute(
            update(CapturePeriod)
            .where(CapturePeriod.capture_code == CODE)
            .values(status=CapturePeriodStatus.CLOSED, closed_at=closed_at)
        )

    _ = send_late(stage)

    by_key = {p.period_key: p for p in periods(stage) if p.capture_code == CODE}
    assert set(by_key) == {f"2026-{month:02d}" for month in range(7, 13)}
    december = by_key["2026-12"]
    assert december.status is CapturePeriodStatus.REOPENED
    assert december.change_count == 1
    assert december.reopened_at is not None
    assert december.report_code == "MX_AVISO_AVI"
    changed = outbox(stage, CAPTURE_PERIOD_CHANGED)
    assert {str(n.data["capture_period_id"]) for n in changed} == {
        str(p.id) for p in by_key.values()
    }
    (notice,) = [n for n in changed if n.data["period_key"] == "2026-12"]
    assert notice.data == {
        "capture_period_id": str(december.id),
        "capture_code": CODE,
        "report_code": "MX_AVISO_AVI",
        "period_key": "2026-12",
        "status": "REOPENED",
        "discrepancy_count": 0,
    }
    assert notice.key == str(december.id)


def test_an_included_event_is_superseded_never_edited(stage: CaptureStage):
    ids = crossed_on_the_11th(stage)
    with tenant_session(system_ctx(stage.tenant_id)) as s:
        _ = s.execute(
            update(ReportableEvent)
            .where(ReportableEvent.transaction_id == ids[0])
            .values(status=ReportableEventStatus.INCLUDED)
        )
    (july,) = [e for e in events(stage, CODE) if e.transaction_id == ids[0]]

    _ = send_late(stage)

    (after,) = [e for e in events(stage, CODE) if e.transaction_id == ids[0]]
    assert after.id == july.id
    # Reporting takes it again; what it carried is unchanged.
    assert after.status is ReportableEventStatus.CAPTURED
    assert after.fields == july.fields
    assert after.unit_values == july.unit_values
    assert after.period_key == july.period_key
    assert after.capture_group_id != july.capture_group_id
    notices = outbox(stage, REPORTABLE_EVENT_SUPERSEDED)
    assert [n.data["reportable_event_id"] for n in notices].count(str(july.id)) == 1


def test_a_late_operation_before_the_window_moves_it_earlier(stage: CaptureStage):
    _ = add_relationship(stage, "crypto-1", product="CRYPTO")
    _ = feed(
        stage,
        [
            crypto_item("avi-07", local(7, 15), "4000.00"),
            crypto_item("avi-07b", local(7, 16), "4000.00"),
        ],
    )
    (before,) = groups(stage)

    _ = feed(
        stage,
        [crypto_item("avi-early", local(7, 10), "4000.00")],
        # Six days late: late, but within the late days, so not BACKFILL.
        received=local(7, 16, 12),
    )

    superseded, rebuilt = groups(stage)
    assert superseded.id == before.id
    assert superseded.status is CaptureGroupStatus.SUPERSEDED
    assert superseded.superseded_by_id == rebuilt.id
    assert rebuilt.status is CaptureGroupStatus.OPEN
    assert rebuilt.window_start == datetime(
        2026, 7, 10, tzinfo=MEXICO_CITY_ZONE
    ).astimezone(UTC)
    assert rebuilt.window_end - rebuilt.window_start > timedelta(days=180)
    assert rebuilt.accumulated_minor == 1_200_000
    assert events(stage, CODE) == []


def test_a_late_threshold_capture_is_just_late(stage: CaptureStage):
    _ = add_relationship(stage, "cust-1")
    (row,) = feed(
        stage,
        [
            make_item(
                external_id="dep-late",
                occurred_at=local(12, 10).isoformat(),
                customer_ref="cust-1",
                account_ref="acct-1",
                amount="6000.00",
                currency="USD",
            )
        ],
        received=SENT_LATE,
    )

    (event,) = events(stage, "ITF_ROR_CASH")
    assert event.transaction_id == row
    assert event.late
    assert event.period_key == "2026-Q4"
    assert outbox(stage, REPORTABLE_EVENT_SUPERSEDED) == []
    assert groups(stage) == []
