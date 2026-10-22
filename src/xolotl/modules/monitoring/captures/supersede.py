"""Regrouping a key's accumulation after a late operation (spec §10.2, §10.5).

A late operation that moves a group's window earlier, or the point where it
crosses, supersedes the groups it changes. The key's operations are grouped
again from the earlier of the late operation and the first changed group, the
way processing in order would have grouped them (§10.2): a window opens on the
day of the first operation not in a window yet and runs for the capture's window,
and the next operation past it opens the next one. Regrouping stops at the first
window, after the late operation's own, that starts where a live group already
starts: from there on the groups are what they were.

Every replaced group becomes ``SUPERSEDED`` and points at the rebuilt group that
holds its start (or the last one). In each rebuilt group that crosses, every
member is captured: an event of a replaced group is captured again in place
(``capture_member``; one row per transaction and capture, plan 4b Deviation 23),
an event retired earlier is brought back, and a member without one gets one. An
event of a replaced group whose transaction is in no crossed group any more is
retired (``SUPERSEDED`` with no successor: not reportable for the capture) and
what merged into it is captured again. Closed periods touched are reopened at
the end of the run, in order.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from xolotl.modules.monitoring.aggregates.windows import accum_window
from xolotl.modules.monitoring.captures.definitions import CaptureDefinition
from xolotl.modules.monitoring.captures.enums import CaptureGroupStatus
from xolotl.modules.monitoring.captures.groups import (
    accumulated,
    crossing_at,
    members_between,
    new_group,
    window_of,
)
from xolotl.modules.monitoring.captures.models import CaptureGroup, ReportableEvent
from xolotl.modules.monitoring.captures.record import (
    LIVE,
    CaptureRun,
    capture_member,
    hold_period,
    retire_event,
)
from xolotl.modules.monitoring.txn.schema import EnrichedTransaction, KeyKind

if TYPE_CHECKING:
    from uuid import UUID


def partition(
    definition: CaptureDefinition,
    members: Sequence[EnrichedTransaction],
    *,
    late: EnrichedTransaction,
    kept_starts: frozenset[datetime],
) -> tuple[list[list[EnrichedTransaction]], datetime | None]:
    """Split ordered members into consecutive accumulation windows.

    Windows after the one holding ``late`` stop at the first that would start at
    one of ``kept_starts`` (a live group's start, unchanged from there on).

    Returns:
        The members of each window, in order, and the start where it stopped
        (None when every member is in a window).

    """
    window = window_of(definition)
    windows: list[list[EnrichedTransaction]] = []
    index = 0
    seen_late = False
    while index < len(members):
        start, end = accum_window(members[index].occurred_at, window)
        if seen_late and start in kept_starts:
            return windows, start
        chunk: list[EnrichedTransaction] = []
        while index < len(members) and members[index].occurred_at < end:
            chunk.append(members[index])
            index += 1
        seen_late = seen_late or any(txn.id == late.id for txn in chunk)
        windows.append(chunk)
    return windows, None


def _live_groups(
    run: CaptureRun,
    definition: CaptureDefinition,
    late: EnrichedTransaction,
    start: datetime,
) -> list[CaptureGroup]:
    return list(
        run.s.scalars(
            select(CaptureGroup)
            .where(
                CaptureGroup.capture_code == definition.code,
                CaptureGroup.key_kind == KeyKind.RELATIONSHIP,
                CaptureGroup.key == str(late.relationship_id),
                CaptureGroup.status.in_((
                    CaptureGroupStatus.OPEN,
                    CaptureGroupStatus.CROSSED,
                )),
                CaptureGroup.window_end > start,
            )
            .order_by(CaptureGroup.window_start)
            .with_for_update()
        )
    )


def _build(
    run: CaptureRun,
    definition: CaptureDefinition,
    chunk: Sequence[EnrichedTransaction],
) -> CaptureGroup:
    group = new_group(run.s, definition, chunk[0], first_at=chunk[0].occurred_at)
    group.accumulated_unit = accumulated(definition, chunk)
    group.accumulated_minor = sum(txn.amount_minor for txn in chunk)
    crossed = crossing_at(definition, chunk)
    if crossed is not None:
        group.status = CaptureGroupStatus.CROSSED
        group.threshold_crossed_at = crossed
    return group


def regroup(
    run: CaptureRun,
    definition: CaptureDefinition,
    *,
    late: EnrichedTransaction,
    first: CaptureGroup,
) -> list[CaptureGroup]:
    """Group the key's operations again from ``late`` or ``first``.

    Args:
        run: The capture evaluation's session and outcome.
        definition: The accumulation capture.
        late: The late operation, being processed.
        first: The first live group it changes (holding it, or reached by its
            window), locked.

    Returns:
        The rebuilt groups, in order.

    """
    s = run.s
    start = min(late.occurred_at, first.window_start)
    live = _live_groups(run, definition, late, start)
    horizon = max(group.window_end for group in [first, *live])
    members = members_between(
        s,
        definition,
        late.relationship_id,
        start=start,
        end=horizon,
        in_flight=(late,),
    )
    windows, stopped_at = partition(
        definition,
        members,
        late=late,
        kept_starts=frozenset(group.window_start for group in live),
    )
    replaced = [
        group for group in live if stopped_at is None or group.window_start < stopped_at
    ]
    # The periods first, then their events: the order of a close (re-review M-R1).
    for code, period_key in sorted(
        s.execute(
            select(ReportableEvent.capture_code, ReportableEvent.period_key)
            .where(
                ReportableEvent.capture_group_id.in_([g.id for g in replaced]),
                ReportableEvent.status.in_(LIVE),
            )
            .distinct()
        ).all()
    ):
        hold_period(run, code, period_key)
    old_events = list(
        s.scalars(
            select(ReportableEvent)
            .where(
                ReportableEvent.capture_group_id.in_([g.id for g in replaced]),
                ReportableEvent.status.in_(LIVE),
            )
            .order_by(ReportableEvent.occurred_at, ReportableEvent.id)
            .with_for_update()
        )
    )
    for group in replaced:
        group.status = CaptureGroupStatus.SUPERSEDED
    # Out of the partial unique index before the new groups take their keys.
    s.flush()
    rebuilt = [_build(run, definition, chunk) for chunk in windows]
    for group in replaced:
        group.superseded_by_id = next(
            (new.id for new in rebuilt if new.window_end > group.window_start),
            rebuilt[-1].id,
        )
    captured: set[UUID] = set()
    for group, chunk in zip(rebuilt, windows, strict=True):
        if group.status is not CaptureGroupStatus.CROSSED:
            continue
        for txn in chunk:
            _ = capture_member(run, definition, txn, group, late=True)
            captured.add(txn.id)
    for event in old_events:
        if event.transaction_id not in captured:
            retire_event(run, event)
    return rebuilt
