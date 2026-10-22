"""Accumulation groups: a relationship's operations below the threshold (spec §10.2).

A group is one key's accumulation window for one capture code: it opens on the
Mexico City day of its first operation and runs for the capture's window (``P6M``,
D21), exactly as the ``ACCUM`` aggregate the capture declares, which is what the
crossing test reads (plan 4b Deviation 9). Its members are the key's processed
transactions in the window that the capture accumulates (its ``on``, below the
threshold), found by a query over ``transaction``; there is no member table.
When the accumulated value crosses the threshold every member is captured, and
later operations of the window are captured as they arrive. A group whose window
elapses without crossing stays ``OPEN`` as history.
"""

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from xolotl.modules.monitoring.aggregates.declarations import matches
from xolotl.modules.monitoring.aggregates.windows import accum_window
from xolotl.modules.monitoring.captures.definitions import CaptureDefinition
from xolotl.modules.monitoring.captures.enums import CaptureGroupStatus
from xolotl.modules.monitoring.captures.models import CaptureGroup, ReportableEvent
from xolotl.modules.monitoring.captures.record import CaptureRun, write_event
from xolotl.modules.monitoring.enrich.service import enriched_from_row
from xolotl.modules.monitoring.enums import TransactionStatus
from xolotl.modules.monitoring.txn.models import Transaction
from xolotl.modules.monitoring.txn.schema import EnrichedTransaction, KeyKind
from xolotl.platform.db import bound_context

_LIVE_GROUPS = (CaptureGroupStatus.OPEN, CaptureGroupStatus.CROSSED)


def _window(definition: CaptureDefinition) -> str:
    if definition.window is None:
        msg = f"capture {definition.code} accumulates without a window"
        raise ValueError(msg)
    return definition.window


def group_containing(
    s: Session, definition: CaptureDefinition, txn: EnrichedTransaction
) -> CaptureGroup | None:
    """Find the key's live group whose window holds ``txn``, locked for update.

    Returns:
        The ``OPEN`` or ``CROSSED`` group, or None.

    """
    return s.scalars(
        select(CaptureGroup)
        .where(
            CaptureGroup.capture_code == definition.code,
            CaptureGroup.key_kind == KeyKind.RELATIONSHIP,
            CaptureGroup.key == str(txn.relationship_id),
            CaptureGroup.status.in_(_LIVE_GROUPS),
            CaptureGroup.window_start <= txn.occurred_at,
            CaptureGroup.window_end > txn.occurred_at,
        )
        .order_by(CaptureGroup.window_start.desc())
        .limit(1)
        .with_for_update()
    ).first()


def group_after(
    s: Session,
    definition: CaptureDefinition,
    txn: EnrichedTransaction,
    *,
    until: datetime,
) -> CaptureGroup | None:
    """Find the key's first live group starting after ``txn`` and before ``until``.

    A late operation whose own window reaches such a group moves that group's
    window earlier (spec §10.5).

    Returns:
        The group, locked for update, or None.

    """
    return s.scalars(
        select(CaptureGroup)
        .where(
            CaptureGroup.capture_code == definition.code,
            CaptureGroup.key_kind == KeyKind.RELATIONSHIP,
            CaptureGroup.key == str(txn.relationship_id),
            CaptureGroup.status.in_(_LIVE_GROUPS),
            CaptureGroup.window_start > txn.occurred_at,
            CaptureGroup.window_start < until,
        )
        .order_by(CaptureGroup.window_start)
        .limit(1)
        .with_for_update()
    ).first()


def new_group(
    s: Session,
    definition: CaptureDefinition,
    txn: EnrichedTransaction,
    *,
    first_at: datetime,
) -> CaptureGroup:
    """Insert an ``OPEN`` group whose window opens on ``first_at``'s day.

    Returns:
        The group, flushed.

    """
    start, end = accum_window(first_at, _window(definition))
    group = CaptureGroup(
        tenant_id=bound_context(s).require_tenant_id(),
        capture_code=definition.code,
        key_kind=KeyKind.RELATIONSHIP,
        key=str(txn.relationship_id),
        period_key=definition.period_key_of(txn),
        window_start=start,
        window_end=end,
        status=CaptureGroupStatus.OPEN,
        accumulated_minor=0,
        accumulated_unit=Decimal(0),
    )
    s.add(group)
    s.flush()
    return group


def add_to_group(
    group: CaptureGroup, txn: EnrichedTransaction, accumulated: Decimal
) -> None:
    """Count ``txn`` in the group; ``accumulated`` is the store's value with it."""
    group.accumulated_minor += txn.amount_minor
    group.accumulated_unit = accumulated


def group_members(
    s: Session,
    definition: CaptureDefinition,
    group: CaptureGroup,
    *,
    in_flight: Sequence[EnrichedTransaction] = (),
) -> list[EnrichedTransaction]:
    """List the key's transactions the group accumulates, in order.

    ``in_flight`` are rows being processed (not ``PROCESSED`` yet) that belong
    too when the capture accumulates them and the window holds them.

    Returns:
        The members, by ``occurred_at`` then id.

    """
    rows = s.scalars(
        select(Transaction).where(
            Transaction.relationship_id == UUID(group.key),
            Transaction.status == TransactionStatus.PROCESSED,
            Transaction.occurred_at >= group.window_start,
            Transaction.occurred_at < group.window_end,
        )
    )
    found = {txn.id: txn for txn in (enriched_from_row(row) for row in rows)}
    for txn in in_flight:
        if group.window_start <= txn.occurred_at < group.window_end:
            found[txn.id] = txn
    members = [txn for txn in found.values() if matches(definition.accumulates, txn)]
    return sorted(members, key=lambda txn: (txn.occurred_at, txn.id))


def accumulated(
    definition: CaptureDefinition, members: Sequence[EnrichedTransaction]
) -> Decimal:
    """Add up the members in the threshold's unit.

    Returns:
        The sum.

    """
    return sum(
        (txn.unit_amount(definition.unit_key) or Decimal(0) for txn in members),
        Decimal(0),
    )


def crossing_at(
    definition: CaptureDefinition, members: Sequence[EnrichedTransaction]
) -> datetime | None:
    """Find the member at which the running sum reaches the threshold.

    Returns:
        That member's ``occurred_at``, or None when the members never reach it.

    """
    running = Decimal(0)
    for txn in members:
        running += txn.unit_amount(definition.unit_key) or Decimal(0)
        if running >= definition.threshold.amount:
            return txn.occurred_at
    return None


def cross_group(
    run: CaptureRun,
    definition: CaptureDefinition,
    group: CaptureGroup,
    *,
    at: datetime,
    members: Sequence[EnrichedTransaction],
    late: bool,
) -> list[ReportableEvent]:
    """Mark the group ``CROSSED`` at ``at`` and capture every member.

    Each member gets its own event with the group's id and its own period key; a
    member that already has an event of the capture keeps it.

    Returns:
        The events written.

    """
    group.status = CaptureGroupStatus.CROSSED
    group.threshold_crossed_at = at
    written: list[ReportableEvent] = []
    for txn in members:
        event = write_event(run, definition, txn, late=late, group=group)
        if event is not None:
            written.append(event)
    return written
