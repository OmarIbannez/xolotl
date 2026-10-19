"""Job runner: reaps, claims and runs jobs, keeping each lease alive while it works.

Handlers are at-least-once. A handler's session commits when it returns and the job
is completed afterwards, in a separate transaction, so a worker that dies in between
(or one whose lease was reaped while its handler hung) leaves a job that runs again.
Fencing protects the job row, not the handler's side effects: write handlers so
that running twice is harmless.

What a failed run leaves in ``job.last_error`` is ``safe_exception_summary`` of the
exception: the table is plain text outside the redaction filter, so database and
validation errors are reduced to a class and a count or SQLSTATE, never their message.
"""

import logging
import threading
from collections.abc import Sequence
from datetime import timedelta
from typing import Self

from xolotl.platform.db import SessionFactory, tenant_session
from xolotl.platform.jobs.queue import QUEUE_ACTOR, Job, PostgresJobQueue, job_handler
from xolotl.platform.logging import safe_exception_summary
from xolotl.platform.tenancy.context import TenantContext
from xolotl.platform.tenancy.tenants import tenant_ids

logger = logging.getLogger(__name__)

DEFAULT_LEASE = timedelta(seconds=60)
HEARTBEAT_THREAD_PREFIX = "job-heartbeat"


class _Heartbeat:
    """Extends a job's lease every half lease, on a daemon thread, while it runs."""

    def __init__(self, queue: PostgresJobQueue, job: Job, lease: timedelta) -> None:
        self._queue: PostgresJobQueue = queue
        self._job: Job = job
        self._lease: timedelta = lease
        self._stopped: threading.Event = threading.Event()
        self._thread: threading.Thread = threading.Thread(
            target=self._beat,
            name=f"{HEARTBEAT_THREAD_PREFIX}-{job.id}",
            daemon=True,
        )

    def __enter__(self) -> Self:
        self._thread.start()
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self._stopped.set()
        self._thread.join()

    def _beat(self) -> None:
        while not self._stopped.wait(self._lease.total_seconds() / 2):
            try:
                alive = self._queue.heartbeat(self._job, lease=self._lease)
            except Exception:
                # A database blip must not end the heartbeat: the next beat may land
                # before the lease runs out.
                logger.exception(
                    "job heartbeat failed",
                    extra={"job_id": str(self._job.id), "job_kind": self._job.kind},
                )
                continue
            if not alive:
                # The handler cannot be stopped, and its own transaction commits
                # whatever it did; only the job row is no longer this run's to move.
                logger.warning(
                    "job lease lost; another worker may run it again",
                    extra={"job_id": str(self._job.id), "job_kind": self._job.kind},
                )
                return


class JobRunner:
    """Runs due jobs for every tenant: the job half of the worker's tick."""

    def __init__(
        self,
        queue: PostgresJobQueue,
        session_factory: SessionFactory,
        *,
        worker_id: str,
        lease: timedelta = DEFAULT_LEASE,
        kinds: Sequence[str] | None = None,
    ) -> None:
        if lease <= timedelta(0):
            # The heartbeat waits half a lease between beats: zero would spin.
            msg = "lease must be positive"
            raise ValueError(msg)
        self._queue: PostgresJobQueue = queue
        self._session_factory: SessionFactory = session_factory
        self._worker_id: str = worker_id
        self._lease: timedelta = lease
        self._kinds: Sequence[str] | None = kinds

    def run_once(self, *, limit: int = 10) -> int:
        """Reap expired leases, then run up to ``limit`` due jobs for each tenant.

        Jobs are claimed one at a time, so a job's lease starts when its handler does
        rather than while earlier jobs of the batch hold the worker.

        Returns:
            How many jobs ran, whether they succeeded or failed.

        """
        ran = 0
        for tenant_id in tenant_ids(self._session_factory, actor_id=QUEUE_ACTOR):
            if reaped := self._queue.reap(tenant_id=tenant_id):
                # A worker died or hung: its jobs were requeued, or dead-lettered if
                # that was their last attempt. Nothing else records that it happened.
                logger.warning(
                    "reaped jobs whose lease had expired",
                    extra={"tenant_id": str(tenant_id), "reaped": reaped},
                )
            for _ in range(limit):
                claimed = self._queue.claim(
                    worker_id=self._worker_id,
                    tenant_id=tenant_id,
                    kinds=self._kinds,
                    limit=1,
                    lease=self._lease,
                )
                if not claimed:
                    break
                self._run(claimed[0])
                ran += 1
        return ran

    def _run(self, job: Job) -> None:
        with _Heartbeat(self._queue, job, self._lease):
            failure = self._execute(job)
        if failure is None:
            settled = self._queue.complete(job)
        else:
            settled = self._queue.fail(job, failure, retry_at=None)
        if not settled:
            logger.warning(
                "job finished after losing its lease; its outcome was not recorded",
                extra={"job_id": str(job.id), "job_kind": job.kind},
            )

    def _execute(self, job: Job) -> str | None:
        """Run the handler in the job's tenant scope.

        Returns:
            ``None`` on success, otherwise the text to record as the failure.

        """
        try:
            handler = job_handler(job.kind)
        except KeyError:
            return f"no handler registered for job kind {job.kind!r}"
        context = TenantContext.system(job.tenant_id, f"job:{job.id}")
        try:
            with tenant_session(context, factory=self._session_factory) as session:
                handler(session, job)
        except Exception as error:
            logger.exception(
                "job failed",
                extra={
                    "job_id": str(job.id),
                    "job_kind": job.kind,
                    "job_attempt": job.attempts,
                },
            )
            return safe_exception_summary(error)
        return None
