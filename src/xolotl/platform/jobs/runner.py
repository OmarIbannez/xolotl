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
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Self
from uuid import UUID

from xolotl.platform.db import SessionFactory, tenant_session
from xolotl.platform.jobs.queue import QUEUE_ACTOR, Job, PostgresJobQueue, job_handler
from xolotl.platform.logging import safe_exception_summary
from xolotl.platform.tenancy.context import TenantContext
from xolotl.platform.tenancy.tenants import tenant_ids

logger = logging.getLogger(__name__)

DEFAULT_LEASE = timedelta(seconds=60)
DEFAULT_BATCH = 10
HEARTBEAT_THREAD_PREFIX = "job-heartbeat"
RUNNER_THREAD_PREFIX = "job-runner"


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


def _never() -> bool:
    return False


@dataclass(slots=True)
class _Round:
    """What the threads of one ``run_once`` share, behind ``lock``.

    ``taken`` counts the jobs claimed per tenant and kind; a kind that has reached
    ``limit`` is left out of the tenant's next claims. ``drained`` holds the tenants
    whose last claim found nothing. ``turn`` rotates the tenant each claim starts
    from, so tenants take turns. ``threads`` only grows, and only from a running
    thread, so joining it in order joins every thread the round started.
    """

    tenants: list[UUID]
    limit: int
    stopping: Callable[[], bool]
    lock: threading.Lock = field(default_factory=threading.Lock)
    taken: Counter[tuple[UUID, str]] = field(default_factory=Counter[tuple[UUID, str]])
    drained: set[UUID] = field(default_factory=set[UUID])
    threads: list[threading.Thread] = field(default_factory=list[threading.Thread])
    ran: int = 0
    turn: int = 0
    failure: Exception | None = None


class JobRunner:
    """Runs due jobs for every tenant: the job half of the worker's tick.

    ``threads`` runs that many jobs at once, each on a thread and in sessions of
    its own; ``batch`` is ``run_once``'s default ``limit``.
    """

    def __init__(
        self,
        queue: PostgresJobQueue,
        session_factory: SessionFactory,
        *,
        worker_id: str,
        lease: timedelta = DEFAULT_LEASE,
        kinds: Sequence[str] | None = None,
        threads: int = 1,
        batch: int = DEFAULT_BATCH,
    ) -> None:
        if lease <= timedelta(0):
            # The heartbeat waits half a lease between beats: zero would spin.
            msg = "lease must be positive"
            raise ValueError(msg)
        if threads < 1:
            msg = "threads must be at least 1"
            raise ValueError(msg)
        if batch < 1:
            msg = "batch must be at least 1"
            raise ValueError(msg)
        self._queue: PostgresJobQueue = queue
        self._session_factory: SessionFactory = session_factory
        self._worker_id: str = worker_id
        self._lease: timedelta = lease
        self._kinds: Sequence[str] | None = kinds
        self._threads: int = threads
        self._batch: int = batch

    def run_once(
        self, *, limit: int | None = None, stopping: Callable[[], bool] = _never
    ) -> int:
        """Reap expired leases, then run up to ``limit`` due jobs per tenant and kind.

        Jobs are claimed one at a time, so a job's lease starts when its handler does
        rather than while earlier jobs of the round hold the worker. Tenants take
        turns, one claim each, and a kind that has had ``limit`` jobs of a tenant
        in this round leaves the rest of the tenant's due jobs to the other kinds,
        so one kind's backlog delays the others by at most a round.

        With several ``threads`` the round starts a helper each time a thread
        claims a job while fewer than ``threads`` are running, so an idle round
        claims once per tenant on the caller's thread alone. Claims are taken one
        at a time under the round's lock, which keeps ``limit`` exact. Once
        ``stopping`` returns true no thread claims again; the call returns when
        every job it claimed has finished. A thread that fails (a claim that
        hits a database blip) stops the round's claims, and the first error is
        raised once every thread has finished.

        Returns:
            How many jobs ran, whether they succeeded or failed.

        """
        tenants = tenant_ids(self._session_factory, actor_id=QUEUE_ACTOR)
        for tenant_id in tenants:
            if reaped := self._queue.reap(tenant_id=tenant_id):
                # A worker died or hung: its jobs were requeued, or dead-lettered if
                # that was their last attempt. Nothing else records that it happened.
                logger.warning(
                    "reaped jobs whose lease had expired",
                    extra={"tenant_id": str(tenant_id), "reaped": reaped},
                )
        work = _Round(
            tenants=tenants,
            limit=self._batch if limit is None else limit,
            stopping=stopping,
        )
        self._work(work)
        index = 0
        while index < len(work.threads):
            work.threads[index].join()
            index += 1
        if work.failure is not None:
            raise work.failure
        return work.ran

    def _work(self, work: _Round, *, helper: bool = False) -> None:
        """Claim and run jobs of the round until it is drained, full or stopping.

        A helper logs its error here, since nothing above its thread would; the
        caller's thread leaves that to whoever called ``run_once``.
        """
        try:
            while (job := self._claim(work)) is not None:
                self._run(job)
                with work.lock:
                    work.ran += 1
        except Exception as error:
            with work.lock:
                if work.failure is None:
                    work.failure = error
            if helper:
                logger.exception("job runner thread failed")

    def _claim(self, work: _Round) -> Job | None:
        """Lease the next job of the round, starting a helper when one may run.

        Returns:
            The job, or ``None`` when the round has nothing left for this thread.

        """
        with work.lock:
            start = work.turn % len(work.tenants) if work.tenants else 0
            work.turn += 1
            for tenant_id in work.tenants[start:] + work.tenants[:start]:
                if work.stopping() or work.failure is not None:
                    return None
                if tenant_id in work.drained:
                    continue
                full = sorted(
                    kind
                    for (tenant, kind), taken in work.taken.items()
                    if tenant == tenant_id and taken >= work.limit
                )
                claimed = self._queue.claim(
                    worker_id=self._worker_id,
                    tenant_id=tenant_id,
                    kinds=self._kinds,
                    exclude_kinds=full,
                    limit=1,
                    lease=self._lease,
                )
                if not claimed:
                    work.drained.add(tenant_id)
                    continue
                job = claimed[0]
                work.taken[tenant_id, job.kind] += 1
                self._add_helper(work)
                return job
        return None

    def _add_helper(self, work: _Round) -> None:
        # Called under the round's lock. The caller's thread plus the helpers.
        if len(work.threads) + 1 >= self._threads:
            return
        helper = threading.Thread(
            target=self._work,
            args=(work,),
            kwargs={"helper": True},
            name=f"{RUNNER_THREAD_PREFIX}-{len(work.threads) + 1}",
            daemon=True,
        )
        work.threads.append(helper)
        helper.start()

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
