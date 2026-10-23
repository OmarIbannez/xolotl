"""Postgres job queue: fenced leases, retries, a reaper and cron schedules (spec §9)."""

import operator
import re
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID
from zoneinfo import ZoneInfo

from croniter import CroniterError, croniter
from pydantic import JsonValue
from sqlalchemy import (
    ColumnElement,
    and_,
    case,
    func,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from xolotl.platform import clock
from xolotl.platform.db import SessionFactory, bound_context, tenant_session
from xolotl.platform.enums import JobStatus
from xolotl.platform.errors import ValidationFailedError
from xolotl.platform.ids import uuid7
from xolotl.platform.jobs.models import JobRow, JobSchedule
from xolotl.platform.tenancy.context import TenantContext

DEFAULT_MAX_ATTEMPTS = 8
QUEUE_ACTOR = "job-runner"
CRON_FIELDS = 5
RETRY_CAP = timedelta(minutes=60)
LEASE_EXPIRED = "lease expired: the worker stopped heartbeating"
# 2 ** 6 minutes already exceeds the cap; the bound keeps huge counts from overflowing.
_MAX_BACKOFF_EXPONENT = 6

ClockFn = Callable[[], datetime]

# Spelled exactly like the ``job_dedup`` index predicate so Postgres infers it.
_DEDUP_PREDICATE = text("key IS NOT NULL AND status IN ('PENDING','RUNNING')")
# One field of croniter's random (R) or hashed (H) syntax: R, R/15, R(0-29), R(0-29)/10.
_RANDOM_FIELD = re.compile(r"^[rh](\(\d+-\d+\))?(/\d+)?$", re.IGNORECASE)


def _require_aware(value: datetime | None, name: str) -> None:
    # A naive datetime would be read in the database session's zone, not as UTC.
    if value is not None and value.utcoffset() is None:
        msg = f"{name} must be timezone-aware"
        raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class Job:
    """A claimed job, as its handler sees it.

    ``attempts`` counts the run in flight, so it is 1 on the first run.
    ``lock_token`` identifies this claim: only the holder of the current token can
    extend, complete or fail the job.
    """

    id: UUID
    tenant_id: UUID
    kind: str
    key: str | None
    payload: dict[str, JsonValue]
    attempts: int
    max_attempts: int
    lock_token: UUID


JobHandler = Callable[[Session, Job], None]
_HANDLERS: dict[str, JobHandler] = {}


def register_job_handler(kind: str, handler: JobHandler) -> None:
    """Route jobs of ``kind`` to ``handler``.

    Registering a kind again replaces its handler: composition runs once per app
    instance, and a test builds many.
    """
    _HANDLERS[kind] = handler


def job_handler(kind: str) -> JobHandler:
    """Look up the handler registered for ``kind``.

    Returns:
        The handler.

    """
    return _HANDLERS[kind]  # KeyError carries the kind name


def retry_delay(attempts: int) -> timedelta:
    """How long a job that has failed ``attempts`` times waits before its next run.

    Returns:
        ``2 ** attempts`` minutes, at most an hour.

    """
    doubled = 1 << min(attempts, _MAX_BACKOFF_EXPONENT)  # 2 ** attempts, typed int
    return min(timedelta(minutes=doubled), RETRY_CAP)


def next_slot(cron: str, tz: str, *, after: datetime) -> datetime:
    """Return the first instant after ``after`` at which ``cron`` matches in ``tz``.

    ``cron`` is a five-field expression or an alias such as ``@hourly``. A seconds
    or a year field is refused: the scheduler ticks about once a second, so a
    seconds field would flood the queue. Random (``R``) and hashed (``H``) fields are
    refused too: croniter draws ``R`` anew on every call, so the cadence would change
    each time the schedule advances. A naive ``after`` raises ``ValueError``.

    The expression follows the zone's clock changes. When daylight saving time
    ends, an expression that matches the repeated hour matches both occurrences
    of it; when it starts, one that matches the skipped hour matches when the
    gap ends. Zones without daylight saving time, Mexico's among them, have
    neither effect.

    Returns:
        The slot as an aware UTC datetime.

    Raises:
        ValidationFailedError: When ``tz`` is not a known zone, or ``cron`` is not a
            five-field expression, uses a random or hashed field, or no date ever
            matches it (31 February).

    """
    _require_aware(after, "after")
    try:
        zone = ZoneInfo(tz)
    except (KeyError, ValueError, OSError) as error:
        # Not found, malformed, or a directory rather than a zone file.
        msg = f"unknown time zone {tz!r}"
        raise ValidationFailedError(msg) from error
    if any(_RANDOM_FIELD.match(field) for field in cron.split()):
        msg = (
            f"cron expression {cron!r} must mean the same every time: "
            "random (R) and hashed (H) fields are not supported"
        )
        raise ValidationFailedError(msg)
    try:
        cursor = croniter(cron, after.astimezone(zone), ret_type=datetime)
        fields = len(cursor.expanded)
        slot = cursor.get_next(datetime)
    except CroniterError as error:
        msg = f"invalid cron expression {cron!r}"
        raise ValidationFailedError(msg) from error
    if fields != CRON_FIELDS:
        msg = f"cron expression {cron!r} must have {CRON_FIELDS} fields"
        raise ValidationFailedError(msg)
    return slot.astimezone(UTC)


def _holds_lease(job: Job) -> tuple[ColumnElement[bool], ...]:
    # Matches the row only while ``job``'s claim is still the current one.
    return (
        JobRow.tenant_id == job.tenant_id,
        JobRow.id == job.id,
        JobRow.lock_token == job.lock_token,
        JobRow.status == JobStatus.RUNNING,
    )


class PostgresJobQueue:
    """The platform's job queue, kept in the ``job`` and ``job_schedule`` tables.

    ``enqueue`` and ``schedule`` run in the caller's transaction. Every other method
    opens its own session scoped to the tenant it acts on, so row-level security
    applies to the queue like to any other code.

    Every time the queue reads comes from ``clock_fn`` (the worker's wall clock by
    default), not from the database's ``now()``: leases, due dates and backoff then
    agree with each other and a test can move them. Workers on different hosts need
    clocks that agree to within a small fraction of the lease.
    """

    def __init__(
        self, session_factory: SessionFactory, *, clock_fn: ClockFn = clock.now
    ) -> None:
        self._session_factory: SessionFactory = session_factory
        self._clock: ClockFn = clock_fn

    def _session(self, tenant_id: UUID) -> AbstractContextManager[Session]:
        return tenant_session(
            TenantContext.system(tenant_id, QUEUE_ACTOR),
            factory=self._session_factory,
        )

    def enqueue(
        self,
        session: Session,
        *,
        kind: str,
        payload: dict[str, JsonValue],
        run_after: datetime | None = None,
        key: str | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> UUID | None:
        """Insert a job in the caller's transaction, so it exists only if that commits.

        A ``key`` makes the job unique among the tenant's ``PENDING`` and ``RUNNING``
        jobs of the same ``kind``.

        Returns:
            The new job's id, or ``None`` when the dedup index rejected a duplicate.

        Raises:
            ValueError: When ``run_after`` is naive (it would be read in the database
                session's time zone, not as UTC) or ``max_attempts`` is below 1.

        """
        _require_aware(run_after, "run_after")
        if max_attempts < 1:
            msg = "max_attempts must be at least 1"
            raise ValueError(msg)
        tenant_id = bound_context(session).require_tenant_id()
        now = self._clock()
        insert_job = (
            insert(JobRow)
            .values(
                tenant_id=tenant_id,
                id=uuid7(),
                kind=kind,
                key=key,
                payload=payload,
                status=JobStatus.PENDING,
                attempts=0,
                max_attempts=max_attempts,
                run_after=now if run_after is None else run_after,
                created_at=now,
            )
            .on_conflict_do_nothing(
                index_elements=[JobRow.tenant_id, JobRow.kind, JobRow.key],
                index_where=_DEDUP_PREDICATE,
            )
            .returning(JobRow.id)
        )
        return session.execute(insert_job).scalar_one_or_none()

    def claim(
        self,
        *,
        worker_id: str,
        tenant_id: UUID,
        kinds: Sequence[str] | None,
        limit: int,
        lease: timedelta,
        exclude_kinds: Sequence[str] = (),
    ) -> list[Job]:
        """Lease up to ``limit`` due jobs of one tenant, oldest due first.

        Rows another transaction has locked are skipped, so concurrent claimers never
        wait for each other or receive the same job. ``kinds`` keeps only those
        kinds and ``exclude_kinds`` leaves those out.

        Returns:
            The claimed jobs, each carrying the token of this claim.

        Raises:
            ValueError: When ``limit`` is negative.

        """
        if limit < 0:
            msg = "limit must not be negative"
            raise ValueError(msg)
        now = self._clock()
        due = select(JobRow.id).where(
            JobRow.tenant_id == tenant_id,
            JobRow.status == JobStatus.PENDING,
            JobRow.run_after <= now,
        )
        if kinds is not None:
            due = due.where(JobRow.kind.in_(kinds))
        if exclude_kinds:
            due = due.where(JobRow.kind.not_in(exclude_kinds))
        picked = (
            due
            .order_by(JobRow.run_after, JobRow.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
            .cte("picked")
            .prefix_with("MATERIALIZED")
        )
        lease_rows = (
            update(JobRow)
            .where(JobRow.tenant_id == tenant_id, JobRow.id == picked.c.id)
            .values(
                status=JobStatus.RUNNING,
                attempts=JobRow.attempts + 1,
                locked_by=worker_id,
                locked_until=now + lease,
                lock_token=func.gen_random_uuid(),
            )
            .returning(
                JobRow.id,
                JobRow.run_after,
                JobRow.kind,
                JobRow.key,
                JobRow.payload,
                JobRow.attempts,
                JobRow.max_attempts,
                JobRow.lock_token,
            )
            .execution_options(synchronize_session=False)
        )
        with self._session(tenant_id) as session:
            rows = session.execute(lease_rows).all()
        return [
            Job(
                id=job_id,
                tenant_id=tenant_id,
                kind=kind,
                key=key,
                payload=payload,
                attempts=attempts,
                max_attempts=max_attempts,
                # The statement above sets it on every row it returns.
                lock_token=cast("UUID", token),
            )
            for job_id, _, kind, key, payload, attempts, max_attempts, token in sorted(
                rows,
                key=operator.itemgetter(1, 0),  # run_after, then id
            )
        ]

    def heartbeat(self, job: Job, *, lease: timedelta) -> bool:
        """Push the lease out to ``lease`` from now while this claim still holds it.

        Returns:
            ``False`` when the lease was lost: the job was reaped and may be running
            elsewhere, so the caller's work is no longer wanted.

        """
        extend = (
            update(JobRow)
            .where(*_holds_lease(job))
            .values(locked_until=self._clock() + lease)
            .returning(JobRow.id)
            .execution_options(synchronize_session=False)
        )
        with self._session(job.tenant_id) as session:
            return session.execute(extend).scalar_one_or_none() is not None

    def complete(self, job: Job) -> bool:
        """Mark ``job`` done if this claim still holds the lease.

        A job that succeeds after failing ends ``DONE`` with no ``last_error``: the
        earlier failures were history, and a finished job should not look broken.

        Returns:
            ``False`` when another worker owns the job now, or it already finished.

        """
        finish = (
            update(JobRow)
            .where(*_holds_lease(job))
            .values(
                status=JobStatus.DONE,
                finished_at=self._clock(),
                last_error=None,
                locked_until=None,
                lock_token=None,
            )
            .returning(JobRow.id)
            .execution_options(synchronize_session=False)
        )
        with self._session(job.tenant_id) as session:
            return session.execute(finish).scalar_one_or_none() is not None

    def fail(self, job: Job, error: str, *, retry_at: datetime | None) -> bool:
        """Record a failed run if this claim still holds the lease.

        The job goes back to ``PENDING`` for ``retry_at`` (default: now plus
        ``retry_delay(job.attempts)``), or to ``DEAD`` once ``attempts`` has reached
        ``max_attempts``. ``fail`` never changes ``attempts``; ``claim`` counted
        this run already.

        ``error`` is stored as given in ``last_error``, a plain column outside the log
        redaction: for an exception pass ``safe_exception_summary(exc)``, never
        ``str(exc)``. A naive ``retry_at`` raises ``ValueError``.

        Returns:
            ``False`` when another worker owns the job now, or it already finished.

        """
        _require_aware(retry_at, "retry_at")
        now = self._clock()
        record = update(JobRow).where(*_holds_lease(job)).returning(JobRow.id)
        if job.attempts >= job.max_attempts:
            record = record.values(
                status=JobStatus.DEAD,
                last_error=error,
                finished_at=now,
                locked_until=None,
                lock_token=None,
            )
        else:
            record = record.values(
                status=JobStatus.PENDING,
                last_error=error,
                run_after=retry_at or now + retry_delay(job.attempts),
                locked_by=None,
                locked_until=None,
                lock_token=None,
            )
        with self._session(job.tenant_id) as session:
            return (
                session.execute(
                    record.execution_options(synchronize_session=False)
                ).scalar_one_or_none()
                is not None
            )

    def reap(self, *, tenant_id: UUID) -> int:
        """Take back the tenant's jobs whose lease ran out.

        A job that still has attempts left returns to ``PENDING`` with ``attempts``
        unchanged, since ``claim`` counted the lost run. One that died on its last
        attempt is ``DEAD``: running it again would exceed ``max_attempts``.

        Returns:
            How many jobs were taken back, requeued or dead-lettered.

        """
        now = self._clock()
        expired = (
            JobRow.tenant_id == tenant_id,
            JobRow.status == JobStatus.RUNNING,
            JobRow.locked_until < now,
        )
        exhausted = (
            update(JobRow)
            .where(*expired, JobRow.attempts >= JobRow.max_attempts)
            .values(
                status=JobStatus.DEAD,
                last_error=LEASE_EXPIRED,
                finished_at=now,
                locked_until=None,
                lock_token=None,
            )
            .returning(JobRow.id)
            .execution_options(synchronize_session=False)
        )
        retryable = (
            update(JobRow)
            .where(*expired)
            .values(
                status=JobStatus.PENDING,
                last_error=LEASE_EXPIRED,
                locked_by=None,
                locked_until=None,
                lock_token=None,
            )
            .returning(JobRow.id)
            .execution_options(synchronize_session=False)
        )
        with self._session(tenant_id) as session:
            # Dead-letter first: those rows leave RUNNING, so the second statement
            # sees only the ones that may run again.
            dead = session.execute(exhausted).all()
            requeued = session.execute(retryable).all()
        return len(dead) + len(requeued)

    def schedule(
        self,
        session: Session,
        *,
        name: str,
        cron: str,
        tz: str,
        kind: str,
        payload: dict[str, JsonValue],
    ) -> UUID:
        """Create or update the tenant's schedule ``name`` in the caller's transaction.

        Declaring a schedule again changes what it runs but keeps its place: the
        next slot is recomputed from now only when ``cron`` or ``tz`` changed, so
        re-declaring it at every start-up never postpones it. ``enabled`` and
        ``last_run_at`` are never touched.

        Returns:
            The schedule's id.

        """
        tenant_id = bound_context(session).require_tenant_id()
        first_slot = next_slot(cron, tz, after=self._clock())
        declare = insert(JobSchedule).values(
            tenant_id=tenant_id,
            id=uuid7(),
            name=name,
            cron=cron,
            tz=tz,
            kind=kind,
            payload=payload,
            enabled=True,
            next_run_at=first_slot,
        )
        proposed = declare.excluded
        upsert = declare.on_conflict_do_update(
            index_elements=[JobSchedule.tenant_id, JobSchedule.name],
            set_={
                "cron": proposed.cron,
                "tz": proposed.tz,
                "kind": proposed.kind,
                "payload": proposed.payload,
                "next_run_at": case(
                    (
                        and_(
                            JobSchedule.cron == proposed.cron,
                            JobSchedule.tz == proposed.tz,
                        ),
                        JobSchedule.next_run_at,
                    ),
                    else_=proposed.next_run_at,
                ),
            },
        ).returning(JobSchedule.id)
        return session.execute(upsert).scalar_one()
