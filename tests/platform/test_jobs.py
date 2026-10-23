"""Job queue: enqueue, claim, completion, dedup and the handler registry (spec §9)."""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import override
from uuid import UUID, uuid4

import pytest
from pydantic import BaseModel, Field
from sqlalchemy import Engine, select, text, update
from sqlalchemy.exc import DataError, OperationalError

from tests.helpers import make_tenant, platform_ctx, system_ctx
from tests.platform.jobs_support import (
    LEASE,
    SAMPLE_PAYLOAD,
    T0,
    FakeClock,
    claim,
    enqueue,
    eventually,
    fetch_job,
    fetch_jobs,
    try_enqueue,
    unique_kind,
)
from xolotl.platform.db import get_sessionmaker, tenant_session
from xolotl.platform.enums import JobStatus
from xolotl.platform.errors import ContextMissingError
from xolotl.platform.jobs.models import JobRow
from xolotl.platform.jobs.queue import (
    Job,
    PostgresJobQueue,
    job_handler,
    register_job_handler,
    retry_delay,
)
from xolotl.platform.jobs.runner import HEARTBEAT_THREAD_PREFIX, JobRunner
from xolotl.platform.logging import EXCEPTION_SUMMARY_LIMIT
from xolotl.platform.tenancy.context import TenantContext


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def queue(engine: Engine, clock: FakeClock) -> PostgresJobQueue:
    return PostgresJobQueue(get_sessionmaker(), clock_fn=clock)


# --- enqueue and claim -----------------------------------------------------------


def test_enqueue_then_claim_round_trip(queue: PostgresJobQueue):
    tenant_id = make_tenant("alpha")
    job_id = enqueue(
        queue,
        tenant_id,
        kind="demo.send",
        payload=SAMPLE_PAYLOAD,
        key="k-1",
        max_attempts=3,
    )
    stored = fetch_job(tenant_id, job_id)
    assert stored.status is JobStatus.PENDING
    assert (stored.attempts, stored.max_attempts, stored.key) == (0, 3, "k-1")
    assert stored.run_after == T0
    assert stored.created_at == T0

    (job,) = claim(queue, tenant_id, worker="w-1")
    assert isinstance(job.lock_token, UUID)
    assert job == Job(
        id=job_id,
        tenant_id=tenant_id,
        kind="demo.send",
        key="k-1",
        payload=SAMPLE_PAYLOAD,
        attempts=1,
        max_attempts=3,
        lock_token=job.lock_token,
    )
    running = fetch_job(tenant_id, job_id)
    assert running.status is JobStatus.RUNNING
    assert running.attempts == 1
    assert running.locked_by == "w-1"
    assert running.locked_until == T0 + LEASE
    assert running.lock_token == job.lock_token
    assert claim(queue, tenant_id, worker="w-2") == []


def test_every_claim_writes_a_fresh_lock_token(queue: PostgresJobQueue):
    tenant_id = make_tenant("alpha")
    _ = enqueue(queue, tenant_id)
    _ = enqueue(queue, tenant_id)
    first, second = claim(queue, tenant_id)
    assert first.lock_token != second.lock_token


def test_claim_only_returns_the_given_tenants_jobs(queue: PostgresJobQueue):
    alpha, beta = make_tenant("alpha"), make_tenant("beta")
    in_alpha = enqueue(queue, alpha)
    in_beta = enqueue(queue, beta)
    assert [job.id for job in claim(queue, alpha)] == [in_alpha]
    assert [job.id for job in claim(queue, beta)] == [in_beta]


def test_a_job_that_is_not_due_yet_is_not_claimed(
    queue: PostgresJobQueue, clock: FakeClock
):
    tenant_id = make_tenant("alpha")
    later = T0 + timedelta(minutes=10)
    job_id = enqueue(queue, tenant_id, run_after=later)
    assert claim(queue, tenant_id) == []
    _ = clock.advance(timedelta(minutes=9, seconds=59))
    assert claim(queue, tenant_id) == []
    _ = clock.advance(timedelta(seconds=1))  # exactly due
    assert [job.id for job in claim(queue, tenant_id)] == [job_id]


def test_claim_filters_by_kind(queue: PostgresJobQueue):
    tenant_id = make_tenant("alpha")
    wanted = enqueue(queue, tenant_id, kind="wanted.v1")
    other = enqueue(queue, tenant_id, kind="other.v1")
    assert [j.id for j in claim(queue, tenant_id, kinds=["wanted.v1"])] == [wanted]
    assert claim(queue, tenant_id, kinds=[]) == []
    assert [j.id for j in claim(queue, tenant_id, kinds=None)] == [other]


def test_claim_leaves_out_the_excluded_kinds(queue: PostgresJobQueue):
    tenant_id = make_tenant("alpha")
    first = enqueue(queue, tenant_id, kind="full.v1")
    second = enqueue(queue, tenant_id, kind="open.v1")
    leased = queue.claim(
        worker_id="w-1",
        tenant_id=tenant_id,
        kinds=None,
        exclude_kinds=["full.v1"],
        limit=5,
        lease=LEASE,
    )
    assert [j.id for j in leased] == [second]
    assert fetch_job(tenant_id, first).status is JobStatus.PENDING


def test_claim_takes_the_oldest_due_jobs_first_up_to_the_limit(
    queue: PostgresJobQueue,
):
    tenant_id = make_tenant("alpha")
    newest = enqueue(queue, tenant_id, run_after=T0 - timedelta(minutes=1))
    oldest = enqueue(queue, tenant_id, run_after=T0 - timedelta(minutes=3))
    middle = enqueue(queue, tenant_id, run_after=T0 - timedelta(minutes=2))
    assert [j.id for j in claim(queue, tenant_id, limit=2)] == [oldest, middle]
    assert [j.id for j in claim(queue, tenant_id, limit=2)] == [newest]


def test_two_claimers_in_threads_never_get_the_same_job(queue: PostgresJobQueue):
    tenant_id = make_tenant("alpha")
    expected = {enqueue(queue, tenant_id, key=f"k-{n}") for n in range(40)}
    start = threading.Barrier(2)

    def drain(worker: str) -> list[UUID]:
        _ = start.wait(timeout=10)
        got: list[UUID] = []
        while batch := claim(queue, tenant_id, worker=worker, limit=3):
            got.extend(job.id for job in batch)
        return got

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.map(drain, ["w-1", "w-2"])
    assert not set(first) & set(second)
    assert set(first) | set(second) == expected
    assert len(first) + len(second) == 40


def test_claim_skips_rows_another_transaction_has_locked(queue: PostgresJobQueue):
    tenant_id = make_tenant("alpha")
    locked = enqueue(queue, tenant_id, run_after=T0 - timedelta(minutes=2))
    free = enqueue(queue, tenant_id, run_after=T0 - timedelta(minutes=1))
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        with tenant_session(system_ctx(tenant_id)) as holder:
            _ = holder.execute(
                select(JobRow.id).where(JobRow.id == locked).with_for_update()
            )
            # The oldest row is locked: a claim that waited for it would time out here.
            future = pool.submit(claim, queue, tenant_id, limit=1)
            claimed = future.result(timeout=10)
    finally:
        pool.shutdown(wait=True)
    assert [job.id for job in claimed] == [free]
    assert fetch_job(tenant_id, locked).status is JobStatus.PENDING


# --- completion ------------------------------------------------------------------


def test_complete_marks_the_job_done_once(queue: PostgresJobQueue, clock: FakeClock):
    tenant_id = make_tenant("alpha")
    job_id = enqueue(queue, tenant_id)
    (job,) = claim(queue, tenant_id, worker="w-1")
    _ = clock.advance(timedelta(seconds=5))
    assert queue.complete(job) is True
    done = fetch_job(tenant_id, job_id)
    assert done.status is JobStatus.DONE
    assert done.finished_at == T0 + timedelta(seconds=5)
    assert done.locked_by == "w-1"  # who finished it stays on record
    assert (done.locked_until, done.lock_token, done.last_error) == (None, None, None)
    assert queue.complete(job) is False  # nothing is left to complete


# --- leases, fencing and the reaper -----------------------------------------------


def test_heartbeat_extends_the_lease(queue: PostgresJobQueue, clock: FakeClock):
    tenant_id = make_tenant("alpha")
    job_id = enqueue(queue, tenant_id)
    lease = timedelta(seconds=10)
    (job,) = claim(queue, tenant_id, lease=lease)
    _ = clock.advance(timedelta(seconds=6))
    assert queue.heartbeat(job, lease=lease) is True
    assert fetch_job(tenant_id, job_id).locked_until == T0 + timedelta(seconds=16)
    # Past the original expiry (10 s) but inside the extended one (16 s).
    _ = clock.advance(timedelta(seconds=6))
    assert queue.reap(tenant_id=tenant_id) == 0
    assert fetch_job(tenant_id, job_id).status is JobStatus.RUNNING


def test_an_expired_lease_is_reaped_and_the_new_owner_wins(
    queue: PostgresJobQueue, clock: FakeClock
):
    tenant_id = make_tenant("alpha")
    job_id = enqueue(queue, tenant_id)
    (first,) = claim(queue, tenant_id, worker="w-1", lease=timedelta(seconds=1))
    assert queue.reap(tenant_id=tenant_id) == 0  # the lease is still running
    _ = clock.advance(timedelta(seconds=2))
    assert queue.reap(tenant_id=tenant_id) == 1

    requeued = fetch_job(tenant_id, job_id)
    assert requeued.status is JobStatus.PENDING
    assert requeued.attempts == 1  # the lost run still counts
    assert requeued.last_error == "lease expired: the worker stopped heartbeating"
    assert (requeued.locked_by, requeued.locked_until, requeued.lock_token) == (
        None,
        None,
        None,
    )

    (second,) = claim(queue, tenant_id, worker="w-2")
    assert second.id == first.id
    assert second.lock_token != first.lock_token
    assert second.attempts == 2
    assert queue.complete(second) is True
    assert queue.complete(first) is False  # the late completion changes nothing
    done = fetch_job(tenant_id, job_id)
    assert done.status is JobStatus.DONE
    assert done.locked_by == "w-2"
    assert done.attempts == 2


def test_a_stale_heartbeat_failure_or_completion_leaves_the_new_run_alone(
    queue: PostgresJobQueue, clock: FakeClock
):
    tenant_id = make_tenant("alpha")
    job_id = enqueue(queue, tenant_id)
    (first,) = claim(queue, tenant_id, worker="w-1", lease=timedelta(seconds=1))
    _ = clock.advance(timedelta(seconds=2))
    assert queue.reap(tenant_id=tenant_id) == 1
    (second,) = claim(queue, tenant_id, worker="w-2")

    def state() -> tuple[object, ...]:
        row = fetch_job(tenant_id, job_id)
        return (
            row.status,
            row.attempts,
            row.run_after,
            row.locked_by,
            row.locked_until,
            row.lock_token,
            row.last_error,
            row.finished_at,
        )

    before = state()
    assert queue.heartbeat(first, lease=LEASE) is False
    assert queue.fail(first, "late failure", retry_at=None) is False
    assert queue.fail(first, "later", retry_at=T0 + timedelta(days=1)) is False
    assert queue.complete(first) is False
    assert state() == before
    assert fetch_job(tenant_id, job_id).status is JobStatus.RUNNING
    assert queue.complete(second) is True


def test_only_a_running_job_can_be_extended_completed_or_failed(
    queue: PostgresJobQueue,
):
    # Every exit clears the token; the status check is the second line of defence if
    # a row ever keeps one.
    tenant_id = make_tenant("alpha")
    job_id = enqueue(queue, tenant_id)
    (job,) = claim(queue, tenant_id)
    with tenant_session(system_ctx(tenant_id)) as s:
        _ = s.execute(
            update(JobRow).where(JobRow.id == job_id).values(status=JobStatus.DONE)
        )
    assert queue.heartbeat(job, lease=LEASE) is False
    assert queue.complete(job) is False
    assert queue.fail(job, "late", retry_at=None) is False
    assert fetch_job(tenant_id, job_id).status is JobStatus.DONE


def test_reap_touches_only_expired_leases_of_its_own_tenant(
    queue: PostgresJobQueue, clock: FakeClock
):
    alpha, beta = make_tenant("alpha"), make_tenant("beta")
    stale = enqueue(queue, alpha)
    _ = claim(queue, alpha, lease=timedelta(seconds=1))
    live = enqueue(queue, alpha)
    _ = claim(queue, alpha, lease=LEASE)
    beta_stale = enqueue(queue, beta)
    _ = claim(queue, beta, lease=timedelta(seconds=1))
    _ = clock.advance(timedelta(seconds=5))

    assert queue.reap(tenant_id=alpha) == 1
    assert fetch_job(alpha, stale).status is JobStatus.PENDING
    assert fetch_job(alpha, live).status is JobStatus.RUNNING
    assert fetch_job(beta, beta_stale).status is JobStatus.RUNNING
    assert queue.reap(tenant_id=beta) == 1
    assert fetch_job(beta, beta_stale).status is JobStatus.PENDING


def test_reap_dead_letters_a_job_that_died_on_its_last_attempt(
    queue: PostgresJobQueue, clock: FakeClock
):
    tenant_id = make_tenant("alpha")
    job_id = enqueue(queue, tenant_id, max_attempts=1)
    _ = claim(queue, tenant_id, lease=timedelta(seconds=1))
    _ = clock.advance(timedelta(seconds=2))
    assert queue.reap(tenant_id=tenant_id) == 1
    dead = fetch_job(tenant_id, job_id)
    assert dead.status is JobStatus.DEAD
    assert dead.attempts == 1  # never beyond max_attempts
    assert dead.finished_at == T0 + timedelta(seconds=2)
    assert dead.last_error == "lease expired: the worker stopped heartbeating"
    assert (dead.locked_until, dead.lock_token) == (None, None)
    assert claim(queue, tenant_id) == []


# --- failure, retries and dead jobs --------------------------------------------------


def test_fail_schedules_a_backoff_retry_then_dies_at_max_attempts(
    queue: PostgresJobQueue, clock: FakeClock
):
    tenant_id = make_tenant("alpha")
    job_id = enqueue(queue, tenant_id, max_attempts=3)

    (job,) = claim(queue, tenant_id)
    assert queue.fail(job, "boom 1", retry_at=None) is True
    retrying = fetch_job(tenant_id, job_id)
    assert retrying.status is JobStatus.PENDING
    assert retrying.attempts == 1
    assert retrying.run_after == T0 + timedelta(minutes=2)
    assert retrying.last_error == "boom 1"
    assert (retrying.locked_by, retrying.locked_until, retrying.lock_token) == (
        None,
        None,
        None,
    )
    assert claim(queue, tenant_id) == []  # still backing off

    _ = clock.advance(timedelta(minutes=2))
    (job,) = claim(queue, tenant_id)
    assert job.attempts == 2
    assert queue.fail(job, "boom 2", retry_at=None) is True
    assert fetch_job(tenant_id, job_id).run_after == clock.now + timedelta(minutes=4)

    _ = clock.advance(timedelta(minutes=4))
    (job,) = claim(queue, tenant_id)
    assert job.attempts == 3
    assert queue.fail(job, "boom 3", retry_at=None) is True
    dead = fetch_job(tenant_id, job_id)
    assert dead.status is JobStatus.DEAD
    assert dead.attempts == 3
    assert dead.last_error == "boom 3"
    assert dead.finished_at == clock.now
    assert (dead.locked_until, dead.lock_token) == (None, None)
    _ = clock.advance(timedelta(days=1))
    assert claim(queue, tenant_id) == []


def test_fail_honours_an_explicit_retry_time(queue: PostgresJobQueue, clock: FakeClock):
    tenant_id = make_tenant("alpha")
    job_id = enqueue(queue, tenant_id)
    (job,) = claim(queue, tenant_id)
    retry_at = T0 + timedelta(minutes=7)
    assert queue.fail(job, "later", retry_at=retry_at) is True
    assert fetch_job(tenant_id, job_id).run_after == retry_at
    _ = clock.advance(timedelta(minutes=6, seconds=59))
    assert claim(queue, tenant_id) == []
    _ = clock.advance(timedelta(seconds=1))
    assert [j.id for j in claim(queue, tenant_id)] == [job_id]


def test_fail_on_the_last_attempt_dead_letters_despite_a_retry_time(
    queue: PostgresJobQueue,
):
    tenant_id = make_tenant("alpha")
    job_id = enqueue(queue, tenant_id, max_attempts=1)
    (job,) = claim(queue, tenant_id)
    assert queue.fail(job, "final", retry_at=T0 + timedelta(minutes=1)) is True
    assert fetch_job(tenant_id, job_id).status is JobStatus.DEAD


@pytest.mark.parametrize(
    ("attempts", "minutes"),
    [(1, 2), (2, 4), (3, 8), (4, 16), (5, 32), (6, 60), (7, 60), (10_000, 60)],
)
def test_retry_delay_doubles_each_attempt_up_to_an_hour(attempts: int, minutes: int):
    assert retry_delay(attempts) == timedelta(minutes=minutes)


# --- dedup keys ------------------------------------------------------------------


def test_a_keyed_job_blocks_duplicates_until_it_is_done(queue: PostgresJobQueue):
    tenant_id = make_tenant("alpha")
    first = enqueue(queue, tenant_id, key="slot-1")
    assert try_enqueue(queue, tenant_id, key="slot-1") is None  # PENDING
    (job,) = claim(queue, tenant_id)
    assert try_enqueue(queue, tenant_id, key="slot-1") is None  # RUNNING
    assert queue.complete(job) is True
    again = enqueue(queue, tenant_id, key="slot-1")  # DONE no longer blocks
    assert again != first
    assert len(fetch_jobs(tenant_id)) == 2


def test_dedup_is_scoped_to_kind_and_tenant_and_needs_a_key(queue: PostgresJobQueue):
    alpha, beta = make_tenant("alpha"), make_tenant("beta")
    _ = enqueue(queue, alpha, kind="a.v1", key="k")
    _ = enqueue(queue, alpha, kind="b.v1", key="k")  # another kind
    _ = enqueue(queue, beta, kind="a.v1", key="k")  # another tenant
    _ = enqueue(queue, alpha, kind="a.v1")  # no key, no dedup
    _ = enqueue(queue, alpha, kind="a.v1")
    assert try_enqueue(queue, alpha, kind="a.v1", key="k") is None


def test_enqueue_joins_the_callers_transaction(queue: PostgresJobQueue):
    tenant_id = make_tenant("alpha")
    msg = "abort"
    with (
        pytest.raises(RuntimeError, match=msg),
        tenant_session(system_ctx(tenant_id)) as s,
    ):
        assert queue.enqueue(s, kind="demo.task", payload={}) is not None
        raise RuntimeError(msg)
    assert fetch_jobs(tenant_id) == []


def test_enqueue_and_schedule_serve_an_operator_scoped_to_a_tenant(
    queue: PostgresJobQueue,
):
    # How tenant creation will call them: the operator's session, switched to the
    # new tenant, in the same transaction that creates it.
    tenant_id = make_tenant("alpha")
    operator = TenantContext.platform("cli:test", tenant_id=tenant_id)
    with tenant_session(operator) as s:
        assert queue.enqueue(s, kind="demo.task", payload={}) is not None
        _ = queue.schedule(
            s, name="hourly", cron="0 * * * *", tz="UTC", kind="demo.task", payload={}
        )
    assert len(fetch_jobs(tenant_id)) == 1


def test_enqueue_needs_a_session_bound_to_a_tenant(queue: PostgresJobQueue):
    with tenant_session(platform_ctx()) as s, pytest.raises(ContextMissingError):
        _ = queue.enqueue(s, kind="demo.task", payload={})


# --- registry and tenants --------------------------------------------------------


def test_job_handler_raises_a_key_error_naming_the_kind():
    with pytest.raises(KeyError, match=r"never\.registered"):
        _ = job_handler("never.registered")


def test_registering_a_kind_again_replaces_its_handler():
    kind = unique_kind()

    def first(session, job):
        pass

    def second(session, job):
        pass

    register_job_handler(kind, first)
    assert job_handler(kind) is first
    register_job_handler(kind, second)
    assert job_handler(kind) is second


# --- the runner ------------------------------------------------------------------


def make_runner(queue: PostgresJobQueue, **options) -> JobRunner:
    return JobRunner(queue, get_sessionmaker(), worker_id="w-1", **options)


def register_noop() -> str:
    kind = unique_kind()

    def noop(session, job):
        pass

    register_job_handler(kind, noop)
    return kind


def test_the_handler_runs_under_the_jobs_tenant_context_and_commits_its_writes(
    queue: PostgresJobQueue,
):
    tenant_id = make_tenant("alpha")
    seen: list[tuple[tuple[str, ...], Job]] = []
    kind = unique_kind()

    def handler(session, job):
        gucs = session.execute(
            text("""
                SELECT current_setting('app.tenant_id', true),
                       current_setting('app.actor_type', true),
                       current_setting('app.actor_id', true)
            """)
        ).one()
        seen.append((tuple(gucs), job))
        _ = queue.enqueue(session, kind="follow.up", payload={"after": str(job.id)})

    register_job_handler(kind, handler)
    job_id = enqueue(queue, tenant_id, kind=kind, payload={"n": 1})

    # Only this kind: the follow-up the handler enqueues would be due in this pass.
    assert make_runner(queue, kinds=[kind]).run_once() == 1
    ((gucs, job),) = seen
    assert gucs == (str(tenant_id), "SYSTEM", f"job:{job_id}")
    assert (job.id, job.payload, job.attempts) == (job_id, {"n": 1}, 1)
    done = fetch_job(tenant_id, job_id)
    assert done.status is JobStatus.DONE
    assert done.locked_by == "w-1"
    (follow_up,) = fetch_jobs(tenant_id, kind="follow.up")
    assert follow_up.payload == {"after": str(job_id)}


def test_run_once_runs_up_to_limit_jobs_per_tenant(queue: PostgresJobQueue):
    alpha, beta = make_tenant("alpha"), make_tenant("beta")
    ran: list[UUID] = []
    kind = unique_kind()

    def record(session, job):
        ran.append(job.id)

    register_job_handler(kind, record)
    expected = {enqueue(queue, alpha, kind=kind) for _ in range(3)}
    expected.add(enqueue(queue, beta, kind=kind))

    runner = make_runner(queue)
    assert runner.run_once(limit=2) == 3  # two of alpha's, one of beta's
    assert runner.run_once(limit=2) == 1
    assert runner.run_once(limit=2) == 0
    assert set(ran) == expected
    assert len(ran) == 4


def test_a_runner_with_kinds_leaves_other_kinds_alone(queue: PostgresJobQueue):
    tenant_id = make_tenant("alpha")
    wanted, other = register_noop(), register_noop()
    wanted_id = enqueue(queue, tenant_id, kind=wanted)
    other_id = enqueue(queue, tenant_id, kind=other)
    assert make_runner(queue, kinds=[wanted]).run_once() == 1
    assert fetch_job(tenant_id, wanted_id).status is JobStatus.DONE
    assert fetch_job(tenant_id, other_id).status is JobStatus.PENDING


def test_run_once_reaps_expired_leases_before_it_claims(
    queue: PostgresJobQueue, clock: FakeClock
):
    tenant_id = make_tenant("alpha")
    job_id = enqueue(queue, tenant_id, kind=register_noop())
    _ = claim(queue, tenant_id, worker="w-dead", lease=timedelta(seconds=1))
    _ = clock.advance(timedelta(seconds=5))
    assert make_runner(queue).run_once() == 1
    done = fetch_job(tenant_id, job_id)
    assert done.status is JobStatus.DONE
    assert (done.attempts, done.locked_by) == (2, "w-1")


def test_a_handler_that_raises_twice_succeeds_on_the_third_attempt(
    queue: PostgresJobQueue, clock: FakeClock
):
    tenant_id = make_tenant("alpha")
    attempts: list[int] = []
    kind = unique_kind()

    def flaky(session, job):
        attempts.append(job.attempts)
        if len(attempts) < 3:
            msg = f"boom {len(attempts)}"
            raise RuntimeError(msg)

    register_job_handler(kind, flaky)
    job_id = enqueue(queue, tenant_id, kind=kind)
    runner = make_runner(queue)

    assert runner.run_once() == 1
    first = fetch_job(tenant_id, job_id)
    assert first.status is JobStatus.PENDING
    assert first.last_error == "RuntimeError: boom 1"
    assert first.run_after == T0 + timedelta(minutes=2)
    assert runner.run_once() == 0  # backing off

    _ = clock.advance(timedelta(minutes=2))
    assert runner.run_once() == 1
    assert fetch_job(tenant_id, job_id).last_error == "RuntimeError: boom 2"

    _ = clock.advance(timedelta(minutes=4))
    assert runner.run_once() == 1
    done = fetch_job(tenant_id, job_id)
    assert done.status is JobStatus.DONE
    assert done.attempts == 3
    assert done.last_error is None  # the earlier failures are history, not state
    assert attempts == [1, 2, 3]
    assert runner.run_once() == 0


def test_a_job_that_keeps_failing_goes_dead_at_max_attempts(
    queue: PostgresJobQueue, clock: FakeClock
):
    tenant_id = make_tenant("alpha")
    kind = unique_kind()

    def always(session, job):
        msg = "never works"
        raise ValueError(msg)

    register_job_handler(kind, always)
    job_id = enqueue(queue, tenant_id, kind=kind, max_attempts=2)
    runner = make_runner(queue)
    assert runner.run_once() == 1
    _ = clock.advance(timedelta(minutes=2))
    assert runner.run_once() == 1
    dead = fetch_job(tenant_id, job_id)
    assert dead.status is JobStatus.DEAD
    assert dead.attempts == 2
    assert dead.last_error == "ValueError: never works"
    _ = clock.advance(timedelta(days=1))
    assert runner.run_once() == 0


def test_a_failing_handler_rolls_back_what_it_wrote(queue: PostgresJobQueue):
    tenant_id = make_tenant("alpha")
    kind = unique_kind()

    def handler(session, job):
        _ = queue.enqueue(session, kind="follow.up", payload={})
        msg = "boom"
        raise RuntimeError(msg)

    register_job_handler(kind, handler)
    job_id = enqueue(queue, tenant_id, kind=kind)
    assert make_runner(queue).run_once() == 1
    assert fetch_jobs(tenant_id, kind="follow.up") == []
    assert fetch_job(tenant_id, job_id).status is JobStatus.PENDING


def test_a_kind_without_a_handler_fails_the_job_with_a_clear_error(
    queue: PostgresJobQueue,
):
    tenant_id = make_tenant("alpha")
    job_id = enqueue(queue, tenant_id, kind="nobody.handles.this")
    assert make_runner(queue).run_once() == 1
    row = fetch_job(tenant_id, job_id)
    assert row.status is JobStatus.PENDING  # a newer deploy may register it
    assert row.last_error == "no handler registered for job kind 'nobody.handles.this'"


def test_a_kind_nobody_handles_is_not_retried_forever(
    queue: PostgresJobQueue, clock: FakeClock
):
    tenant_id = make_tenant("alpha")
    job_id = enqueue(queue, tenant_id, kind="nobody.handles.this", max_attempts=3)
    runner = make_runner(queue)
    for attempt in (1, 2, 3):
        assert runner.run_once() == 1
        assert fetch_job(tenant_id, job_id).attempts == attempt
        _ = clock.advance(timedelta(days=1))  # past any backoff
    dead = fetch_job(tenant_id, job_id)
    assert dead.status is JobStatus.DEAD
    assert dead.last_error == "no handler registered for job kind 'nobody.handles.this'"
    assert runner.run_once() == 0


def test_database_errors_are_stored_without_the_values_they_echo(
    queue: PostgresJobQueue,
):
    tenant_id = make_tenant("alpha")
    kind = unique_kind()

    def handler(session, job):
        _ = session.execute(text("SELECT CAST('ana@example.mx' AS uuid)"))

    register_job_handler(kind, handler)
    job_id = enqueue(queue, tenant_id, kind=kind)
    assert make_runner(queue).run_once() == 1
    error = fetch_job(tenant_id, job_id).last_error
    assert error == "DataError [sqlstate=22P02]"


def run_failing_job(queue: PostgresJobQueue, handler) -> JobRow:
    # Runs one job whose handler is ``handler``; run_once must come back having
    # recorded the failure, whatever the handler raised. Returns the job's row.
    tenant_id = make_tenant("alpha")
    kind = unique_kind()
    register_job_handler(kind, handler)
    job_id = enqueue(queue, tenant_id, kind=kind)
    assert make_runner(queue, kinds=[kind]).run_once() == 1
    return fetch_job(tenant_id, job_id)


def run_failing(queue: PostgresJobQueue, handler) -> str | None:
    # Runs one job whose handler is ``handler``; returns what it left in last_error.
    return run_failing_job(queue, handler).last_error


def assert_retried_not_left_running(row: JobRow) -> None:
    # The failure was recorded: back to PENDING with the first backoff, lease released.
    assert row.status is JobStatus.PENDING
    assert row.attempts == 1
    assert row.run_after == T0 + timedelta(minutes=2)
    assert (row.locked_by, row.locked_until, row.lock_token) == (None, None, None)


def test_a_driver_error_raised_on_the_raw_cursor_is_stored_without_its_values(
    queue: PostgresJobQueue,
):
    def handler(session, job):
        # A bulk-load style handler on the DBAPI connection: psycopg's own error
        # reaches the runner, with no SQLAlchemy wrapper around it.
        cursor = session.connection().connection.cursor()
        cursor.execute("SELECT CAST('ana@example.mx' AS uuid)")

    error = run_failing(queue, handler)
    assert error == "InvalidTextRepresentation [sqlstate=22P02]"


def test_an_error_that_embeds_the_database_message_is_stored_without_it(
    queue: PostgresJobQueue,
):
    def handler(session, job):
        try:
            _ = session.execute(text("SELECT CAST('ana@example.mx' AS uuid)"))
        except DataError as exc:
            msg = f"party load failed: {exc}"  # the usual way to add context
            raise RuntimeError(msg) from exc

    error = run_failing(queue, handler)
    assert error == "RuntimeError [sqlstate=22P02]"


def test_a_validation_error_is_stored_without_the_input_values(
    queue: PostgresJobQueue,
):
    class Party(BaseModel):
        curp: str = Field(max_length=5)

    def handler(session, job):
        _ = Party(curp="ABCD010101HDFXXX01")  # pydantic echoes the input value

    error = run_failing(queue, handler)
    assert error == "ValidationError (1 errors)"


def test_a_plain_exception_is_stored_with_its_message(queue: PostgresJobQueue):
    def handler(session, job):
        msg = "quantity must be positive"
        raise ValueError(msg)

    assert run_failing(queue, handler) == "ValueError: quantity must be positive"


def test_an_exception_whose_str_raises_still_fails_the_job(queue: PostgresJobQueue):
    class OddError(Exception):
        @override
        def __str__(self) -> str:
            msg = "self.response is None"
            raise AttributeError(msg)

    def handler(session, job):
        raise OddError

    row = run_failing_job(queue, handler)
    assert_retried_not_left_running(row)
    assert row.last_error == "OddError: <unprintable OddError>"


def test_a_nul_and_a_lone_surrogate_in_the_message_are_cleaned_before_storing(
    queue: PostgresJobQueue,
):
    def handler(session, job):
        # Postgres text cannot hold a NUL, and psycopg cannot encode a lone surrogate.
        msg = "invalid literal: 'a\x00b' and 'c\ud800d'"
        raise ValueError(msg)

    row = run_failing_job(queue, handler)
    assert_retried_not_left_running(row)
    assert row.last_error == "ValueError: invalid literal: 'ab' and 'c?d'"


def test_a_database_error_hidden_by_raise_from_none_is_still_scrubbed(
    queue: PostgresJobQueue,
):
    def handler(session, job):
        try:
            _ = session.execute(text("SELECT CAST('ana@example.mx' AS uuid)"))
        except DataError as exc:
            msg = f"party load failed: {exc}"
            # `from None` hides the database error from tracebacks, but it is still
            # the new exception's __context__, and its text is in the message.
            raise RuntimeError(msg) from None

    row = run_failing_job(queue, handler)
    assert_retried_not_left_running(row)
    assert row.last_error == "RuntimeError [sqlstate=22P02]"


def test_a_connection_error_is_stored_by_class_alone(queue: PostgresJobQueue):
    # SQLAlchemy wraps whatever the driver raised; with no SQLSTATE there is only
    # the class to keep.
    tenant_id = make_tenant("alpha")
    kind = unique_kind()

    def handler(session, job):
        statement = "SELECT 1"
        driver_error = ConnectionError("server closed")
        raise OperationalError(statement, {}, driver_error)

    register_job_handler(kind, handler)
    job_id = enqueue(queue, tenant_id, kind=kind)
    assert make_runner(queue).run_once() == 1
    assert fetch_job(tenant_id, job_id).last_error == "OperationalError"


def test_a_long_error_message_is_cut_to_a_bounded_length(queue: PostgresJobQueue):
    tenant_id = make_tenant("alpha")
    kind = unique_kind()

    def handler(session, job):
        raise RuntimeError("x" * 10 * EXCEPTION_SUMMARY_LIMIT)

    register_job_handler(kind, handler)
    job_id = enqueue(queue, tenant_id, kind=kind)
    assert make_runner(queue).run_once() == 1
    error = fetch_job(tenant_id, job_id).last_error
    assert error is not None
    assert len(error) == EXCEPTION_SUMMARY_LIMIT
    assert error.startswith("RuntimeError: xxx")


def heartbeat_threads() -> list[threading.Thread]:
    return [
        t for t in threading.enumerate() if t.name.startswith(HEARTBEAT_THREAD_PREFIX)
    ]


def test_the_heartbeat_thread_extends_the_lease_while_the_handler_runs(
    queue: PostgresJobQueue, clock: FakeClock
):
    tenant_id = make_tenant("alpha")
    kind = unique_kind()
    lease = timedelta(milliseconds=200)  # a beat every 100 ms of real time
    extended: list[bool] = []

    def slow(session, job):
        _ = clock.advance(timedelta(minutes=5))
        wanted = T0 + timedelta(minutes=5) + lease
        extended.append(
            eventually(lambda: fetch_job(job.tenant_id, job.id).locked_until == wanted)
        )

    register_job_handler(kind, slow)
    job_id = enqueue(queue, tenant_id, kind=kind)
    assert make_runner(queue, lease=lease).run_once() == 1
    assert extended == [True]
    assert fetch_job(tenant_id, job_id).status is JobStatus.DONE
    assert heartbeat_threads() == []  # stopped and joined before run_once returned


class FlakyHeartbeatQueue(PostgresJobQueue):
    """A queue whose first heartbeat hits a database error."""

    beats: int = 0

    @override
    def heartbeat(self, job: Job, *, lease: timedelta) -> bool:
        self.beats += 1
        if self.beats == 1:
            msg = "connection reset"
            raise ConnectionError(msg)
        return super().heartbeat(job, lease=lease)


def test_a_transient_heartbeat_error_does_not_stop_the_heartbeat(
    engine: Engine, clock: FakeClock
):
    flaky = FlakyHeartbeatQueue(get_sessionmaker(), clock_fn=clock)
    tenant_id = make_tenant("alpha")
    kind = unique_kind()
    survived: list[bool] = []

    def slow(session, job):
        survived.append(eventually(lambda: flaky.beats >= 3))

    register_job_handler(kind, slow)
    job_id = enqueue(flaky, tenant_id, kind=kind)
    runner = JobRunner(
        flaky, get_sessionmaker(), worker_id="w-1", lease=timedelta(milliseconds=100)
    )
    assert runner.run_once() == 1
    assert survived == [True]
    assert fetch_job(tenant_id, job_id).status is JobStatus.DONE


@pytest.mark.parametrize("outcome", ["returns", "raises"])
def test_a_handler_that_outlives_its_lease_cannot_overwrite_the_new_owner(
    queue: PostgresJobQueue, clock: FakeClock, outcome: str
):
    tenant_id = make_tenant("alpha")
    kind = unique_kind()
    started, release = threading.Event(), threading.Event()

    def hangs(session, job):
        started.set()
        assert release.wait(timeout=30)
        if outcome == "raises":
            msg = "failed after the lease was lost"
            raise RuntimeError(msg)

    register_job_handler(kind, hangs)
    job_id = enqueue(queue, tenant_id, kind=kind)
    slow_worker = JobRunner(
        queue, get_sessionmaker(), worker_id="w-slow", lease=timedelta(seconds=60)
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(slow_worker.run_once)
        assert started.wait(timeout=10)
        _ = clock.advance(timedelta(minutes=5))  # the slow worker's lease is long gone
        assert queue.reap(tenant_id=tenant_id) == 1
        (taken_over,) = claim(queue, tenant_id, worker="w-fast")
        owned = fetch_job(tenant_id, job_id)
        release.set()  # the slow handler finishes while w-fast still owns the job
        assert running.result(timeout=10) == 1

    # The stale run's complete (or fail) must have changed nothing.
    after = fetch_job(tenant_id, job_id)
    assert after.status is JobStatus.RUNNING
    assert (after.locked_by, after.lock_token) == ("w-fast", taken_over.lock_token)
    assert after.locked_until == owned.locked_until == clock.now + LEASE
    assert (after.attempts, after.last_error) == (2, owned.last_error)

    assert queue.complete(taken_over) is True
    done = fetch_job(tenant_id, job_id)
    assert done.status is JobStatus.DONE
    assert done.locked_by == "w-fast"
    assert done.attempts == 2


def test_the_heartbeat_stops_itself_when_the_lease_is_lost(
    queue: PostgresJobQueue, caplog: pytest.LogCaptureFixture
):
    tenant_id = make_tenant("alpha")
    kind = unique_kind()
    stopped: list[bool] = []

    def hangs(session, job):
        # Another worker takes the job over: a new token, so the next beat fails.
        with tenant_session(system_ctx(job.tenant_id)) as s:
            _ = s.execute(
                update(JobRow)
                .where(JobRow.id == job.id)
                .values(lock_token=uuid4(), locked_by="w-fast")
            )
        stopped.append(eventually(lambda: heartbeat_threads() == []))

    register_job_handler(kind, hangs)
    job_id = enqueue(queue, tenant_id, kind=kind)
    caplog.set_level(logging.WARNING)
    runner = make_runner(queue, lease=timedelta(milliseconds=200))
    assert runner.run_once() == 1

    assert stopped == [True]
    messages = [record.getMessage() for record in caplog.records]
    assert "job lease lost; another worker may run it again" in messages
    assert (
        "job finished after losing its lease; its outcome was not recorded" in messages
    )
    taken = fetch_job(tenant_id, job_id)
    assert (taken.status, taken.locked_by) == (JobStatus.RUNNING, "w-fast")


def test_with_the_default_clock_a_job_runs_end_to_end(engine: Engine):
    # Every other test moves time by hand; this one lets the wall clock drive it.
    queue = PostgresJobQueue(get_sessionmaker())
    tenant_id = make_tenant("alpha")
    job_id = enqueue(queue, tenant_id, kind=register_noop())
    before = datetime.now(UTC)
    assert make_runner(queue).run_once() == 1
    after = datetime.now(UTC)
    done = fetch_job(tenant_id, job_id)
    assert done.status is JobStatus.DONE
    assert done.finished_at is not None
    assert before <= done.finished_at <= after
    assert done.created_at <= before


def test_two_runners_in_threads_run_every_job_exactly_once(queue: PostgresJobQueue):
    tenant_id = make_tenant("alpha")
    kind = unique_kind()
    ran: list[UUID] = []
    guard = threading.Lock()

    def record(session, job):
        with guard:
            ran.append(job.id)

    register_job_handler(kind, record)
    expected = {enqueue(queue, tenant_id, kind=kind) for _ in range(30)}
    runners = [
        JobRunner(queue, get_sessionmaker(), worker_id=f"w-{n}", kinds=[kind])
        for n in range(2)
    ]
    start = threading.Barrier(2)

    def drain(runner: JobRunner) -> int:
        _ = start.wait(timeout=10)
        total = 0
        while count := runner.run_once(limit=5):
            total += count
        return total

    with ThreadPoolExecutor(max_workers=2) as pool:
        counts = list(pool.map(drain, runners))
    assert sum(counts) == 30
    assert sorted(ran) == sorted(expected)  # each job ran once, none twice
    statuses = {row.status for row in fetch_jobs(tenant_id, kind=kind)}
    assert statuses == {JobStatus.DONE}


@pytest.mark.parametrize("lease", [timedelta(0), timedelta(seconds=-1)])
def test_a_runner_refuses_a_lease_that_is_not_positive(
    queue: PostgresJobQueue, lease: timedelta
):
    with pytest.raises(ValueError, match="lease must be positive"):
        _ = make_runner(queue, lease=lease)


# --- M5: the reaper's work is reported ---------------------------------------------


def test_run_once_reports_the_leases_it_reaped(
    queue: PostgresJobQueue, clock: FakeClock, caplog: pytest.LogCaptureFixture
):
    tenant_id = make_tenant("alpha")
    _ = enqueue(queue, tenant_id, kind=register_noop())
    _ = claim(queue, tenant_id, worker="w-dead", lease=timedelta(seconds=1))
    _ = clock.advance(timedelta(seconds=5))
    caplog.set_level(logging.WARNING)

    assert make_runner(queue).run_once() == 1
    (record,) = [r for r in caplog.records if "reaped" in r.getMessage()]
    assert record.getMessage() == "reaped jobs whose lease had expired"
    assert record.levelno == logging.WARNING
    extra = vars(record)  # the fields the runner attached to the record
    assert (extra["reaped"], extra["tenant_id"]) == (1, str(tenant_id))

    caplog.clear()
    assert make_runner(queue).run_once() == 0  # nothing left to reap: nothing logged
    assert caplog.records == []


# --- M8: arguments that would be misread are refused --------------------------------


def test_enqueue_refuses_a_naive_run_after(queue: PostgresJobQueue):
    tenant_id = make_tenant("alpha")
    naive = T0.replace(tzinfo=None)
    with (
        tenant_session(system_ctx(tenant_id)) as s,
        pytest.raises(ValueError, match="run_after must be timezone-aware"),
    ):
        _ = queue.enqueue(s, kind="demo.task", payload={}, run_after=naive)
    assert fetch_jobs(tenant_id) == []


@pytest.mark.parametrize("max_attempts", [0, -1])
def test_enqueue_refuses_a_max_attempts_below_one(
    queue: PostgresJobQueue, max_attempts: int
):
    tenant_id = make_tenant("alpha")
    with (
        tenant_session(system_ctx(tenant_id)) as s,
        pytest.raises(ValueError, match="max_attempts must be at least 1"),
    ):
        _ = queue.enqueue(s, kind="demo.task", payload={}, max_attempts=max_attempts)
    assert fetch_jobs(tenant_id) == []


def test_fail_refuses_a_naive_retry_time_and_writes_nothing(queue: PostgresJobQueue):
    tenant_id = make_tenant("alpha")
    job_id = enqueue(queue, tenant_id)
    (job,) = claim(queue, tenant_id)
    naive = T0.replace(tzinfo=None) + timedelta(minutes=5)
    with pytest.raises(ValueError, match="retry_at must be timezone-aware"):
        _ = queue.fail(job, "boom", retry_at=naive)
    assert fetch_job(tenant_id, job_id).status is JobStatus.RUNNING


def test_claim_refuses_a_negative_limit_and_a_zero_limit_claims_nothing(
    queue: PostgresJobQueue,
):
    tenant_id = make_tenant("alpha")
    _ = enqueue(queue, tenant_id)
    with pytest.raises(ValueError, match="limit must not be negative"):
        _ = claim(queue, tenant_id, limit=-1)
    assert claim(queue, tenant_id, limit=0) == []
    assert len(claim(queue, tenant_id, limit=1)) == 1


# --- M10: last_error is history, DONE is state ---------------------------------------


def test_complete_clears_the_error_an_earlier_attempt_left(
    queue: PostgresJobQueue, clock: FakeClock
):
    tenant_id = make_tenant("alpha")
    job_id = enqueue(queue, tenant_id)
    (job,) = claim(queue, tenant_id)
    assert queue.fail(job, "boom 1", retry_at=None) is True
    assert fetch_job(tenant_id, job_id).last_error == "boom 1"
    _ = clock.advance(timedelta(minutes=2))
    (job,) = claim(queue, tenant_id)
    assert queue.complete(job) is True
    done = fetch_job(tenant_id, job_id)
    assert (done.status, done.last_error) == (JobStatus.DONE, None)


# --- threads: a round runs its jobs side by side -------------------------------------


def test_a_runner_with_threads_runs_its_jobs_side_by_side(queue: PostgresJobQueue):
    tenant_id = make_tenant("alpha")
    kind = unique_kind()
    ran: list[UUID] = []
    guard = threading.Lock()
    # Each job waits until two more are running beside it: only three threads
    # running the round at once get every job through.
    together = threading.Barrier(3, timeout=10)

    def record(session, job):
        _ = together.wait()
        with guard:
            ran.append(job.id)

    register_job_handler(kind, record)
    expected = {enqueue(queue, tenant_id, kind=kind) for _ in range(6)}

    assert make_runner(queue, threads=3).run_once(limit=6) == 6
    assert sorted(ran) == sorted(expected)
    assert {row.status for row in fetch_jobs(tenant_id, kind=kind)} == {JobStatus.DONE}


def test_an_idle_round_claims_once_per_tenant_whatever_its_threads(
    queue: PostgresJobQueue, monkeypatch: pytest.MonkeyPatch
):
    tenants = {make_tenant("alpha"), make_tenant("beta")}
    claimed: list[UUID] = []
    claim_jobs = queue.claim

    def counting(**options):
        claimed.append(options["tenant_id"])
        return claim_jobs(**options)

    monkeypatch.setattr(queue, "claim", counting)
    assert make_runner(queue, threads=4).run_once() == 0
    # Helper threads start only once a job turns up, so an idle tick costs one
    # look per tenant, as with one thread.
    assert sorted(claimed) == sorted(tenants)


@pytest.mark.parametrize("threads", [1, 3])
def test_a_kinds_backlog_leaves_room_in_the_round_for_other_kinds(
    queue: PostgresJobQueue, clock: FakeClock, threads: int
):
    tenant_id = make_tenant("alpha")
    backlog, other = register_noop(), register_noop()
    for _ in range(12):
        _ = enqueue(queue, tenant_id, kind=backlog)
    _ = clock.advance(timedelta(minutes=1))
    # Due after the whole backlog: oldest first alone would run it last.
    late = enqueue(queue, tenant_id, kind=other)

    assert make_runner(queue, threads=threads).run_once(limit=4) == 5
    assert fetch_job(tenant_id, late).status is JobStatus.DONE
    done = [
        r for r in fetch_jobs(tenant_id, kind=backlog) if r.status is JobStatus.DONE
    ]
    assert len(done) == 4


@pytest.mark.parametrize("threads", [1, 3])
def test_run_once_keeps_its_limit_per_tenant_with_threads(
    queue: PostgresJobQueue, threads: int
):
    alpha, beta = make_tenant("alpha"), make_tenant("beta")
    kind = register_noop()
    for _ in range(5):
        _ = enqueue(queue, alpha, kind=kind)
    beta_job = enqueue(queue, beta, kind=kind)

    # Alpha's backlog takes its two places; beta's job still runs in the round.
    assert make_runner(queue, threads=threads).run_once(limit=2) == 3
    assert fetch_job(beta, beta_job).status is JobStatus.DONE
    statuses = [row.status for row in fetch_jobs(alpha, kind=kind)]
    assert statuses.count(JobStatus.DONE) == 2


def test_a_runner_takes_its_default_limit_from_its_batch(queue: PostgresJobQueue):
    tenant_id = make_tenant("alpha")
    kind = register_noop()
    for _ in range(5):
        _ = enqueue(queue, tenant_id, kind=kind)
    assert make_runner(queue, batch=3).run_once() == 3


def test_a_stopping_round_claims_no_more_and_waits_for_its_running_jobs(
    queue: PostgresJobQueue,
):
    tenant_id = make_tenant("alpha")
    kind = unique_kind()
    stop = threading.Event()
    both_running = threading.Barrier(2, timeout=10)

    def slow(session, job):
        _ = both_running.wait()
        stop.set()
        time.sleep(0.2)  # still running when the round is asked to stop

    register_job_handler(kind, slow)
    for _ in range(6):
        _ = enqueue(queue, tenant_id, kind=kind)

    ran = make_runner(queue, threads=2).run_once(limit=6, stopping=stop.is_set)

    assert ran == 2
    statuses = [row.status for row in fetch_jobs(tenant_id, kind=kind)]
    # Both jobs that had started finished before run_once returned.
    assert sorted(statuses) == sorted([JobStatus.DONE] * 2 + [JobStatus.PENDING] * 4)


def test_a_runner_threads_failure_surfaces_once_the_others_have_finished(
    queue: PostgresJobQueue,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    tenant_id = make_tenant("alpha")
    kind = unique_kind()
    first_running = threading.Event()

    def wait_for_the_failure(session, job):
        first_running.set()
        time.sleep(0.2)

    register_job_handler(kind, wait_for_the_failure)
    for _ in range(3):
        _ = enqueue(queue, tenant_id, kind=kind)
    claim_jobs = queue.claim
    calls: list[int] = []

    def flaky(**options):
        calls.append(1)
        if len(calls) == 2:
            _ = first_running.wait(10)
            msg = "database unreachable"
            raise RuntimeError(msg)
        return claim_jobs(**options)

    monkeypatch.setattr(queue, "claim", flaky)
    caplog.set_level(logging.ERROR, logger="xolotl.platform.jobs.runner")
    with pytest.raises(RuntimeError, match="database unreachable"):
        _ = make_runner(queue, threads=2).run_once(limit=3)
    # The helper's claim failed (the caller's thread was running the first job):
    # it logged the error itself, through the redacting handler.
    (logged,) = [r for r in caplog.records if r.name == "xolotl.platform.jobs.runner"]
    assert logged.getMessage() == "job runner thread failed"
    assert logged.exc_info is not None
    statuses = [row.status for row in fetch_jobs(tenant_id, kind=kind)]
    # The job claimed before the failure was not abandoned mid-run.
    assert JobStatus.RUNNING not in statuses
    assert statuses.count(JobStatus.DONE) >= 1


def test_a_runner_refuses_fewer_than_one_thread_or_job(queue: PostgresJobQueue):
    with pytest.raises(ValueError, match="threads"):
        _ = make_runner(queue, threads=0)
    with pytest.raises(ValueError, match="batch"):
        _ = make_runner(queue, batch=0)


def test_a_round_takes_its_tenants_in_turn(queue: PostgresJobQueue):
    alpha, beta = make_tenant("alpha"), make_tenant("beta")
    kind = unique_kind()
    order: list[UUID] = []

    def record(session, job):
        order.append(job.tenant_id)

    register_job_handler(kind, record)
    for tenant_id in (alpha, beta):
        for _ in range(3):
            _ = enqueue(queue, tenant_id, kind=kind)

    assert make_runner(queue).run_once(limit=10) == 6
    # One tenant's backlog does not hold the other's jobs until it is through.
    assert all(first != second for first, second in pairwise(order))
