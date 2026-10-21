"""The ``monitoring.process`` job: one party's drain, in order (spec §8.5, D12).

The queue keeps one pending or running job per party (key ``process:<party_id>``),
and the job takes the party's ``RECEIVED`` rows in ``occurred_at, id`` order, a
page at a time, each row in its own database transaction: enrich it, flag it
late, add it to the declared aggregates, run every processing hook, mark it
``PROCESSED`` and publish ``monitoring.transaction_processed.v1``. A row whose
policy or unit value is not there yet is parked and ends the drain, so the rows
behind it wait in order until the sweep sets it ``RECEIVED`` again.

A run whose lease was reaped may still be draining while another run of the same
job starts, and the CLI drains without a lease. Each row's transaction first takes
the party's advisory lock, so two drains of one party never fold rows at once, and
then locks the row and reads its status again: a row is processed once, a row
another run processed is passed over, and a row another run parked stops this one
too. A run that sees its heartbeat refused stops after the page.

The party's latest processed instant, which decides the out-of-order flag, is read
once per page and carried forward from row to row; it is read again only when a
row had to wait for the party's lock, because another drain held it. One gap
remains: another drain that runs entirely between two of this drain's row
transactions, on rows outside this page, is not seen until the next page, so a
row of this page behind one of those may go unflagged.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from uuid import UUID

from sqlalchemy import Select, delete, func, select, text
from sqlalchemy.orm import Session

from xolotl.modules.monitoring.aggregates.declarations import AggregateDeclaration
from xolotl.modules.monitoring.aggregates.postgres import (
    PostgresAggregateStore,
    backfilled_ids,
    ensure_declarations,
    load_declarations,
)
from xolotl.modules.monitoring.enrich.policy import (
    SnapshotCache,
    SnapshotRef,
    policy_date,
    snapshot_for,
)
from xolotl.modules.monitoring.enrich.service import (
    ParkTransactionError,
    apply_enrichment,
    enrich,
)
from xolotl.modules.monitoring.enums import ParkReason, TransactionStatus
from xolotl.modules.monitoring.events import (
    TRANSACTION_PARKED,
    TRANSACTION_PROCESSED,
    publish_monitoring_event,
)
from xolotl.modules.monitoring.process.hooks import (
    ProcessingContext,
    processing_hooks,
)
from xolotl.modules.monitoring.txn.models import Transaction, TransactionParked
from xolotl.modules.monitoring.txn.schema import EnrichedTransaction
from xolotl.modules.party.service import relationship_ref
from xolotl.platform import clock
from xolotl.platform.calendar import Holidays, load_holidays
from xolotl.platform.config import Settings
from xolotl.platform.db import SessionFactory, tenant_session
from xolotl.platform.enums import HolidayScope
from xolotl.platform.jobs.queue import Job, PostgresJobQueue
from xolotl.platform.policy import PolicyNotAssignedError
from xolotl.platform.tenancy.context import TenantContext
from xolotl.platform.units import UnitService

# A row received more than a day after it occurred is late (spec §8.5 step 2).
LATE_RECEIPT = timedelta(days=1)
_LAST_ERROR_MAX = 200
_PARTY_TRY_LOCK = text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 0))")
_PARTY_LOCK = text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))")

# The declarations each hook reported, by the snapshot id they were asked for.
type HookDeclarations = dict[UUID, tuple[AggregateDeclaration, ...]]


@dataclass(frozen=True, slots=True)
class ProcessReport:
    """What one drain did; ``stopped_at`` is the ``PARKED`` row that ended it."""

    processed: int
    parked: int
    stopped_at: UUID | None


def latest_processed_at(session: Session, party_id: UUID) -> datetime | None:
    """Read the latest ``occurred_at`` among the party's processed rows.

    Returns:
        The instant, or None when nothing of the party was processed yet.

    """
    return session.scalar(
        select(func.max(Transaction.occurred_at)).where(
            Transaction.party_id == party_id,
            Transaction.status == TransactionStatus.PROCESSED,
        )
    )


def lock_party(session: Session, tenant_id: UUID, party_id: UUID) -> bool:
    """Take the party's processing lock until the caller's transaction ends.

    Every row transaction of a drain takes it first, so two drains of one party
    (a reaped run and its successor, or the CLI beside the worker) take turns. It
    is tried first and waited for only when another transaction holds it.

    Returns:
        True when another transaction held the lock and this one waited for it.

    """
    params = {"key": f"monitoring.process:{tenant_id}:{party_id}"}
    if session.execute(_PARTY_TRY_LOCK, params).scalar_one():
        return False
    _ = session.execute(_PARTY_LOCK, params)
    return True


def _pending[*Ts](stmt: Select[*Ts], party_id: UUID, *, limit: int) -> Select[*Ts]:
    return (
        stmt
        .where(
            Transaction.party_id == party_id,
            Transaction.status.in_((
                TransactionStatus.RECEIVED,
                TransactionStatus.PARKED,
            )),
        )
        .order_by(Transaction.occurred_at, Transaction.id)
        .limit(limit)
    )


def pending_rows(session: Session, party_id: UUID, *, limit: int) -> list[Transaction]:
    """Read the party's next ``RECEIVED`` and ``PARKED`` rows, in order.

    Returns:
        Up to ``limit`` rows ordered by ``occurred_at`` then ``id``.

    """
    return list(session.scalars(_pending(select(Transaction), party_id, limit=limit)))


def _is_late(row: Transaction, latest: datetime | None) -> bool:
    out_of_order = latest is not None and row.occurred_at < latest
    return out_of_order or row.received_at - row.occurred_at > LATE_RECEIPT


@dataclass(slots=True)
class DrainState:
    """What one drain carries from row to row.

    ``declarations``, ``complete`` and ``latest`` are read again for each page,
    ``latest`` also after a wait for the party's lock; the snapshots and the
    hooks' declarations are kept for the whole drain.
    """

    queue: PostgresJobQueue
    holidays: Holidays
    cache: SnapshotCache = field(default_factory=dict)
    hooked: HookDeclarations = field(default_factory=dict)
    declarations: list[AggregateDeclaration] = field(default_factory=list)
    complete: frozenset[str] = frozenset()
    latest: datetime | None = None
    processed: int = 0
    parked: int = 0


def _row_declarations(
    session: Session,
    snapshot: SnapshotRef,
    drain: DrainState,
    *,
    on: date,
    product: str | None,
) -> list[AggregateDeclaration]:
    """Join the loaded declarations and the hooks' for ``snapshot``, once each.

    The first time a drain meets a snapshot, every hook names its declarations
    and ``ensure_declarations`` records the ones ``agg_declaration`` lacks.

    Returns:
        The declarations the row is added to, without two of one ``agg_id``.

    """
    reported = drain.hooked.get(snapshot.snapshot_id)
    if reported is None:
        reported = tuple(
            declaration
            for hook in processing_hooks()
            for declaration in hook.declarations(
                session, snapshot, on=on, product=product
            )
        )
        _ = ensure_declarations(session, reported, queue=drain.queue)
        drain.hooked[snapshot.snapshot_id] = reported
    joined: dict[str, AggregateDeclaration] = {}
    for declaration in (*drain.declarations, *reported):
        _ = joined.setdefault(declaration.agg_id(), declaration)
    return list(joined.values())


def process_row(
    session: Session,
    row: Transaction,
    *,
    units: UnitService,
    store: PostgresAggregateStore,
    drain: DrainState,
) -> EnrichedTransaction:
    """Process one row in the caller's transaction and mark it ``PROCESSED``.

    Args:
        session: The row's transaction.
        row: The transaction, ``RECEIVED`` and locked by the caller.
        units: The unit service of ``session``.
        store: The aggregate store of ``session``.
        drain: The drain's holidays, snapshots, declarations, the party's latest
            processed ``occurred_at`` and the job queue.

    Returns:
        The enriched transaction as the hooks saw it.

    Raises:
        ParkTransactionError: When the date has no policy (``POLICY_NOT_ASSIGNED``)
            or a unit value is not there yet; the caller parks the row.

    """
    relationship = relationship_ref(session, row.relationship_id)
    on = policy_date(row.occurred_at)
    try:
        snapshot = snapshot_for(
            session, on=on, product=relationship.product, cache=drain.cache
        )
    except PolicyNotAssignedError as error:
        raise ParkTransactionError(ParkReason.POLICY_NOT_ASSIGNED) from error
    row.late = _is_late(row, drain.latest)
    txn = enrich(
        session,
        row,
        units=units,
        holidays=drain.holidays,
        relationship=relationship,
        snapshot=snapshot,
    )
    apply_enrichment(row, txn)
    declarations = _row_declarations(
        session, snapshot, drain, on=on, product=relationship.product
    )
    store.add(txn, declarations)
    context = ProcessingContext(
        txn=txn,
        row=row,
        store=store,
        declarations=declarations,
        snapshot=snapshot,
        units=units,
        holidays=drain.holidays,
        late=row.late,
        queue=drain.queue,
    )
    for hook in processing_hooks():
        hook.evaluate(session, txn, context)
    row.status = TransactionStatus.PROCESSED
    row.processed_at = clock.now()
    _ = publish_monitoring_event(
        session,
        TRANSACTION_PROCESSED,
        entity="transaction",
        entity_id=row.id,
        key=str(row.party_id),
        data={
            "transaction_id": str(row.id),
            "external_id": row.external_id,
            "status": TransactionStatus.PROCESSED.value,
        },
        policy_snapshot_id=snapshot.snapshot_id,
    )
    return txn


def park_row(
    session: Session,
    row: Transaction,
    *,
    reason: ParkReason,
    unit_code: str | None,
    needed_on: date | None,
    error: str,
) -> None:
    """Park a row in the caller's transaction and publish why.

    The row's previous ``transaction_parked`` entry, if any, is replaced by one
    with ``attempts`` one higher and ``parked_at`` now. ``error`` must hold no
    personal data; it is kept to 200 characters.
    """
    previous = session.scalar(
        select(TransactionParked.attempts).where(
            TransactionParked.transaction_id == row.id
        )
    )
    _ = session.execute(
        delete(TransactionParked).where(TransactionParked.transaction_id == row.id)
    )
    session.add(
        TransactionParked(
            tenant_id=row.tenant_id,
            transaction_id=row.id,
            occurred_at=row.occurred_at,
            reason=reason,
            unit_code=unit_code,
            needed_on=needed_on,
            attempts=(previous or 0) + 1,
            parked_at=clock.now(),
            last_error=error[:_LAST_ERROR_MAX],
        )
    )
    row.status = TransactionStatus.PARKED
    _ = publish_monitoring_event(
        session,
        TRANSACTION_PARKED,
        entity="transaction",
        entity_id=row.id,
        key=str(row.party_id),
        data={
            "transaction_id": str(row.id),
            "reason": reason.value,
            "unit_code": unit_code,
            "needed_on": None if needed_on is None else needed_on.isoformat(),
        },
    )


def _park_error(error: ParkTransactionError) -> str:
    if error.reason is ParkReason.POLICY_NOT_ASSIGNED:
        return "no policy pack in force on the transaction's date"
    needed = "?" if error.needed_on is None else error.needed_on.isoformat()
    return f"no {error.unit_code} value for {needed}"


def _context(tenant_id: UUID, job: Job | None, actor: str | None) -> TenantContext:
    if actor is None:
        actor = "job:monitoring.process" if job is None else f"job:{job.id}"
    return TenantContext.system(tenant_id, actor)


def _page(
    ctx: TenantContext,
    factory: SessionFactory,
    drain: DrainState | None,
    party_id: UUID,
    *,
    queue: PostgresJobQueue,
    limit: int,
) -> tuple[DrainState, list[tuple[datetime, UUID]]]:
    """Read the next page and refresh what may change between pages.

    Returns:
        The drain state and the page's primary keys past the tenant.

    """
    with tenant_session(ctx, factory=factory) as s:
        if drain is None:
            drain = DrainState(
                queue=queue,
                holidays=load_holidays(s, country="MX", scope=HolidayScope.BANXICO),
            )
        # A declaration activated or backfilled mid-drain applies from here on.
        drain.declarations = load_declarations(s)
        drain.complete = backfilled_ids(s)
        drain.latest = latest_processed_at(s, party_id)
        # Only the keys: the row itself is read again, locked, in its transaction.
        keys = [
            (occurred_at, row_id)
            for occurred_at, row_id in s.execute(
                _pending(
                    select(Transaction.occurred_at, Transaction.id),
                    party_id,
                    limit=limit,
                )
            )
        ]
    return drain, keys


def _later(latest: datetime | None, at: datetime) -> datetime:
    return at if latest is None or at > latest else latest


def _drain_row(
    s: Session,
    key: tuple[datetime, UUID],
    drain: DrainState,
    *,
    tenant_id: UUID,
    party_id: UUID,
) -> UUID | None:
    """Process, pass over or park one row in its own transaction.

    The party's lock comes first, then the row's: another drain of the party
    finishes its row before this one reads the row's status. When the party's
    lock had to be waited for, the party's latest processed instant is read
    again, since the other drain may have processed rows past it.

    Returns:
        The row's id when the drain stops at it (it is ``PARKED``), else None.

    """
    occurred_at, row_id = key
    if lock_party(s, tenant_id, party_id):
        drain.latest = latest_processed_at(s, party_id)
    row = s.get(
        Transaction,
        (tenant_id, occurred_at, row_id),
        with_for_update=True,
        populate_existing=True,
    )
    if row is None or row.status in {
        TransactionStatus.PROCESSED,
        TransactionStatus.BACKFILL,
    }:
        # Another run of this job processed it while this one waited.
        if row is not None and row.status is TransactionStatus.PROCESSED:
            drain.latest = _later(drain.latest, row.occurred_at)
        return None
    if row.status is TransactionStatus.PARKED:
        return row.id
    store = PostgresAggregateStore(s, complete_ids=drain.complete, queue=drain.queue)
    try:
        # A savepoint, so a park raised after ``add`` (a hook's conversion) takes
        # the row's aggregates back with it.
        with s.begin_nested():
            _ = process_row(s, row, units=UnitService(s), store=store, drain=drain)
    except ParkTransactionError as error:
        park_row(
            s,
            row,
            reason=error.reason,
            unit_code=error.unit_code,
            needed_on=error.needed_on,
            error=_park_error(error),
        )
        drain.parked += 1
        return row.id
    drain.processed += 1
    drain.latest = _later(drain.latest, row.occurred_at)
    drain.complete = store.complete_ids or frozenset()
    return None


def process_party(
    session_factory: SessionFactory,
    tenant_id: UUID,
    party_id: UUID,
    *,
    queue: PostgresJobQueue,
    job: Job | None,
    settings: Settings,
    actor: str | None = None,
) -> ProcessReport:
    """Drain one party's pending rows in ``occurred_at`` order.

    Each row is its own transaction under a system context: ``job:<id>`` when
    ``job`` is given, else ``actor`` (the CLI passes ``cli:<user>``). After each
    page the job's lease is extended; a refused heartbeat means another run owns
    the job now, and this one stops. When the job was reaped to ``DEAD`` rather
    than handed on, no run owns it and the rows left ``RECEIVED`` wait for A9's
    sweep, which enqueues the party again.

    Returns:
        How many rows were processed and parked, and the row the drain stopped at.

    """
    ctx = _context(tenant_id, job, actor)
    lease = timedelta(seconds=settings.monitoring_process_lease_seconds)
    drain: DrainState | None = None
    while True:
        drain, keys = _page(
            ctx,
            session_factory,
            drain,
            party_id,
            queue=queue,
            limit=settings.monitoring_process_page,
        )
        for key in keys:
            # One transaction per row (TS-05 §5): a failure leaves the rows
            # before it processed and this one RECEIVED.
            with tenant_session(ctx, factory=session_factory) as s:
                stopped = _drain_row(
                    s, key, drain, tenant_id=tenant_id, party_id=party_id
                )
            if stopped is not None:
                return ProcessReport(drain.processed, drain.parked, stopped)
        if not keys:
            return ProcessReport(drain.processed, drain.parked, None)
        if job is not None and not queue.heartbeat(job, lease=lease):
            return ProcessReport(drain.processed, drain.parked, None)


class ProcessJob:
    """The handler of ``monitoring.process``: payload ``{"party_id"}``."""

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        queue: PostgresJobQueue,
        settings: Settings,
    ) -> None:
        self._session_factory: SessionFactory = session_factory
        self._queue: PostgresJobQueue = queue
        self._settings: Settings = settings

    def run(self, _session: Session, job: Job) -> None:
        """Drain the job's party; each row commits in a session of its own."""
        _ = process_party(
            self._session_factory,
            job.tenant_id,
            UUID(str(job.payload["party_id"])),
            queue=self._queue,
            job=job,
            settings=self._settings,
        )
