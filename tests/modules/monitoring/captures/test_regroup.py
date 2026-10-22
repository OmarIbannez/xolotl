"""Regrouping after a backfilled operation (B10 fix round, review C1 and I1).

The review's reproduction: a CRYPTO customer's A1 (1 August 2026, MXN 600, 5.11
UMA), X1 (20 December, MXN 13,000, 110.82 UMA) and X2 (10 January 2027, MXN
13,000) form one group from 1 August that crosses 210 UMA at X2. An operation L
dated 10 June 2026 (MXN 600) arrives on 20 January 2027, more than the late days
after, so a backfill processes it. Grouped in order (§10.2), the windows are then
[10 June, 10 December) with L and A1 (10.23 UMA, open) and [20 December, 20 June)
with X1 and X2 (221.64 UMA, crossed): X1 and X2 stay reportable, A1 no longer is.
"""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import Engine

from tests.helpers import staff_session
from tests.modules.monitoring.captures.support import (
    UNIT_VALUES_EXTRA,
    CaptureStage,
    add_relationship,
    build_stage,
    crypto_item,
    events,
    feed,
    groups,
    ingest,
    outbox,
)
from xolotl.modules.monitoring.aggregates.windows import MEXICO_CITY_ZONE
from xolotl.modules.monitoring.captures.enums import (
    CaptureGroupStatus,
    ReportableEventStatus,
)
from xolotl.modules.monitoring.captures.models import ReportableEvent
from xolotl.modules.monitoring.events import REPORTABLE_EVENT_SUPERSEDED
from xolotl.modules.monitoring.process.backfill import request_backfill
from xolotl.modules.monitoring.process.hooks import reset_processing_hooks
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform import clock

CODE = "AVI_XVI_A"
FEE = "AVI_XVI_B"


def at(year: int, month: int, day: int, hour: int = 10) -> datetime:
    return datetime(year, month, day, hour, tzinfo=MEXICO_CITY_ZONE).astimezone(UTC)


@pytest.fixture
def wide(tenant: CreatedTenant, admin_engine: Engine):
    """Build the capture stage with June 2026 and early 2027 unit values too.

    Yields:
        The stage; the processing hooks are forgotten afterwards.

    """
    yield build_stage(tenant, admin_engine, more_units=[UNIT_VALUES_EXTRA])
    reset_processing_hooks()


def backfill(stage: CaptureStage, item: dict[str, object], *, now: datetime) -> UUID:
    """Ingest ``item`` on ``now`` (past the late days) and run its backfill.

    Returns:
        The row's id.

    """
    (row,) = ingest(stage, [item], received=now)
    day = datetime.fromisoformat(str(item["occurred_at"])).astimezone(MEXICO_CITY_ZONE)
    with pytest.MonkeyPatch.context() as patched:
        patched.setattr(clock, "now", lambda: now)
        with staff_session(stage.tenant_id, stage.tenant.admin_user_id) as s:
            _ = request_backfill(
                s,
                from_=day.date(),
                to=day.date(),
                party_id=None,
                queue=stage.services.queue,
            )
        while stage.services.job_runner.run_once():
            pass
    return row


def three_operations(stage: CaptureStage, *, fee: str | None = None) -> list[UUID]:
    _ = add_relationship(stage, "crypto-1", product="CRYPTO")
    return feed(
        stage,
        [
            crypto_item("a1", at(2026, 8, 1), "600.00", fee=fee),
            crypto_item("x1", at(2026, 12, 20), "13000.00"),
            crypto_item("x2", at(2027, 1, 10), "13000.00"),
        ],
    )


def by_txn(stage: CaptureStage, code: str = CODE) -> dict[UUID, ReportableEvent]:
    return {event.transaction_id: event for event in events(stage, code)}


def test_a_backfilled_operation_keeps_later_crossings_captured(wide: CaptureStage):
    a1, x1, x2 = three_operations(wide)
    (g1,) = groups(wide)
    before = by_txn(wide)
    assert g1.threshold_crossed_at == at(2027, 1, 10)
    assert set(before) == {a1, x1, x2}

    late = backfill(
        wide, crypto_item("late", at(2026, 6, 10), "600.00"), now=at(2027, 1, 20)
    )

    old, early, later = groups(wide)
    assert old.id == g1.id
    assert old.status is CaptureGroupStatus.SUPERSEDED
    assert early.window_start == datetime(
        2026, 6, 10, tzinfo=MEXICO_CITY_ZONE
    ).astimezone(UTC)
    assert early.status is CaptureGroupStatus.OPEN
    assert later.window_start == datetime(
        2026, 12, 20, tzinfo=MEXICO_CITY_ZONE
    ).astimezone(UTC)
    assert later.status is CaptureGroupStatus.CROSSED
    assert later.threshold_crossed_at == at(2027, 1, 10)
    assert old.superseded_by_id == early.id
    found = by_txn(wide)
    assert late not in found
    # X1 and X2 stay reportable, in their own crossed group, the same rows.
    for txn in (x1, x2):
        event = found[txn]
        assert event.id == before[txn].id
        assert event.status is ReportableEventStatus.CAPTURED
        assert event.capture_group_id == later.id
        assert event.late
    # A1 is in no crossed group any more: retired, with no successor.
    retired = found[a1]
    assert retired.id == before[a1].id
    assert retired.status is ReportableEventStatus.SUPERSEDED
    assert retired.superseded_by_id is None
    notices = {
        n.data["reportable_event_id"]: n.data["superseded_by_id"]
        for n in outbox(wide, REPORTABLE_EVENT_SUPERSEDED)
    }
    assert notices == {
        str(retired.id): None,
        str(found[x1].id): str(found[x1].id),
        str(found[x2].id): str(found[x2].id),
    }

    (w,) = feed(wide, [crypto_item("w", at(2027, 2, 15), "600.00")])

    assert by_txn(wide)[w].capture_group_id == later.id
    assert len(groups(wide)) == 3


def test_a_retired_event_gives_back_its_merged_fee_capture(wide: CaptureStage):
    a1, _x1, _x2 = three_operations(wide, fee="500.00")
    (fee_event,) = events(wide, FEE)
    assert fee_event.transaction_id == a1
    assert fee_event.status is ReportableEventStatus.VOIDED
    assert by_txn(wide)[a1].fields["merged_captures"] == [FEE]

    _ = backfill(
        wide, crypto_item("late", at(2026, 6, 10), "600.00"), now=at(2027, 1, 20)
    )

    (restored,) = events(wide, FEE)
    assert restored.id == fee_event.id
    assert restored.status is ReportableEventStatus.CAPTURED
    assert restored.void_reason is None
    assert restored.late
    assert by_txn(wide)[a1].status is ReportableEventStatus.SUPERSEDED


def test_a_retired_event_is_captured_again_when_its_group_crosses(
    wide: CaptureStage,
):
    a1, _x1, _x2 = three_operations(wide, fee="500.00")
    late = backfill(
        wide, crypto_item("late", at(2026, 6, 10), "600.00"), now=at(2027, 1, 20)
    )
    retired = by_txn(wide)[a1]
    assert retired.status is ReportableEventStatus.SUPERSEDED

    # 204.59 UMA inside the June window: with L and A1 it reaches 214.82.
    july = backfill(
        wide, crypto_item("july", at(2026, 7, 1), "24000.00"), now=at(2027, 1, 25)
    )

    early = next(
        g
        for g in groups(wide)
        if g.window_start
        == datetime(2026, 6, 10, tzinfo=MEXICO_CITY_ZONE).astimezone(UTC)
    )
    assert early.status is CaptureGroupStatus.CROSSED
    assert early.threshold_crossed_at == at(2026, 8, 1)
    found = by_txn(wide)
    assert {txn for txn, e in found.items() if e.capture_group_id == early.id} == {
        late,
        july,
        a1,
    }
    revived = found[a1]
    assert revived.id == retired.id
    assert revived.status is ReportableEventStatus.CAPTURED
    assert revived.late
    # The fee capture merges into it again: one aviso for the operation.
    (fee_event,) = events(wide, FEE)
    assert fee_event.status is ReportableEventStatus.VOIDED
    assert fee_event.void_reason == "MERGED"
    assert revived.fields["merged_captures"] == [FEE]
