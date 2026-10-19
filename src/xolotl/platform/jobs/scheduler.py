"""Cron scheduler: turns due schedules into jobs, one slot at a time.

A schedule is due when ``next_run_at`` has passed. Each tick enqueues one job per due
schedule, keyed ``<name>:<slot>`` in UTC so the same slot can never be queued twice
while its job is pending or running, and moves ``next_run_at`` on, all in one
transaction: a tick that dies leaves neither half behind.

Catch-up policy: slots missed while no worker was running are skipped, never replayed.
A schedule that is several slots late enqueues once, for its earliest due slot, and
jumps to the first slot after now. ``last_run_at`` records the slot that was enqueued.
Replaying a backlog would burst every missed run at once on restart, and a recurring
job is meant to look at the state of the world now, not at the slot's instant.

``schedule()`` validates the expression and the zone, so a row that no longer parses
was edited behind its back. Such a row is skipped, left due, and reported once at
WARNING for each definition it has had: it must not stall the schedules after it, nor
write a log line per tick for as long as nobody has fixed it.
"""

import logging
from datetime import UTC
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from xolotl.platform import clock
from xolotl.platform.db import SessionFactory, tenant_session
from xolotl.platform.errors import ValidationFailedError
from xolotl.platform.jobs.models import JobSchedule
from xolotl.platform.jobs.queue import ClockFn, PostgresJobQueue, next_slot
from xolotl.platform.tenancy.context import TenantContext
from xolotl.platform.tenancy.tenants import tenant_ids

logger = logging.getLogger(__name__)

SCHEDULER_ACTOR = "scheduler"


class Scheduler:
    """Enqueues the jobs of due schedules: the cron half of the worker's tick."""

    def __init__(
        self,
        queue: PostgresJobQueue,
        session_factory: SessionFactory,
        *,
        clock_fn: ClockFn = clock.now,
    ) -> None:
        self._queue: PostgresJobQueue = queue
        self._session_factory: SessionFactory = session_factory
        self._clock: ClockFn = clock_fn
        # Corrupt schedules already reported, with the (cron, tz) that was reported.
        self._reported: dict[UUID, tuple[str, str]] = {}

    def tick(self) -> int:
        """Enqueue the job of every enabled schedule that is due, tenant by tenant.

        Returns:
            How many jobs were enqueued; a slot whose job already exists counts for
            nothing.

        """
        enqueued = 0
        for tenant_id in tenant_ids(self._session_factory, actor_id=SCHEDULER_ACTOR):
            context = TenantContext.system(tenant_id, SCHEDULER_ACTOR)
            with tenant_session(context, factory=self._session_factory) as session:
                enqueued += self._enqueue_due(session, tenant_id)
        return enqueued

    def _enqueue_due(self, session: Session, tenant_id: UUID) -> int:
        now = self._clock()
        # SKIP LOCKED: a schedule another scheduler is handling is not ours to touch.
        due = session.scalars(
            select(JobSchedule)
            .where(
                JobSchedule.tenant_id == tenant_id,
                JobSchedule.enabled.is_(True),
                JobSchedule.next_run_at <= now,
            )
            .order_by(JobSchedule.next_run_at, JobSchedule.name)
            .with_for_update(skip_locked=True)
        ).all()
        enqueued = 0
        for schedule in due:
            try:
                upcoming = next_slot(schedule.cron, schedule.tz, after=now)
            except ValidationFailedError as error:
                self._report_corrupt(tenant_id, schedule, error)
                continue
            # It parses: should it break again later, that is reported afresh.
            _ = self._reported.pop(schedule.id, None)
            slot = schedule.next_run_at.astimezone(UTC)
            job_id = self._queue.enqueue(
                session,
                kind=schedule.kind,
                payload=schedule.payload,
                key=f"{schedule.name}:{slot.isoformat()}",
            )
            schedule.last_run_at = slot
            schedule.next_run_at = upcoming
            enqueued += job_id is not None
        return enqueued

    def _report_corrupt(
        self, tenant_id: UUID, schedule: JobSchedule, error: ValidationFailedError
    ) -> None:
        definition = (schedule.cron, schedule.tz)
        if self._reported.get(schedule.id) == definition:
            return
        self._reported[schedule.id] = definition
        logger.warning(
            "schedule skipped: its cron expression or time zone is invalid",
            extra={
                "tenant_id": str(tenant_id),
                "schedule": schedule.name,
                "reason": error.detail,
            },
        )
