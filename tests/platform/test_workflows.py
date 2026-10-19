"""Step-table workflows: definitions, steps, signals, timers and restarts (spec §9)."""

import logging
import threading
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import psycopg
import pytest
from pydantic import JsonValue, ValidationError
from sqlalchemy import Engine, delete, select, text, update
from sqlalchemy.exc import OperationalError

from tests.helpers import make_tenant, platform_ctx, system_ctx
from tests.platform.jobs_support import (
    T0,
    FakeClock,
    claim,
    enqueue,
    eventually,
    fetch_job,
    fetch_jobs,
    unique_kind,
)
from xolotl.platform.audit.chain import AuditEvent, record
from xolotl.platform.audit.models import AuditEventRow
from xolotl.platform.db import get_sessionmaker, tenant_session
from xolotl.platform.enums import JobStatus, RunStatus, StepStatus
from xolotl.platform.errors import ContextMissingError, DomainError, NotFoundError
from xolotl.platform.jobs import JobRunner, PostgresJobQueue, job_handler
from xolotl.platform.jobs.models import JobRow
from xolotl.platform.jobs.queue import LEASE_EXPIRED
from xolotl.platform.workflows import runner as runner_module
from xolotl.platform.workflows.definition import (
    Step,
    StepInput,
    StepResult,
    WorkflowDefinition,
    register_workflow,
    workflow,
)
from xolotl.platform.workflows.models import (
    WorkflowRunRow,
    WorkflowSignalRow,
    WorkflowStepRow,
)
from xolotl.platform.workflows.runner import (
    ADVANCE_KIND,
    SWEEP_KIND,
    TIMER_KIND,
    PostgresWorkflowRunner,
)

HOUR = timedelta(hours=1)
RUNNER_LOGGER = "xolotl.platform.workflows.runner"

Calls = list[tuple[str | None, ...]]


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def queue(engine: Engine, clock: FakeClock) -> PostgresJobQueue:
    return PostgresJobQueue(get_sessionmaker(), clock_fn=clock)


@pytest.fixture
def workflows(queue: PostgresJobQueue, clock: FakeClock) -> PostgresWorkflowRunner:
    runner = PostgresWorkflowRunner(get_sessionmaker(), queue, clock_fn=clock)
    runner.register_job_handlers()
    return runner


@pytest.fixture
def worker(queue: PostgresJobQueue, workflows: PostgresWorkflowRunner) -> JobRunner:
    return JobRunner(
        queue,
        get_sessionmaker(),
        worker_id="w-1",
        kinds=[ADVANCE_KIND, TIMER_KIND, SWEEP_KIND],
    )


def move_on(session, run, state, step_input):
    return StepResult(state=state)


def approval_workflow(calls: Calls, *, timeout: timedelta | None = HOUR) -> str:
    """Register collect -> approve (waits for the signal ``approve``) -> finish.

    Returns:
        The name the definition is registered under.

    """

    def collect(session, run, state, step_input):
        calls.append(("collect",))
        return StepResult(state={**state, "collected": True})

    def approve(session, run, state, step_input):
        calls.append(("approve", step_input.signal, str(step_input.timed_out)))
        if step_input.timed_out:
            return StepResult(state={**state, "outcome": "timed_out"}, done=True)
        if step_input.signal is None:
            return StepResult(state=state, wait_for_signal="approve")
        return StepResult(state={**state, "approved_by": step_input.payload["by"]})

    def finish(session, run, state, step_input):
        calls.append(("finish",))
        return StepResult(state={**state, "outcome": "approved"}, done=True)

    definition = WorkflowDefinition(
        name=unique_kind("approval"),
        steps=(("collect", collect), ("approve", approve), ("finish", finish)),
        timeout=timeout,
    )
    register_workflow(definition)
    return definition.name


def two_step_workflow(calls: Calls, *, clock: FakeClock | None = None) -> str:
    """Register collect -> finish, where ``finish`` does not say ``done``.

    ``collect`` moves ``clock`` on by five seconds when it is given one.

    Returns:
        The name the definition is registered under.

    """

    def collect(session, run, state, step_input):
        calls.append(("collect",))
        if clock is not None:
            _ = clock.advance(timedelta(seconds=5))
        return StepResult(state={**state, "collected": True})

    def finish(session, run, state, step_input):
        calls.append(("finish",))
        return StepResult(state={**state, "finished": True})

    definition = WorkflowDefinition(
        name=unique_kind("two-step"), steps=(("collect", collect), ("finish", finish))
    )
    register_workflow(definition)
    return definition.name


def begin(
    workflows: PostgresWorkflowRunner,
    tenant_id: UUID,
    name: str,
    *,
    key: str = "case-1",
    input_: dict[str, JsonValue] | None = None,
) -> UUID:
    """Start a run in its own committed transaction.

    Returns:
        The run's id.

    """
    with tenant_session(system_ctx(tenant_id)) as s:
        return workflows.start(
            s, name, business_key=key, input_={} if input_ is None else input_
        )


def fetch_run(tenant_id: UUID, run_id: UUID) -> WorkflowRunRow:
    with tenant_session(system_ctx(tenant_id)) as s:
        return s.scalars(
            select(WorkflowRunRow).where(WorkflowRunRow.id == run_id)
        ).one()


def fetch_runs(tenant_id: UUID) -> list[WorkflowRunRow]:
    with tenant_session(system_ctx(tenant_id)) as s:
        return list(s.scalars(select(WorkflowRunRow)))


def fetch_steps(tenant_id: UUID, run_id: UUID) -> list[WorkflowStepRow]:
    with tenant_session(system_ctx(tenant_id)) as s:
        return list(
            s.scalars(
                select(WorkflowStepRow)
                .where(WorkflowStepRow.run_id == run_id)
                .order_by(WorkflowStepRow.step_id, WorkflowStepRow.attempt)
            )
        )


def send(
    workflows: PostgresWorkflowRunner,
    tenant_id: UUID,
    run_id: UUID,
    name: str = "approve",
    payload: dict[str, JsonValue] | None = None,
) -> None:
    """Send a signal in its own committed transaction."""
    with tenant_session(system_ctx(tenant_id)) as s:
        workflows.signal(s, run_id, name, {} if payload is None else payload)


def fetch_signals(tenant_id: UUID, run_id: UUID) -> list[WorkflowSignalRow]:
    with tenant_session(system_ctx(tenant_id)) as s:
        return list(
            s.scalars(
                select(WorkflowSignalRow)
                .where(WorkflowSignalRow.run_id == run_id)
                .order_by(WorkflowSignalRow.received_at, WorkflowSignalRow.id)
            )
        )


def pending_advances(tenant_id: UUID) -> list[str]:
    """List the keys of the advance jobs still waiting to run.

    Returns:
        The keys, sorted.

    """
    return sorted(
        job.key
        for job in fetch_jobs(tenant_id, kind=ADVANCE_KIND)
        if job.status is JobStatus.PENDING and job.key is not None
    )


def audit_actions(tenant_id: UUID) -> list[str]:
    with tenant_session(system_ctx(tenant_id)) as s:
        return list(s.scalars(select(AuditEventRow.action).order_by(AuditEventRow.seq)))


def lock_waiters(admin: Engine) -> int:
    """Count the database sessions that are waiting for a lock right now.

    Returns:
        How many are.

    """
    with admin.connect() as conn:
        return conn.execute(
            text("""
                SELECT count(*) FROM pg_stat_activity
                WHERE wait_event_type = 'Lock' AND datname = current_database()
            """)
        ).scalar_one()


def workflow_logs(
    caplog: pytest.LogCaptureFixture, level: int
) -> list[logging.LogRecord]:
    """List what the workflow runner logged so far at exactly ``level``.

    Returns:
        The records, oldest first.

    """
    return [
        record
        for record in caplog.records
        if record.name == RUNNER_LOGGER and record.levelno == level
    ]


def workflow_warnings(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return workflow_logs(caplog, logging.WARNING)


def step_log(tenant_id: UUID, run_id: UUID) -> list[tuple[str, int, StepStatus]]:
    """List the run's step rows as (step, attempt, status), by step id then attempt.

    Returns:
        One tuple per row.

    """
    return [
        (row.step_id, row.attempt, row.status) for row in fetch_steps(tenant_id, run_id)
    ]


# --- declaring a workflow -----------------------------------------------------------


def test_a_step_input_starts_empty():
    step_input = StepInput()
    assert (step_input.signal, step_input.payload, step_input.timed_out) == (
        None,
        {},
        False,
    )


def test_step_inputs_do_not_share_their_payload():
    assert StepInput().payload is not StepInput().payload


def test_a_step_result_moves_on_unless_it_says_otherwise():
    result = StepResult(state={"n": 1})
    assert result.state == {"n": 1}
    assert (result.done, result.wait_for_signal, result.wake_at) == (False, None, None)


@pytest.mark.parametrize("waiting", [{"wait_for_signal": "approve"}, {"wake_at": T0}])
def test_a_step_that_finishes_the_run_cannot_also_wait(waiting):
    with pytest.raises(ValidationError, match="cannot also wait"):
        _ = StepResult(state={}, done=True, **waiting)


def test_a_wake_time_needs_a_time_zone():
    naive = datetime.fromisoformat("2026-10-19T13:00:00")
    with pytest.raises(ValidationError, match="timezone"):
        _ = StepResult(state={}, wake_at=naive)


def test_a_definition_keeps_its_steps_in_order():
    definition = WorkflowDefinition(
        name="onboarding", steps=(("collect", move_on), ("review", move_on))
    )
    assert [step_id for step_id, _ in definition.steps] == ["collect", "review"]
    assert definition.timeout is None


def test_a_definition_needs_a_step():
    with pytest.raises(ValueError, match="at least one step"):
        _ = WorkflowDefinition(name="empty", steps=())


def test_step_ids_are_unique_within_a_definition():
    with pytest.raises(ValueError, match="duplicate step id 'collect'"):
        _ = WorkflowDefinition(
            name="twice", steps=(("collect", move_on), ("collect", move_on))
        )


def test_a_registered_definition_is_found_by_name():
    definition = WorkflowDefinition(
        name=unique_kind("wf"),
        steps=(("only", move_on),),
        timeout=timedelta(hours=1),
    )
    register_workflow(definition)
    assert workflow(definition.name) is definition


def test_an_unknown_workflow_is_a_key_error_naming_it():
    with pytest.raises(KeyError, match=r"never\.registered"):
        _ = workflow("never.registered")


def test_registering_the_same_definition_again_changes_nothing():
    # Composition runs once per app instance, and a test builds many.
    name = unique_kind("wf")
    first = WorkflowDefinition(name=name, steps=(("a", move_on),))
    register_workflow(first)
    register_workflow(first)
    register_workflow(WorkflowDefinition(name=name, steps=(("a", move_on),)))  # equal
    assert workflow(name) is first


def test_a_different_definition_cannot_take_a_registered_name():
    # Two modules that pick the same name would otherwise get whichever registered
    # last, and runs already started under the other would hit the wrong steps.
    name = unique_kind("wf")
    first = WorkflowDefinition(name=name, steps=(("a", move_on),))
    register_workflow(first)
    for other in (
        WorkflowDefinition(name=name, steps=(("b", move_on),)),
        WorkflowDefinition(name=name, steps=(("a", move_on),), timeout=HOUR),
    ):
        with pytest.raises(ValueError, match="already registered"):
            register_workflow(other)
    assert workflow(name) is first


def test_a_definition_finds_a_step_by_id_and_refuses_an_unknown_one():
    definition = WorkflowDefinition(
        name="onboarding", steps=(("collect", move_on), ("review", move_on))
    )
    assert definition.index_of("review") == 1
    with pytest.raises(LookupError, match="has no step 'ghost'"):
        _ = definition.index_of("ghost")


# --- starting a run -----------------------------------------------------------------


def test_the_runner_registers_its_job_handlers(workflows: PostgresWorkflowRunner):
    assert job_handler(ADVANCE_KIND) == workflows.handle_job
    assert job_handler(TIMER_KIND) == workflows.handle_job
    assert job_handler(SWEEP_KIND) == workflows.handle_sweep


def test_start_inserts_a_running_run_and_queues_its_first_step(
    workflows: PostgresWorkflowRunner,
):
    tenant_id = make_tenant("alpha")
    name = approval_workflow([])
    run_id = begin(workflows, tenant_id, name, input_={"case": "c-1", "n": [1, 2]})

    run = fetch_run(tenant_id, run_id)
    assert (run.definition, run.business_key) == (name, "case-1")
    assert run.status is RunStatus.RUNNING
    assert run.current_step == "collect"
    assert run.state == {"case": "c-1", "n": [1, 2]}
    assert (run.wake_at, run.finished_at, run.last_error) == (None, None, None)
    assert run.started_at == T0

    (job,) = fetch_jobs(tenant_id, kind=ADVANCE_KIND)
    assert job.status is JobStatus.PENDING
    assert job.payload == {"run_id": str(run_id)}
    assert job.key == f"{run_id}:collect:1"
    assert job.run_after == T0


def test_a_business_key_is_used_once_per_definition(
    workflows: PostgresWorkflowRunner,
):
    tenant_id = make_tenant("alpha")
    name = approval_workflow([])
    _ = begin(workflows, tenant_id, name, key="case-1")
    with pytest.raises(DomainError) as refused:
        _ = begin(workflows, tenant_id, name, key="case-1")
    assert refused.value.code == "CONFLICT"
    assert "case-1" not in refused.value.detail  # keys may be identifiers of people
    assert len(fetch_runs(tenant_id)) == 1
    assert len(fetch_jobs(tenant_id, kind=ADVANCE_KIND)) == 1  # the refusal queued none


def test_a_business_key_is_free_under_another_definition_or_tenant(
    workflows: PostgresWorkflowRunner,
):
    alpha, beta = make_tenant("alpha"), make_tenant("beta")
    first, second = approval_workflow([]), approval_workflow([])
    runs = {
        begin(workflows, alpha, first, key="case-1"),
        begin(workflows, alpha, second, key="case-1"),
        begin(workflows, beta, first, key="case-1"),
    }
    assert len(runs) == 3


def test_start_joins_the_callers_transaction(workflows: PostgresWorkflowRunner):
    tenant_id = make_tenant("alpha")
    name = approval_workflow([])
    msg = "abort"
    with (
        pytest.raises(RuntimeError, match=msg),
        tenant_session(system_ctx(tenant_id)) as s,
    ):
        _ = workflows.start(s, name, business_key="case-1", input_={})
        raise RuntimeError(msg)
    assert fetch_runs(tenant_id) == []
    assert fetch_jobs(tenant_id) == []


def test_starting_an_unregistered_workflow_is_a_key_error(
    workflows: PostgresWorkflowRunner,
):
    tenant_id = make_tenant("alpha")
    with pytest.raises(KeyError, match=r"never\.registered"):
        _ = begin(workflows, tenant_id, "never.registered")


def test_start_needs_a_session_bound_to_a_tenant(workflows: PostgresWorkflowRunner):
    name = approval_workflow([])
    with tenant_session(platform_ctx()) as s, pytest.raises(ContextMissingError):
        _ = workflows.start(s, name, business_key="case-1", input_={})


def test_get_run_returns_the_run(workflows: PostgresWorkflowRunner):
    tenant_id = make_tenant("alpha")
    run_id = begin(workflows, tenant_id, approval_workflow([]), input_={"k": "v"})
    with tenant_session(system_ctx(tenant_id)) as s:
        run = workflows.get_run(s, run_id)
        assert (run.id, run.tenant_id, run.state) == (run_id, tenant_id, {"k": "v"})


def test_get_run_cannot_see_another_tenants_run(workflows: PostgresWorkflowRunner):
    alpha, beta = make_tenant("alpha"), make_tenant("beta")
    run_id = begin(workflows, alpha, approval_workflow([]))
    with tenant_session(system_ctx(beta)) as s, pytest.raises(NotFoundError):
        _ = workflows.get_run(s, run_id)
    with tenant_session(system_ctx(alpha)) as s, pytest.raises(NotFoundError):
        _ = workflows.get_run(s, UUID(int=1))  # no such run at all


# --- running steps -----------------------------------------------------------------


def test_a_two_step_run_completes_with_done_and_two_step_rows(
    workflows: PostgresWorkflowRunner, worker: JobRunner
):
    tenant_id = make_tenant("alpha")
    calls: Calls = []
    name = two_step_workflow(calls)
    run_id = begin(workflows, tenant_id, name, input_={"case": "c-1"})

    assert worker.run_once() == 2  # one advance job per step
    run = fetch_run(tenant_id, run_id)
    assert run.status is RunStatus.DONE
    assert run.current_step is None
    assert run.state == {"case": "c-1", "collected": True, "finished": True}
    assert (run.finished_at, run.wake_at, run.last_error) == (T0, None, None)
    assert step_log(tenant_id, run_id) == [
        ("collect", 1, StepStatus.DONE),
        ("finish", 1, StepStatus.DONE),
    ]
    assert calls == [("collect",), ("finish",)]
    assert {job.status for job in fetch_jobs(tenant_id)} == {JobStatus.DONE}
    assert worker.run_once() == 0


def test_a_step_row_records_what_the_step_returned_and_when(
    workflows: PostgresWorkflowRunner, clock: FakeClock
):
    tenant_id = make_tenant("alpha")
    name = two_step_workflow([], clock=clock)
    run_id = begin(workflows, tenant_id, name, input_={"case": "c-1"})

    workflows.advance(run_id, tenant_id)
    (row,) = fetch_steps(tenant_id, run_id)
    assert row.status is StepStatus.DONE
    assert row.error is None
    assert row.output == {
        "state": {"case": "c-1", "collected": True},
        "done": False,
        "wait_for_signal": None,
        "wake_at": None,
    }
    # started before the step ran, finished after it did
    assert (row.started_at, row.finished_at) == (T0, T0 + timedelta(seconds=5))


def test_each_advance_runs_one_step_and_queues_the_next(
    workflows: PostgresWorkflowRunner,
):
    tenant_id = make_tenant("alpha")
    calls: Calls = []
    run_id = begin(workflows, tenant_id, two_step_workflow(calls))

    workflows.advance(run_id, tenant_id)
    run = fetch_run(tenant_id, run_id)
    assert (run.status, run.current_step) == (RunStatus.RUNNING, "finish")
    assert calls == [("collect",)]
    queued = {job.key: job for job in fetch_jobs(tenant_id, kind=ADVANCE_KIND)}
    assert set(queued) == {f"{run_id}:collect:1", f"{run_id}:finish:1"}
    next_job = queued[f"{run_id}:finish:1"]
    assert next_job.payload == {"run_id": str(run_id)}
    assert (next_job.status, next_job.run_after) == (JobStatus.PENDING, T0)

    workflows.advance(run_id, tenant_id)
    assert fetch_run(tenant_id, run_id).status is RunStatus.DONE
    assert calls == [("collect",), ("finish",)]


def test_advancing_a_finished_run_does_nothing(workflows: PostgresWorkflowRunner):
    tenant_id = make_tenant("alpha")
    calls: Calls = []
    run_id = begin(workflows, tenant_id, two_step_workflow(calls))
    workflows.advance(run_id, tenant_id)
    workflows.advance(run_id, tenant_id)
    finished = fetch_run(tenant_id, run_id)
    jobs_before = len(fetch_jobs(tenant_id))

    workflows.advance(run_id, tenant_id)

    assert calls == [("collect",), ("finish",)]
    assert len(fetch_steps(tenant_id, run_id)) == 2
    assert len(fetch_jobs(tenant_id)) == jobs_before
    assert fetch_run(tenant_id, run_id).finished_at == finished.finished_at


def test_a_step_that_says_done_ends_the_run_without_the_steps_after_it(
    workflows: PostgresWorkflowRunner, worker: JobRunner
):
    tenant_id = make_tenant("alpha")
    calls: Calls = []

    def decide(session, run, state, step_input):
        calls.append(("decide",))
        return StepResult(state={**state, "outcome": "rejected"}, done=True)

    def never(session, run, state, step_input):
        calls.append(("never",))
        return StepResult(state=state)

    name = unique_kind("early")
    register_workflow(
        WorkflowDefinition(name=name, steps=(("decide", decide), ("never", never)))
    )
    run_id = begin(workflows, tenant_id, name)

    assert worker.run_once() == 1
    run = fetch_run(tenant_id, run_id)
    assert (run.status, run.current_step) == (RunStatus.DONE, None)
    assert run.state == {"outcome": "rejected"}
    assert step_log(tenant_id, run_id) == [("decide", 1, StepStatus.DONE)]
    assert calls == [("decide",)]


def test_a_step_receives_the_run_its_state_and_an_empty_input(
    workflows: PostgresWorkflowRunner,
):
    tenant_id = make_tenant("alpha")
    seen: list[tuple[object, ...]] = []

    def inspect(session, run, state, step_input):
        seen.append((
            run.id,
            run.tenant_id,
            run.business_key,
            state,
            step_input,
            session,
        ))
        return StepResult(state=state)

    name = unique_kind("inspect")
    register_workflow(WorkflowDefinition(name=name, steps=(("inspect", inspect),)))
    run_id = begin(workflows, tenant_id, name, key="case-7", input_={"n": [1]})

    workflows.advance(run_id, tenant_id)
    ((run_seen, tenant_seen, key_seen, state_seen, input_seen, session_seen),) = seen
    assert (run_seen, tenant_seen, key_seen) == (run_id, tenant_id, "case-7")
    assert state_seen == {"n": [1]}
    assert input_seen == StepInput()
    assert session_seen is not None


def test_a_step_that_edits_its_state_in_place_still_has_it_saved(
    workflows: PostgresWorkflowRunner,
):
    # SQLAlchemy would see no change if the step edited the very object the run
    # holds and handed it back, so the step gets a copy.
    tenant_id = make_tenant("alpha")

    def edits(session, run, state, step_input):
        state["edited"] = True
        return StepResult(state=state)

    name = unique_kind("in-place")
    register_workflow(WorkflowDefinition(name=name, steps=(("edits", edits),)))
    run_id = begin(workflows, tenant_id, name, input_={"kept": 1})

    workflows.advance(run_id, tenant_id)
    assert fetch_run(tenant_id, run_id).state == {"kept": 1, "edited": True}


def test_advance_cannot_reach_another_tenants_run(workflows: PostgresWorkflowRunner):
    alpha, beta = make_tenant("alpha"), make_tenant("beta")
    calls: Calls = []
    run_id = begin(workflows, alpha, two_step_workflow(calls))

    with pytest.raises(NotFoundError):
        workflows.advance(run_id, beta)
    assert calls == []
    assert fetch_run(alpha, run_id).current_step == "collect"
    assert fetch_steps(alpha, run_id) == []


# --- waiting for signals and for time ------------------------------------------------


def park(
    workflows: PostgresWorkflowRunner,
    worker: JobRunner,
    tenant_id: UUID,
    calls: Calls,
    *,
    timeout: timedelta | None = HOUR,
) -> UUID:
    """Start an approval run and take it to the point where it waits for ``approve``.

    Returns:
        The run's id.

    """
    run_id = begin(workflows, tenant_id, approval_workflow(calls, timeout=timeout))
    assert worker.run_once() == 2  # collect, then the first call of approve
    assert fetch_run(tenant_id, run_id).status is RunStatus.WAITING
    return run_id


def test_a_waiting_step_parks_the_run_with_a_wake_time_and_a_timer(
    workflows: PostgresWorkflowRunner, worker: JobRunner
):
    tenant_id = make_tenant("alpha")
    calls: Calls = []
    run_id = park(workflows, worker, tenant_id, calls)

    run = fetch_run(tenant_id, run_id)
    assert (run.status, run.current_step) == (RunStatus.WAITING, "approve")
    assert run.wake_at == T0 + HOUR  # the definition's timeout
    assert run.state == {"collected": True}
    assert (run.finished_at, run.last_error) == (None, None)
    assert calls == [("collect",), ("approve", None, "False")]
    assert step_log(tenant_id, run_id) == [
        ("approve", 1, StepStatus.DONE),
        ("collect", 1, StepStatus.DONE),
    ]
    waiting_call = fetch_steps(tenant_id, run_id)[0]
    assert waiting_call.output == {
        "state": {"collected": True},
        "done": False,
        "wait_for_signal": "approve",
        "wake_at": None,
    }
    (timer,) = fetch_jobs(tenant_id, kind=TIMER_KIND)
    assert timer.key == f"{run_id}:approve:{T0 + HOUR}"
    assert timer.payload == {"run_id": str(run_id)}
    assert (timer.status, timer.run_after) == (JobStatus.PENDING, T0 + HOUR)
    assert worker.run_once() == 0  # nothing is due


def test_a_signal_wakes_the_waiting_step(
    workflows: PostgresWorkflowRunner, worker: JobRunner
):
    tenant_id = make_tenant("alpha")
    calls: Calls = []
    run_id = park(workflows, worker, tenant_id, calls)

    send(workflows, tenant_id, run_id, "approve", {"by": "ana"})
    assert pending_advances(tenant_id) == [f"{run_id}:approve:2"]
    assert worker.run_once() == 2  # approve with the signal, then finish

    run = fetch_run(tenant_id, run_id)
    assert run.status is RunStatus.DONE
    assert run.state == {
        "collected": True,
        "approved_by": "ana",
        "outcome": "approved",
    }
    assert run.wake_at is None
    assert calls[2:] == [("approve", "approve", "False"), ("finish",)]
    assert step_log(tenant_id, run_id) == [
        ("approve", 1, StepStatus.DONE),
        ("approve", 2, StepStatus.DONE),
        ("collect", 1, StepStatus.DONE),
        ("finish", 1, StepStatus.DONE),
    ]
    (signal,) = fetch_signals(tenant_id, run_id)
    assert (signal.name, signal.payload) == ("approve", {"by": "ana"})
    assert (signal.received_at, signal.consumed_at) == (T0, T0)


def test_a_duplicate_signal_is_dropped_while_the_first_one_is_pending(
    workflows: PostgresWorkflowRunner,
    worker: JobRunner,
    caplog: pytest.LogCaptureFixture,
):
    tenant_id = make_tenant("alpha")
    caplog.set_level(logging.INFO, logger=RUNNER_LOGGER)
    calls: Calls = []
    run_id = park(workflows, worker, tenant_id, calls)

    send(workflows, tenant_id, run_id, "approve", {"by": "ana"})
    assert not workflow_logs(caplog, logging.INFO)
    send(workflows, tenant_id, run_id, "approve", {"by": "ben"})
    (dropped,) = workflow_logs(caplog, logging.INFO)  # no payload in the log
    assert dropped.getMessage() == (
        "workflow signal dropped: one of that name is already pending"
    )
    assert dropped.__dict__["workflow_run_id"] == str(run_id)
    assert dropped.__dict__["workflow_signal"] == "approve"
    assert pending_advances(tenant_id) == [f"{run_id}:approve:2"]  # one wake-up
    # The first signal of a name is the one that counts: the second is not stored.
    assert [s.payload["by"] for s in fetch_signals(tenant_id, run_id)] == ["ana"]
    assert worker.run_once() == 2
    assert fetch_run(tenant_id, run_id).state["approved_by"] == "ana"
    assert [s.consumed_at for s in fetch_signals(tenant_id, run_id)] == [T0]

    # Once the first is consumed, a signal of that name is a new signal again. This
    # one comes after the run is over, so it is stored and nothing is queued for it.
    before = (list(calls), step_log(tenant_id, run_id))
    workflows.advance(run_id, tenant_id)
    send(workflows, tenant_id, run_id, "approve", {"by": "cy"})
    assert pending_advances(tenant_id) == []
    assert worker.run_once() == 0  # nothing is due: the timer's hour has not passed
    assert (calls, step_log(tenant_id, run_id)) == before
    assert [
        (s.payload["by"], s.consumed_at is None)
        for s in fetch_signals(tenant_id, run_id)
    ] == [
        ("ana", False),
        ("cy", True),
    ]


def test_should_two_signals_of_a_name_ever_be_pending_the_oldest_is_taken_first(
    workflows: PostgresWorkflowRunner, worker: JobRunner
):
    # signal() keeps one pending per name, so it cannot happen through it. The lookup
    # still takes the oldest, so rows written another way are not read in an
    # arbitrary order.
    tenant_id = make_tenant("alpha")
    run_id = park(workflows, worker, tenant_id, [])
    with tenant_session(system_ctx(tenant_id)) as s:
        for who, received in (("late", T0 + timedelta(seconds=1)), ("early", T0)):
            s.add(
                WorkflowSignalRow(
                    tenant_id=tenant_id,
                    run_id=run_id,
                    name="approve",
                    payload={"by": who},
                    received_at=received,
                )
            )
    workflows.advance(run_id, tenant_id)
    assert fetch_run(tenant_id, run_id).state["approved_by"] == "early"


def two_level_workflow() -> str:
    """Register l1 -> l2, each waiting for its own ``approve`` (four eyes).

    Returns:
        The name the definition is registered under.

    """

    def level(level_id: str, *, last: bool) -> Step:
        def step(session, run, state, step_input):
            if step_input.signal is None:
                return StepResult(state=state, wait_for_signal="approve")
            return StepResult(
                state={**state, level_id: step_input.payload["by"]}, done=last
            )

        return step

    name = unique_kind("four-eyes")
    register_workflow(
        WorkflowDefinition(
            name=name,
            steps=(("l1", level("l1", last=False)), ("l2", level("l2", last=True))),
        )
    )
    return name


def test_a_double_click_cannot_approve_two_levels(
    workflows: PostgresWorkflowRunner, worker: JobRunner
):
    tenant_id = make_tenant("alpha")
    run_id = begin(workflows, tenant_id, two_level_workflow())
    assert worker.run_once() == 1  # level 1 waits for an approval

    send(workflows, tenant_id, run_id, "approve", {"by": "ana"})
    send(workflows, tenant_id, run_id, "approve", {"by": "ana"})  # the double click
    assert worker.run_once() == 2  # level 1 takes it, level 2 starts waiting
    run = fetch_run(tenant_id, run_id)
    assert (run.status, run.current_step) == (RunStatus.WAITING, "l2")
    assert run.state == {"l1": "ana"}  # nobody has approved level 2

    send(workflows, tenant_id, run_id, "approve", {"by": "ben"})
    assert worker.run_once() == 1
    run = fetch_run(tenant_id, run_id)
    assert (run.status, run.state) == (RunStatus.DONE, {"l1": "ana", "l2": "ben"})
    assert [
        (s.payload["by"], s.consumed_at is not None)
        for s in fetch_signals(tenant_id, run_id)
    ] == [
        ("ana", True),
        ("ben", True),
    ]


def test_a_double_click_before_the_run_reaches_the_wait_counts_once(
    workflows: PostgresWorkflowRunner, worker: JobRunner
):
    tenant_id = make_tenant("alpha")
    run_id = begin(workflows, tenant_id, two_level_workflow())

    send(workflows, tenant_id, run_id, "approve", {"by": "ana"})  # early
    send(workflows, tenant_id, run_id, "approve", {"by": "ana"})  # early, again
    assert worker.run_once() == 3  # l1 parks, l1 takes the signal, l2 parks
    run = fetch_run(tenant_id, run_id)
    assert (run.status, run.current_step) == (RunStatus.WAITING, "l2")
    assert len(fetch_signals(tenant_id, run_id)) == 1


def test_signals_with_different_names_are_all_kept(
    workflows: PostgresWorkflowRunner, worker: JobRunner
):
    tenant_id = make_tenant("alpha")
    run_id = park(workflows, worker, tenant_id, [])

    send(workflows, tenant_id, run_id, "approve", {"by": "ana"})
    send(workflows, tenant_id, run_id, "reject", {"by": "ben"})
    assert [(s.name, s.payload["by"]) for s in fetch_signals(tenant_id, run_id)] == [
        ("approve", "ana"),
        ("reject", "ben"),
    ]


def test_a_signal_the_run_does_not_wait_for_is_stored_and_ignored(
    workflows: PostgresWorkflowRunner, worker: JobRunner
):
    tenant_id = make_tenant("alpha")
    calls: Calls = []
    run_id = park(workflows, worker, tenant_id, calls)

    send(workflows, tenant_id, run_id, "rumour", {"by": "ana"})
    assert pending_advances(tenant_id) == []
    assert worker.run_once() == 0
    workflows.advance(run_id, tenant_id)
    assert calls == [("collect",), ("approve", None, "False")]
    assert fetch_run(tenant_id, run_id).status is RunStatus.WAITING
    (signal,) = fetch_signals(tenant_id, run_id)
    assert signal.consumed_at is None


def test_an_early_signal_is_kept_until_the_run_reaches_the_wait(
    workflows: PostgresWorkflowRunner, worker: JobRunner
):
    tenant_id = make_tenant("alpha")
    calls: Calls = []
    run_id = begin(workflows, tenant_id, approval_workflow(calls))

    send(workflows, tenant_id, run_id, "approve", {"by": "ana"})  # still at collect
    assert pending_advances(tenant_id) == [f"{run_id}:collect:1"]  # it queued nothing

    # collect, approve (which waits and finds the signal), approve again, finish
    assert worker.run_once() == 4
    run = fetch_run(tenant_id, run_id)
    assert run.status is RunStatus.DONE
    assert run.state["approved_by"] == "ana"
    assert calls == [
        ("collect",),
        ("approve", None, "False"),
        ("approve", "approve", "False"),
        ("finish",),
    ]
    (signal,) = fetch_signals(tenant_id, run_id)
    assert signal.consumed_at == T0


def test_the_timer_wakes_the_run_with_timed_out_set(
    workflows: PostgresWorkflowRunner, worker: JobRunner, clock: FakeClock
):
    tenant_id = make_tenant("alpha")
    calls: Calls = []
    run_id = park(workflows, worker, tenant_id, calls)

    _ = clock.advance(HOUR - timedelta(seconds=1))
    assert worker.run_once() == 0
    _ = clock.advance(timedelta(seconds=1))
    assert worker.run_once() == 1  # the timer job

    run = fetch_run(tenant_id, run_id)
    assert run.status is RunStatus.DONE
    assert run.state == {"collected": True, "outcome": "timed_out"}
    assert (run.finished_at, run.wake_at) == (T0 + HOUR, None)
    assert calls[-1] == ("approve", None, "True")
    assert step_log(tenant_id, run_id) == [
        ("approve", 1, StepStatus.DONE),
        ("approve", 2, StepStatus.DONE),
        ("collect", 1, StepStatus.DONE),
    ]
    (timer,) = fetch_jobs(tenant_id, kind=TIMER_KIND)
    assert timer.status is JobStatus.DONE


def test_a_step_that_fails_a_timed_out_run_is_retried_until_the_run_fails(
    workflows: PostgresWorkflowRunner, worker: JobRunner, clock: FakeClock
):
    tenant_id = make_tenant("alpha")

    def approve(session, run, state, step_input):
        if step_input.timed_out:
            msg = "nobody approved in time"
            raise TimeoutError(msg)
        return StepResult(state=state, wait_for_signal="approve")

    name = unique_kind("strict")
    register_workflow(
        WorkflowDefinition(name=name, steps=(("approve", approve),), timeout=HOUR)
    )
    run_id = begin(workflows, tenant_id, name)
    assert worker.run_once() == 1  # parks the run

    for _attempt in range(8):  # the timer job's attempts
        _ = clock.advance(HOUR)
        assert worker.run_once() == 1
    run = fetch_run(tenant_id, run_id)
    assert run.status is RunStatus.FAILED
    assert run.last_error == "TimeoutError: nobody approved in time"
    assert step_log(tenant_id, run_id) == [("approve", 1, StepStatus.DONE)] + [
        ("approve", attempt, StepStatus.FAILED) for attempt in range(2, 10)
    ]
    (timer,) = fetch_jobs(tenant_id, kind=TIMER_KIND)
    assert (timer.status, timer.attempts) == (JobStatus.DEAD, 8)


def test_the_definition_timeout_counts_from_the_moment_the_step_returns(
    workflows: PostgresWorkflowRunner, worker: JobRunner, clock: FakeClock
):
    tenant_id = make_tenant("alpha")

    def slow_to_park(session, run, state, step_input):
        _ = clock.advance(timedelta(seconds=5))
        return StepResult(state=state, wait_for_signal="go")

    name = unique_kind("slow-park")
    register_workflow(
        WorkflowDefinition(name=name, steps=(("park", slow_to_park),), timeout=HOUR)
    )
    run_id = begin(workflows, tenant_id, name)
    assert worker.run_once() == 1
    assert fetch_run(tenant_id, run_id).wake_at == T0 + timedelta(seconds=5) + HOUR


def test_a_timer_that_fires_after_the_run_moved_on_changes_nothing(
    workflows: PostgresWorkflowRunner, worker: JobRunner, clock: FakeClock
):
    tenant_id = make_tenant("alpha")
    calls: Calls = []
    run_id = park(workflows, worker, tenant_id, calls)
    send(workflows, tenant_id, run_id, "approve", {"by": "ana"})
    assert worker.run_once() == 2
    before = (list(calls), step_log(tenant_id, run_id))

    _ = clock.advance(HOUR)
    assert worker.run_once() == 1  # the leftover timer finds nothing to do
    assert (calls, step_log(tenant_id, run_id)) == before
    assert fetch_run(tenant_id, run_id).status is RunStatus.DONE


def test_a_stale_timer_does_not_time_out_a_later_wait(
    workflows: PostgresWorkflowRunner, worker: JobRunner, clock: FakeClock
):
    tenant_id = make_tenant("alpha")
    calls: Calls = []

    def first(session, run, state, step_input):
        calls.append(("first", step_input.signal, str(step_input.timed_out)))
        if step_input.signal is None and not step_input.timed_out:
            return StepResult(state=state, wait_for_signal="a", wake_at=T0 + HOUR)
        return StepResult(state={**state, "first": "done"})

    def second(session, run, state, step_input):
        calls.append(("second", step_input.signal, str(step_input.timed_out)))
        if step_input.signal is None and not step_input.timed_out:
            return StepResult(state=state, wait_for_signal="b", wake_at=T0 + 3 * HOUR)
        outcome = "timed_out" if step_input.timed_out else "signalled"
        return StepResult(state={**state, "second": outcome}, done=True)

    name = unique_kind("two-waits")
    register_workflow(
        WorkflowDefinition(name=name, steps=(("first", first), ("second", second)))
    )
    run_id = begin(workflows, tenant_id, name)
    assert worker.run_once() == 1  # first waits for "a" until T0 + 1 hour
    send(workflows, tenant_id, run_id, "a")
    assert worker.run_once() == 2  # first takes the signal, second waits for "b"

    # The timer set for "first" fires, but the run waits somewhere else now.
    _ = clock.advance(HOUR)
    assert worker.run_once() == 1
    assert fetch_run(tenant_id, run_id).status is RunStatus.WAITING
    assert calls[-1] == ("second", None, "False")

    _ = clock.advance(2 * HOUR)  # second's own timer
    assert worker.run_once() == 1
    run = fetch_run(tenant_id, run_id)
    assert run.status is RunStatus.DONE
    assert run.state == {"first": "done", "second": "timed_out"}
    assert calls[-1] == ("second", None, "True")


def test_a_signal_beats_a_timeout_that_is_due_at_the_same_time(
    workflows: PostgresWorkflowRunner, worker: JobRunner, clock: FakeClock
):
    tenant_id = make_tenant("alpha")
    calls: Calls = []
    run_id = park(workflows, worker, tenant_id, calls)

    _ = clock.advance(2 * HOUR)  # the timer is due ...
    send(workflows, tenant_id, run_id, "approve", {"by": "ana"})  # ... and so is this
    _ = worker.run_once()  # the timer job is older, so it is claimed first

    assert ("approve", None, "True") not in calls
    assert ("approve", "approve", "False") in calls
    run = fetch_run(tenant_id, run_id)
    assert run.status is RunStatus.DONE
    assert run.state["approved_by"] == "ana"


def test_a_step_level_wake_time_beats_the_definition_timeout(
    workflows: PostgresWorkflowRunner, worker: JobRunner
):
    tenant_id = make_tenant("alpha")
    soon = T0 + timedelta(minutes=10)

    def wait_a_bit(session, run, state, step_input):
        return StepResult(state=state, wait_for_signal="go", wake_at=soon)

    name = unique_kind("soon")
    register_workflow(
        WorkflowDefinition(name=name, steps=(("wait", wait_a_bit),), timeout=HOUR)
    )
    run_id = begin(workflows, tenant_id, name)
    assert worker.run_once() == 1
    assert fetch_run(tenant_id, run_id).wake_at == soon
    (timer,) = fetch_jobs(tenant_id, kind=TIMER_KIND)
    assert (timer.key, timer.run_after) == (f"{run_id}:wait:{soon}", soon)


def test_a_wake_time_in_another_time_zone_is_the_same_instant_and_the_same_key(
    workflows: PostgresWorkflowRunner, worker: JobRunner
):
    tenant_id = make_tenant("alpha")
    mexico = timezone(timedelta(hours=-6))
    local = (T0 + HOUR).astimezone(mexico)

    def wait_a_bit(session, run, state, step_input):
        return StepResult(state=state, wake_at=local)

    name = unique_kind("zone")
    register_workflow(WorkflowDefinition(name=name, steps=(("wait", wait_a_bit),)))
    run_id = begin(workflows, tenant_id, name)
    assert worker.run_once() == 1
    assert fetch_run(tenant_id, run_id).wake_at == T0 + HOUR
    (timer,) = fetch_jobs(tenant_id, kind=TIMER_KIND)
    assert timer.key == f"{run_id}:wait:{T0 + HOUR}"  # always spelled in UTC


def test_without_a_timeout_a_signal_wait_has_no_wake_time_and_no_timer(
    workflows: PostgresWorkflowRunner, worker: JobRunner, clock: FakeClock
):
    tenant_id = make_tenant("alpha")
    run_id = park(workflows, worker, tenant_id, [], timeout=None)
    assert fetch_run(tenant_id, run_id).wake_at is None
    assert fetch_jobs(tenant_id, kind=TIMER_KIND) == []
    _ = clock.advance(timedelta(days=365))
    assert worker.run_once() == 0  # it waits for as long as it takes


def test_a_step_can_sleep_until_a_time_without_waiting_for_a_signal(
    workflows: PostgresWorkflowRunner, worker: JobRunner, clock: FakeClock
):
    tenant_id = make_tenant("alpha")
    calls: Calls = []
    wake = T0 + timedelta(minutes=5)

    def sleeper(session, run, state, step_input):
        calls.append(("sleeper", str(step_input.timed_out)))
        if not step_input.timed_out:
            return StepResult(state={**state, "slept": False}, wake_at=wake)
        return StepResult(state={**state, "slept": True})

    def after(session, run, state, step_input):
        calls.append(("after",))
        return StepResult(state=state, done=True)

    name = unique_kind("sleep")
    register_workflow(
        WorkflowDefinition(name=name, steps=(("sleeper", sleeper), ("after", after)))
    )
    run_id = begin(workflows, tenant_id, name)
    assert worker.run_once() == 1
    run = fetch_run(tenant_id, run_id)
    assert (run.status, run.wake_at) == (RunStatus.WAITING, wake)

    send(workflows, tenant_id, run_id, "anything")  # nothing waits for a signal
    assert pending_advances(tenant_id) == []

    _ = clock.advance(timedelta(minutes=5))
    assert worker.run_once() == 2  # the timer, then the step after it
    assert fetch_run(tenant_id, run_id).state == {"slept": True}
    assert calls == [("sleeper", "False"), ("sleeper", "True"), ("after",)]


def test_signal_sees_the_run_as_it_is_now_even_if_the_session_read_it_earlier(
    workflows: PostgresWorkflowRunner, worker: JobRunner
):
    tenant_id = make_tenant("alpha")
    run_id = begin(workflows, tenant_id, approval_workflow([]))
    with tenant_session(system_ctx(tenant_id)) as s:
        read_earlier = workflows.get_run(s, run_id)  # the session now holds the run
        assert read_earlier.status is RunStatus.RUNNING
        # Meanwhile, in other transactions, the run reaches its wait.
        assert worker.run_once() == 2
        workflows.signal(s, run_id, "approve", {"by": "ana"})
        assert read_earlier.status is RunStatus.WAITING  # refreshed under the lock
    assert pending_advances(tenant_id) == [f"{run_id}:approve:2"]


def test_signal_cannot_reach_another_tenants_run(workflows: PostgresWorkflowRunner):
    alpha, beta = make_tenant("alpha"), make_tenant("beta")
    run_id = begin(workflows, alpha, approval_workflow([]))
    with pytest.raises(NotFoundError):
        send(workflows, beta, run_id)
    assert fetch_signals(alpha, run_id) == []


def test_signal_to_an_unknown_run_is_not_found(workflows: PostgresWorkflowRunner):
    tenant_id = make_tenant("alpha")
    with pytest.raises(NotFoundError):
        send(workflows, tenant_id, UUID(int=1))


# --- failing steps --------------------------------------------------------------------


def single(step_id: str, step: Step) -> str:
    """Register a one-step workflow.

    Returns:
        The name the definition is registered under.

    """
    name = unique_kind(step_id)
    register_workflow(WorkflowDefinition(name=name, steps=((step_id, step),)))
    return name


def test_a_step_that_raises_is_recorded_failed_and_retried_as_the_next_attempt(
    workflows: PostgresWorkflowRunner,
    worker: JobRunner,
    queue: PostgresJobQueue,
    clock: FakeClock,
):
    tenant_id = make_tenant("alpha")
    calls: Calls = []

    def flaky(session, run, state, step_input):
        calls.append(("flaky",))
        _ = queue.enqueue(session, kind="demo.effect", payload={"call": len(calls)})
        if len(calls) == 1:
            msg = "vendor unavailable"
            raise RuntimeError(msg)
        return StepResult(state={**state, "ok": True})

    run_id = begin(workflows, tenant_id, single("flaky", flaky))

    assert worker.run_once() == 1
    (failed,) = fetch_steps(tenant_id, run_id)
    assert (failed.step_id, failed.attempt) == ("flaky", 1)
    assert failed.status is StepStatus.FAILED
    assert failed.error == "RuntimeError: vendor unavailable"
    assert failed.output is None
    assert (failed.started_at, failed.finished_at) == (T0, T0)
    retrying = fetch_run(tenant_id, run_id)
    assert retrying.status is RunStatus.RUNNING  # not over yet
    assert retrying.last_error == "RuntimeError: vendor unavailable"  # but it says why
    assert fetch_jobs(tenant_id, kind="demo.effect") == []  # its writes went with it
    (job,) = fetch_jobs(tenant_id, kind=ADVANCE_KIND)
    assert job.status is JobStatus.PENDING
    assert job.run_after == T0 + timedelta(minutes=2)

    assert worker.run_once() == 0  # backing off
    _ = clock.advance(timedelta(minutes=2))
    assert worker.run_once() == 1
    assert step_log(tenant_id, run_id) == [
        ("flaky", 1, StepStatus.FAILED),
        ("flaky", 2, StepStatus.DONE),
    ]
    done = fetch_run(tenant_id, run_id)
    assert (done.status, done.last_error) == (RunStatus.DONE, None)  # success clears it
    (effect,) = fetch_jobs(tenant_id, kind="demo.effect")
    assert effect.payload == {"call": 2}


def test_a_step_that_breaks_the_transaction_still_has_its_failure_recorded(
    workflows: PostgresWorkflowRunner, worker: JobRunner
):
    tenant_id = make_tenant("alpha")

    def breaks(session, run, state, step_input):
        _ = session.execute(text("SELECT CAST('ana@example.mx' AS uuid)"))
        return StepResult(state=state)

    run_id = begin(workflows, tenant_id, single("breaks", breaks))
    assert worker.run_once() == 1
    (failed,) = fetch_steps(tenant_id, run_id)
    assert failed.status is StepStatus.FAILED
    # the driver's message would echo the value it choked on
    assert failed.error == "DataError [sqlstate=22P02]"


def test_a_signal_is_not_lost_when_the_step_that_took_it_fails(
    workflows: PostgresWorkflowRunner, worker: JobRunner, clock: FakeClock
):
    tenant_id = make_tenant("alpha")
    calls: list[str | None] = []

    def review(session, run, state, step_input):
        calls.append(step_input.signal)
        if step_input.signal is None:
            return StepResult(state=state, wait_for_signal="approve")
        if len(calls) == 2:
            msg = "reviewer service down"
            raise RuntimeError(msg)
        return StepResult(
            state={**state, "approved_by": step_input.payload["by"]}, done=True
        )

    run_id = begin(workflows, tenant_id, single("review", review))
    assert worker.run_once() == 1  # waits
    send(workflows, tenant_id, run_id, "approve", {"by": "ana"})
    assert worker.run_once() == 1  # takes the signal and fails

    (signal,) = fetch_signals(tenant_id, run_id)
    assert signal.consumed_at is None  # rolled back with the attempt
    assert fetch_run(tenant_id, run_id).status is RunStatus.WAITING
    assert step_log(tenant_id, run_id) == [
        ("review", 1, StepStatus.DONE),
        ("review", 2, StepStatus.FAILED),
    ]

    _ = clock.advance(timedelta(minutes=2))
    assert worker.run_once() == 1  # the retry finds the same signal
    assert calls == [None, "approve", "approve"]
    run = fetch_run(tenant_id, run_id)
    assert (run.status, run.state) == (RunStatus.DONE, {"approved_by": "ana"})
    (signal,) = fetch_signals(tenant_id, run_id)
    assert signal.consumed_at == T0 + timedelta(minutes=2)


def test_a_step_that_keeps_failing_ends_the_run_failed_on_the_last_attempt(
    workflows: PostgresWorkflowRunner,
    worker: JobRunner,
    clock: FakeClock,
    caplog: pytest.LogCaptureFixture,
):
    tenant_id = make_tenant("alpha")
    caplog.set_level(logging.WARNING, logger=RUNNER_LOGGER)

    def always(session, run, state, step_input):
        msg = "never works"
        raise ValueError(msg)

    run_id = begin(workflows, tenant_id, single("always", always))
    for attempt in range(1, 9):
        assert worker.run_once() == 1
        if attempt < 8:
            assert fetch_run(tenant_id, run_id).status is RunStatus.RUNNING
            assert not workflow_warnings(caplog)  # nothing is said until it is over
            _ = clock.advance(HOUR)  # past any backoff

    (warning,) = workflow_warnings(caplog)
    assert warning.getMessage() == "workflow run failed"
    assert warning.__dict__["workflow_run_id"] == str(run_id)
    assert warning.__dict__["workflow_error"] == "ValueError: never works"

    run = fetch_run(tenant_id, run_id)
    assert run.status is RunStatus.FAILED
    assert run.current_step == "always"  # where it stopped
    assert run.last_error == "ValueError: never works"
    assert run.finished_at == T0 + 7 * HOUR
    assert run.wake_at is None
    assert [(s.attempt, s.status) for s in fetch_steps(tenant_id, run_id)] == [
        (n, StepStatus.FAILED) for n in range(1, 9)
    ]
    (job,) = fetch_jobs(tenant_id, kind=ADVANCE_KIND)
    assert (job.status, job.attempts) == (JobStatus.DEAD, 8)

    # Finished for good: a signal is stored and nothing is queued or run.
    send(workflows, tenant_id, run_id, "approve")
    assert pending_advances(tenant_id) == []
    workflows.advance(run_id, tenant_id)
    assert len(fetch_steps(tenant_id, run_id)) == 8
    assert worker.run_once() == 0


def break_the_definition(tenant_id: UUID, run_id: UUID, **changes: str | None) -> None:
    """Edit the run row behind the runner's back, as a deploy or a bad write would."""
    with tenant_session(system_ctx(tenant_id)) as s:
        _ = s.execute(
            update(WorkflowRunRow).where(WorkflowRunRow.id == run_id).values(**changes)
        )


@pytest.mark.parametrize("attempts", [7, 8])
def test_a_failure_before_any_step_runs_is_left_to_the_job_not_the_run(
    workflows: PostgresWorkflowRunner, queue: PostgresJobQueue, attempts: int
):
    # A workflow nobody registers any more is not a step failing. Whatever attempt of
    # the job it is, the run is not failed here: the job dies and the sweep decides.
    tenant_id = make_tenant("alpha")
    run_id = begin(workflows, tenant_id, approval_workflow([]))
    break_the_definition(tenant_id, run_id, definition="gone.workflow")
    (job,) = claim(queue, tenant_id, kinds=[ADVANCE_KIND])
    assert job.max_attempts == 8

    with (
        tenant_session(system_ctx(tenant_id)) as s,
        pytest.raises(KeyError, match=r"gone\.workflow"),
    ):
        workflows.handle_job(s, replace(job, attempts=attempts))
    run = fetch_run(tenant_id, run_id)
    assert (run.status, run.last_error, run.finished_at) == (
        RunStatus.RUNNING,
        None,
        None,
    )


def test_a_database_error_in_a_step_fails_the_run_without_echoing_values(
    workflows: PostgresWorkflowRunner, worker: JobRunner, clock: FakeClock
):
    tenant_id = make_tenant("alpha")

    def breaks(session, run, state, step_input):
        _ = session.execute(text("SELECT CAST('ana@example.mx' AS uuid)"))
        return StepResult(state=state)

    run_id = begin(workflows, tenant_id, single("breaks", breaks))
    for _attempt in range(8):
        assert worker.run_once() == 1
        _ = clock.advance(HOUR)
    run = fetch_run(tenant_id, run_id)
    assert (run.status, run.last_error) == (
        RunStatus.FAILED,
        "DataError [sqlstate=22P02]",
    )
    assert {(s.status, s.error) for s in fetch_steps(tenant_id, run_id)} == {
        (StepStatus.FAILED, "DataError [sqlstate=22P02]")
    }


def test_a_stale_job_on_its_last_attempt_does_not_fail_a_run_whose_next_step_fails_once(
    workflows: PostgresWorkflowRunner, worker: JobRunner, queue: PostgresJobQueue
):
    # A duplicate job is normal (a retry, a signal and a timer can all be queued for
    # one run). This one is on its last attempt, finds the next step due and calls it
    # early; the step fails once. That is the step's first failure, not the job's
    # eighth, and the step's own job is still waiting to retry it.
    tenant_id = make_tenant("alpha")
    calls: list[str] = []

    def s1(session, run, state, step_input):
        calls.append("s1")
        return StepResult(state=state)

    def s2(session, run, state, step_input):
        calls.append("s2")
        if calls.count("s2") == 1:
            msg = "s2 hiccup"
            raise RuntimeError(msg)
        return StepResult(state=state)

    def s3(session, run, state, step_input):
        calls.append("s3")
        return StepResult(state=state, done=True)

    name = unique_kind("stale")
    register_workflow(
        WorkflowDefinition(name=name, steps=(("s1", s1), ("s2", s2), ("s3", s3)))
    )
    run_id = begin(workflows, tenant_id, name)
    (job1,) = claim(queue, tenant_id, kinds=[ADVANCE_KIND])
    with tenant_session(system_ctx(tenant_id)) as s:
        workflows.handle_job(s, job1)  # runs s1; its worker dies before completing it

    last_attempt = replace(job1, attempts=job1.max_attempts)
    with (
        tenant_session(system_ctx(tenant_id)) as s,
        pytest.raises(RuntimeError, match="s2 hiccup"),
    ):
        workflows.handle_job(s, last_attempt)
    run = fetch_run(tenant_id, run_id)
    assert (run.status, run.current_step) == (RunStatus.RUNNING, "s2")
    assert run.last_error == "RuntimeError: s2 hiccup"
    assert step_log(tenant_id, run_id) == [
        ("s1", 1, StepStatus.DONE),
        ("s2", 1, StepStatus.FAILED),
    ]

    assert pending_advances(tenant_id) == [f"{run_id}:s2:1"]  # s2's own job
    assert worker.run_once() == 2  # s2 again, then s3
    assert fetch_run(tenant_id, run_id).status is RunStatus.DONE
    assert calls == ["s1", "s2", "s2", "s3"]


def test_a_step_called_directly_is_recorded_each_time_and_never_fails_the_run(
    workflows: PostgresWorkflowRunner,
):
    # A direct call has no job and so no retries to run out of: the caller decides.
    tenant_id = make_tenant("alpha")

    def always(session, run, state, step_input):
        msg = "never works"
        raise ValueError(msg)

    run_id = begin(workflows, tenant_id, single("always", always))
    for _call in range(10):
        with pytest.raises(ValueError, match="never works"):
            workflows.advance(run_id, tenant_id)
    assert len(fetch_steps(tenant_id, run_id)) == 10
    run = fetch_run(tenant_id, run_id)
    assert (run.status, run.last_error) == (
        RunStatus.RUNNING,
        "ValueError: never works",
    )


def test_a_step_that_fails_eight_times_in_a_row_fails_the_run_whichever_jobs_deliver_it(
    workflows: PostgresWorkflowRunner, queue: PostgresJobQueue
):
    tenant_id = make_tenant("alpha")

    def always(session, run, state, step_input):
        msg = "never works"
        raise ValueError(msg)

    run_id = begin(workflows, tenant_id, single("always", always))
    (job,) = claim(queue, tenant_id, kinds=[ADVANCE_KIND])
    for delivery in range(1, 9):
        with (
            tenant_session(system_ctx(tenant_id)) as s,
            pytest.raises(ValueError, match="never works"),
        ):
            # A job that has never been tried before every time: none is near its
            # last attempt, so only the step's own count can end the run.
            workflows.handle_job(s, replace(job, attempts=1))
        status = fetch_run(tenant_id, run_id).status
        assert status is (RunStatus.FAILED if delivery == 8 else RunStatus.RUNNING)


def test_failures_separated_by_a_success_do_not_add_up(
    workflows: PostgresWorkflowRunner, worker: JobRunner, clock: FakeClock
):
    # The step waits for ticks and fails once after every wake before it succeeds:
    # nine failures in all, never more than one in a row.
    tenant_id = make_tenant("alpha")
    failing_next = [True]
    ticks: list[int] = []

    def poll(session, run, state, step_input):
        if step_input.signal is None:
            return StepResult(state=state, wait_for_signal="tick")
        if failing_next[0]:
            failing_next[0] = False
            msg = "blip"
            raise RuntimeError(msg)
        failing_next[0] = True
        ticks.append(len(ticks) + 1)
        return StepResult(state={**state, "ticks": len(ticks)}, wait_for_signal="tick")

    run_id = begin(workflows, tenant_id, single("poll", poll))
    assert worker.run_once() == 1  # parks, waiting for the first tick
    for _tick in range(9):
        send(workflows, tenant_id, run_id, "tick")
        assert worker.run_once() == 1  # the wake fails
        _ = clock.advance(timedelta(minutes=2))
        assert worker.run_once() == 1  # the retry succeeds and parks again
        run = fetch_run(tenant_id, run_id)
        assert (run.status, run.last_error) == (RunStatus.WAITING, None)

    failures = [
        s for s in fetch_steps(tenant_id, run_id) if s.status is StepStatus.FAILED
    ]
    assert len(failures) == 9  # more than the eight a step may fail in a row
    assert fetch_run(tenant_id, run_id).state == {"ticks": 9}


def test_a_run_at_a_step_the_definition_no_longer_has_fails_clearly(
    workflows: PostgresWorkflowRunner,
):
    tenant_id = make_tenant("alpha")
    run_id = begin(workflows, tenant_id, approval_workflow([]))
    break_the_definition(tenant_id, run_id, current_step="renamed")
    with pytest.raises(LookupError, match="has no step 'renamed'"):
        workflows.advance(run_id, tenant_id)


def test_a_waiting_run_whose_step_never_completed_fails_clearly(
    workflows: PostgresWorkflowRunner,
):
    # Nothing says what the run is waiting for, so it must not wait for ever.
    tenant_id = make_tenant("alpha")
    run_id = begin(workflows, tenant_id, approval_workflow([]))
    break_the_definition(tenant_id, run_id, status=RunStatus.WAITING)
    with pytest.raises(LookupError, match="no completed call"):
        workflows.advance(run_id, tenant_id)
    with pytest.raises(LookupError, match="no completed call"):
        send(workflows, tenant_id, run_id)


def test_a_run_without_a_current_step_fails_clearly(workflows: PostgresWorkflowRunner):
    tenant_id = make_tenant("alpha")
    run_id = begin(workflows, tenant_id, approval_workflow([]))
    break_the_definition(tenant_id, run_id, current_step=None)
    with pytest.raises(LookupError, match="has no current step"):
        workflows.advance(run_id, tenant_id)


def test_a_job_without_a_run_id_is_refused(
    workflows: PostgresWorkflowRunner, queue: PostgresJobQueue
):
    tenant_id = make_tenant("alpha")
    _ = enqueue(queue, tenant_id, kind=ADVANCE_KIND, payload={"wrong": "shape"})
    (job,) = claim(queue, tenant_id, kinds=[ADVANCE_KIND])
    with tenant_session(system_ctx(tenant_id)) as s, pytest.raises(ValidationError):
        workflows.handle_job(s, job)


# --- restarts, redelivery and concurrent workers -----------------------------------


class SimulatedCrashError(Exception):
    """Stands in for the process dying."""


def test_a_crash_between_the_step_row_and_the_enqueue_leaves_nothing_behind(
    workflows: PostgresWorkflowRunner,
    queue: PostgresJobQueue,
    monkeypatch: pytest.MonkeyPatch,
):
    # The next job is queued in the step's own transaction, so there is no moment at
    # which the step is saved and its successor is not: a crash takes both, together
    # with whatever the step wrote, and the restart starts the step over.
    tenant_id = make_tenant("alpha")
    calls: Calls = []

    def collect(session, run, state, step_input):
        calls.append(("collect",))
        _ = record(session, AuditEvent(action="demo.collected"))
        return StepResult(state={**state, "collected": True})

    def finish(session, run, state, step_input):
        calls.append(("finish",))
        return StepResult(state=state, done=True)

    name = unique_kind("crash")
    register_workflow(
        WorkflowDefinition(name=name, steps=(("collect", collect), ("finish", finish)))
    )
    run_id = begin(workflows, tenant_id, name)

    def crash(*args, **kwargs):
        raise SimulatedCrashError

    real_enqueue = queue.enqueue
    monkeypatch.setattr(queue, "enqueue", crash)
    with pytest.raises(SimulatedCrashError):
        workflows.advance(run_id, tenant_id)
    monkeypatch.setattr(queue, "enqueue", real_enqueue)

    run = fetch_run(tenant_id, run_id)
    assert (run.status, run.current_step, run.state) == (
        RunStatus.RUNNING,
        "collect",
        {},
    )
    assert fetch_steps(tenant_id, run_id) == []
    assert audit_actions(tenant_id) == []
    assert pending_advances(tenant_id) == [f"{run_id}:collect:1"]  # only the first job

    workflows.advance(run_id, tenant_id)  # the restart
    assert step_log(tenant_id, run_id) == [("collect", 1, StepStatus.DONE)]
    assert audit_actions(tenant_id) == ["demo.collected"]  # once, not twice
    assert calls == [("collect",), ("collect",)]  # the first call died with its work
    workflows.advance(run_id, tenant_id)
    assert fetch_run(tenant_id, run_id).status is RunStatus.DONE
    assert calls[2:] == [("finish",)]


def test_a_job_redelivered_after_its_step_committed_does_not_run_that_step_again(
    workflows: PostgresWorkflowRunner,
    worker: JobRunner,
    queue: PostgresJobQueue,
    clock: FakeClock,
):
    tenant_id = make_tenant("alpha")
    calls: Calls = []
    run_id = begin(workflows, tenant_id, two_step_workflow(calls))

    # A worker runs the first step, commits, and dies before it can finish the job.
    (job,) = claim(queue, tenant_id, kinds=[ADVANCE_KIND], lease=timedelta(seconds=30))
    with tenant_session(system_ctx(tenant_id)) as s:
        workflows.handle_job(s, job)
    assert calls == [("collect",)]
    _ = clock.advance(timedelta(seconds=31))
    assert queue.reap(tenant_id=tenant_id) == 1  # the lease ran out: the job is back

    # The redelivered job and the one the first run queued both reach a worker.
    assert worker.run_once() == 2
    assert calls == [("collect",), ("finish",)]  # collect did not run a second time
    assert step_log(tenant_id, run_id) == [
        ("collect", 1, StepStatus.DONE),
        ("finish", 1, StepStatus.DONE),
    ]
    assert fetch_run(tenant_id, run_id).status is RunStatus.DONE
    assert {job.status for job in fetch_jobs(tenant_id)} == {JobStatus.DONE}


def test_a_run_survives_a_worker_restart_between_steps(
    workflows: PostgresWorkflowRunner, worker: JobRunner, clock: FakeClock
):
    tenant_id = make_tenant("alpha")
    calls: Calls = []
    run_id = begin(workflows, tenant_id, two_step_workflow(calls))
    assert worker.run_once(limit=1) == 1  # the first step, then the worker stops
    assert fetch_run(tenant_id, run_id).current_step == "finish"

    # A new process has nothing but the tables to go on.
    queue = PostgresJobQueue(get_sessionmaker(), clock_fn=clock)
    restarted = PostgresWorkflowRunner(get_sessionmaker(), queue, clock_fn=clock)
    restarted.register_job_handlers()
    after = JobRunner(queue, get_sessionmaker(), worker_id="w-2")
    assert after.run_once() == 1
    assert fetch_run(tenant_id, run_id).status is RunStatus.DONE
    assert calls == [("collect",), ("finish",)]


def test_with_the_default_clock_a_run_completes_end_to_end(engine: Engine):
    # Every other test moves time by hand; this one lets the wall clock drive it.
    queue = PostgresJobQueue(get_sessionmaker())
    runner = PostgresWorkflowRunner(get_sessionmaker(), queue)
    runner.register_job_handlers()
    jobs = JobRunner(
        queue, get_sessionmaker(), worker_id="w-1", kinds=[ADVANCE_KIND, TIMER_KIND]
    )
    tenant_id = make_tenant("alpha")
    before = datetime.now(UTC)
    run_id = begin(runner, tenant_id, two_step_workflow([]))
    assert jobs.run_once() == 2
    after = datetime.now(UTC)

    run = fetch_run(tenant_id, run_id)
    assert run.status is RunStatus.DONE
    assert run.finished_at is not None
    assert before <= run.started_at <= run.finished_at <= after


@contextmanager
def run_locked(
    tenant_id: UUID, run_id: UUID, *, at_most: float = 3.0
) -> Generator[None]:
    """Hold the run's row lock, as a long step would, for the length of the block.

    The lock is held by a thread of its own and let go after ``at_most`` seconds at
    the latest, so that code that wrongly waits for it ends by getting it instead of
    hanging the test.
    """
    locked, release = threading.Event(), threading.Event()

    def hold() -> None:
        with tenant_session(system_ctx(tenant_id)) as holder:
            _ = holder.execute(
                select(WorkflowRunRow.id)
                .where(WorkflowRunRow.id == run_id)
                .with_for_update()
            )
            locked.set()
            _ = release.wait(timeout=at_most)

    holder_thread = threading.Thread(target=hold)
    holder_thread.start()
    try:
        assert locked.wait(timeout=10)
        yield
    finally:
        release.set()
        holder_thread.join(timeout=10)


def test_advance_gives_up_waiting_for_a_locked_run_and_the_job_is_retried(
    workflows: PostgresWorkflowRunner,
    worker: JobRunner,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(runner_module, "LOCK_TIMEOUT", "250ms")
    tenant_id = make_tenant("alpha")
    calls: Calls = []
    run_id = begin(workflows, tenant_id, two_step_workflow(calls))

    with run_locked(tenant_id, run_id):
        with pytest.raises(OperationalError) as raised:
            workflows.advance(run_id, tenant_id)
        assert isinstance(raised.value.orig, psycopg.errors.LockNotAvailable)
        assert raised.value.orig.sqlstate == "55P03"

        # In a job it is a failed attempt, not a failed step: nothing is recorded
        # against the step or the run, and the queue retries with its backoff.
        assert worker.run_once() == 1
    (job,) = fetch_jobs(tenant_id, kind=ADVANCE_KIND)
    assert (job.status, job.attempts) == (JobStatus.PENDING, 1)
    assert job.last_error == "OperationalError [sqlstate=55P03]"
    run = fetch_run(tenant_id, run_id)
    assert (run.status, run.current_step, run.last_error) == (
        RunStatus.RUNNING,
        "collect",
        None,
    )
    assert fetch_steps(tenant_id, run_id) == []
    assert calls == []

    _ = clock.advance(timedelta(minutes=2))
    assert worker.run_once() == 2  # the lock is free: both steps run
    assert fetch_run(tenant_id, run_id).status is RunStatus.DONE


def test_signal_gives_up_waiting_for_a_locked_run(
    workflows: PostgresWorkflowRunner, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(runner_module, "LOCK_TIMEOUT", "250ms")
    tenant_id = make_tenant("alpha")
    run_id = begin(workflows, tenant_id, approval_workflow([]))

    with run_locked(tenant_id, run_id), pytest.raises(OperationalError) as raised:
        send(workflows, tenant_id, run_id, "approve", {"by": "ana"})
    assert isinstance(raised.value.orig, psycopg.errors.LockNotAvailable)
    assert fetch_signals(tenant_id, run_id) == []


def test_the_lock_timeout_covers_the_run_lock_and_nothing_else(
    workflows: PostgresWorkflowRunner,
):
    tenant_id = make_tenant("alpha")
    seen: list[str] = []

    def look(session, run, state, step_input):
        seen.append(session.execute(text("SHOW lock_timeout")).scalar_one())
        return StepResult(state=state)

    run_id = begin(workflows, tenant_id, single("look", look))
    with tenant_session(system_ctx(tenant_id)) as s:
        default = s.execute(text("SHOW lock_timeout")).scalar_one()
    workflows.advance(run_id, tenant_id)
    assert seen == [default]  # the step waits for its own locks as it always did

    # And the caller's own setting survives a signal.
    with tenant_session(system_ctx(tenant_id)) as s:
        _ = s.execute(text("SET LOCAL lock_timeout = '42s'"))
        workflows.signal(s, run_id, "anything", {})
        assert s.execute(text("SHOW lock_timeout")).scalar_one() == "42s"


def test_two_workers_given_the_same_run_never_run_a_step_twice(
    workflows: PostgresWorkflowRunner, admin_engine: Engine
):
    tenant_id = make_tenant("alpha")
    entered, release = threading.Event(), threading.Event()
    calls: list[str] = []

    def slow(session, run, state, step_input):
        calls.append("slow")
        entered.set()
        assert release.wait(timeout=30)
        return StepResult(state=state)

    def second(session, run, state, step_input):
        calls.append("second")
        return StepResult(state=state, done=True)

    name = unique_kind("slow")
    register_workflow(
        WorkflowDefinition(name=name, steps=(("slow", slow), ("second", second)))
    )
    run_id = begin(workflows, tenant_id, name)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(workflows.advance, run_id, tenant_id)
        assert entered.wait(timeout=10)
        other = pool.submit(workflows.advance, run_id, tenant_id)
        # The second delivery waits on the run's row lock, where it would otherwise
        # have read the run still at "slow" and run that step a second time.
        assert eventually(lambda: lock_waiters(admin_engine) == 1)
        release.set()
        first.result(timeout=10)
        other.result(timeout=10)

    assert calls == ["slow", "second"]
    assert step_log(tenant_id, run_id) == [
        ("second", 1, StepStatus.DONE),
        ("slow", 1, StepStatus.DONE),
    ]
    assert fetch_run(tenant_id, run_id).status is RunStatus.DONE


def test_a_signal_sent_while_the_run_is_parking_is_not_lost(
    workflows: PostgresWorkflowRunner,
    queue: PostgresJobQueue,
    admin_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
):
    tenant_id = make_tenant("alpha")
    run_id = begin(workflows, tenant_id, approval_workflow([]))
    workflows.advance(run_id, tenant_id)  # collect; the run is at approve now

    # approve parks the run. After it has looked for a signal that came early (none
    # had) and before it commits, a signal arrives on another connection.
    real_enqueue = queue.enqueue
    pool = ThreadPoolExecutor(max_workers=1)
    sent = []

    def enqueue_then_signal(session, **options):
        queued = real_enqueue(session, **options)
        if options["kind"] == TIMER_KIND:
            sent.append(
                pool.submit(
                    send, workflows, tenant_id, run_id, "approve", {"by": "ana"}
                )
            )
            # It has to wait for the run's row lock, which this transaction holds.
            assert eventually(lambda: lock_waiters(admin_engine) == 1)
        return queued

    monkeypatch.setattr(queue, "enqueue", enqueue_then_signal)
    try:
        workflows.advance(run_id, tenant_id)
        sent[0].result(timeout=10)
    finally:
        pool.shutdown(wait=True)

    # The signal ran after the commit, saw a run waiting for it and queued the wake-up
    # (the jobs of the two steps that already ran were never completed here).
    assert fetch_run(tenant_id, run_id).status is RunStatus.WAITING
    assert f"{run_id}:approve:2" in pending_advances(tenant_id)


def test_two_starts_with_the_same_business_key_make_one_run(
    workflows: PostgresWorkflowRunner,
):
    tenant_id = make_tenant("alpha")
    name = approval_workflow([])
    start = threading.Barrier(2)

    def attempt(_: int) -> UUID | None:
        _ = start.wait(timeout=10)
        try:
            return begin(workflows, tenant_id, name, key="case-1")
        except DomainError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, range(2)))
    assert sorted(result is None for result in results) == [False, True]
    assert len(fetch_runs(tenant_id)) == 1
    assert len(fetch_jobs(tenant_id, kind=ADVANCE_KIND)) == 1


# --- runs that no job will ever advance --------------------------------------------


def dead_letter(
    queue: PostgresJobQueue, clock: FakeClock, tenant_id: UUID, kind: str
) -> None:
    """Kill the one waiting job of ``kind`` as the reaper does.

    Its worker dies on the job's last attempt and never answers, so the lease runs
    out and the job is dead-lettered without any code of ours running.
    """
    with tenant_session(system_ctx(tenant_id)) as s:
        _ = s.execute(
            update(JobRow)
            .where(JobRow.kind == kind, JobRow.status == JobStatus.PENDING)
            .values(attempts=JobRow.max_attempts - 1)
        )
    (job,) = claim(queue, tenant_id, kinds=[kind], lease=timedelta(seconds=30))
    assert job.attempts == job.max_attempts
    _ = clock.advance(timedelta(seconds=31))
    assert queue.reap(tenant_id=tenant_id) == 1
    assert fetch_job(tenant_id, job.id).status is JobStatus.DEAD


def sweep(workflows: PostgresWorkflowRunner, tenant_id: UUID) -> int:
    with tenant_session(system_ctx(tenant_id)) as s:
        return workflows.fail_stranded(s)


def test_the_sweep_fails_a_running_run_whose_job_died_with_its_worker(
    workflows: PostgresWorkflowRunner,
    worker: JobRunner,
    queue: PostgresJobQueue,
    clock: FakeClock,
    caplog: pytest.LogCaptureFixture,
):
    tenant_id = make_tenant("alpha")
    caplog.set_level(logging.WARNING, logger=RUNNER_LOGGER)
    run_id = begin(workflows, tenant_id, two_step_workflow([]))
    dead_letter(queue, clock, tenant_id, ADVANCE_KIND)
    assert worker.run_once() == 0  # nothing will ever run it
    assert fetch_run(tenant_id, run_id).status is RunStatus.RUNNING

    assert sweep(workflows, tenant_id) == 1
    run = fetch_run(tenant_id, run_id)
    assert (run.status, run.current_step) == (RunStatus.FAILED, "collect")
    assert (run.finished_at, run.wake_at) == (clock.now, None)
    assert run.last_error == f"{ADVANCE_KIND} job dead-lettered: {LEASE_EXPIRED}"
    (warning,) = workflow_warnings(caplog)
    assert warning.getMessage() == "workflow run failed"
    assert warning.__dict__["workflow_run_id"] == str(run_id)

    assert sweep(workflows, tenant_id) == 0  # nothing more to do
    again = fetch_run(tenant_id, run_id)
    assert (again.status, again.last_error, again.finished_at) == (
        run.status,
        run.last_error,
        run.finished_at,
    )


def test_the_sweep_fails_a_waiting_run_whose_timer_died(
    workflows: PostgresWorkflowRunner,
    worker: JobRunner,
    queue: PostgresJobQueue,
    clock: FakeClock,
):
    tenant_id = make_tenant("alpha")
    run_id = park(workflows, worker, tenant_id, [])
    _ = clock.advance(HOUR)  # the timer is due
    dead_letter(queue, clock, tenant_id, TIMER_KIND)
    assert worker.run_once() == 0
    waiting = fetch_run(tenant_id, run_id)
    assert (waiting.status, waiting.wake_at) == (RunStatus.WAITING, T0 + HOUR)

    assert sweep(workflows, tenant_id) == 1
    run = fetch_run(tenant_id, run_id)
    assert (run.status, run.wake_at) == (RunStatus.FAILED, None)
    assert run.last_error == f"{TIMER_KIND} job dead-lettered: {LEASE_EXPIRED}"


def test_the_sweep_fails_a_run_whose_wake_up_died_and_keeps_its_signal(
    workflows: PostgresWorkflowRunner,
    worker: JobRunner,
    queue: PostgresJobQueue,
    clock: FakeClock,
):
    # No timeout, so no timer: the signal's wake-up is the only job the run has.
    tenant_id = make_tenant("alpha")
    run_id = park(workflows, worker, tenant_id, [], timeout=None)
    send(workflows, tenant_id, run_id, "approve", {"by": "ana"})
    dead_letter(queue, clock, tenant_id, ADVANCE_KIND)
    assert worker.run_once() == 0
    assert fetch_run(tenant_id, run_id).status is RunStatus.WAITING

    assert sweep(workflows, tenant_id) == 1
    run = fetch_run(tenant_id, run_id)
    assert run.status is RunStatus.FAILED
    assert run.last_error == f"{ADVANCE_KIND} job dead-lettered: {LEASE_EXPIRED}"
    (signal,) = fetch_signals(tenant_id, run_id)
    assert signal.consumed_at is None  # the record of what was never acted on


def test_a_job_that_keeps_failing_before_any_step_runs_ends_in_the_sweep(
    workflows: PostgresWorkflowRunner, worker: JobRunner, clock: FakeClock
):
    # The case the handler cannot decide for itself: nothing in the run is wrong, the
    # code that should run it is gone. The job retries and dies; the sweep ends the run.
    tenant_id = make_tenant("alpha")
    run_id = begin(workflows, tenant_id, approval_workflow([]))
    break_the_definition(tenant_id, run_id, definition="gone.workflow")
    for _attempt in range(8):
        assert worker.run_once() == 1
        _ = clock.advance(HOUR)
    (job,) = fetch_jobs(tenant_id, kind=ADVANCE_KIND)
    assert (job.status, job.attempts) == (JobStatus.DEAD, 8)
    assert fetch_run(tenant_id, run_id).status is RunStatus.RUNNING

    assert sweep(workflows, tenant_id) == 1
    run = fetch_run(tenant_id, run_id)
    assert run.status is RunStatus.FAILED
    assert (
        run.last_error == f"{ADVANCE_KIND} job dead-lettered: KeyError: 'gone.workflow'"
    )


def test_the_sweep_leaves_alone_a_run_that_waits_for_a_signal_without_a_timer(
    workflows: PostgresWorkflowRunner, worker: JobRunner
):
    tenant_id = make_tenant("alpha")
    run_id = park(workflows, worker, tenant_id, [], timeout=None)
    assert sweep(workflows, tenant_id) == 0  # no job is live, and none is dead
    assert fetch_run(tenant_id, run_id).status is RunStatus.WAITING


def test_the_sweep_leaves_alone_a_run_whose_job_is_retrying_or_running(
    workflows: PostgresWorkflowRunner,
    worker: JobRunner,
    queue: PostgresJobQueue,
    clock: FakeClock,
):
    tenant_id = make_tenant("alpha")
    calls: Calls = []

    def flaky(session, run, state, step_input):
        calls.append(("flaky",))
        if len(calls) == 1:
            msg = "vendor unavailable"
            raise RuntimeError(msg)
        return StepResult(state=state)

    run_id = begin(workflows, tenant_id, single("flaky", flaky))
    assert worker.run_once() == 1  # fails once: the job waits for its backoff
    assert sweep(workflows, tenant_id) == 0

    _ = clock.advance(timedelta(minutes=2))
    claimed = claim(queue, tenant_id, kinds=[ADVANCE_KIND])  # a worker has it now
    assert len(claimed) == 1
    assert sweep(workflows, tenant_id) == 0
    assert fetch_run(tenant_id, run_id).status is RunStatus.RUNNING


def test_the_sweep_leaves_alone_a_run_whose_older_timer_is_still_alive(
    workflows: PostgresWorkflowRunner,
    worker: JobRunner,
    queue: PostgresJobQueue,
    clock: FakeClock,
):
    # The signal's wake-up died, but the hour-long timer queued before it is still
    # waiting, and when it fires the signal wins over the timeout.
    tenant_id = make_tenant("alpha")
    run_id = park(workflows, worker, tenant_id, [])
    send(workflows, tenant_id, run_id, "approve", {"by": "ana"})
    dead_letter(queue, clock, tenant_id, ADVANCE_KIND)

    assert sweep(workflows, tenant_id) == 0
    assert fetch_run(tenant_id, run_id).status is RunStatus.WAITING
    _ = clock.advance(HOUR)
    assert worker.run_once() == 2  # the timer takes the signal, then finish runs
    run = fetch_run(tenant_id, run_id)
    assert (run.status, run.state["approved_by"]) == (RunStatus.DONE, "ana")


def test_the_sweep_leaves_finished_runs_alone(
    workflows: PostgresWorkflowRunner, worker: JobRunner, clock: FakeClock
):
    tenant_id = make_tenant("alpha")
    done_id = begin(workflows, tenant_id, two_step_workflow([]), key="done")
    assert worker.run_once() == 2

    def always(session, run, state, step_input):
        msg = "never works"
        raise ValueError(msg)

    failed_id = begin(workflows, tenant_id, single("always", always), key="failed")
    for _attempt in range(8):
        assert worker.run_once() == 1
        _ = clock.advance(HOUR)
    failed = fetch_run(tenant_id, failed_id)
    assert failed.status is RunStatus.FAILED

    assert sweep(workflows, tenant_id) == 0
    assert fetch_run(tenant_id, done_id).status is RunStatus.DONE
    still_failed = fetch_run(tenant_id, failed_id)  # its own error is not replaced
    assert (still_failed.status, still_failed.last_error, still_failed.finished_at) == (
        RunStatus.FAILED,
        "ValueError: never works",
        failed.finished_at,
    )


def test_the_sweep_leaves_alone_a_run_that_has_no_job_to_judge_by(
    workflows: PostgresWorkflowRunner,
):
    tenant_id = make_tenant("alpha")
    run_id = begin(workflows, tenant_id, two_step_workflow([]))
    with tenant_session(system_ctx(tenant_id)) as s:
        _ = s.execute(delete(JobRow))
    assert sweep(workflows, tenant_id) == 0
    assert fetch_run(tenant_id, run_id).status is RunStatus.RUNNING


def test_an_older_dead_job_does_not_strand_a_run_that_moved_on(
    workflows: PostgresWorkflowRunner, worker: JobRunner
):
    # What counts is the newest job: an earlier wait's timer that died, before a
    # signal moved the run to a later wait with no timer, says nothing about the run.
    tenant_id = make_tenant("alpha")

    def first(session, run, state, step_input):
        if step_input.signal is None:
            return StepResult(state=state, wait_for_signal="a", wake_at=T0 + HOUR)
        return StepResult(state=state)

    def second(session, run, state, step_input):
        return StepResult(state=state, wait_for_signal="b")

    name = unique_kind("two-waits")
    register_workflow(
        WorkflowDefinition(name=name, steps=(("first", first), ("second", second)))
    )
    run_id = begin(workflows, tenant_id, name)
    assert worker.run_once() == 1  # first waits for "a", with a timer
    with tenant_session(system_ctx(tenant_id)) as s:
        _ = s.execute(
            update(JobRow)
            .where(JobRow.kind == TIMER_KIND)
            .values(status=JobStatus.DEAD, last_error="lease expired")
        )
    send(workflows, tenant_id, run_id, "a")
    assert worker.run_once() == 2  # first takes the signal, second waits for "b"

    assert sweep(workflows, tenant_id) == 0
    run = fetch_run(tenant_id, run_id)
    assert (run.status, run.current_step) == (RunStatus.WAITING, "second")


def test_the_sweep_only_touches_its_own_tenant(
    workflows: PostgresWorkflowRunner, queue: PostgresJobQueue, clock: FakeClock
):
    alpha, beta = make_tenant("alpha"), make_tenant("beta")
    in_alpha = begin(workflows, alpha, two_step_workflow([]))
    in_beta = begin(workflows, beta, two_step_workflow([]))
    dead_letter(queue, clock, alpha, ADVANCE_KIND)
    dead_letter(queue, clock, beta, ADVANCE_KIND)

    assert sweep(workflows, alpha) == 1
    assert fetch_run(alpha, in_alpha).status is RunStatus.FAILED
    assert fetch_run(beta, in_beta).status is RunStatus.RUNNING
    assert sweep(workflows, beta) == 1
    assert fetch_run(beta, in_beta).status is RunStatus.FAILED


def test_the_sweep_skips_a_run_a_step_is_executing_right_now(
    workflows: PostgresWorkflowRunner, queue: PostgresJobQueue, clock: FakeClock
):
    tenant_id = make_tenant("alpha")
    run_id = begin(workflows, tenant_id, two_step_workflow([]))
    dead_letter(queue, clock, tenant_id, ADVANCE_KIND)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with run_locked(tenant_id, run_id):
            # Without SKIP LOCKED the sweep would wait here for the lock.
            assert pool.submit(sweep, workflows, tenant_id).result(timeout=10) == 0
        assert fetch_run(tenant_id, run_id).status is RunStatus.RUNNING
    assert sweep(workflows, tenant_id) == 1


def test_the_sweep_needs_a_session_bound_to_a_tenant(
    workflows: PostgresWorkflowRunner,
):
    with tenant_session(platform_ctx()) as s, pytest.raises(ContextMissingError):
        _ = workflows.fail_stranded(s)


def test_a_sweep_job_runs_the_sweep_for_its_tenant(
    workflows: PostgresWorkflowRunner,
    worker: JobRunner,
    queue: PostgresJobQueue,
    clock: FakeClock,
):
    tenant_id = make_tenant("alpha")
    empty = make_tenant("beta")  # a tenant with nothing to sweep
    run_id = begin(workflows, tenant_id, two_step_workflow([]))
    dead_letter(queue, clock, tenant_id, ADVANCE_KIND)
    job_id = enqueue(queue, tenant_id, kind=SWEEP_KIND)
    _ = enqueue(queue, empty, kind=SWEEP_KIND)

    assert worker.run_once() == 2  # the two sweep jobs, one per tenant
    assert fetch_run(tenant_id, run_id).status is RunStatus.FAILED
    assert fetch_job(tenant_id, job_id).status is JobStatus.DONE
