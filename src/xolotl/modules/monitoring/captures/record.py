"""Writing reportable events and touching their periods (SP-04 spec §10.2, §10.5).

Every writer of ``reportable_event`` is here: thresholds, group crossings and
supersession all go through it, in the processing row's database transaction. A
transaction has one event per capture code (D13, the table's unique key), so an
event is never copied: ``write_event`` inserts the first one, ``capture_member``
captures a group member again in place (from another group, or back from
``SUPERSEDED``), and ``retire_event`` marks one ``SUPERSEDED`` when its
transaction no longer belongs to any group that crossed: it is then simply not
reportable for that capture.

``monitoring.reportable_event_superseded.v1`` carries ``superseded_by_id``: the
event's own id when it was re-captured in place into a new group (a second
``reportable_event_captured`` follows), or null when it was retired. SP-05 must
record which events a report took in its own report lines, since the status of a
re-captured ``INCLUDED`` event goes back to ``CAPTURED`` (plan 4b Deviation 23).

When an event is captured for a capture other captures merge into (``AVI_XVI_B``
into ``AVI_XVI_A``), their live events for the transaction are voided as
``MERGED`` and named in ``fields.merged_captures`` (Deviation 10); when it is
retired, those are captured again.

``open_period`` and ``reopen_period`` are the minimal period writers B11
completes: the first inserts the ``capture_period`` row of an event's period when
it is missing and never locks an existing one (every party's events share it),
the second marks a closed period ``REOPENED``. A run reopens the periods it
touched once, at its end, in ascending order, so two runs never lock them in
opposite orders.
"""

import calendar
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from xolotl.modules.monitoring.captures.definitions import CaptureDefinition
from xolotl.modules.monitoring.captures.enums import (
    CapturePeriodStatus,
    ReportableEventStatus,
)
from xolotl.modules.monitoring.captures.fields import (
    event_fields,
    read_field_map,
    unit_values,
)
from xolotl.modules.monitoring.captures.models import (
    CaptureGroup,
    CapturePeriod,
    ReportableEvent,
)
from xolotl.modules.monitoring.enrich.policy import policy_date
from xolotl.modules.monitoring.enrich.service import enriched_from_row
from xolotl.modules.monitoring.enrich.units import (
    ParkTransactionError,
    unit_amounts_for,
)
from xolotl.modules.monitoring.enums import ParkReason
from xolotl.modules.monitoring.events import (
    CAPTURE_PERIOD_CHANGED,
    REPORTABLE_EVENT_CAPTURED,
    REPORTABLE_EVENT_SUPERSEDED,
    publish_monitoring_event,
)
from xolotl.modules.monitoring.txn.models import Account, Transaction
from xolotl.modules.monitoring.txn.schema import EnrichedTransaction, UnitAmount
from xolotl.platform import clock
from xolotl.platform.calendar import Holidays
from xolotl.platform.db import bound_context
from xolotl.platform.ids import uuid7
from xolotl.platform.units import UNIT_OF, UnitService

if TYPE_CHECKING:
    from pydantic import JsonValue

# What a report takes: an event in either status is live.
LIVE = frozenset({ReportableEventStatus.CAPTURED, ReportableEventStatus.INCLUDED})
MERGED = "MERGED"
LOCAL_CCY = "MXN"
_CENTS = Decimal(100)
_MONTH_KEY = re.compile(r"^(\d{4})-(\d{2})$")
_QUARTER_KEY = re.compile(r"^(\d{4})-Q([1-4])$")
_MONTHS_PER_QUARTER = 3


@dataclass(slots=True)
class CaptureRun:
    """What one capture evaluation writes with, and what it wrote.

    ``definitions`` are the snapshot's, by code; ``events`` collects the events
    written or captured again, ``voided`` the ones merged away and ``superseded``
    the ones retired. ``reopen`` holds the closed periods touched, reopened by
    ``reopen_touched`` at the run's end.
    """

    s: Session
    definitions: Mapping[str, CaptureDefinition]
    units: UnitService
    holidays: Holidays
    events: list[ReportableEvent] = field(default_factory=list)
    voided: list[ReportableEvent] = field(default_factory=list)
    superseded: list[ReportableEvent] = field(default_factory=list)
    reopen: set[tuple[str, str]] = field(default_factory=set)
    _accounts: dict[UUID, str] = field(default_factory=dict)

    def merged_from(self, code: str) -> tuple[str, ...]:
        """Name the captures that merge into ``code``.

        Returns:
            Their codes, in the snapshot's order.

        """
        return tuple(
            other.code
            for other in self.definitions.values()
            if other.merge_into == code
        )

    def account_ref(self, account_id: UUID | None) -> str | None:
        """Read the client's reference of an account, once per run.

        Returns:
            ``account.external_ref``, or None for a transaction without one.

        """
        if account_id is None:
            return None
        found = self._accounts.get(account_id)
        if found is None:
            tenant_id = bound_context(self.s).require_tenant_id()
            found = self.s.get_one(Account, (tenant_id, account_id)).external_ref
            self._accounts[account_id] = found
        return found


def unit_amount_of(definition: CaptureDefinition, txn: EnrichedTransaction) -> Decimal:
    """Read the transaction in the capture's unit.

    Returns:
        ``txn.unit_amounts[definition.unit_key]``.

    Raises:
        ParkTransactionError: ``UNIT_VALUE_MISSING`` when enrichment recorded no
            such amount: the row parks, as 4a parks a missing unit value, rather
            than skip a capture.

    """
    amount = txn.unit_amount(definition.unit_key)
    if amount is None:
        unit = definition.threshold.unit
        raise ParkTransactionError(
            ParkReason.UNIT_VALUE_MISSING,
            UNIT_OF.get(unit, unit),
            needed_on=policy_date(txn.occurred_at),
        )
    return amount


def fee_amount(
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


def period_bounds(period_key: str) -> tuple[date, date]:
    """Return the first and last day of a ``MONTH`` or ``QUARTER`` period key.

    Returns:
        ``(start, end)``, both included.

    Raises:
        ValueError: When ``period_key`` is neither ``"2027-02"`` nor ``"2027-Q1"``.

    """
    if month := _MONTH_KEY.match(period_key):
        year, first = int(month.group(1)), int(month.group(2))
        last = first
    elif quarter := _QUARTER_KEY.match(period_key):
        year = int(quarter.group(1))
        first = (int(quarter.group(2)) - 1) * _MONTHS_PER_QUARTER + 1
        last = first + _MONTHS_PER_QUARTER - 1
    else:
        msg = f"{period_key!r} is not a period key"
        raise ValueError(msg)
    return date(year, first, 1), date(year, last, calendar.monthrange(year, last)[1])


def open_period(
    s: Session, definition: CaptureDefinition, period_key: str
) -> CapturePeriod:
    """Make sure the capture's period row exists, without locking it.

    Returns:
        The period row, as it stands.

    """
    start, end = period_bounds(period_key)
    _ = s.execute(
        insert(CapturePeriod)
        .values(
            tenant_id=bound_context(s).require_tenant_id(),
            capture_code=definition.code,
            report_code=definition.report_code,
            period_key=period_key,
            period_start=start,
            period_end=end,
        )
        .on_conflict_do_nothing(
            index_elements=["tenant_id", "capture_code", "period_key"]
        )
    )
    return s.scalars(
        select(CapturePeriod).where(
            CapturePeriod.capture_code == definition.code,
            CapturePeriod.period_key == period_key,
        )
    ).one()


def _discrepancy_count(period: CapturePeriod) -> int:
    if period.discrepancies is None:
        return 0
    return sum(
        len(ids) for ids in period.discrepancies.values() if isinstance(ids, list)
    )


def reopen_period(
    s: Session, capture_code: str, period_key: str
) -> CapturePeriod | None:
    """Mark a closed period ``REOPENED`` and tell reporting it changed.

    A ``CLOSED`` period becomes ``REOPENED`` with ``reopened_at`` now and
    ``change_count`` one higher, and ``monitoring.capture_period_changed.v1`` is
    published (SP-05 regenerates a drafted report, D14). An ``OPEN`` or already
    ``REOPENED`` period is left as it is.

    Returns:
        The period, or None when the capture has no row for it.

    """
    period = s.scalars(
        select(CapturePeriod).where(
            CapturePeriod.capture_code == capture_code,
            CapturePeriod.period_key == period_key,
        )
    ).first()
    if period is None or period.status is not CapturePeriodStatus.CLOSED:
        return period
    # Only a closed period is locked, and only to change it: the update waits for
    # a close in flight and acts on what it committed.
    changed = s.execute(
        update(CapturePeriod)
        .where(
            CapturePeriod.id == period.id,
            CapturePeriod.status == CapturePeriodStatus.CLOSED,
        )
        .values(
            status=CapturePeriodStatus.REOPENED,
            reopened_at=clock.now(),
            change_count=CapturePeriod.change_count + 1,
        )
        .returning(CapturePeriod.id)
    ).first()
    s.refresh(period)
    if changed is None:
        return period
    _ = publish_monitoring_event(
        s,
        CAPTURE_PERIOD_CHANGED,
        entity="capture_period",
        entity_id=period.id,
        key=str(period.id),
        data={
            "capture_period_id": str(period.id),
            "capture_code": period.capture_code,
            "report_code": period.report_code,
            "period_key": period.period_key,
            "status": period.status.value,
            "discrepancy_count": _discrepancy_count(period),
        },
    )
    return period


def note_period(
    run: CaptureRun, definition: CaptureDefinition, period_key: str
) -> None:
    """Open an event's period; a closed one is reopened at the run's end."""
    period = open_period(run.s, definition, period_key)
    if period.status is CapturePeriodStatus.CLOSED:
        run.reopen.add((definition.code, period_key))


def reopen_touched(run: CaptureRun) -> None:
    """Reopen the closed periods the run touched, in ascending order."""
    for capture_code, period_key in sorted(run.reopen):
        _ = reopen_period(run.s, capture_code, period_key)
    run.reopen.clear()


def event_of(
    s: Session, capture_code: str, transaction_id: UUID
) -> ReportableEvent | None:
    """Read a transaction's event for one capture, locked for update.

    Returns:
        The event, or None.

    """
    return s.scalars(
        select(ReportableEvent)
        .where(
            ReportableEvent.capture_code == capture_code,
            ReportableEvent.transaction_id == transaction_id,
        )
        .with_for_update()
    ).first()


def publish_captured(s: Session, event: ReportableEvent) -> None:
    """Publish ``monitoring.reportable_event_captured.v1`` for ``event``."""
    _ = publish_monitoring_event(
        s,
        REPORTABLE_EVENT_CAPTURED,
        entity="reportable_event",
        entity_id=event.id,
        key=str(event.party_id),
        data={
            "reportable_event_id": str(event.id),
            "capture_code": event.capture_code,
            "report_code": event.report_code,
            "period_key": event.period_key,
            "transaction_id": str(event.transaction_id),
        },
        policy_snapshot_id=event.policy_snapshot_id,
    )


def publish_superseded(
    run: CaptureRun, event: ReportableEvent, successor: UUID | None
) -> None:
    """Publish ``monitoring.reportable_event_superseded.v1`` for ``event``.

    ``successor`` is the event's own id when it was captured again in place, or
    None when it was retired.
    """
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


def _merged_names(event: ReportableEvent) -> list[str]:
    names = event.fields.get("merged_captures")
    return [str(name) for name in names] if isinstance(names, list) else []


def note_merged(event: ReportableEvent, codes: Sequence[str]) -> None:
    """Name ``codes`` in the event's ``fields.merged_captures``, once each."""
    names = _merged_names(event)
    added = [code for code in codes if code not in names]
    if not added:
        return
    # A new dict, so the JSONB column is written.
    fields: dict[str, JsonValue] = {
        **event.fields,
        "merged_captures": [*names, *added],
    }
    event.fields = fields


def _merge(run: CaptureRun, event: ReportableEvent) -> None:
    """Void the transaction's live events that merge into ``event``'s capture."""
    voided: list[str] = []
    for code in run.merged_from(event.capture_code):
        other = event_of(run.s, code, event.transaction_id)
        if other is None or other.status not in LIVE:
            continue
        other.status = ReportableEventStatus.VOIDED
        other.void_reason = MERGED
        run.voided.append(other)
        voided.append(code)
    note_merged(event, voided)


def _restore_merged(run: CaptureRun, event: ReportableEvent) -> None:
    """Capture again what merged into a retired event (``AVI_XVI_B``).

    A ``MERGED`` event comes back as ``CAPTURED``; a capture merged when both
    fired at once, so never written, is evaluated again and written.
    """
    s = run.s
    named = _merged_names(event)
    txn: EnrichedTransaction | None = None
    for code in run.merged_from(event.capture_code):
        other = event_of(s, code, event.transaction_id)
        if other is not None:
            if (
                other.status is ReportableEventStatus.VOIDED
                and other.void_reason == MERGED
            ):
                other.status = ReportableEventStatus.CAPTURED
                other.void_reason = None
                other.late = True
                note_period(run, run.definitions[code], other.period_key)
                publish_captured(s, other)
                run.events.append(other)
            continue
        if code not in named:
            continue
        if txn is None:
            tenant_id = bound_context(s).require_tenant_id()
            row = s.get_one(
                Transaction, (tenant_id, event.occurred_at, event.transaction_id)
            )
            txn = enriched_from_row(row)
        definition = run.definitions[code]
        fee = fee_amount(definition, txn, units=run.units, holidays=run.holidays)
        if fee is not None and fee.amount >= definition.threshold.amount:
            _ = write_event(run, definition, txn, late=True, fee=fee)


def write_event(
    run: CaptureRun,
    definition: CaptureDefinition,
    txn: EnrichedTransaction,
    *,
    late: bool,
    group: CaptureGroup | None = None,
    fee: UnitAmount | None = None,
) -> ReportableEvent | None:
    """Capture ``txn`` for ``definition`` unless it already has an event.

    The event records the pre-mapped fields, the unit values, the snapshot and
    the period of the capture (``period_keys.MONTH`` or ``.QUARTER``); its period
    row is opened (reopened when closed), the captures merging into it are voided
    and ``monitoring.reportable_event_captured.v1`` is published. An existing
    event of the capture for ``txn`` is left as it is (``capture_member`` captures
    a group member again).

    Returns:
        The new event, or None when ``txn`` already had one.

    """
    s = run.s
    if event_of(s, definition.code, txn.id) is not None:
        return None
    on = policy_date(txn.occurred_at)
    converted = run.units.threshold_in_ccy(
        definition.threshold, LOCAL_CCY, on, definition.conversion_rule
    )
    period_key = definition.period_key_of(txn)
    account_ref = run.account_ref(txn.account_id) or ""
    event = s.scalars(
        insert(ReportableEvent)
        .values(
            tenant_id=bound_context(s).require_tenant_id(),
            id=uuid7(),
            capture_code=definition.code,
            report_code=definition.report_code,
            capture_kind=definition.kind,
            transaction_id=txn.id,
            occurred_at=txn.occurred_at,
            party_id=txn.party_id,
            relationship_id=txn.relationship_id,
            product=txn.product,
            period_key=period_key,
            capture_group_id=None if group is None else group.id,
            fields=event_fields(
                txn, account_ref, read_field_map(definition.report_code)
            ),
            unit_values=unit_values(definition, txn, converted, fee=fee),
            policy_snapshot_id=txn.policy_snapshot_id,
            late=late,
        )
        .on_conflict_do_nothing(
            index_elements=["tenant_id", "capture_code", "transaction_id"]
        )
        .returning(ReportableEvent)
    ).first()
    if event is None:
        # Written by another session since the read: that event stands.
        return None
    note_period(run, definition, period_key)
    _merge(run, event)
    publish_captured(s, event)
    run.events.append(event)
    return event


def capture_member(
    run: CaptureRun,
    definition: CaptureDefinition,
    txn: EnrichedTransaction,
    group: CaptureGroup,
    *,
    late: bool,
) -> ReportableEvent | None:
    """Capture a member of a crossed group, writing or capturing it again.

    A member without an event gets one. A member whose event is live in this
    group keeps it. Any other event of the capture for it (live in a superseded
    group, ``SUPERSEDED`` or ``VOIDED``) is captured again in place: it joins the
    group as ``CAPTURED`` with ``late`` set, keeping its fields and unit values;
    an event that was live elsewhere is published as superseded by itself first.
    The captures merging into it are voided again.

    Returns:
        The event written or captured again, or None when it was already there.

    """
    existing = event_of(run.s, definition.code, txn.id)
    if existing is None:
        return write_event(run, definition, txn, late=late, group=group)
    if existing.status in LIVE and existing.capture_group_id == group.id:
        return None
    if existing.status in LIVE:
        publish_superseded(run, existing, existing.id)
    existing.capture_group_id = group.id
    existing.status = ReportableEventStatus.CAPTURED
    existing.superseded_by_id = None
    existing.void_reason = None
    existing.late = True
    note_period(run, definition, existing.period_key)
    _merge(run, existing)
    publish_captured(run.s, existing)
    run.events.append(existing)
    return existing


def retire_event(run: CaptureRun, event: ReportableEvent) -> None:
    """Retire an event whose transaction no longer belongs to a crossed group.

    It becomes ``SUPERSEDED`` with no successor: the transaction is not
    reportable for this capture any more. What merged into it is captured again.
    """
    event.status = ReportableEventStatus.SUPERSEDED
    event.superseded_by_id = None
    event.late = True
    publish_superseded(run, event, None)
    run.reopen.add((event.capture_code, event.period_key))
    run.superseded.append(event)
    _restore_merged(run, event)
