"""The ``monitoring.sweep`` job: the processing fallback of every minute (spec §8.5).

Per tenant, the sweep

1. enqueues ``monitoring.process`` for every party whose next pending row is
   ``RECEIVED`` and that has a ``RECEIVED`` row received over ``RECEIVED_AGE`` ago:
   the batch that arrived after a running job's last read and saw its enqueue
   deduplicated away, and the party whose job went ``DEAD`` mid-drain;
2. sets ``RECEIVED`` again every ``UNIT_VALUE_MISSING`` row whose unit value for
   ``needed_on`` now exists, and enqueues its party;
3. does the same for the ``POLICY_NOT_ASSIGNED`` rows parked over ``POLICY_RETRY``
   ago, or parked before a pack assignment was created (plan 4a Deviation 10);
4. notifies ADMIN and MLRO, once a Mexico City day, while a row parked over
   ``PARKED_ALERT_AGE`` ago waits (Deviation 11);
5. enqueues the backfill of every declaration still incomplete with no live
   ``monitoring.agg_backfill`` job (a backfill that went ``DEAD``, or a late split
   whose enqueue a running backfill deduplicated), unless one went ``DEAD`` within
   ``DEAD_BACKFILL_BACKOFF``, and logs every transactions file left
   ``PROCESSING`` with no live ``monitoring.ingest_file`` job;
6. fails every rule simulation whose run was lost (its one-attempt job is no
   longer live), so the staff view stops showing it running (plan 4b B8).

An un-parked row keeps its ``transaction_parked`` entry: parking it again counts the
attempt (Deviation 10), so every reader of parked rows joins ``status = PARKED``.
Everything runs in the job's one transaction; rows are locked in primary-key order.
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import (
    ColumnElement,
    Text,
    Uuid,
    and_,
    exists,
    func,
    literal,
    or_,
    select,
    tuple_,
    type_coerce,
    update,
)
from sqlalchemy import (
    cast as sa_cast,
)
from sqlalchemy.orm import Session

from xolotl.modules.monitoring.aggregates.models import AggDeclaration
from xolotl.modules.monitoring.enums import FileStatus, ParkReason, TransactionStatus
from xolotl.modules.monitoring.kinds import AGG_BACKFILL_KIND, INGEST_FILE_KIND
from xolotl.modules.monitoring.process.enqueue import enqueue_processing
from xolotl.modules.monitoring.simulation.job import fail_lost_simulations
from xolotl.modules.monitoring.txn.models import (
    Transaction,
    TransactionFile,
    TransactionParked,
)
from xolotl.platform import clock
from xolotl.platform.calendar import MEXICO_CITY
from xolotl.platform.enums import JobStatus, Role
from xolotl.platform.events.models import Notification
from xolotl.platform.events.notifier import Notifier
from xolotl.platform.jobs.models import JobRow
from xolotl.platform.jobs.queue import ClockFn, Job, PostgresJobQueue
from xolotl.platform.policy.models import TenantPackAssignment
from xolotl.platform.units.store import current_row

logger = logging.getLogger(__name__)

PARKED_TEMPLATE = "monitoring.parked"
RECEIVED_AGE = timedelta(seconds=30)
PARKED_ALERT_AGE = timedelta(hours=6)
POLICY_RETRY = timedelta(hours=1)
# A policy row is parked again at each hourly retry, so its seventh park means it
# has waited over ``PARKED_ALERT_AGE``.
POLICY_ALERT_ATTEMPTS = 7
# A backfill that went DEAD is enqueued again at most this often.
DEAD_BACKFILL_BACKOFF = timedelta(hours=1)
# From this many deaths a re-enqueued backfill is logged, once a day.
DEAD_BACKFILL_WARN = 2
_NOTIFIED = (Role.ADMIN, Role.MLRO)
_PENDING = (TransactionStatus.RECEIVED, TransactionStatus.PARKED)
_LIVE = (JobStatus.PENDING, JobStatus.RUNNING)


@dataclass(frozen=True, slots=True)
class SweepReport:
    """What one sweep did.

    ``enqueued`` counts the processing jobs created for parties with old
    ``RECEIVED`` rows; ``unparked`` and ``policy_retried`` count the rows set
    ``RECEIVED`` again; ``backfills`` the aggregate backfills enqueued again and
    ``stranded_files`` the files left ``PROCESSING`` with no live job.
    """

    enqueued: int
    unparked: int
    policy_retried: int
    notified: bool
    backfills: int = 0
    stranded_files: int = 0
    lost_simulations: int = 0


def _parked_join() -> ColumnElement[bool]:
    return and_(
        TransactionParked.tenant_id == Transaction.tenant_id,
        TransactionParked.occurred_at == Transaction.occurred_at,
        TransactionParked.transaction_id == Transaction.id,
    )


def _parties_to_wake(session: Session, *, cutoff: datetime) -> list[UUID]:
    """Find the parties a drain would move that have waited past ``cutoff``.

    A loose index scan over ``ix_transaction_pending`` visits each pending party
    once, so a long backlog of one party costs one probe, not one row each. A
    party whose next pending row is ``PARKED`` cannot move and is left out; the
    un-parking enqueues it.

    Returns:
        The party ids, in order.

    """
    pending = Transaction.status.in_(_PENDING)
    anchor = select(
        select(Transaction.party_id)
        .where(pending)
        .order_by(Transaction.party_id)
        .limit(1)
        .scalar_subquery()
        .label("party_id")
    ).cte("parties", recursive=True)
    following = (
        select(Transaction.party_id)
        .where(pending, Transaction.party_id > anchor.c.party_id)
        .order_by(Transaction.party_id)
        .limit(1)
        .scalar_subquery()
    )
    parties = anchor.union_all(select(following).where(anchor.c.party_id.is_not(None)))
    party_id = type_coerce(parties.c.party_id, Uuid())
    # In the drain's order, so the head is the row a drain would take first.
    head = (
        select(Transaction.status)
        .where(Transaction.party_id == party_id, pending)
        .order_by(Transaction.occurred_at, Transaction.id)
        .limit(1)
        .scalar_subquery()
    )
    # Scalar subqueries, so each stays a per-party probe of the pending index; the
    # probe stops at the party's first row received before the cutoff.
    waited = (
        select(Transaction.id)
        .where(
            Transaction.party_id == party_id,
            Transaction.status == TransactionStatus.RECEIVED,
            Transaction.received_at < cutoff,
        )
        .limit(1)
        .scalar_subquery()
    )
    return list(
        session.scalars(
            select(party_id)
            .where(
                party_id.is_not(None),
                head == TransactionStatus.RECEIVED,
                waited.is_not(None),
            )
            .order_by(party_id)
        )
    )


def _unpark(session: Session, *conditions: ColumnElement[bool]) -> list[UUID]:
    """Set ``RECEIVED`` again the ``PARKED`` rows that match ``conditions``.

    Returns:
        The party of each row set ``RECEIVED``, one entry per row.

    """
    keys = session.execute(
        select(Transaction.occurred_at, Transaction.id, Transaction.party_id)
        .join(TransactionParked, _parked_join())
        .where(Transaction.status == TransactionStatus.PARKED, *conditions)
        .order_by(Transaction.occurred_at, Transaction.id)
        .with_for_update(of=Transaction)
    ).all()
    if not keys:
        return []
    _ = session.execute(
        update(Transaction)
        .where(
            tuple_(Transaction.occurred_at, Transaction.id).in_([
                (occurred_at, row_id) for occurred_at, row_id, _ in keys
            ]),
            Transaction.status == TransactionStatus.PARKED,
        )
        .values(status=TransactionStatus.RECEIVED)
        .execution_options(synchronize_session=False)
    )
    return [party_id for _, _, party_id in keys]


def _unit_values_arrived(session: Session) -> list[UUID]:
    """Un-park the rows whose missing unit value has been loaded since.

    Returns:
        The party of each row set ``RECEIVED``.

    """
    needed = session.execute(
        select(TransactionParked.unit_code, TransactionParked.needed_on)
        .join(Transaction, _parked_join())
        .where(
            Transaction.status == TransactionStatus.PARKED,
            TransactionParked.reason == ParkReason.UNIT_VALUE_MISSING,
        )
        .distinct()
    ).all()
    ready: list[tuple[str, date]] = [
        (unit_code, needed_on)
        for unit_code, needed_on in needed
        if unit_code is not None
        and needed_on is not None
        and current_row(session, unit_code, needed_on) is not None
    ]
    if not ready:
        return []
    return _unpark(
        session,
        TransactionParked.reason == ParkReason.UNIT_VALUE_MISSING,
        tuple_(TransactionParked.unit_code, TransactionParked.needed_on).in_(ready),
    )


def _policies_to_retry(session: Session, *, now: datetime) -> list[UUID]:
    """Un-park the policy rows due an hourly retry or met by a newer assignment.

    Returns:
        The party of each row set ``RECEIVED``.

    """
    assigned_since = exists().where(
        TenantPackAssignment.created_at > TransactionParked.parked_at
    )
    return _unpark(
        session,
        TransactionParked.reason == ParkReason.POLICY_NOT_ASSIGNED,
        or_(TransactionParked.parked_at < now - POLICY_RETRY, assigned_since),
    )


def _notify_parked(session: Session, notifier: Notifier, *, now: datetime) -> bool:
    """Tell ADMIN and MLRO, once a Mexico City day, that rows have waited too long.

    A ``PARKED`` row is overdue once it was parked over ``PARKED_ALERT_AGE`` ago,
    or, for ``POLICY_NOT_ASSIGNED``, once it has been parked
    ``POLICY_ALERT_ATTEMPTS`` times: a policy retry parks a row again every hour
    with a new ``parked_at``, so its ``parked_at`` never ages past six hours. The
    notice's ``oldest_parked_at`` is the earliest ``parked_at`` among the overdue
    rows (for a policy row, its latest park).

    Returns:
        Whether the notices were written.

    """
    # The earliest ``parked_at`` among the overdue rows; None when there are none.
    oldest = session.scalar(
        select(func.min(TransactionParked.parked_at))
        .select_from(Transaction)
        .join(TransactionParked, _parked_join())
        .where(
            Transaction.status == TransactionStatus.PARKED,
            or_(
                TransactionParked.parked_at < now - PARKED_ALERT_AGE,
                and_(
                    TransactionParked.reason == ParkReason.POLICY_NOT_ASSIGNED,
                    TransactionParked.attempts >= POLICY_ALERT_ATTEMPTS,
                ),
            ),
        )
    )
    if oldest is None:
        return False
    midnight = _mexico_city_midnight(now)
    told = session.scalar(
        select(Notification.id)
        .where(
            Notification.template == PARKED_TEMPLATE,
            Notification.created_at >= midnight,
        )
        .limit(1)
    )
    if told is not None:
        return False
    count = session.scalar(
        select(func.count())
        .select_from(Transaction)
        .where(Transaction.status == TransactionStatus.PARKED)
    )
    for role in _NOTIFIED:
        _ = notifier.notify(
            session,
            template=PARKED_TEMPLATE,
            data={
                "parked_count": count,
                "oldest_parked_at": oldest.isoformat(),
            },
            recipient_role=role,
        )
    return True


def _no_live_job(kind: str, key: ColumnElement[str]) -> ColumnElement[bool]:
    return ~exists().where(
        JobRow.kind == kind, JobRow.key == key, JobRow.status.in_(_LIVE)
    )


def _mexico_city_midnight(now: datetime) -> datetime:
    zone = ZoneInfo(MEXICO_CITY)
    return datetime.combine(now.astimezone(zone).date(), time(0), tzinfo=zone)


def _requeue_backfills(
    session: Session, queue: PostgresJobQueue, *, now: datetime
) -> int:
    """Enqueue the backfill of each incomplete declaration no live job walks.

    A job counts for a declaration by its payload's ``agg_id``, so a deferred
    run's follow-up (keyed apart) counts too. A declaration whose backfill went
    ``DEAD`` within ``DEAD_BACKFILL_BACKOFF`` waits, so a backfill that fails every
    time is retried hourly, not every minute; once it has died twice, the first
    enqueue of each Mexico City day logs a warning with its ``agg_id``.

    Returns:
        How many backfills were enqueued.

    """
    payload_agg_id = type_coerce(
        func.jsonb_extract_path_text(JobRow.payload, "agg_id"), Text()
    )
    for_declaration = and_(
        JobRow.kind == AGG_BACKFILL_KIND, payload_agg_id == AggDeclaration.agg_id
    )
    stranded = session.scalars(
        select(AggDeclaration.agg_id)
        .where(
            AggDeclaration.backfilled_through.is_(None),
            ~exists().where(for_declaration, JobRow.status.in_(_LIVE)),
        )
        .order_by(AggDeclaration.agg_id)
    ).all()
    midnight = _mexico_city_midnight(now)
    created = 0
    for agg_id in stranded:
        dead = JobRow.status == JobStatus.DEAD
        deaths, last_death, today = session.execute(
            select(
                func.count().filter(dead),
                func.max(JobRow.finished_at).filter(dead),
                func.count().filter(JobRow.created_at >= midnight),
            ).where(JobRow.kind == AGG_BACKFILL_KIND, payload_agg_id == agg_id)
        ).one()
        if last_death is not None and last_death > now - DEAD_BACKFILL_BACKOFF:
            continue
        if deaths >= DEAD_BACKFILL_WARN and today == 0:
            logger.warning(
                "aggregate backfill keeps dying; enqueued again",
                extra={"agg_id": agg_id, "deaths": deaths},
            )
        job_id = queue.enqueue(
            session,
            kind=AGG_BACKFILL_KIND,
            payload={"agg_id": agg_id},
            key=f"agg_backfill:{agg_id}",
        )
        created += job_id is not None
    return created


def _stranded_files(session: Session) -> list[UUID]:
    return list(
        session.scalars(
            select(TransactionFile.id)
            .where(
                TransactionFile.status == FileStatus.PROCESSING,
                _no_live_job(
                    INGEST_FILE_KIND,
                    literal("ingest_file:") + sa_cast(TransactionFile.id, Text),
                ),
            )
            .order_by(TransactionFile.id)
        )
    )


def sweep(
    session: Session, *, queue: PostgresJobQueue, notifier: Notifier, now: datetime
) -> SweepReport:
    """Run the sweep once for the session's tenant, in the caller's transaction.

    Returns:
        What it enqueued, un-parked and notified.

    """
    waiting = _parties_to_wake(session, cutoff=now - RECEIVED_AGE)
    unparked = _unit_values_arrived(session)
    retried = _policies_to_retry(session, now=now)
    enqueued = enqueue_processing(session, queue, [*waiting, *unparked, *retried])
    notified = _notify_parked(session, notifier, now=now)
    backfills = _requeue_backfills(session, queue, now=now)
    files = _stranded_files(session)
    lost = fail_lost_simulations(session, now=now)
    if files:
        logger.warning(
            "transactions files left processing with no live ingest job",
            extra={"file_ids": [str(file_id) for file_id in files]},
        )
    return SweepReport(
        enqueued=enqueued,
        unparked=len(unparked),
        policy_retried=len(retried),
        notified=notified,
        backfills=backfills,
        stranded_files=len(files),
        lost_simulations=lost,
    )


class SweepJob:
    """The handler of ``monitoring.sweep``, scheduled every minute per tenant."""

    def __init__(
        self,
        *,
        queue: PostgresJobQueue,
        notifier: Notifier,
        clock_fn: ClockFn = clock.now,
    ) -> None:
        self._queue: PostgresJobQueue = queue
        self._notifier: Notifier = notifier
        self._clock: ClockFn = clock_fn

    def run(self, session: Session, _job: Job) -> None:
        """Sweep the job's tenant in the job's session."""
        report = sweep(
            session, queue=self._queue, notifier=self._notifier, now=self._clock()
        )
        logger.info(
            "monitoring sweep ran",
            extra={
                "enqueued": report.enqueued,
                "unparked": report.unparked,
                "policy_retried": report.policy_retried,
                "notified": report.notified,
                "backfills": report.backfills,
                "stranded_files": report.stranded_files,
                "lost_simulations": report.lost_simulations,
            },
        )
