"""The one tenant read the dispatcher, the job runner and the scheduler share.

Row-level security hides every tenant table from a session without a tenant, so the
background loops list the tenants through a platform-operator read of ``tenant`` and
then work inside each tenant's own scope. All three loops go through
``tenant_ids``; the tests below pin what that means for each of them.
"""

from collections.abc import Generator
from datetime import timedelta
from uuid import UUID

import pytest
from sqlalchemy import Engine, select, update
from sqlalchemy.orm import Session

from tests.helpers import make_tenant, system_ctx
from tests.platform.jobs_support import unique_kind
from xolotl.platform import clock
from xolotl.platform.db import get_sessionmaker, tenant_session
from xolotl.platform.enums import JobStatus, OutboxStatus, TenantStatus
from xolotl.platform.events.bus import OutboxEventBus
from xolotl.platform.events.event import Event
from xolotl.platform.events.models import OutboxRow
from xolotl.platform.events.registry import register_subscriber, reset_subscribers
from xolotl.platform.ids import uuid7
from xolotl.platform.jobs.models import JobRow, JobSchedule
from xolotl.platform.jobs.queue import Job, PostgresJobQueue, register_job_handler
from xolotl.platform.jobs.runner import JobRunner
from xolotl.platform.jobs.scheduler import Scheduler
from xolotl.platform.tenancy.tenants import tenant_ids


@pytest.fixture
def clean_registry() -> Generator[None]:
    reset_subscribers()
    yield
    reset_subscribers()


def test_every_tenant_is_listed_oldest_first_whatever_its_status(engine: Engine):
    alpha = make_tenant("alpha")
    beta = make_tenant("beta", status=TenantStatus.SUSPENDED)
    gamma = make_tenant("gamma", status=TenantStatus.OFFBOARDED)
    assert tenant_ids(get_sessionmaker(), actor_id="test-loop") == [alpha, beta, gamma]


def test_no_tenant_means_an_empty_list(engine: Engine):
    assert tenant_ids(get_sessionmaker(), actor_id="test-loop") == []


def _publish_due_event(tenant_id: UUID) -> Event:
    event = Event(
        id=uuid7(),
        type="test.loops.v1",
        source="xolotl.tests",
        tenant_id=tenant_id,
        subject="test/loops",
        time=clock.now(),
        key="loops",
        data={},
    )
    with tenant_session(system_ctx(tenant_id)) as s:
        OutboxEventBus.publish(s, event)
    with tenant_session(system_ctx(tenant_id)) as s:
        # Past the one-second grace window, as waiting would make it.
        _ = s.execute(
            update(OutboxRow).values(
                created_at=OutboxRow.created_at - timedelta(minutes=1)
            )
        )
    return event


def _outbox_status(event: Event) -> OutboxStatus:
    with tenant_session(system_ctx(event.tenant_id)) as s:
        return s.get_one(OutboxRow, (event.tenant_id, event.id)).status


@pytest.mark.usefixtures("clean_registry")
@pytest.mark.parametrize("status", [TenantStatus.SUSPENDED, TenantStatus.OFFBOARDED])
def test_all_three_loops_still_serve_a_tenant_that_is_no_longer_active(
    engine: Engine, status: TenantStatus
):
    # Work queued before the tenant's status changed still runs to its end.
    tenant_id = make_tenant("dormant", status=status)
    factory = get_sessionmaker()
    queue = PostgresJobQueue(factory)

    received: list[UUID] = []

    def receive(_session: Session, event: Event) -> None:
        received.append(event.id)

    register_subscriber("test.loops.v1", receive, consumer="loops")
    event = _publish_due_event(tenant_id)

    ran: list[UUID] = []
    kind = unique_kind("loops")

    def run(_session: Session, job: Job) -> None:
        ran.append(job.id)

    register_job_handler(kind, run)
    with tenant_session(system_ctx(tenant_id)) as s:
        job_id = queue.enqueue(s, kind=kind, payload={})
        _ = queue.schedule(
            s, name="loops", cron="0 * * * *", tz="UTC", kind=kind, payload={}
        )
        _ = s.execute(
            update(JobSchedule).values(next_run_at=clock.now() - timedelta(hours=1))
        )

    assert OutboxEventBus(factory).dispatch_pending() == 1
    assert received == [event.id]
    assert _outbox_status(event) is OutboxStatus.PUBLISHED

    assert JobRunner(queue, factory, worker_id="w-loops").run_once() == 1
    assert ran == [job_id]

    assert Scheduler(queue, factory).tick() == 1
    with tenant_session(system_ctx(tenant_id)) as s:
        statuses = list(s.scalars(select(JobRow.status).where(JobRow.id != job_id)))
    assert statuses == [JobStatus.PENDING]  # the schedule's job, waiting for a run
