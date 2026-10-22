"""Writing a reportable event and touching its period (SP-04 spec §10.2, §10.5).

``write_event`` is the one writer of ``reportable_event``: thresholds, group
crossings and supersession all go through it, in the processing row's database
transaction. A transaction has one event per capture code (D13, the table's
unique key); an event that already exists is kept, except that supersession
re-captures it in place (plan 4b B10 deviation: the unique key leaves no room
for a second row). When an event is written for a capture other captures merge
into (``AVI_XVI_B`` into ``AVI_XVI_A``), their live events for the transaction
are voided as ``MERGED`` and named in ``fields.merged_captures`` (Deviation 10).

``open_period`` and ``reopen_period`` are the minimal period writers B11
completes: the first inserts the ``capture_period`` row of an event's period when
it is missing and never locks an existing one (every party's events share it),
the second marks a closed period ``REOPENED`` when a late event changes it.
"""

import calendar
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
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
from xolotl.modules.monitoring.events import (
    CAPTURE_PERIOD_CHANGED,
    REPORTABLE_EVENT_CAPTURED,
    publish_monitoring_event,
)
from xolotl.modules.monitoring.txn.models import Account
from xolotl.modules.monitoring.txn.schema import EnrichedTransaction, UnitAmount
from xolotl.platform import clock
from xolotl.platform.db import bound_context
from xolotl.platform.ids import uuid7
from xolotl.platform.units import UnitService

if TYPE_CHECKING:
    from pydantic import JsonValue

# What a report takes: an event in either status is live.
LIVE = frozenset({ReportableEventStatus.CAPTURED, ReportableEventStatus.INCLUDED})
MERGED = "MERGED"
LOCAL_CCY = "MXN"
_MONTH_KEY = re.compile(r"^(\d{4})-(\d{2})$")
_QUARTER_KEY = re.compile(r"^(\d{4})-Q([1-4])$")
_MONTHS_PER_QUARTER = 3


@dataclass(slots=True)
class CaptureRun:
    """What one capture evaluation writes with, and what it wrote.

    ``definitions`` are the snapshot's, by code; ``events`` collects the events
    written or re-captured, ``voided`` the ones merged away and ``superseded``
    the ones supersession retired.
    """

    s: Session
    definitions: Mapping[str, CaptureDefinition]
    units: UnitService
    events: list[ReportableEvent] = field(default_factory=list)
    voided: list[ReportableEvent] = field(default_factory=list)
    superseded: list[ReportableEvent] = field(default_factory=list)
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


def touch_period(s: Session, definition: CaptureDefinition, period_key: str) -> None:
    """Open the event's period, reopening it when it was already closed."""
    period = open_period(s, definition, period_key)
    if period.status is CapturePeriodStatus.CLOSED:
        _ = reopen_period(s, definition.code, period_key)


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


def _merge(run: CaptureRun, event: ReportableEvent, merged: Sequence[str]) -> None:
    """Void the transaction's live events that merge into ``event``'s capture."""
    names = list(merged)
    for code in run.merged_from(event.capture_code):
        other = event_of(run.s, code, event.transaction_id)
        if other is None or other.status not in LIVE:
            continue
        other.status = ReportableEventStatus.VOIDED
        other.void_reason = MERGED
        run.voided.append(other)
        if code not in names:
            names.append(code)
    if names != event.fields.get("merged_captures"):
        # A new dict, so the JSONB column is written.
        fields: dict[str, JsonValue] = {**event.fields, "merged_captures": [*names]}
        event.fields = fields


def write_event(
    run: CaptureRun,
    definition: CaptureDefinition,
    txn: EnrichedTransaction,
    *,
    late: bool,
    group: CaptureGroup | None = None,
    fee: UnitAmount | None = None,
    merged: Sequence[str] = (),
) -> ReportableEvent | None:
    """Capture ``txn`` for ``definition`` unless it already has an event.

    The event records the pre-mapped fields, the unit values, the snapshot and
    the period of the capture (``period_keys.MONTH`` or ``.QUARTER``); its period
    row is opened (reopened when closed), the captures merging into it are voided
    and ``monitoring.reportable_event_captured.v1`` is published. An existing
    event of the capture for ``txn``, whatever its status, is left as it is.

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
                txn, account_ref, read_field_map(definition.report_code), merged=merged
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
    touch_period(s, definition, period_key)
    _merge(run, event, merged)
    publish_captured(s, event)
    run.events.append(event)
    return event
