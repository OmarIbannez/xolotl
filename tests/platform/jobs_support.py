"""Helpers shared by the job queue, runner and scheduler tests."""

import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from pydantic import JsonValue
from sqlalchemy import select

from tests.helpers import system_ctx
from xolotl.platform.db import tenant_session
from xolotl.platform.jobs.models import JobRow, JobSchedule
from xolotl.platform.jobs.queue import Job, PostgresJobQueue

T0 = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
LEASE = timedelta(seconds=60)
SAMPLE_PAYLOAD: dict[str, JsonValue] = {
    "entity_id": "42",
    "steps": [1, 2],
    "nested": {"ok": True},
}


class FakeClock:
    """A settable clock for the ``clock_fn`` seam: time moves only when told to."""

    def __init__(self, start: datetime = T0) -> None:
        self.now: datetime = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> datetime:
        self.now += delta
        return self.now


def unique_kind(prefix: str = "test") -> str:
    """Return a job kind no other test registers a handler for.

    Returns:
        ``<prefix>.<8 hex digits>``.

    """
    return f"{prefix}.{uuid4().hex[:8]}"


def try_enqueue(
    queue: PostgresJobQueue,
    tenant_id: UUID,
    *,
    kind: str = "demo.task",
    payload: dict[str, JsonValue] | None = None,
    run_after: datetime | None = None,
    key: str | None = None,
    max_attempts: int = 8,
) -> UUID | None:
    """Enqueue one job in its own committed transaction.

    Returns:
        The job id, or ``None`` when the dedup index rejected it.

    """
    with tenant_session(system_ctx(tenant_id)) as s:
        return queue.enqueue(
            s,
            kind=kind,
            payload=payload if payload is not None else {},
            run_after=run_after,
            key=key,
            max_attempts=max_attempts,
        )


def enqueue(
    queue: PostgresJobQueue,
    tenant_id: UUID,
    *,
    kind: str = "demo.task",
    payload: dict[str, JsonValue] | None = None,
    run_after: datetime | None = None,
    key: str | None = None,
    max_attempts: int = 8,
) -> UUID:
    """Enqueue one job that is expected to be accepted.

    Returns:
        The job id.

    """
    job_id = try_enqueue(
        queue,
        tenant_id,
        kind=kind,
        payload=payload,
        run_after=run_after,
        key=key,
        max_attempts=max_attempts,
    )
    assert job_id is not None, "the dedup index rejected the job"
    return job_id


def claim(
    queue: PostgresJobQueue,
    tenant_id: UUID,
    *,
    worker: str = "w-1",
    limit: int = 5,
    lease: timedelta = LEASE,
    kinds: Sequence[str] | None = None,
) -> list[Job]:
    return queue.claim(
        worker_id=worker, tenant_id=tenant_id, kinds=kinds, limit=limit, lease=lease
    )


def fetch_job(tenant_id: UUID, job_id: UUID) -> JobRow:
    with tenant_session(system_ctx(tenant_id)) as s:
        return s.scalars(select(JobRow).where(JobRow.id == job_id)).one()


def fetch_jobs(tenant_id: UUID, *, kind: str | None = None) -> list[JobRow]:
    with tenant_session(system_ctx(tenant_id)) as s:
        query = select(JobRow).order_by(JobRow.created_at, JobRow.id)
        if kind is not None:
            query = query.where(JobRow.kind == kind)
        return list(s.scalars(query))


def fetch_schedule(tenant_id: UUID, name: str) -> JobSchedule:
    with tenant_session(system_ctx(tenant_id)) as s:
        return s.scalars(select(JobSchedule).where(JobSchedule.name == name)).one()


def eventually(condition: Callable[[], bool], *, timeout: float = 10.0) -> bool:
    """Poll ``condition`` until it holds or ``timeout`` real seconds pass.

    For state another thread produces; time the code under test reads goes through
    ``FakeClock`` instead.

    Returns:
        Whether ``condition`` held before the deadline.

    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return condition()
