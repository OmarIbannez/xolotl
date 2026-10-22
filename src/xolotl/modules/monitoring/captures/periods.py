"""Capture periods and their close, reconciled by re-evaluation (spec §10.4).

A ``capture_period`` row is one capture code's period (``2026-12`` or
``2026-Q4``). Events open it as they are written (``record.open_period``); the
monthly ``monitoring.period_close`` job first makes sure every capture of every
policy in force on the last day of each ended period has its row, so a period
without an event is reconciled too (plan 4b Deviation 11), then closes each
``OPEN`` or ``REOPENED`` period whose last day has passed.

The close re-runs the capture over the period's ``PROCESSED`` transactions in
check mode (``check_captures``, no writes), each under its own policy snapshot's
definition: a threshold or fee at or above the threshold, and every member of
an accumulation window that crosses, the windows chained in ``occurred_at``
order as processing chains them (spec §10.2); a capture that merges into
another is not expected where the other fires. The set of transaction ids is
compared with the period's live events (``CAPTURED``, ``INCLUDED``): a missing
one is written, or captured again in place, with ``late`` set; an extra one is
``VOIDED`` with ``void_reason = "RECONCILIATION"``. The period records
``reconciled_count`` (what the check expects), ``event_count`` (the live events
after the close) and the ids it found (``discrepancies``), becomes ``CLOSED`` and
publishes ``monitoring.capture_period_closed.v1``. A discrepancy is a defect,
not a business event. Voiding an ``INCLUDED`` event, or changing a period closed
before, is a change reporting must see: the period counts it
(``change_count``) and publishes ``monitoring.capture_period_changed.v1`` first.
"""

import logging
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from xolotl.modules.monitoring.aggregates.declarations import matches
from xolotl.modules.monitoring.aggregates.windows import (
    MEXICO_CITY_ZONE,
    accum_window,
)
from xolotl.modules.monitoring.captures.definitions import (
    CaptureDefinition,
    capture_definitions,
)
from xolotl.modules.monitoring.captures.enums import (
    CaptureGroupStatus,
    CaptureKind,
    CapturePeriodStatus,
    ReportableEventStatus,
)
from xolotl.modules.monitoring.captures.groups import (
    crossing_at,
    group_containing,
    members_between,
    window_of,
)
from xolotl.modules.monitoring.captures.models import (
    CaptureGroup,
    CapturePeriod,
    ReportableEvent,
)
from xolotl.modules.monitoring.captures.record import (
    LIVE,
    CaptureRun,
    capture_member,
    discrepancy_count,
    fee_amount,
    insert_period,
    period_bounds,
    reopen_touched,
    void_event,
)
from xolotl.modules.monitoring.captures.supersede import partition
from xolotl.modules.monitoring.enrich.policy import policy_date
from xolotl.modules.monitoring.enrich.service import enriched_from_row
from xolotl.modules.monitoring.enums import TransactionStatus
from xolotl.modules.monitoring.errors import PeriodNotEndedError
from xolotl.modules.monitoring.events import (
    CAPTURE_PERIOD_CHANGED,
    CAPTURE_PERIOD_CLOSED,
    publish_monitoring_event,
)
from xolotl.modules.monitoring.txn.models import Transaction
from xolotl.modules.monitoring.txn.schema import (
    EnrichedTransaction,
    Instrument,
    KeyKind,
)
from xolotl.platform import clock
from xolotl.platform.audit import AuditEvent, record
from xolotl.platform.calendar import Holidays, load_holidays
from xolotl.platform.db import bound_context
from xolotl.platform.enums import HolidayScope
from xolotl.platform.errors import NotFoundError, ValidationFailedError
from xolotl.platform.jobs.queue import Job
from xolotl.platform.policy import (
    PolicyNotAssignedError,
    assignments_in_force,
    effective_policy,
    get_snapshot,
)
from xolotl.platform.policy.durations import iso_duration_days
from xolotl.platform.units import UnitService

if TYPE_CHECKING:
    from pydantic import JsonValue

PERIOD_CLOSE_KIND = "monitoring.period_close"
RECONCILIATION = "RECONCILIATION"
CLOSED_BY_HAND = "captures.period_closed"
_OPEN = (CapturePeriodStatus.OPEN, CapturePeriodStatus.REOPENED)
_MONTHS_PER_QUARTER = 3
_QUARTERS = 4
_STREAM_PAGE = 500
_DAYS_PER_MONTH = 30
_LONGEST_MONTH = 31

logger = logging.getLogger(__name__)

type Definitions = dict[str, CaptureDefinition]


def _mexico_start(day: date) -> datetime:
    return datetime.combine(day, time(), tzinfo=MEXICO_CITY_ZONE).astimezone(UTC)


def _instants(period_key: str) -> tuple[datetime, datetime]:
    """Return the period's first instant and the first instant after it.

    Returns:
        ``[start, end)`` in UTC, from Mexico City days.

    """
    first, last = period_bounds(period_key)
    return _mexico_start(first), _mexico_start(last + timedelta(days=1))


@dataclass(slots=True)
class _Snapshots:
    """The capture definitions of each policy snapshot met, read once."""

    s: Session
    _found: dict[UUID, Definitions] = field(default_factory=dict)

    def of(self, snapshot_id: UUID | None) -> Definitions:
        """Read a snapshot's captures by code.

        Returns:
            Its definitions, empty for a row without a snapshot.

        """
        if snapshot_id is None:
            return {}
        found = self._found.get(snapshot_id)
        if found is None:
            policy = get_snapshot(self.s, snapshot_id).policy
            found = {d.code: d for d in capture_definitions(policy)}
            self._found[snapshot_id] = found
        return found


def definitions_on(s: Session, on: date) -> Definitions:
    """Read the captures of every policy in force on ``on``, by code.

    Every product with an assignment in force (and the tenant-wide policy) is
    resolved; a product whose packs are not in force yet adds nothing.

    Returns:
        One definition per capture code, the first product's when two share one.

    """
    products = sorted(
        {row.product for row in assignments_in_force(s, on=on)},
        key=lambda product: product or "",
    )
    found: Definitions = {}
    for product in products:
        try:
            policy = effective_policy(s, on=on, product=product)
        except PolicyNotAssignedError:
            continue
        for definition in capture_definitions(policy):
            _ = found.setdefault(definition.code, definition)
    return found


def definition_for(s: Session, period: CapturePeriod) -> CaptureDefinition | None:
    """Find the definition a period's close re-runs.

    Returns:
        The capture of the policy in force on the period's last day, else the one
        of the snapshot of one of its events, else None (nothing is expected).

    """
    found = definitions_on(s, period.period_end).get(period.capture_code)
    if found is not None:
        return found
    snapshot_id = s.scalars(
        select(ReportableEvent.policy_snapshot_id)
        .where(
            ReportableEvent.capture_code == period.capture_code,
            ReportableEvent.period_key == period.period_key,
        )
        .limit(1)
    ).first()
    return _Snapshots(s).of(snapshot_id).get(period.capture_code)


def _ended_periods(
    first: date, before: date
) -> Iterator[tuple[Literal["MONTH", "QUARTER"], str, date]]:
    """List every month and quarter from ``first``'s that ended before ``before``.

    Yields:
        ``(period, key, last_day)``.

    """
    year, month = first.year, first.month
    while True:
        key = f"{year:04d}-{month:02d}"
        _, last = period_bounds(key)
        if last >= before:
            break
        yield "MONTH", key, last
        year, month = divmod(year * 12 + month, 12)
        month += 1
    year, quarter = first.year, (first.month - 1) // _MONTHS_PER_QUARTER + 1
    while True:
        key = f"{year:04d}-Q{quarter}"
        _, last = period_bounds(key)
        if last >= before:
            break
        yield "QUARTER", key, last
        year, quarter = (year + 1, 1) if quarter == _QUARTERS else (year, quarter + 1)


def ensure_periods(s: Session, *, ended_before: date) -> int:
    """Give every capture a row for each period that ended (Deviation 11).

    From the month and quarter of the tenant's earliest ``PROCESSED`` transaction
    (or, when every capture in force has closed a period, from the day after the
    earliest of their latest closes: what earlier runs covered), skipping each
    capture's periods up to its own latest close, to the last ones ending before
    ``ended_before``, every capture of every policy in force on the period's last
    day gets its row (``MONTH`` captures a month's, ``QUARTER`` captures a
    quarter's), so a period without events is reconciled too. On a first deploy
    no period is closed yet, so every period since the first processed
    transaction gets its row and is closed by the run.

    Returns:
        How many rows were created.

    """
    earliest = s.scalar(
        select(func.min(Transaction.occurred_at)).where(
            Transaction.status == TransactionStatus.PROCESSED
        )
    )
    if earliest is None:
        return 0
    first = policy_date(earliest)
    closed_through: dict[str, date] = dict(
        s.execute(
            select(CapturePeriod.capture_code, func.max(CapturePeriod.period_end))
            .where(CapturePeriod.status == CapturePeriodStatus.CLOSED)
            .group_by(CapturePeriod.capture_code)
        ).all()
    )
    yesterday = ended_before - timedelta(days=1)
    in_force = definitions_on(s, yesterday)
    if closed_through and all(code in closed_through for code in in_force):
        # Every capture in force has closed: what earlier runs covered is skipped.
        first = max(first, min(closed_through.values()) + timedelta(days=1))
    by_day: dict[date, Definitions] = {yesterday: in_force}
    created = 0
    for period, key, last_day in _ended_periods(first, ended_before):
        if last_day not in by_day:
            by_day[last_day] = definitions_on(s, last_day)
        for definition in by_day[last_day].values():
            through = closed_through.get(definition.code)
            if definition.period != period or (
                through is not None and last_day <= through
            ):
                continue
            if insert_period(
                s,
                capture_code=definition.code,
                report_code=definition.report_code,
                period_key=key,
            ):
                created += 1
    return created


type Candidates = list[tuple[EnrichedTransaction, CaptureDefinition]]


def _period_candidates(
    s: Session, snapshots: _Snapshots, codes: Sequence[str], period_key: str
) -> dict[str, Candidates]:
    """Stream the period's processed rows, keeping each capture's candidates.

    Only rows whose snapshot has the first capture are read, in pages
    (``yield_per``), restricted in SQL to the instruments the captures look at;
    a row is kept, per code, when its snapshot's definition's ``on`` matches it
    and its period is ``period_key``. The rest is never held (B11 review I2).

    Returns:
        The candidates of each code, in ``occurred_at`` order.

    """
    found: dict[str, Candidates] = {code: [] for code in codes}
    start, end = _instants(period_key)
    in_range = (
        Transaction.status == TransactionStatus.PROCESSED,
        Transaction.occurred_at >= start,
        Transaction.occurred_at < end,
    )
    snapshot_ids = s.scalars(
        select(Transaction.policy_snapshot_id).where(*in_range).distinct()
    ).all()
    wanted = [sid for sid in snapshot_ids if codes[0] in snapshots.of(sid)]
    if not wanted:
        return found
    stmt = select(Transaction).where(
        *in_range, Transaction.policy_snapshot_id.in_(wanted)
    )
    filters = [
        definition.on.instrument
        for sid in wanted
        for code in codes
        if (definition := snapshots.of(sid).get(code)) is not None
    ]
    if filters and all(filters):
        instruments: set[Instrument] = set()
        for instrument in filters:
            instruments |= instrument
        stmt = stmt.where(Transaction.instrument.in_(instruments))
    rows = s.scalars(
        stmt.order_by(Transaction.occurred_at, Transaction.id).execution_options(
            yield_per=_STREAM_PAGE
        )
    )
    for row in rows:
        txn = enriched_from_row(row)
        definitions = snapshots.of(txn.policy_snapshot_id)
        for code in codes:
            definition = definitions.get(code)
            if (
                definition is not None
                and matches(definition.on, txn)
                and definition.period_key_of(txn) == period_key
            ):
                found[code].append((txn, definition))
    return found


def _reach(window: str) -> timedelta:
    # At least as long as the window in days, whatever the months it spans.
    return timedelta(
        days=iso_duration_days(window) * _LONGEST_MONTH // _DAYS_PER_MONTH + 2
    )


def _reaching(
    s: Session, definition: CaptureDefinition, relationship_id: UUID, cursor: datetime
) -> list[EnrichedTransaction]:
    """List the earlier operations whose own window reaches past ``cursor``.

    Returns:
        Them, in order; when there are none, the first operation from ``cursor``
        opens a window.

    """
    window = window_of(definition)
    earlier = members_between(
        s, definition, relationship_id, start=cursor - _reach(window), end=cursor
    )
    return [txn for txn in earlier if accum_window(txn.occurred_at, window)[1] > cursor]


def _chain_start(
    s: Session, definition: CaptureDefinition, relationship_id: UUID, at: datetime
) -> datetime:
    """Find an instant from which grouping in order reaches ``at`` as processing did.

    Windows chain: one opens on the first operation not in an earlier window
    (spec §10.2). Walking back from ``at``, the walk stops where no earlier
    operation's window reaches past the cursor, so the first operation from there
    opens a window.

    Returns:
        That instant (``at`` itself when nothing earlier reaches it).

    """
    cursor = at
    while reaching := _reaching(s, definition, relationship_id, cursor):
        cursor = reaching[0].occurred_at
    return cursor


def _group_anchor(
    s: Session, definition: CaptureDefinition, relationship_id: UUID, at: datetime
) -> datetime | None:
    """Find where processing opened the relationship's window holding ``at``.

    Processing opens a group for every accumulated operation not in a window, so
    the latest live group opened by ``at`` starts a chain that reaches ``at``.

    Returns:
        That group's ``window_start``, or None when there is none.

    """
    return s.scalars(
        select(CaptureGroup.window_start)
        .where(
            CaptureGroup.capture_code == definition.code,
            CaptureGroup.key_kind == KeyKind.RELATIONSHIP,
            CaptureGroup.key == str(relationship_id),
            CaptureGroup.status.in_((
                CaptureGroupStatus.OPEN,
                CaptureGroupStatus.CROSSED,
            )),
            CaptureGroup.window_start <= at,
        )
        .order_by(CaptureGroup.window_start.desc())
        .limit(1)
    ).first()


def _crossed_members(
    s: Session,
    definition: CaptureDefinition,
    relationship_id: UUID,
    *,
    start: datetime,
    end: datetime,
) -> set[UUID]:
    """List the members of the relationship's crossing windows around a range.

    Every accumulated operation of a window that crosses is captured, whenever it
    crossed: the windows holding an operation in ``[start, end)`` are chained from
    ``_chain_start``, which walks back from the start of the relationship's latest
    live group opened by ``start`` (the period's start when there is none): one
    step confirms that start when no earlier window reaches past it, so a
    misplaced group never misleads the check (B11 review I3, re-review I-R1).
    The windows are read to their own end.

    Returns:
        The ids of the members of those windows that cross.

    """
    window = window_of(definition)
    horizon = accum_window(end - timedelta(microseconds=1), window)[1]
    # Processing's group start is where the walk begins, not where it ends: its
    # first step confirms it, or walks on (B11 re-review I-R1).
    anchor = _group_anchor(s, definition, relationship_id, start)
    members = members_between(
        s,
        definition,
        relationship_id,
        start=_chain_start(
            s, definition, relationship_id, start if anchor is None else anchor
        ),
        end=horizon,
    )
    if not members:
        return set()
    # No kept group starts: every window, the way processing in order opens them.
    chunks, _ = partition(definition, members, late=members[0], kept_starts=frozenset())
    return {
        txn.id
        for chunk in chunks
        if crossing_at(definition, chunk) is not None
        for txn in chunk
    }


def _measured(
    definition: CaptureDefinition,
    txn: EnrichedTransaction,
    *,
    units: UnitService,
    holidays: Holidays,
) -> Decimal | None:
    if definition.kind is CaptureKind.FEE_THRESHOLD:
        fee = fee_amount(definition, txn, units=units, holidays=holidays)
        return None if fee is None else fee.amount
    return txn.unit_amount(definition.unit_key)


def _firing(
    s: Session,
    candidates: Candidates,
    *,
    period_key: str,
    units: UnitService,
    holidays: Holidays,
) -> set[UUID]:
    """Re-run one capture over candidates its ``on`` matches (check mode).

    Returns:
        The ids of the candidates the capture takes.

    """
    fired: set[UUID] = set()
    accumulating: dict[UUID, CaptureDefinition] = {}
    for txn, definition in candidates:
        measured = _measured(definition, txn, units=units, holidays=holidays)
        if measured is None:
            continue
        if measured >= definition.threshold.amount:
            fired.add(txn.id)
        elif definition.kind is CaptureKind.ACCUMULATION:
            _ = accumulating.setdefault(txn.relationship_id, definition)
    start, end = _instants(period_key)
    for relationship_id, definition in accumulating.items():
        fired |= _crossed_members(
            s, definition, relationship_id, start=start, end=end
        ) & {txn.id for txn, _ in candidates}
    return fired


def _check(
    s: Session,
    definition: CaptureDefinition,
    *,
    period_key: str,
    units: UnitService,
    holidays: Holidays,
) -> dict[UUID, EnrichedTransaction]:
    snapshots = _Snapshots(s)
    target = definition.merge_into
    codes = (definition.code,) if target is None else (definition.code, target)
    found = _period_candidates(s, snapshots, codes, period_key)
    candidates = found[definition.code]
    fired = _firing(
        s, candidates, period_key=period_key, units=units, holidays=holidays
    )
    if target is not None and fired:
        # One report for both: where the target fires, this capture is merged.
        fired -= _firing(
            s, found[target], period_key=period_key, units=units, holidays=holidays
        )
    return {txn.id: txn for txn, _ in candidates if txn.id in fired}


def check_captures(
    s: Session,
    definition: CaptureDefinition,
    *,
    period_key: str,
    units: UnitService,
    holidays: Holidays,
) -> set[UUID]:
    """Re-run a capture over its period's processed transactions, writing nothing.

    Args:
        s: The tenant's session.
        definition: The capture (its code, kind and, for an accumulation, window
            and threshold); each transaction is judged under its own snapshot's
            definition of that code.
        period_key: The period (``2026-12``, ``2026-Q4``).
        units: The unit service of ``s``, for fee conversions.
        holidays: The ``BANXICO`` holidays, for fee conversions.

    Returns:
        The ids of the transactions that should have a live event in the period.

    """
    return set(
        _check(s, definition, period_key=period_key, units=units, holidays=holidays)
    )


def _run_for(
    s: Session,
    snapshot_id: UUID | None,
    *,
    runs: dict[UUID | None, CaptureRun],
    snapshots: _Snapshots,
    units: UnitService,
    holidays: Holidays,
) -> CaptureRun:
    run = runs.get(snapshot_id)
    if run is None:
        run = CaptureRun(s, snapshots.of(snapshot_id), units, holidays)
        runs[snapshot_id] = run
    return run


def _restore(run: CaptureRun, code: str, txn: EnrichedTransaction) -> None:
    """Write a missing event, or capture its row again in place, as ``late``.

    An accumulated operation joins the live crossed group holding it, if there is
    one; anything else is captured on its own.
    """
    definition = run.definitions[code]
    fee = None
    group = None
    if definition.kind is CaptureKind.FEE_THRESHOLD:
        fee = fee_amount(definition, txn, units=run.units, holidays=run.holidays)
    elif definition.kind is CaptureKind.ACCUMULATION and matches(
        definition.accumulates, txn
    ):
        group = group_containing(run.s, definition, txn)
        if group is not None and group.status is not CaptureGroupStatus.CROSSED:
            group = None
    _ = capture_member(run, definition, txn, group, late=True, fee=fee)


def _publish(s: Session, event_type: str, period: CapturePeriod) -> None:
    _ = publish_monitoring_event(
        s,
        event_type,
        entity="capture_period",
        entity_id=period.id,
        key=str(period.id),
        data={
            "capture_period_id": str(period.id),
            "capture_code": period.capture_code,
            "report_code": period.report_code,
            "period_key": period.period_key,
            "status": period.status.value,
            "discrepancy_count": discrepancy_count(period),
        },
    )


def close_period(
    s: Session, period: CapturePeriod, *, units: UnitService
) -> CapturePeriod:
    """Reconcile a period that ended by re-evaluation and close it (spec §10.4).

    Returns:
        The period, ``CLOSED``.

    Raises:
        PeriodNotEndedError: When its last day is today or later (409).

    """
    if period.period_end >= policy_date(clock.now()):
        raise PeriodNotEndedError
    # A late event reopening the period waits for the close, and sees it.
    s.refresh(period, with_for_update=True)
    was_closed = period.status is CapturePeriodStatus.CLOSED
    holidays = load_holidays(s, country="MX", scope=HolidayScope.BANXICO)
    definition = definition_for(s, period)
    expected = (
        {}
        if definition is None
        else _check(
            s,
            definition,
            period_key=period.period_key,
            units=units,
            holidays=holidays,
        )
    )
    stored = {
        event.transaction_id: event
        for event in s.scalars(
            select(ReportableEvent)
            .where(
                ReportableEvent.capture_code == period.capture_code,
                ReportableEvent.period_key == period.period_key,
                ReportableEvent.status.in_(LIVE),
            )
            .with_for_update()
        )
    }
    missing = sorted(expected.keys() - stored.keys(), key=str)
    extra = sorted(stored.keys() - expected.keys(), key=str)
    runs: dict[UUID | None, CaptureRun] = {}
    snapshots = _Snapshots(s)
    for txn_id in missing:
        txn = expected[txn_id]
        run = _run_for(
            s,
            txn.policy_snapshot_id,
            runs=runs,
            snapshots=snapshots,
            units=units,
            holidays=holidays,
        )
        _restore(run, period.capture_code, txn)
    included = False
    for txn_id in extra:
        event = stored[txn_id]
        included = included or event.status is ReportableEventStatus.INCLUDED
        run = _run_for(
            s,
            event.policy_snapshot_id,
            runs=runs,
            snapshots=snapshots,
            units=units,
            holidays=holidays,
        )
        # Published as superseded, and what merged into it comes back (review I1).
        void_event(run, event, RECONCILIATION)
    own = (period.capture_code, period.period_key)
    for run in runs.values():
        # This period is closed below; others touched are reopened.
        run.reopen.discard(own)
        reopen_touched(run)
    s.flush()
    now = clock.now()
    discrepancies: dict[str, JsonValue] | None = None
    if missing or extra:
        discrepancies = {
            "missing": [str(txn_id) for txn_id in missing],
            "extra": [str(txn_id) for txn_id in extra],
        }
    period.discrepancies = discrepancies
    if included or (was_closed and discrepancies is not None):
        # An INCLUDED event is never changed silently (spec §10.5).
        period.status = CapturePeriodStatus.REOPENED
        period.reopened_at = now
        period.change_count += 1
        _publish(s, CAPTURE_PERIOD_CHANGED, period)
    period.event_count = (
        s.scalar(
            select(func.count()).where(
                ReportableEvent.capture_code == period.capture_code,
                ReportableEvent.period_key == period.period_key,
                ReportableEvent.status.in_(LIVE),
            )
        )
        or 0
    )
    period.reconciled_count = len(expected)
    period.reconciled_at = now
    period.closed_at = now
    period.status = CapturePeriodStatus.CLOSED
    s.flush()
    _publish(s, CAPTURE_PERIOD_CLOSED, period)
    return period


def close_due_periods(s: Session, *, units: UnitService) -> list[CapturePeriod]:
    """Close every open or reopened period that ended, after opening the missing.

    Each period closes in its own transaction, committed before the next: its
    ``FOR UPDATE`` lock is held only while it closes, and a period that fails
    (logged) does not keep the others open. Periods close in order of their last
    day, then capture code, so a capture merging into another (``AVI_XVI_B`` into
    ``AVI_XVI_A``) closes after it.

    Returns:
        The periods closed, in that order.

    Raises:
        RuntimeError: When any period failed to close, after the others closed
            (the job runs again and closes what is left).

    """
    today = policy_date(clock.now())
    _ = ensure_periods(s, ended_before=today)
    tenant_id = bound_context(s).require_tenant_id()
    due = s.scalars(
        select(CapturePeriod.id)
        .where(CapturePeriod.status.in_(_OPEN), CapturePeriod.period_end < today)
        .order_by(CapturePeriod.period_end, CapturePeriod.capture_code)
    ).all()
    s.commit()
    closed: list[CapturePeriod] = []
    failed = 0
    for period_id in due:
        try:
            with s.begin_nested():
                period = s.get_one(CapturePeriod, (tenant_id, period_id))
                closed.append(close_period(s, period, units=units))
        except Exception:
            failed += 1
            logger.exception(
                "capture period failed to close",
                extra={"capture_period_id": str(period_id)},
            )
        s.commit()
    if failed:
        msg = f"{failed} of {len(due)} capture periods failed to close"
        raise RuntimeError(msg)
    return closed


def close_period_by_hand(
    s: Session, period: CapturePeriod, *, units: UnitService
) -> CapturePeriod:
    """Close a period on a person's request (API, CLI), recording who asked.

    The audit row (``captures.period_closed`` on the period, ids and counts only)
    names the actor; ``capture_period`` has no ``closed_by`` column.

    Returns:
        The period, ``CLOSED``.

    """
    closed = close_period(s, period, units=units)
    _ = record(
        s,
        AuditEvent(
            action=CLOSED_BY_HAND,
            entity_type="capture_period",
            entity_id=str(closed.id),
            detail={
                "capture_code": closed.capture_code,
                "period_key": closed.period_key,
                "discrepancy_count": discrepancy_count(closed),
                "change_count": closed.change_count,
            },
        ),
    )
    return closed


def close_named_period(
    s: Session, *, capture_code: str, period_key: str, units: UnitService
) -> CapturePeriod:
    """Close one capture's period now, opening its row when it has none.

    Returns:
        The period, ``CLOSED``.

    Raises:
        ValidationFailedError: When ``period_key`` is not a period key.
        NotFoundError: When no row exists and no policy in force on the period's
            last day has the capture.

    """
    try:
        _, last = period_bounds(period_key)
    except ValueError as error:
        msg = "the period must be written YYYY-MM or YYYY-Qn"
        raise ValidationFailedError(msg) from error
    find = select(CapturePeriod).where(
        CapturePeriod.capture_code == capture_code,
        CapturePeriod.period_key == period_key,
    )
    period = s.scalars(find).first()
    if period is None:
        definition = definitions_on(s, last).get(capture_code)
        if definition is None:
            msg = "no capture has this code for the period"
            raise NotFoundError(msg)
        # Inserted, then locked FOR UPDATE by the close itself: two closes of a
        # new row serialise instead of both upgrading a shared lock (M-R1).
        _ = insert_period(
            s,
            capture_code=capture_code,
            report_code=definition.report_code,
            period_key=period_key,
        )
        period = s.scalars(find).one()
    return close_period_by_hand(s, period, units=units)


def handle_period_close(s: Session, job: Job) -> None:
    """Handle ``monitoring.period_close``.

    The schedule's payload ``{}`` closes every period that ended; the CLI's and
    a manual close's ``{"capture_code", "period_key"}`` close that one.

    Raises:
        TypeError: When the payload names one of the two and not the other.

    """
    capture_code = job.payload.get("capture_code")
    period_key = job.payload.get("period_key")
    units = UnitService(s)
    if capture_code is None and period_key is None:
        closed = close_due_periods(s, units=units)
    elif isinstance(capture_code, str) and isinstance(period_key, str):
        closed = [
            close_named_period(
                s, capture_code=capture_code, period_key=period_key, units=units
            )
        ]
    else:
        msg = "capture_code and period_key must be strings, given together"
        raise TypeError(msg)
    logger.info(
        "capture periods closed",
        extra={
            "tenant_id": str(job.tenant_id),
            "count": len(closed),
            "discrepancies": sum(discrepancy_count(period) for period in closed),
        },
    )
