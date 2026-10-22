"""Supersession of an accumulation group by a late operation (spec §10.5, FR-TM-009).

A late operation that moves a group's window earlier, or the point where it
crosses, supersedes the group: the group becomes ``SUPERSEDED`` and points at a
new group rebuilt from the key's transactions (the late one included), whose
crossing is found again in ``occurred_at`` order.

``reportable_event`` keeps one row per transaction and capture (its unique key),
so an event of the old group is re-captured in place rather than copied: it
moves to the new group, becomes ``CAPTURED`` again (an ``INCLUDED`` one too, so
reporting takes it anew) with ``late`` set, keeps its fields and unit values, and
``monitoring.reportable_event_superseded.v1`` (``superseded_by_id`` the event
itself) and ``monitoring.reportable_event_captured.v1`` are published. An event
whose transaction the new group no longer captures becomes ``SUPERSEDED`` with
no successor. Every period touched that was ``CLOSED`` is ``REOPENED``.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select

from xolotl.modules.monitoring.captures.definitions import CaptureDefinition
from xolotl.modules.monitoring.captures.enums import (
    CaptureGroupStatus,
    ReportableEventStatus,
)
from xolotl.modules.monitoring.captures.groups import (
    accumulated,
    crossing_at,
    group_members,
    new_group,
)
from xolotl.modules.monitoring.captures.models import CaptureGroup, ReportableEvent
from xolotl.modules.monitoring.captures.record import (
    LIVE,
    CaptureRun,
    event_of,
    publish_captured,
    reopen_period,
    touch_period,
    write_event,
)
from xolotl.modules.monitoring.events import (
    REPORTABLE_EVENT_SUPERSEDED,
    publish_monitoring_event,
)
from xolotl.modules.monitoring.txn.schema import EnrichedTransaction


def _publish_superseded(
    run: CaptureRun, event: ReportableEvent, successor: UUID | None
) -> None:
    _ = publish_monitoring_event(
        run.s,
        REPORTABLE_EVENT_SUPERSEDED,
        entity="reportable_event",
        entity_id=event.id,
        key=str(event.party_id),
        data={
            "reportable_event_id": str(event.id),
            "superseded_by_id": None if successor is None else str(successor),
            "period_key": event.period_key,
        },
        policy_snapshot_id=event.policy_snapshot_id,
    )


def _recapture(
    run: CaptureRun,
    definition: CaptureDefinition,
    event: ReportableEvent,
    group: CaptureGroup,
) -> None:
    """Move ``event`` to ``group`` as a fresh ``CAPTURED`` event, in place."""
    event.capture_group_id = group.id
    event.status = ReportableEventStatus.CAPTURED
    event.superseded_by_id = None
    event.void_reason = None
    event.late = True
    _publish_superseded(run, event, event.id)
    touch_period(run.s, definition, event.period_key)
    publish_captured(run.s, event)
    run.events.append(event)


def supersede_group(
    run: CaptureRun,
    definition: CaptureDefinition,
    group: CaptureGroup,
    *,
    late: EnrichedTransaction,
    first_at: datetime,
) -> CaptureGroup:
    """Replace ``group`` by one rebuilt with the late operation.

    Args:
        run: The capture evaluation's session and outcome.
        definition: The accumulation capture.
        group: The live group the late operation changes, locked.
        late: The late operation, being processed.
        first_at: The first operation of the new window (the store's).

    Returns:
        The new group: ``CROSSED`` with its members captured when they reach the
        threshold, else ``OPEN``.

    """
    s = run.s
    old_events = list(
        s.scalars(
            select(ReportableEvent)
            .where(
                ReportableEvent.capture_group_id == group.id,
                ReportableEvent.status.in_(LIVE),
            )
            .with_for_update()
        )
    )
    group.status = CaptureGroupStatus.SUPERSEDED
    # Out of the partial unique index before the new group takes its key.
    s.flush()
    rebuilt = new_group(s, definition, late, first_at=first_at)
    group.superseded_by_id = rebuilt.id
    members = group_members(s, definition, rebuilt, in_flight=(late,))
    if members:
        rebuilt.period_key = definition.period_key_of(members[0])
    rebuilt.accumulated_unit = accumulated(definition, members)
    rebuilt.accumulated_minor = sum(txn.amount_minor for txn in members)
    kept: set[UUID] = set()
    crossed = crossing_at(definition, members)
    if crossed is not None:
        rebuilt.status = CaptureGroupStatus.CROSSED
        rebuilt.threshold_crossed_at = crossed
        for txn in members:
            existing = event_of(s, definition.code, txn.id)
            if existing is None:
                _ = write_event(run, definition, txn, late=True, group=rebuilt)
            elif existing.capture_group_id == group.id or (
                existing.status is ReportableEventStatus.SUPERSEDED
            ):
                _recapture(run, definition, existing, rebuilt)
                kept.add(existing.id)
    for event in old_events:
        if event.id in kept:
            continue
        event.status = ReportableEventStatus.SUPERSEDED
        event.superseded_by_id = None
        event.late = True
        _publish_superseded(run, event, None)
        _ = reopen_period(s, event.capture_code, event.period_key)
        run.superseded.append(event)
    return rebuilt
