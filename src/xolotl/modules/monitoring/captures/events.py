"""Capturing a transaction for every capture of its snapshot (SP-04 spec §10.2).

For each capture whose ``on`` the transaction matches, in a fixed order (merge
targets before the captures that merge into them):

- ``THRESHOLD``: the transaction in the threshold's unit (``unit_amounts``, 4a's
  one conversion path) at or above the threshold is captured (Deviation 8).
- ``FEE_THRESHOLD``: the fee, converted through the same path, at or above it.
- ``ACCUMULATION``: an operation at or above the threshold is captured on its
  own; one below joins the key's group, which is captured whole when the
  ``ACCUM`` aggregate (read from the store, the transaction included) reaches the
  threshold, and whose later operations are captured as they arrive. A late
  operation that moves a group's window earlier, or its crossing point, groups
  the key's operations again (``supersede.regroup``).
- ``merge_into``: a capture whose target already holds an event for the
  transaction writes nothing and is named in the target's
  ``fields.merged_captures``; a target written later voids it (Deviation 10).

A transaction without the capture's unit amount parks (``UNIT_VALUE_MISSING``)
rather than skip the capture. A capture never opens a hit and never reaches an
API client (spec §10.1).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import Session

from xolotl.modules.monitoring.aggregates.declarations import matches
from xolotl.modules.monitoring.aggregates.store import AggregateKey, AggregateStore
from xolotl.modules.monitoring.aggregates.windows import accum_window
from xolotl.modules.monitoring.captures.definitions import CaptureDefinition
from xolotl.modules.monitoring.captures.enums import CaptureGroupStatus, CaptureKind
from xolotl.modules.monitoring.captures.groups import (
    accumulated,
    add_to_group,
    cross_group,
    crossing_at,
    group_after,
    group_containing,
    group_members,
    new_group,
    window_of,
)
from xolotl.modules.monitoring.captures.models import CaptureGroup, ReportableEvent
from xolotl.modules.monitoring.captures.record import (
    LIVE,
    CaptureRun,
    event_of,
    fee_amount,
    note_merged,
    reopen_touched,
    unit_amount_of,
    write_event,
)
from xolotl.modules.monitoring.captures.supersede import regroup
from xolotl.modules.monitoring.txn.schema import (
    EnrichedTransaction,
    KeyKind,
    UnitAmount,
)
from xolotl.platform.calendar import Holidays
from xolotl.platform.units import UnitService


@dataclass(frozen=True, slots=True)
class CaptureOutcome:
    """What capturing one transaction wrote.

    ``events`` were written or captured again, ``voided`` were merged away and
    ``superseded`` were retired by a regrouping (no crossed group holds them).
    """

    events: tuple[ReportableEvent, ...]
    voided: tuple[ReportableEvent, ...]
    superseded: tuple[ReportableEvent, ...] = ()


def _store_total(
    store: AggregateStore,
    definition: CaptureDefinition,
    txn: EnrichedTransaction,
    group: CaptureGroup,
) -> Decimal | None:
    """Read the group's total from the store's accumulator (Deviation 9).

    Returns:
        The accumulator's value when it is complete and its window is the
        group's, else None (a backfill pending, or a window the store keeps
        otherwise after a regrouping or for rows older than it keeps).

    """
    (declaration,) = definition.declarations()
    value = store.accumulator_read(
        AggregateKey(KeyKind.RELATIONSHIP, str(txn.relationship_id)),
        declaration,
        at=txn.occurred_at,
    )
    if not value.complete or value.first_at is None:
        return None
    start, _ = accum_window(value.first_at, declaration.window)
    return value.value if start == group.window_start else None


def _accumulate(
    run: CaptureRun,
    definition: CaptureDefinition,
    txn: EnrichedTransaction,
    *,
    store: AggregateStore,
    late: bool,
) -> None:
    """Capture an operation for an ``ACCUMULATION`` capture (spec §10.2, §10.5).

    An operation in order adds to the group holding it, or opens one on its day,
    and the group's total is the store's accumulator when the two agree. A late
    operation is judged on the group's rows: it joins the group holding it
    (crossing where the running sum in ``occurred_at`` order reaches the
    threshold), or regroups the key when it reaches a later group or moves a
    crossing point.
    """
    amount = unit_amount_of(definition, txn)
    threshold = definition.threshold.amount
    if amount >= threshold:
        _ = write_event(run, definition, txn, late=late)
        return
    s = run.s
    group = group_containing(s, definition, txn)
    if group is None:
        if late:
            _, window_end = accum_window(txn.occurred_at, window_of(definition))
            displaced = group_after(s, definition, txn, until=window_end)
            if displaced is not None:
                # The late operation opens a window earlier (spec §10.5).
                _ = regroup(run, definition, late=txn, first=displaced)
                return
        group = new_group(s, definition, txn, first_at=txn.occurred_at)
    total = None if late else _store_total(store, definition, txn, group)
    members: list[EnrichedTransaction] | None = None
    if total is None:
        members = group_members(s, definition, group, in_flight=(txn,))
        total = accumulated(definition, members)
        crossing = crossing_at(definition, members)
    else:
        crossing = txn.occurred_at if total >= threshold else None
    add_to_group(group, txn, total)
    if group.status is CaptureGroupStatus.OPEN:
        if crossing is not None:
            if members is None:
                members = group_members(s, definition, group, in_flight=(txn,))
            _ = cross_group(
                run, definition, group, at=crossing, members=members, late=late
            )
        return
    if late and crossing != group.threshold_crossed_at:
        # The late operation moves the crossing point (spec §10.5).
        _ = regroup(run, definition, late=txn, first=group)
        return
    _ = write_event(run, definition, txn, late=late, group=group)


def capture_transaction(
    s: Session,
    definitions: Sequence[CaptureDefinition],
    txn: EnrichedTransaction,
    *,
    store: AggregateStore,
    units: UnitService,
    holidays: Holidays,
    late: bool,
) -> CaptureOutcome:
    """Capture ``txn`` for every definition of its snapshot, in its transaction.

    Args:
        s: The processing row's session.
        definitions: The snapshot's capture definitions.
        txn: The transaction being processed (not ``PROCESSED`` yet).
        store: The aggregate store, which already includes ``txn``.
        units: The unit service of ``s``.
        holidays: The ``BANXICO`` holidays, for the fee conversion.
        late: The row's late flag; it marks the events and lets a group be
            superseded.

    Returns:
        The events written or captured again, voided and superseded.

    """
    run = CaptureRun(s, {d.code: d for d in definitions}, units, holidays)
    ordered = sorted(definitions, key=lambda d: d.merge_into is not None)
    for definition in ordered:
        if not matches(definition.on, txn):
            continue
        if definition.kind is CaptureKind.ACCUMULATION:
            _accumulate(run, definition, txn, store=store, late=late)
            continue
        fee: UnitAmount | None = None
        if definition.kind is CaptureKind.FEE_THRESHOLD:
            fee = fee_amount(definition, txn, units=units, holidays=holidays)
            if fee is None:
                continue
            measured = fee.amount
        else:
            measured = unit_amount_of(definition, txn)
        if measured < definition.threshold.amount:
            continue
        target = (
            None
            if definition.merge_into is None
            else event_of(s, definition.merge_into, txn.id)
        )
        if target is not None and target.status in LIVE:
            # One report for both: the target's event names this capture.
            note_merged(target, [definition.code])
            continue
        _ = write_event(run, definition, txn, late=late, fee=fee)
    reopen_touched(run)
    return CaptureOutcome(
        events=tuple(run.events),
        voided=tuple(run.voided),
        superseded=tuple(run.superseded),
    )
