"""The ``webhooks`` consumer: one delivery and one job per matching endpoint (§9.2).

These run the real bus and queue against the real database. Nothing is sent: the
fan-out only writes ``webhook_delivery`` rows and enqueues ``webhook.deliver`` jobs, in
the transaction that records the consumption, so an event is fanned out exactly once
or not at all.
"""

from collections.abc import Generator
from datetime import timedelta
from uuid import UUID

import pytest
from sqlalchemy import Engine, select, update
from sqlalchemy.orm import Session

from tests.helpers import make_tenant_with_keys, system_ctx
from tests.platform.jobs_support import T0, FakeClock
from tests.platform.webhooks_support import (
    Stack,
    StackFactory,
    an_event,
    deliveries,
    delivery_for,
    delivery_jobs,
    make_endpoint,
    stack_factory,
)
from xolotl.platform.db import apply_context, tenant_session
from xolotl.platform.enums import (
    DeliveryStatus,
    EndpointStatus,
    JobStatus,
    OutboxStatus,
)
from xolotl.platform.events.event import Event
from xolotl.platform.events.models import EventConsumption, OutboxRow
from xolotl.platform.events.registry import (
    consumers,
    reset_subscribers,
    subscribers_for,
)
from xolotl.platform.events.webhooks import CONSUMER, DELIVER_KIND
from xolotl.platform.jobs.queue import job_handler
from xolotl.platform.tenancy.context import TenantContext


@pytest.fixture
def stack(engine: Engine) -> Generator[Stack]:
    # The registry is process-wide state: leave it as empty as it was found.
    reset_subscribers()
    yield Stack(FakeClock())
    reset_subscribers()


@pytest.fixture
def make_stack(engine: Engine) -> Generator[StackFactory]:
    reset_subscribers()
    yield stack_factory(FakeClock())
    reset_subscribers()


@pytest.fixture
def tenant_id(engine: Engine) -> UUID:
    return make_tenant_with_keys("alpha")


def outbox_row(event: Event) -> OutboxRow:
    with tenant_session(system_ctx(event.tenant_id)) as s:
        return s.get_one(OutboxRow, (event.tenant_id, event.id))


def consumed_by_webhooks(event: Event) -> bool:
    with tenant_session(system_ctx(event.tenant_id)) as s:
        return (
            s.scalars(
                select(EventConsumption).where(
                    EventConsumption.event_id == event.id,
                    EventConsumption.consumer == CONSUMER,
                )
            ).one_or_none()
            is not None
        )


# ---- registration ----------------------------------------------------------------


def test_the_consumer_is_called_webhooks_and_the_job_kind_webhook_deliver():
    assert CONSUMER == "webhooks"
    assert DELIVER_KIND == "webhook.deliver"


def test_registering_subscribes_every_event_type_and_the_delivery_job(stack: Stack):
    assert consumers() == frozenset({"webhooks"})
    for event_type in ("party.created.v1", "test.ping.v1", "anything.else.v9"):
        assert [name for name, _ in subscribers_for(event_type)] == ["webhooks"]
    assert callable(job_handler(DELIVER_KIND))


def test_registering_twice_leaves_one_subscription(
    stack: Stack, make_stack: StackFactory
):
    _ = make_stack()
    assert [name for name, _ in subscribers_for("party.created.v1")] == ["webhooks"]


# ---- which endpoints get a delivery ----------------------------------------------


def test_one_delivery_and_one_job_per_matching_active_endpoint(
    stack: Stack, tenant_id: UUID
):
    exact = make_endpoint(tenant_id, event_types=["party.created.v1"])
    star = make_endpoint(tenant_id, event_types=["*"])
    several = make_endpoint(tenant_id, event_types=["x.v1", "party.created.v1"])
    other = make_endpoint(tenant_id, event_types=["party.deleted.v1"])
    disabled = make_endpoint(
        tenant_id, event_types=["*"], status=EndpointStatus.DISABLED
    )
    event = an_event(tenant_id, type_="party.created.v1")

    assert stack.fan_out(tenant_id, event) == 1

    rows = deliveries(tenant_id)
    assert {row.endpoint_id for row in rows} == {exact.id, star.id, several.id}
    assert other.id not in {row.endpoint_id for row in rows}
    assert disabled.id not in {row.endpoint_id for row in rows}
    jobs = delivery_jobs(tenant_id)
    assert len(jobs) == len(rows) == 3
    assert {str(job.payload["delivery_id"]) for job in jobs} == {
        str(r.id) for r in rows
    }
    assert outbox_row(event).status is OutboxStatus.PUBLISHED
    assert consumed_by_webhooks(event)


def test_a_new_delivery_is_pending_and_due_on_the_clock(stack: Stack, tenant_id: UUID):
    hook = make_endpoint(tenant_id)
    event = an_event(tenant_id)
    _ = stack.fan_out(tenant_id, event)

    row = delivery_for(tenant_id, event, hook)
    assert row.status is DeliveryStatus.PENDING
    assert row.attempt == 0
    assert (row.response_code, row.last_error, row.delivered_at) == (None, None, None)
    # Stamped from the clock the service was given, not from the database: the 24
    # hours of retrying are counted from this instant.
    assert row.created_at == T0
    assert row.next_attempt_at == T0
    (job,) = delivery_jobs(tenant_id)
    assert job.status is JobStatus.PENDING
    assert job.run_after == T0
    assert job.payload == {"delivery_id": str(row.id)}
    assert job.key is None


def test_an_event_nobody_listens_for_creates_nothing(stack: Stack, tenant_id: UUID):
    _ = make_endpoint(tenant_id, event_types=["party.deleted.v1"])
    event = an_event(tenant_id, type_="party.created.v1")

    assert stack.fan_out(tenant_id, event) == 1

    assert deliveries(tenant_id) == []
    assert delivery_jobs(tenant_id) == []
    assert outbox_row(event).status is OutboxStatus.PUBLISHED
    assert consumed_by_webhooks(event)


def test_every_event_of_a_batch_is_fanned_out(stack: Stack, tenant_id: UUID):
    _ = make_endpoint(tenant_id)
    first, second = an_event(tenant_id, key="a"), an_event(tenant_id, key="b")

    assert stack.fan_out(tenant_id, first, second) == 2

    assert {row.event_id for row in deliveries(tenant_id)} == {first.id, second.id}


def test_an_endpoints_deliveries_are_made_in_the_order_the_endpoints_were_created(
    stack: Stack, tenant_id: UUID
):
    hooks = [make_endpoint(tenant_id) for _ in range(3)]
    event = an_event(tenant_id)
    _ = stack.fan_out(tenant_id, event)
    jobs = delivery_jobs(tenant_id)
    by_delivery = {str(row.id): row.endpoint_id for row in deliveries(tenant_id)}
    assert [by_delivery[str(job.payload["delivery_id"])] for job in jobs] == [
        hook.id for hook in hooks
    ]


# ---- tenants ---------------------------------------------------------------------


def test_an_event_never_reaches_another_tenants_endpoint(stack: Stack, tenant_id: UUID):
    beta = make_tenant_with_keys("beta")
    mine = make_endpoint(tenant_id)
    theirs = make_endpoint(beta)
    event = an_event(tenant_id)

    _ = stack.fan_out(tenant_id, event)

    assert [row.endpoint_id for row in deliveries(tenant_id)] == [mine.id]
    assert deliveries(beta) == []
    assert delivery_jobs(beta) == []
    assert theirs.id != mine.id


def test_the_fan_out_asks_for_its_own_tenants_endpoints_by_name(
    stack: Stack, tenant_id: UUID, admin_engine: Engine
):
    # Run as a superuser, which row-level security does not bind: only the fan-out's
    # own condition keeps the other tenant's endpoint out.
    beta = make_tenant_with_keys("beta")
    mine = make_endpoint(tenant_id)
    _ = make_endpoint(beta)
    event = an_event(tenant_id)
    ((_, fanout),) = subscribers_for(event.type)
    ctx = TenantContext.platform("test", tenant_id=tenant_id)
    with Session(admin_engine) as s:
        apply_context(s, ctx)
        fanout(s, event)
        s.commit()

    assert [row.endpoint_id for row in deliveries(tenant_id)] == [mine.id]
    assert deliveries(beta) == []


def test_each_tenants_events_go_to_its_own_endpoints(stack: Stack, tenant_id: UUID):
    beta = make_tenant_with_keys("beta")
    mine = make_endpoint(tenant_id)
    theirs = make_endpoint(beta)
    _ = stack.fan_out(tenant_id, an_event(tenant_id))
    _ = stack.fan_out(beta, an_event(beta))

    assert [row.endpoint_id for row in deliveries(tenant_id)] == [mine.id]
    assert [row.endpoint_id for row in deliveries(beta)] == [theirs.id]


# ---- exactly once ----------------------------------------------------------------


def test_fanning_an_event_out_again_creates_no_second_delivery(
    stack: Stack, tenant_id: UUID
):
    _ = make_endpoint(tenant_id)
    event = an_event(tenant_id)
    _ = stack.fan_out(tenant_id, event)

    replayed = stack.bus.replay(
        since=T0 - timedelta(days=1), types=[event.type], consumer=CONSUMER
    )

    assert replayed == 1
    assert len(deliveries(tenant_id)) == 1
    assert len(delivery_jobs(tenant_id)) == 1


def test_a_replay_gives_a_new_endpoint_the_events_it_missed(
    stack: Stack, tenant_id: UUID
):
    old = make_endpoint(tenant_id)
    event = an_event(tenant_id)
    _ = stack.fan_out(tenant_id, event)
    new = make_endpoint(tenant_id)

    _ = stack.bus.replay(
        since=T0 - timedelta(days=1), types=[event.type], consumer=CONSUMER
    )

    assert {row.endpoint_id for row in deliveries(tenant_id)} == {old.id, new.id}
    assert len(delivery_jobs(tenant_id)) == 2


def test_a_fan_out_that_fails_leaves_no_trace_and_is_tried_again(
    stack: Stack, tenant_id: UUID, monkeypatch: pytest.MonkeyPatch
):
    _ = make_endpoint(tenant_id)
    _ = make_endpoint(tenant_id)
    event = an_event(tenant_id)
    real_enqueue = stack.queue.enqueue
    calls: list[int] = []

    def flaky(session, **options):
        calls.append(1)
        if len(calls) == 2:
            msg = "queue is down"
            raise RuntimeError(msg)
        return real_enqueue(session, **options)

    monkeypatch.setattr(stack.queue, "enqueue", flaky)
    assert stack.fan_out(tenant_id, event) == 1

    # The first endpoint's delivery and job went with the transaction.
    assert deliveries(tenant_id) == []
    assert delivery_jobs(tenant_id) == []
    row = outbox_row(event)
    assert (row.status, row.attempts) == (OutboxStatus.PENDING, 1)
    assert row.last_error is not None
    assert "queue is down" in row.last_error
    assert not consumed_by_webhooks(event)

    monkeypatch.setattr(stack.queue, "enqueue", real_enqueue)
    with tenant_session(system_ctx(tenant_id)) as s:
        _ = s.execute(update(OutboxRow).values(locked_until=None))
    assert stack.bus.dispatch_pending() == 1
    assert len(deliveries(tenant_id)) == 2
    assert len(delivery_jobs(tenant_id)) == 2
    assert outbox_row(event).status is OutboxStatus.PUBLISHED
