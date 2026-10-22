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
  operation that moves a group's window earlier, or its crossing point,
  supersedes the group (``supersede.py``).
- ``merge_into``: a capture whose target already holds an event for the
  transaction writes nothing and is named in the target's
  ``fields.merged_captures``; a target written later voids it (Deviation 10).

A capture never opens a hit and never reaches an API client (spec §10.1).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from xolotl.modules.monitoring.aggregates.declarations import matches
from xolotl.modules.monitoring.aggregates.store import AggregateKey, AggregateStore
from xolotl.modules.monitoring.aggregates.windows import accum_window
from xolotl.modules.monitoring.captures.definitions import CaptureDefinition
from xolotl.modules.monitoring.captures.enums import CaptureGroupStatus, CaptureKind
from xolotl.modules.monitoring.captures.groups import (
    add_to_group,
    cross_group,
    crossing_at,
    group_after,
    group_containing,
    group_members,
    new_group,
)
from xolotl.modules.monitoring.captures.models import ReportableEvent
from xolotl.modules.monitoring.captures.record import (
    LIVE,
    CaptureRun,
    event_of,
    write_event,
)
from xolotl.modules.monitoring.captures.supersede import supersede_group
from xolotl.modules.monitoring.enrich.policy import policy_date
from xolotl.modules.monitoring.enrich.units import unit_amounts_for
from xolotl.modules.monitoring.txn.schema import (
    EnrichedTransaction,
    KeyKind,
    UnitAmount,
)
from xolotl.platform.calendar import Holidays
from xolotl.platform.units import UnitService

if TYPE_CHECKING:
    from pydantic import JsonValue

_CENTS = Decimal(100)


@dataclass(frozen=True, slots=True)
class CaptureOutcome:
    """What capturing one transaction wrote.

    ``events`` were written or re-captured, ``voided`` were merged away and
    ``superseded`` were retired by a supersession without a successor.
    """

    events: tuple[ReportableEvent, ...]
    voided: tuple[ReportableEvent, ...]
    superseded: tuple[ReportableEvent, ...] = ()


def _fee_amount(
    definition: CaptureDefinition,
    txn: EnrichedTransaction,
    *,
    units: UnitService,
    holidays: Holidays,
) -> UnitAmount | None:
    """Convert the fee into the threshold's unit, as 4a converts amounts.

    Returns:
        The fee's unit amount, or None when the transaction carries no fee.

    """
    if txn.fee_minor is None:
        return None
    converted = unit_amounts_for(
        units,
        amount=Decimal(txn.fee_minor) / _CENTS,
        currency=txn.currency,
        on=policy_date(txn.occurred_at),
        pairs={(definition.threshold.unit, definition.conversion_rule)},
        holidays=holidays,
    )
    return converted.get(definition.unit_key)


def _note_merged(target: ReportableEvent, code: str) -> None:
    names = target.fields.get("merged_captures")
    known = [str(name) for name in names] if isinstance(names, list) else []
    if code in known:
        return
    fields: dict[str, JsonValue] = {**target.fields, "merged_captures": [*known, code]}
    target.fields = fields


def _accumulate(
    run: CaptureRun,
    definition: CaptureDefinition,
    txn: EnrichedTransaction,
    *,
    store: AggregateStore,
    late: bool,
) -> None:
    """Capture an operation for an ``ACCUMULATION`` capture (spec §10.2, §10.5)."""
    amount = txn.unit_amount(definition.unit_key)
    if amount is None:
        return
    if amount >= definition.threshold.amount:
        _ = write_event(run, definition, txn, late=late)
        return
    (declaration,) = definition.declarations()
    value = store.accumulator_read(
        AggregateKey(KeyKind.RELATIONSHIP, str(txn.relationship_id)),
        declaration,
        at=txn.occurred_at,
    )
    first_at = value.first_at or txn.occurred_at
    s = run.s
    group = group_containing(s, definition, txn)
    if group is None:
        _, window_end = accum_window(first_at, declaration.window)
        displaced = group_after(s, definition, txn, until=window_end)
        if displaced is not None:
            # The late operation opens the window earlier (spec §10.5).
            _ = supersede_group(run, definition, displaced, late=txn, first_at=first_at)
            return
        group = new_group(s, definition, txn, first_at=first_at)
    add_to_group(group, txn, value.value)
    if group.status is CaptureGroupStatus.OPEN:
        if value.value >= definition.threshold.amount:
            members = group_members(s, definition, group, in_flight=(txn,))
            at = (crossing_at(definition, members) if late else None) or txn.occurred_at
            _ = cross_group(run, definition, group, at=at, members=members, late=late)
        return
    crossed = group.threshold_crossed_at
    if late and crossed is not None and txn.occurred_at < crossed:
        members = group_members(s, definition, group, in_flight=(txn,))
        if crossing_at(definition, members) != crossed:
            # The late operation moves the crossing point (spec §10.5).
            _ = supersede_group(run, definition, group, late=txn, first_at=first_at)
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
        The events written or re-captured, voided and superseded.

    """
    run = CaptureRun(s, {d.code: d for d in definitions}, units)
    ordered = sorted(definitions, key=lambda d: d.merge_into is not None)
    for definition in ordered:
        if not matches(definition.on, txn):
            continue
        if definition.kind is CaptureKind.ACCUMULATION:
            _accumulate(run, definition, txn, store=store, late=late)
            continue
        fee: UnitAmount | None = None
        if definition.kind is CaptureKind.FEE_THRESHOLD:
            fee = _fee_amount(definition, txn, units=units, holidays=holidays)
            measured = None if fee is None else fee.amount
        else:
            measured = txn.unit_amount(definition.unit_key)
        if measured is None or measured < definition.threshold.amount:
            continue
        target = (
            None
            if definition.merge_into is None
            else event_of(s, definition.merge_into, txn.id)
        )
        if target is not None and target.status in LIVE:
            # One report for both: the target's event names this capture.
            _note_merged(target, definition.code)
            continue
        _ = write_event(run, definition, txn, late=late, fee=fee)
    return CaptureOutcome(
        events=tuple(run.events),
        voided=tuple(run.voided),
        superseded=tuple(run.superseded),
    )
