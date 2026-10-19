"""Workflow runner on Postgres: durable steps, signals and timers (spec §9).

A run is one ``workflow_run`` row, one ``workflow_step`` row per call of a step and
one ``workflow_signal`` row per signal received. Those tables are all there is to a
run: no worker holds any of it, so any worker can pick up any run and a restart loses
nothing.

Jobs move a run. ``start`` queues ``workflow.advance``, and each advance runs one step.
The transaction that records the step also queues the next job (the outbox pattern,
applied to jobs), so there is no moment at which a step is saved and its successor is
not. In that one transaction ``advance``:

1. locks the run row, which serialises every worker, signal and timer that touches
   the run;
2. works out what to call the step with. A ``RUNNING`` run takes a plain call. A
   ``WAITING`` one takes the oldest signal it asked for, else ``timed_out`` once its
   ``wake_at`` has passed, else nothing: the job was a duplicate or a stale timer;
3. calls the step in a savepoint, writes its row (``DONE``, with the ``StepResult`` as
   the output), the run's new ``state``, ``current_step``, ``status`` and ``wake_at``,
   and queues the next job: ``workflow.advance`` for the following step or for a
   waiting step that already has its signal, ``workflow.timer`` (due at ``wake_at``)
   for a wait with a wake time.

A step's ``attempt`` is one more than the rows the step already has, whatever their
status, so each call of a step, retried or woken, is one row. A wait that is woken
therefore shows as ``(approve, 1)`` for the call that parked the run and
``(approve, 2)`` for the call that took the signal.

Redelivery is harmless because the tables, not the job, say where the run is. A step
whose row is committed is never called again: the same transaction moved the run past
it. A duplicate or late job runs the step that is due or finds nothing due. A
transaction that does not commit leaves nothing behind, neither the step's writes nor
its row, nor the signal it consumed, nor the job it queued, and the retry starts that
step over as the same attempt.

A step that raises is rolled back to its savepoint and recorded as a ``FAILED`` row,
committed before the error goes on because the job runner rolls the handler's session
back, and the job retries it as the next attempt with the queue's backoff. While it
retries, the run's ``last_error`` says why. The step has a budget of attempts of its
own, the job's ``max_attempts``: when the failures since its last completed call reach
it, the run becomes ``FAILED`` in the same commit as that last ``FAILED`` row. The
budget belongs to the step, not to the job that happens to run it, because a run can
have several jobs queued (a retry, a signal, a timer) and a duplicate on its last
attempt must not end a run whose next step has failed once.

Failures that are not a step failing (the workflow is no longer registered, a row is
corrupt, the run's lock could not be had) leave the run alone and fail the job. If the
job dies, and no other job of the run is alive, the run is stranded: the reaper
dead-letters a job whose worker died on its last attempt without any of this module's
code running, and nothing else would ever advance the run. ``fail_stranded``, run now
and then as the ``workflow.sweep`` job, finds those runs and fails them with the
job's error.

``signal`` locks the run row as well. A signal that arrives while the run is parking
either waits for that transaction and finds the run waiting, or commits first and is
found by the parking transaction, which looks for a signal that came early before it
commits. Either way it is not lost. Waiting for a run's lock is bounded by
``LOCK_TIMEOUT``: a request or a worker that cannot get it fails with the database's
lock-timeout error (SQLSTATE 55P03) and tries again, instead of queueing behind a
slow or stuck transaction.
"""

import logging
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

from pydantic import BaseModel, JsonValue
from sqlalchemy import ColumnElement, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from xolotl.platform import clock
from xolotl.platform.db import SessionFactory, bound_context, tenant_session
from xolotl.platform.enums import JobStatus, RunStatus, StepStatus
from xolotl.platform.errors import DomainError, NotFoundError
from xolotl.platform.ids import uuid7
from xolotl.platform.jobs.models import JobRow
from xolotl.platform.jobs.queue import (
    ClockFn,
    Job,
    PostgresJobQueue,
    register_job_handler,
)
from xolotl.platform.logging import safe_exception_summary
from xolotl.platform.tenancy.context import TenantContext
from xolotl.platform.workflows.definition import StepInput, StepResult, workflow
from xolotl.platform.workflows.models import (
    WorkflowRunRow,
    WorkflowSignalRow,
    WorkflowStepRow,
)

logger = logging.getLogger(__name__)

ADVANCE_KIND = "workflow.advance"
TIMER_KIND = "workflow.timer"
SWEEP_KIND = "workflow.sweep"
WORKFLOW_ACTOR = "workflow-runner"
# How long to wait for a run's row lock before giving up. A step holds it for as long
# as it runs, and an API request that signals the run, or a worker that advances it,
# should fail and be retried rather than queue behind a slow or stuck transaction.
LOCK_TIMEOUT = "5s"

_FINISHED = frozenset({RunStatus.DONE, RunStatus.FAILED})
_LIVE_JOBS = frozenset({JobStatus.PENDING, JobStatus.RUNNING})
_LOCK_TIMEOUT_SQL = text("SELECT current_setting('lock_timeout')")
_SET_LOCK_TIMEOUT_SQL = text("SELECT set_config('lock_timeout', :value, true)")


class _RunJob(BaseModel):
    """The payload of both kinds of job."""

    run_id: UUID


@dataclass(frozen=True, slots=True)
class _JobsOfRun:
    """What the sweep needs to know about a run's advance and timer jobs."""

    live: bool  # one of them is PENDING or RUNNING
    newest_kind: str
    newest_status: JobStatus
    newest_error: str | None


def _jobs_by_run(
    session: Session, tenant_id: UUID, run_ids: list[str]
) -> dict[str, _JobsOfRun]:
    """Summarise the advance and timer jobs of the given runs.

    The runs are named by the ``run_id`` in the job payload, so this reads the
    tenant's workflow jobs, once, however many runs there are.

    Returns:
        For each run that has any such job, whether one is live and what the newest
        one (by creation) is.

    """
    run_ref = cast(
        "ColumnElement[str]", func.jsonb_extract_path_text(JobRow.payload, "run_id")
    )
    rows = session.execute(
        select(run_ref, JobRow.kind, JobRow.status, JobRow.last_error)
        .where(
            JobRow.tenant_id == tenant_id,
            JobRow.kind.in_((ADVANCE_KIND, TIMER_KIND)),
            run_ref.in_(run_ids),
        )
        .order_by(JobRow.created_at, JobRow.id)
    ).all()
    summary: dict[str, _JobsOfRun] = {}
    for ref, kind, status, last_error in rows:  # oldest first: the last one wins
        seen_live = ref in summary and summary[ref].live
        summary[ref] = _JobsOfRun(
            live=seen_live or status in _LIVE_JOBS,
            newest_kind=kind,
            newest_status=status,
            newest_error=last_error,
        )
    return summary


def _load_run(session: Session, run_id: UUID, *, lock: bool) -> WorkflowRunRow:
    """Load a run, locking its row when ``lock`` is set.

    A lock that cannot be had within ``LOCK_TIMEOUT`` raises the database's
    ``LockNotAvailable`` (SQLSTATE 55P03), which aborts the caller's transaction like
    any other statement error. The timeout is in force for the lock wait only: the
    caller's own setting is put back before this returns, so neither the step that
    runs next nor the caller's later statements wait on anything differently.

    Returns:
        The run.

    Raises:
        NotFoundError: When the session's tenant has no such run.

    """
    query = select(WorkflowRunRow).where(WorkflowRunRow.id == run_id)
    callers_timeout: str | None = None
    if lock:
        callers_timeout = cast("str", session.execute(_LOCK_TIMEOUT_SQL).scalar_one())
        _ = session.execute(_SET_LOCK_TIMEOUT_SQL, {"value": LOCK_TIMEOUT})
        # populate_existing: a row this session read earlier may be stale by now.
        query = query.with_for_update().execution_options(populate_existing=True)
    run = session.scalars(query).one_or_none()
    if callers_timeout is not None:
        _ = session.execute(_SET_LOCK_TIMEOUT_SQL, {"value": callers_timeout})
    if run is None:
        msg = "workflow run not found"
        raise NotFoundError(msg)
    return run


def _current_step(run: WorkflowRunRow) -> str:
    if run.current_step is None:
        msg = f"workflow run of {run.definition!r} has no current step"
        raise LookupError(msg)
    return run.current_step


def _next_attempt(session: Session, run_id: UUID, step_id: str) -> int:
    recorded = session.execute(
        select(func.count())
        .select_from(WorkflowStepRow)
        .where(WorkflowStepRow.run_id == run_id, WorkflowStepRow.step_id == step_id)
    ).scalar_one()
    return recorded + 1


def _awaited(session: Session, run_id: UUID, step_id: str) -> str | None:
    """Find the signal a step asked for the last time it ran to completion.

    Failed calls say nothing about what the step waits for, so only ``DONE`` rows
    count.

    Returns:
        The signal's name, or ``None`` when the step waits for time alone.

    Raises:
        LookupError: When the step has no completed call at all, so nothing says
            what the run waits for and it would wait for ever.

    """
    latest = session.scalars(
        select(WorkflowStepRow)
        .where(
            WorkflowStepRow.run_id == run_id,
            WorkflowStepRow.step_id == step_id,
            WorkflowStepRow.status == StepStatus.DONE,
        )
        .order_by(WorkflowStepRow.attempt.desc())
        .limit(1)
    ).one_or_none()
    if latest is None or latest.output is None:
        msg = f"step {step_id!r} has no completed call to say what its run waits for"
        raise LookupError(msg)
    return StepResult.model_validate(latest.output).wait_for_signal


def _oldest_signal(
    session: Session, run_id: UUID, name: str | None
) -> WorkflowSignalRow | None:
    """Find the run's unconsumed signal of ``name``, the oldest if there are several.

    ``signal`` keeps at most one pending per name, so there is no choice to make
    through it; the order is a safeguard for rows written another way.

    Returns:
        The signal, or ``None`` when there is none or ``name`` is ``None``.

    """
    if name is None:
        return None
    return session.scalars(
        select(WorkflowSignalRow)
        .where(
            WorkflowSignalRow.run_id == run_id,
            WorkflowSignalRow.name == name,
            WorkflowSignalRow.consumed_at.is_(None),
        )
        .order_by(WorkflowSignalRow.received_at, WorkflowSignalRow.id)
        .limit(1)
    ).one_or_none()


def _call_input(
    session: Session, run: WorkflowRunRow, step_id: str, now: datetime
) -> tuple[StepInput, WorkflowSignalRow | None] | None:
    """Decide what to call the run's current step with.

    A signal wins over a timeout that is due at the same moment.

    Returns:
        The input for the step and the signal it takes, if any; ``None`` when the run
        is waiting and neither a signal nor its wake time has come.

    """
    if run.status is RunStatus.RUNNING:
        return StepInput(), None
    signal = _oldest_signal(session, run.id, _awaited(session, run.id, step_id))
    if signal is not None:
        return StepInput(signal=signal.name, payload=signal.payload), signal
    if run.wake_at is not None and run.wake_at <= now:
        return StepInput(timed_out=True), None
    return None


def _finish(run: WorkflowRunRow, now: datetime) -> None:
    run.status = RunStatus.DONE
    run.current_step = None
    run.wake_at = None
    run.finished_at = now


def _fail_run(run: WorkflowRunRow, *, now: datetime, error: str) -> None:
    run.status = RunStatus.FAILED
    run.wake_at = None
    run.finished_at = now
    run.last_error = error
    # Nothing else makes a failed run loud: no event is published for it.
    logger.warning(
        "workflow run failed",
        extra={
            "workflow_run_id": str(run.id),
            "workflow_definition": run.definition,
            "workflow_step": run.current_step,
            "workflow_error": error,
        },
    )


def _out_of_attempts(
    session: Session, record: WorkflowStepRow, max_attempts: int | None
) -> bool:
    """Say whether the call that just failed was the step's last allowed attempt.

    Attempts are counted per step, not per job: a run can have several jobs queued
    (a retry, a signal, a timer), any of which may be the one that runs the step.
    Only failures in a row count. A success starts the count again, because the job
    that wakes the step next has its own attempts to spend.

    Returns:
        Whether the failures since the step's last completed call, this one
        included, have reached ``max_attempts``. Never, when ``max_attempts`` is
        ``None`` (a direct call has no retries to run out of).

    """
    if max_attempts is None:
        return False
    last_completed = session.execute(
        select(func.max(WorkflowStepRow.attempt)).where(
            WorkflowStepRow.run_id == record.run_id,
            WorkflowStepRow.step_id == record.step_id,
            WorkflowStepRow.status == StepStatus.DONE,
        )
    ).scalar_one()
    return record.attempt - (last_completed or 0) >= max_attempts


class PostgresWorkflowRunner:
    """Runs workflows whose state lives in the ``workflow_*`` tables.

    ``start`` and ``signal`` run in the caller's transaction, like ``enqueue``.
    ``advance`` and ``handle_job`` are the worker's side: both lock the run and take
    one step. The steps are plain functions (see ``WorkflowDefinition``); a step must
    not commit or roll back the session it is given.
    """

    def __init__(
        self,
        session_factory: SessionFactory,
        queue: PostgresJobQueue,
        *,
        clock_fn: ClockFn = clock.now,
    ) -> None:
        self._session_factory: SessionFactory = session_factory
        self._queue: PostgresJobQueue = queue
        self._clock: ClockFn = clock_fn

    def register_job_handlers(self) -> None:
        """Route the ``workflow.*`` jobs to this runner.

        Registers ``workflow.advance`` and ``workflow.timer`` (``handle_job``) and
        ``workflow.sweep`` (``handle_sweep``). Call it once at composition time,
        before the first job is claimed. Nothing queues the sweep: schedule a
        ``workflow.sweep`` job for each tenant (hourly is plenty).
        """
        register_job_handler(ADVANCE_KIND, self.handle_job)
        register_job_handler(TIMER_KIND, self.handle_job)
        register_job_handler(SWEEP_KIND, self.handle_sweep)

    def start(
        self,
        session: Session,
        definition: str,
        *,
        business_key: str,
        input_: dict[str, JsonValue],
    ) -> UUID:
        """Start a run of the registered workflow ``definition``.

        ``input_`` (spelled ``input`` in the spec; ruff forbids a parameter named after
        the builtin) becomes the run's first ``state``. The run and the
        ``workflow.advance`` job that starts it join the caller's transaction: they
        exist, and the first step runs, only if it commits.

        Returns:
            The new run's id.

        Raises:
            DomainError: When the tenant already has a run of this workflow for
                ``business_key``. The key is left out of the message, as it may
                identify a person.

        """
        first_step = workflow(definition).steps[0][0]
        tenant_id = bound_context(session).require_tenant_id()
        run_id = uuid7()
        created = session.execute(
            insert(WorkflowRunRow)
            .values(
                tenant_id=tenant_id,
                id=run_id,
                definition=definition,
                business_key=business_key,
                status=RunStatus.RUNNING,
                current_step=first_step,
                state=input_,
                started_at=self._clock(),
            )
            .on_conflict_do_nothing(
                index_elements=[
                    WorkflowRunRow.tenant_id,
                    WorkflowRunRow.definition,
                    WorkflowRunRow.business_key,
                ]
            )
            .returning(WorkflowRunRow.id)
        ).scalar_one_or_none()
        if created is None:
            msg = f"a run of workflow {definition!r} already exists for this key"
            raise DomainError(msg)
        self._enqueue_advance(session, run_id, first_step, attempt=1)
        return run_id

    def signal(
        self, session: Session, run_id: UUID, name: str, payload: dict[str, JsonValue]
    ) -> None:
        """Store a signal for a run, and wake the run if it is waiting for it.

        A signal is durable until a step consumes it, and deduplicated while it is
        pending: if the run already holds an unconsumed signal of this name, this one
        is dropped, whatever its payload, because a double click or a client retry
        must not count twice. One that the run is not waiting for (it came early, or
        the run is over) is stored and ignored until some step waits for its name; a
        signal sent after the first was consumed is a new signal. The wake-up is an
        ``advance`` job in the caller's transaction. A run the tenant does not have,
        including one that belongs to another tenant, is a ``NotFoundError``.

        Give each wait of a run its own signal name where a leftover must not satisfy
        a later wait: a signal that arrives once the first was consumed cannot be told
        from an early one for the next wait of the same name.

        This takes the run's row lock, so it waits for a step of that run that is
        executing. A step that records audit events holds the run lock before it takes
        the tenant's audit lock, so a caller that sends signals and records audit
        events should signal first and record last, as ``record`` asks.
        """
        run = _load_run(session, run_id, lock=True)
        if _oldest_signal(session, run.id, name) is not None:
            # Said, not silent: the payload of the dropped signal is gone.
            logger.info(
                "workflow signal dropped: one of that name is already pending",
                extra={"workflow_run_id": str(run.id), "workflow_signal": name},
            )
            return
        session.add(
            WorkflowSignalRow(
                tenant_id=run.tenant_id,
                run_id=run.id,
                name=name,
                payload=payload,
                received_at=self._clock(),
            )
        )
        if run.status is not RunStatus.WAITING:
            return
        step_id = _current_step(run)
        if _awaited(session, run.id, step_id) == name:
            attempt = _next_attempt(session, run.id, step_id)
            self._enqueue_advance(session, run.id, step_id, attempt=attempt)

    def advance(self, run_id: UUID, tenant_id: UUID) -> None:
        """Take one step of the run in a transaction of its own.

        Does nothing for a finished run, or a waiting one that has neither its signal
        nor its wake time. Whatever the step raises is raised again after the failed
        attempt is recorded, and a run the tenant does not have is a ``NotFoundError``.
        """
        context = TenantContext.system(tenant_id, WORKFLOW_ACTOR)
        with tenant_session(context, factory=self._session_factory) as session:
            self._advance(session, run_id, max_attempts=None)

    def handle_job(self, session: Session, job: Job) -> None:
        """Run a ``workflow.advance`` or ``workflow.timer`` job.

        The timer is the same as an advance: the run's own ``wake_at`` says whether
        its time has come. This takes the step in the job's session. A step that
        fails for the last time (``job.max_attempts`` failures in a row) fails the run
        in the same commit as its ``FAILED`` row, before the error goes on. Any other
        error leaves the run alone: the job retries, and if it dies the sweep finds the
        run.
        """
        run_id = _RunJob.model_validate(job.payload).run_id
        self._advance(session, run_id, max_attempts=job.max_attempts)

    def handle_sweep(self, session: Session, _job: Job) -> None:
        """Run a ``workflow.sweep`` job: fail the job's tenant's stranded runs."""
        _ = self.fail_stranded(session)

    def fail_stranded(self, session: Session) -> int:
        """Fail the session's tenant's runs that no job will ever advance again.

        A run is stranded when none of its ``workflow.advance`` and ``workflow.timer``
        jobs is live (``PENDING`` or ``RUNNING``) and the newest of them is ``DEAD``:
        the queue gave up on it, without any of this module's code running, because
        its worker died on the last attempt or the job failed before a step was
        called. Such a run becomes ``FAILED`` with the job's error in ``last_error``.
        It is not advanced again, since the usual cause is a step that kills the
        worker and would do it again.

        Nothing else is touched. A running run always has a live job, because the next
        job is queued in the transaction of the step before it, and a retry is
        ``PENDING``; a run that waits for a signal with no timer has no live job, but
        its newest job is ``DONE``. A run a step is executing right now is locked
        and skipped, as is any run with no job to judge it by.

        Meant to run now and then (hourly, per tenant, as the ``workflow.sweep`` job
        that ``handle_sweep`` runs), not on every tick.

        Returns:
            How many runs were failed.

        """
        tenant_id = bound_context(session).require_tenant_id()
        runs = session.scalars(
            select(WorkflowRunRow)
            .where(
                WorkflowRunRow.tenant_id == tenant_id,
                WorkflowRunRow.status.in_((RunStatus.RUNNING, RunStatus.WAITING)),
            )
            .order_by(WorkflowRunRow.started_at, WorkflowRunRow.id)
            .with_for_update(skip_locked=True)
        ).all()
        if not runs:
            return 0
        jobs = _jobs_by_run(session, tenant_id, [str(run.id) for run in runs])
        now = self._clock()
        failed = 0
        for run in runs:
            of_run = jobs.get(str(run.id))
            if (
                of_run is None
                or of_run.live
                or of_run.newest_status is not JobStatus.DEAD
            ):
                continue
            reason = of_run.newest_error or "no error was recorded"
            error = f"{of_run.newest_kind} job dead-lettered: {reason}"
            _fail_run(run, now=now, error=error)
            failed += 1
        return failed

    @staticmethod
    def get_run(session: Session, run_id: UUID) -> WorkflowRunRow:
        """Load a run of the session's tenant.

        A run that does not exist, or belongs to another tenant, which row-level
        security hides, is a ``NotFoundError``.

        Returns:
            The run.

        """
        return _load_run(session, run_id, lock=False)

    def _advance(
        self, session: Session, run_id: UUID, *, max_attempts: int | None
    ) -> None:
        run = _load_run(session, run_id, lock=True)
        if run.status in _FINISHED:
            return
        definition = workflow(run.definition)
        index = definition.index_of(_current_step(run))
        step_id, step = definition.steps[index]
        now = self._clock()
        call = _call_input(session, run, step_id, now)
        if call is None:
            return
        step_input, signal = call
        record = WorkflowStepRow(
            tenant_id=run.tenant_id,
            run_id=run.id,
            step_id=step_id,
            attempt=_next_attempt(session, run.id, step_id),
            started_at=now,
        )
        try:
            # A savepoint, so a step that fails, even with a database error, takes back
            # its own writes and the signal it consumed while the transaction stays
            # usable for the row that records the failure.
            with session.begin_nested():
                if signal is not None:
                    signal.consumed_at = now
                # A copy: SQLAlchemy would see no change if the step edited the dict
                # the run holds and handed it back.
                result = step(session, run, deepcopy(run.state), step_input)
        except Exception as error:
            self._record_failure(session, run, record, error, max_attempts=max_attempts)
            raise
        finished_at = self._clock()
        run.last_error = None  # a call that succeeded ends whatever was failing
        record.status = StepStatus.DONE
        record.output = cast("dict[str, JsonValue]", result.model_dump(mode="json"))
        record.finished_at = finished_at
        session.add(record)
        following = (
            definition.steps[index + 1][0]
            if index + 1 < len(definition.steps)
            else None
        )
        self._move(
            session, run, result, following, timeout=definition.timeout, now=finished_at
        )

    def _record_failure(
        self,
        session: Session,
        run: WorkflowRunRow,
        record: WorkflowStepRow,
        error: Exception,
        *,
        max_attempts: int | None,
    ) -> None:
        # Committed here rather than left to the caller: the job runner rolls the
        # session back when the handler raises, and the failed call has to survive
        # that because its row is what makes the retry the next attempt. The run
        # row is locked, so the row and the run's new state go together.
        now = self._clock()
        summary = safe_exception_summary(error)
        record.status = StepStatus.FAILED
        record.error = summary
        record.finished_at = now
        session.add(record)
        run.last_error = summary
        if _out_of_attempts(session, record, max_attempts):
            _fail_run(run, now=now, error=summary)
        session.commit()

    def _move(
        self,
        session: Session,
        run: WorkflowRunRow,
        result: StepResult,
        following: str | None,
        *,
        timeout: timedelta | None,
        now: datetime,
    ) -> None:
        run.state = result.state
        if result.done:
            _finish(run, now)
        elif result.wait_for_signal is not None or result.wake_at is not None:
            self._park(session, run, result, timeout=timeout, now=now)
        elif following is not None:
            run.status = RunStatus.RUNNING
            run.current_step = following
            run.wake_at = None
            self._enqueue_advance(session, run.id, following, attempt=1)
        else:
            _finish(run, now)

    def _park(
        self,
        session: Session,
        run: WorkflowRunRow,
        result: StepResult,
        *,
        timeout: timedelta | None,
        now: datetime,
    ) -> None:
        step_id = _current_step(run)
        wake_at = result.wake_at
        if wake_at is None and timeout is not None:
            wake_at = now + timeout
        run.status = RunStatus.WAITING
        run.wake_at = None if wake_at is None else wake_at.astimezone(UTC)
        # A signal that came early is already waiting for this step: wake it now.
        if _oldest_signal(session, run.id, result.wait_for_signal) is not None:
            attempt = _next_attempt(session, run.id, step_id)
            self._enqueue_advance(session, run.id, step_id, attempt=attempt)
        if run.wake_at is not None:
            _ = self._queue.enqueue(
                session,
                kind=TIMER_KIND,
                payload={"run_id": str(run.id)},
                run_after=run.wake_at,
                key=f"{run.id}:{step_id}:{run.wake_at}",
            )

    def _enqueue_advance(
        self, session: Session, run_id: UUID, step_id: str, *, attempt: int
    ) -> None:
        _ = self._queue.enqueue(
            session,
            kind=ADVANCE_KIND,
            payload={"run_id": str(run_id)},
            key=f"{run_id}:{step_id}:{attempt}",
        )
