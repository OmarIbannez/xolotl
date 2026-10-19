"""The outbox event bus: subscribers, publish, dispatch, ordering, replay (spec §9.2).

Everything past the registry runs against the real database with real commits, so row
level security, ``FOR UPDATE SKIP LOCKED`` and the lease behave as in production. Time
is moved by editing ``created_at`` and ``locked_until`` through the application role,
which is what waiting would do.
"""

import contextlib
import json
import threading
import time
from collections import defaultdict
from collections.abc import Callable, Generator, Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta, timezone
from typing import cast
from uuid import UUID

import pytest
from sqlalchemy import Engine, create_engine, func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from tests.helpers import make_tenant, platform_ctx, system_ctx
from xolotl.platform import clock
from xolotl.platform.config import get_settings
from xolotl.platform.db import (
    apply_context,
    bound_context,
    get_sessionmaker,
    tenant_session,
)
from xolotl.platform.enums import ActorType, OutboxStatus
from xolotl.platform.errors import ContextMissingError, ValidationFailedError
from xolotl.platform.events.bus import (
    ConsumptionLostError,
    InvalidStoredEventError,
    OutboxEventBus,
    claim_statement,
)
from xolotl.platform.events.event import Event, event_from_row
from xolotl.platform.events.models import EventConsumption, Notification, OutboxRow
from xolotl.platform.events.registry import (
    Handler,
    consumers,
    register_subscriber,
    reset_subscribers,
    subscribers_for,
)
from xolotl.platform.ids import uuid7
from xolotl.platform.logging import EXCEPTION_SUMMARY_LIMIT
from xolotl.platform.tenancy.context import TenantContext

# The registry is process-wide state: every test starts and ends with it empty.
pytestmark = pytest.mark.usefixtures("clean_registry")

PING = "test.ping.v1"
PONG = "test.pong.v1"
CONSUMERS = ("audit", "search")
FROZEN = datetime(2026, 10, 19, 15, 30, 0, 123456, tzinfo=UTC)
# A stored envelope that validates, for rows inserted straight into the table.
ENVELOPE = json.dumps({
    "source": "xolotl.tests",
    "subject": "test/backlog",
    "time": "2026-01-01T00:00:00Z",
    "correlation_id": None,
    "policy_snapshot_id": None,
    "data": {},
})


@pytest.fixture
def clean_registry() -> Generator[None]:
    reset_subscribers()
    yield
    reset_subscribers()


@pytest.fixture
def bus(engine: Engine) -> OutboxEventBus:
    return OutboxEventBus(get_sessionmaker())


@pytest.fixture
def tenant_id(engine: Engine) -> UUID:
    return make_tenant("alpha")


@pytest.fixture
def tokyo_bus(engine: Engine) -> Generator[OutboxEventBus]:
    # Its sessions run in Asia/Tokyo, which reads a naive datetime nine hours away
    # from UTC; the database in the test container itself runs in UTC.
    tokyo = create_engine(
        get_settings().database_url,
        connect_args={"options": "-c TimeZone=Asia/Tokyo"},
        hide_parameters=True,
    )
    yield OutboxEventBus(sessionmaker(bind=tokyo, expire_on_commit=False))
    tokyo.dispose()


# ---- helpers --------------------------------------------------------------------


# A distinct no-op handler, so tests can tell registrations apart.
def a_handler() -> Handler:
    def handle(session: Session, event: Event) -> None:
        pass

    return handle


class Probe:
    """A subscriber that records what it receives and fails on demand."""

    def __init__(
        self, *, fail_times: int = 0, fail_once_for: Iterable[UUID] = ()
    ) -> None:
        self.calls: list[Event] = []  # every invocation, failed or not
        self.done: list[Event] = []  # the invocations that returned normally
        self._fail_times = fail_times
        self._fail_once_for = set(fail_once_for)
        self._lock = threading.Lock()

    def __call__(self, _session: Session, event: Event) -> None:
        with self._lock:
            self.calls.append(event)
            fail = self._fail_times > 0 or event.id in self._fail_once_for
            self._fail_times = max(0, self._fail_times - 1)
            self._fail_once_for.discard(event.id)
            if not fail:
                self.done.append(event)
        if fail:
            msg = "boom"
            raise RuntimeError(msg)


class AbortError(Exception):
    pass


class ProcessDiedError(BaseException):
    """Not an ``Exception``: nothing in the dispatcher may swallow it."""


def an_event(
    tenant_id: UUID, *, type_: str = PING, key: str = "k", **overrides: object
) -> Event:
    fields: dict[str, object] = {
        "id": uuid7(),
        "type": type_,
        "source": "xolotl.tests",
        "tenant_id": tenant_id,
        "subject": f"test/{key}",
        "time": datetime(2026, 1, 1, tzinfo=UTC),
        "key": key,
        "data": {"n": 1},
    }
    return Event.model_validate(fields | overrides)


def emit(bus: OutboxEventBus, tenant_id: UUID, *events: Event) -> None:
    """Publish in one transaction, so the rows get consecutive ``seq`` values."""
    with tenant_session(system_ctx(tenant_id)) as s:
        for event in events:
            bus.publish(s, event)


def stored(event: Event) -> OutboxRow:
    with tenant_session(system_ctx(event.tenant_id)) as s:
        return s.get_one(OutboxRow, (event.tenant_id, event.id))


def outbox_count(tenant_id: UUID) -> int:
    with tenant_session(system_ctx(tenant_id)) as s:
        return cast("int", s.scalar(select(func.count()).select_from(OutboxRow)))


def consumed_by(event: Event) -> set[str]:
    with tenant_session(system_ctx(event.tenant_id)) as s:
        return set(
            s.scalars(
                select(EventConsumption.consumer).where(
                    EventConsumption.event_id == event.id
                )
            )
        )


def consumed_at(event: Event, consumer: str) -> datetime:
    with tenant_session(system_ctx(event.tenant_id)) as s:
        return s.scalars(
            select(EventConsumption.consumed_at).where(
                EventConsumption.event_id == event.id,
                EventConsumption.consumer == consumer,
            )
        ).one()


def notification_count(tenant_id: UUID) -> int:
    with tenant_session(system_ctx(tenant_id)) as s:
        return cast("int", s.scalar(select(func.count()).select_from(Notification)))


def make_due(tenant_id: UUID) -> None:
    """Age every event past the one-second grace window, as waiting would."""
    with tenant_session(system_ctx(tenant_id)) as s:
        _ = s.execute(
            text("UPDATE outbox SET created_at = created_at - interval '1 minute'")
        )


def release_locks(tenant_id: UUID) -> None:
    """Expire every lease and backoff, as waiting would."""
    with tenant_session(system_ctx(tenant_id)) as s:
        _ = s.execute(text("UPDATE outbox SET locked_until = NULL"))


def set_age(event: Event, seconds: float) -> None:
    """Make the event ``seconds`` old (negative: created in the future)."""
    with tenant_session(system_ctx(event.tenant_id)) as s:
        _ = s.execute(
            text(
                """
                UPDATE outbox SET created_at = now() - make_interval(secs => :s)
                WHERE id = :id
                """
            ),
            {"s": seconds, "id": event.id},
        )


def set_lease(event: Event, seconds_ahead: float) -> None:
    with tenant_session(system_ctx(event.tenant_id)) as s:
        _ = s.execute(
            text(
                """
                UPDATE outbox SET locked_until = now() + make_interval(secs => :s)
                WHERE id = :id
                """
            ),
            {"s": seconds_ahead, "id": event.id},
        )


def set_attempts(event: Event, attempts: int) -> None:
    with tenant_session(system_ctx(event.tenant_id)) as s:
        _ = s.execute(
            text("UPDATE outbox SET attempts = :n WHERE id = :id"),
            {"n": attempts, "id": event.id},
        )


# Seconds until the event's lease or backoff expires.
def locked_for(event: Event) -> float:
    with tenant_session(system_ctx(event.tenant_id)) as s:
        remaining = s.scalar(
            text(
                """
                SELECT extract(epoch FROM locked_until - now())::float8
                FROM outbox WHERE id = :id
                """
            ),
            {"id": event.id},
        )
    return cast("float", remaining)


# Insert old, valid events straight into the table, ``keys`` keys in rotation.
def seed_rows(
    tenant_id: UUID,
    *,
    rows: int,
    keys: int,
    status: OutboxStatus = OutboxStatus.PENDING,
) -> None:
    with tenant_session(system_ctx(tenant_id)) as s:
        _ = s.execute(
            text(
                """
                INSERT INTO outbox
                    (tenant_id, id, type, key, payload, status, created_at)
                SELECT :tenant, gen_random_uuid(), 'test.ping.v1', 'k' || (g % :keys),
                       CAST(:envelope AS jsonb), :status, now() - interval '1 hour'
                FROM generate_series(1, :rows) g
                """
            ),
            {
                "tenant": tenant_id,
                "envelope": ENVELOPE,
                "keys": keys,
                "status": status.value,
                "rows": rows,
            },
        )


# ---- the subscriber registry ----------------------------------------------------


def test_subscribers_come_back_in_registration_order():
    first, second = a_handler(), a_handler()
    register_subscriber("party.created.v1", first, consumer="audit")
    register_subscriber("party.created.v1", second, consumer="search")
    assert subscribers_for("party.created.v1") == [
        ("audit", first),
        ("search", second),
    ]


def test_only_subscribers_of_that_type_are_returned():
    created, closed = a_handler(), a_handler()
    register_subscriber("party.created.v1", created, consumer="audit")
    register_subscriber("party.closed.v1", closed, consumer="search")
    assert subscribers_for("party.created.v1") == [("audit", created)]
    assert subscribers_for("party.closed.v1") == [("search", closed)]


def test_a_type_nobody_subscribed_to_has_no_subscribers():
    register_subscriber("party.created.v1", a_handler(), consumer="audit")
    assert subscribers_for("party.closed.v1") == []


def test_a_star_subscription_receives_every_type():
    everything = a_handler()
    register_subscriber("*", everything, consumer="webhooks")
    assert subscribers_for("party.created.v1") == [("webhooks", everything)]
    assert subscribers_for("anything.else.v7") == [("webhooks", everything)]


def test_star_and_exact_subscriptions_keep_one_registration_order():
    exact_first, star, exact_last = a_handler(), a_handler(), a_handler()
    register_subscriber("party.created.v1", exact_first, consumer="audit")
    register_subscriber("*", star, consumer="webhooks")
    register_subscriber("party.created.v1", exact_last, consumer="search")
    assert subscribers_for("party.created.v1") == [
        ("audit", exact_first),
        ("webhooks", star),
        ("search", exact_last),
    ]


def test_one_consumer_may_handle_several_types_with_different_handlers():
    created, closed = a_handler(), a_handler()
    register_subscriber("party.created.v1", created, consumer="audit")
    register_subscriber("party.closed.v1", closed, consumer="audit")
    assert subscribers_for("party.created.v1") == [("audit", created)]
    assert subscribers_for("party.closed.v1") == [("audit", closed)]


def test_registering_a_consumer_again_for_a_type_replaces_its_handler_in_place():
    original, replacement, other = a_handler(), a_handler(), a_handler()
    register_subscriber("party.created.v1", original, consumer="audit")
    register_subscriber("party.created.v1", other, consumer="search")
    register_subscriber("party.created.v1", replacement, consumer="audit")
    assert subscribers_for("party.created.v1") == [
        ("audit", replacement),
        ("search", other),
    ]


@pytest.mark.parametrize(
    ("first", "second"),
    [("*", "party.created.v1"), ("party.created.v1", "*")],
    ids=["star-then-exact", "exact-then-star"],
)
def test_a_consumer_cannot_hold_a_star_and_an_exact_subscription(
    first: str, second: str
):
    # Its consumption row is keyed by consumer and event, so the second handler
    # would silently never run.
    kept = a_handler()
    register_subscriber(first, kept, consumer="audit")
    with pytest.raises(ValueError, match="audit"):
        register_subscriber(second, a_handler(), consumer="audit")
    assert subscribers_for("party.created.v1") == [("audit", kept)]


def test_different_consumers_may_mix_star_and_exact_subscriptions():
    star, exact = a_handler(), a_handler()
    register_subscriber("*", star, consumer="webhooks")
    register_subscriber("party.created.v1", exact, consumer="audit")
    assert subscribers_for("party.created.v1") == [
        ("webhooks", star),
        ("audit", exact),
    ]


def test_consumers_lists_every_registered_consumer_once():
    assert consumers() == frozenset()
    register_subscriber("party.created.v1", a_handler(), consumer="audit")
    register_subscriber("party.closed.v1", a_handler(), consumer="audit")
    register_subscriber("*", a_handler(), consumer="webhooks")
    assert consumers() == frozenset({"audit", "webhooks"})


def test_reset_subscribers_forgets_everything():
    register_subscriber("party.created.v1", a_handler(), consumer="audit")
    register_subscriber("*", a_handler(), consumer="webhooks")
    reset_subscribers()
    assert subscribers_for("party.created.v1") == []
    assert consumers() == frozenset()


# ---- publish --------------------------------------------------------------------


def test_publish_stores_the_event_as_a_pending_row(
    bus: OutboxEventBus, tenant_id: UUID
):
    event = an_event(
        tenant_id, key="party-7", correlation_id="req-1", data={"party_id": "p-1"}
    )
    emit(bus, tenant_id, event)
    row = stored(event)
    assert (row.type, row.key, row.status, row.attempts) == (
        PING,
        "party-7",
        OutboxStatus.PENDING,
        0,
    )
    assert row.locked_until is None
    assert row.published_at is None
    assert event_from_row(row) == event  # nothing but the envelope is stored


def test_publish_is_invisible_until_the_publishers_transaction_commits(
    bus: OutboxEventBus, tenant_id: UUID
):
    with tenant_session(system_ctx(tenant_id)) as writer:
        bus.publish(writer, an_event(tenant_id))
        assert outbox_count(tenant_id) == 0  # a second connection sees nothing yet
        assert writer.scalar(select(func.count()).select_from(OutboxRow)) == 1
    assert outbox_count(tenant_id) == 1


def test_publish_rolls_back_with_the_publishers_transaction(
    bus: OutboxEventBus, tenant_id: UUID
):
    with pytest.raises(AbortError), tenant_session(system_ctx(tenant_id)) as writer:
        bus.publish(writer, an_event(tenant_id))
        raise AbortError
    assert outbox_count(tenant_id) == 0


def clock_that_must_not_be_read() -> datetime:
    msg = "publishing must not read the clock"
    raise AssertionError(msg)


@pytest.mark.parametrize(
    ("supplied", "payload_time"),
    [
        (FROZEN, "2026-10-19T15:30:00.123456Z"),
        (
            datetime(
                2026, 10, 19, 9, 30, 0, 123456, tzinfo=timezone(timedelta(hours=-6))
            ),
            "2026-10-19T09:30:00.123456-06:00",
        ),
    ],
    ids=["utc", "mexico-city-offset"],
)
def test_publish_keeps_an_aware_time_as_supplied(
    engine: Engine, tenant_id: UUID, supplied: datetime, payload_time: str
):
    bus = OutboxEventBus(get_sessionmaker(), clock_fn=clock_that_must_not_be_read)
    event = an_event(tenant_id, time=supplied)
    emit(bus, tenant_id, event)
    row = stored(event)
    assert row.payload["time"] == payload_time
    restored = event_from_row(row).time
    assert restored == supplied
    assert restored.utcoffset() == supplied.utcoffset()


def test_publish_reads_a_naive_time_as_utc(engine: Engine, tenant_id: UUID):
    bus = OutboxEventBus(get_sessionmaker(), clock_fn=clock_that_must_not_be_read)
    naive = datetime.fromisoformat("2026-10-19T15:30:00.123456")
    assert naive.tzinfo is None
    event = an_event(tenant_id, time=naive)
    emit(bus, tenant_id, event)
    row = stored(event)
    assert row.payload["time"] == "2026-10-19T15:30:00.123456Z"
    restored = event_from_row(row).time
    assert restored == FROZEN  # the same wall time, now an aware UTC instant
    assert restored.utcoffset() == timedelta(0)


def test_publish_for_another_tenant_raises_and_stores_nothing(
    bus: OutboxEventBus, tenant_id: UUID
):
    other = make_tenant("beta")
    with (
        tenant_session(system_ctx(tenant_id)) as s,
        pytest.raises(ContextMissingError, match=str(other)),
    ):
        bus.publish(s, an_event(other))
    assert outbox_count(tenant_id) == 0
    assert outbox_count(other) == 0


def test_publish_needs_a_tenant_scoped_session(bus: OutboxEventBus, tenant_id: UUID):
    with (
        tenant_session(platform_ctx()) as s,
        pytest.raises(ContextMissingError, match="tenant-scoped"),
    ):
        bus.publish(s, an_event(tenant_id))


def test_publish_needs_a_bound_context(bus: OutboxEventBus, tenant_id: UUID):
    with get_sessionmaker()() as s, pytest.raises(ContextMissingError):
        bus.publish(s, an_event(tenant_id))


def test_an_operator_session_scoped_to_the_tenant_may_publish(
    bus: OutboxEventBus, tenant_id: UUID
):
    # What create_tenant does: the platform operator, narrowed to the new tenant.
    ctx = TenantContext.platform("cli:test", tenant_id=tenant_id)
    with tenant_session(ctx) as s:
        bus.publish(s, an_event(tenant_id))
    assert outbox_count(tenant_id) == 1


def test_publish_after_the_session_narrows_to_the_tenant_mid_transaction(
    bus: OutboxEventBus, tenant_id: UUID
):
    # What create_tenant does: start as the platform operator, create the tenant, then
    # switch the same transaction to that tenant before publishing.
    with tenant_session(platform_ctx()) as s:
        apply_context(s, TenantContext.platform("cli:test", tenant_id=tenant_id))
        bus.publish(s, an_event(tenant_id))
    assert outbox_count(tenant_id) == 1


# ---- dispatch -------------------------------------------------------------------


def test_dispatch_runs_each_subscriber_once_and_marks_the_event_published(
    engine: Engine, tenant_id: UUID
):
    bus = OutboxEventBus(get_sessionmaker(), clock_fn=lambda: FROZEN)
    audit, search = Probe(), Probe()
    register_subscriber(PING, audit, consumer="audit")
    register_subscriber(PING, search, consumer="search")
    event = an_event(tenant_id)
    emit(bus, tenant_id, event)
    make_due(tenant_id)

    assert bus.dispatch_pending() == 1

    assert audit.done == [event]  # the event as it was published, time included
    assert search.done == [event]
    row = stored(event)
    assert row.status is OutboxStatus.PUBLISHED
    assert row.attempts == 0
    assert row.published_at == FROZEN
    assert row.locked_until is None
    assert row.last_error is None
    assert consumed_by(event) == {"audit", "search"}
    assert bus.dispatch_pending() == 0
    assert len(audit.calls) == len(search.calls) == 1


def test_a_star_subscriber_receives_every_type(bus: OutboxEventBus, tenant_id: UUID):
    everything, pings = Probe(), Probe()
    register_subscriber("*", everything, consumer="webhooks")
    register_subscriber(PING, pings, consumer="pings")
    ping = an_event(tenant_id, type_=PING, key="a")
    pong = an_event(tenant_id, type_=PONG, key="b")
    emit(bus, tenant_id, ping, pong)
    make_due(tenant_id)

    assert bus.dispatch_pending() == 2

    assert [e.id for e in everything.done] == [ping.id, pong.id]
    assert [e.id for e in pings.done] == [ping.id]
    assert stored(ping).status is OutboxStatus.PUBLISHED
    assert stored(pong).status is OutboxStatus.PUBLISHED
    assert consumed_by(pong) == {"webhooks"}


def test_an_event_nobody_subscribed_to_is_published_without_running_anything(
    bus: OutboxEventBus, tenant_id: UUID
):
    other = Probe()
    register_subscriber(PONG, other, consumer="other")
    event = an_event(tenant_id, type_=PING)
    emit(bus, tenant_id, event)
    make_due(tenant_id)

    assert bus.dispatch_pending() == 1

    assert stored(event).status is OutboxStatus.PUBLISHED
    assert other.calls == []
    assert consumed_by(event) == set()


def test_dispatch_with_nothing_pending_handles_nothing(bus: OutboxEventBus):
    assert bus.dispatch_pending() == 0


def test_published_at_comes_from_the_platform_clock_by_default(
    bus: OutboxEventBus, tenant_id: UUID
):
    event = an_event(tenant_id)  # nobody subscribed: published on dispatch
    emit(bus, tenant_id, event)
    make_due(tenant_id)

    before = clock.now()
    assert bus.dispatch_pending() == 1
    after = clock.now()

    published_at = stored(event).published_at
    assert published_at is not None
    assert before <= published_at <= after
    assert published_at.utcoffset() == timedelta(0)


def test_dispatch_visits_every_tenant(engine: Engine):
    bus = OutboxEventBus(get_sessionmaker())
    probe = Probe()
    register_subscriber(PING, probe, consumer="audit")
    alpha, beta = make_tenant("alpha"), make_tenant("beta")
    first, second = an_event(alpha), an_event(beta)
    emit(bus, alpha, first)
    emit(bus, beta, second)
    make_due(alpha)
    make_due(beta)

    assert bus.dispatch_pending() == 2

    assert {e.id for e in probe.done} == {first.id, second.id}


def test_handlers_run_in_the_events_tenant_context_as_the_dispatcher(engine: Engine):
    bus = OutboxEventBus(get_sessionmaker())
    alpha, beta = make_tenant("alpha"), make_tenant("beta")
    seen: list[tuple[UUID, UUID | None, ActorType, str, set[UUID], str]] = []

    def spy(session: Session, event: Event) -> None:
        ctx = bound_context(session)
        visible = set(session.scalars(select(OutboxRow.tenant_id)))
        guc = cast(
            "str", session.scalar(text("SELECT current_setting('app.tenant_id')"))
        )
        seen.append((
            event.tenant_id,
            ctx.tenant_id,
            ctx.actor_type,
            ctx.actor_id,
            visible,
            guc,
        ))

    register_subscriber("*", spy, consumer="spy")
    emit(bus, alpha, an_event(alpha))
    emit(bus, beta, an_event(beta))
    make_due(alpha)
    make_due(beta)

    assert bus.dispatch_pending() == 2

    assert {entry[0] for entry in seen} == {alpha, beta}
    for event_tenant, ctx_tenant, actor_type, actor_id, visible, guc in seen:
        assert ctx_tenant == event_tenant
        assert actor_type is ActorType.SYSTEM
        assert actor_id == "dispatcher"
        assert visible == {event_tenant}  # row level security shows one tenant
        assert guc == str(event_tenant)


def test_a_claimed_event_is_leased_for_sixty_seconds_while_its_handlers_run(
    bus: OutboxEventBus, tenant_id: UUID
):
    leases: list[float] = []

    def spy(session: Session, event: Event) -> None:
        leases.append(locked_for(event))

    register_subscriber(PING, spy, consumer="spy")
    emit(bus, tenant_id, an_event(tenant_id))
    make_due(tenant_id)

    assert bus.dispatch_pending() == 1

    assert len(leases) == 1
    assert 30 < leases[0] <= 60  # a minute, less whatever the handler took to start


def test_an_event_younger_than_one_second_is_not_dispatched(
    bus: OutboxEventBus, tenant_id: UUID
):
    probe = Probe()
    register_subscriber(PING, probe, consumer="audit")
    event = an_event(tenant_id)
    emit(bus, tenant_id, event)

    set_age(event, -3600)  # certainly inside the grace window
    assert bus.dispatch_pending() == 0
    assert probe.calls == []

    set_age(event, 1.1)  # just past it
    assert bus.dispatch_pending() == 1
    assert [e.id for e in probe.done] == [event.id]


def test_a_leased_event_is_skipped_until_its_lease_expires(
    bus: OutboxEventBus, tenant_id: UUID
):
    probe = Probe()
    register_subscriber(PING, probe, consumer="audit")
    event = an_event(tenant_id)
    emit(bus, tenant_id, event)
    make_due(tenant_id)

    set_lease(event, 3600)
    assert bus.dispatch_pending() == 0

    set_lease(event, -1)  # the dispatcher that held it is gone
    assert bus.dispatch_pending() == 1
    assert [e.id for e in probe.done] == [event.id]


def test_dispatch_handles_at_most_batch_events_per_tenant(
    bus: OutboxEventBus, tenant_id: UUID
):
    probe = Probe()
    register_subscriber(PING, probe, consumer="audit")
    events = [an_event(tenant_id, key=f"k{n}") for n in range(5)]
    emit(bus, tenant_id, *events)
    make_due(tenant_id)

    assert bus.dispatch_pending(batch=2) == 2
    assert [e.id for e in probe.done] == [e.id for e in events[:2]]  # seq order
    assert bus.dispatch_pending(batch=2) == 2
    assert bus.dispatch_pending(batch=2) == 1
    assert bus.dispatch_pending(batch=2) == 0
    assert [e.id for e in probe.done] == [e.id for e in events]


def test_the_batch_limit_applies_to_each_tenant_separately(engine: Engine):
    bus = OutboxEventBus(get_sessionmaker())
    probe = Probe()
    register_subscriber(PING, probe, consumer="audit")
    alpha, beta = make_tenant("alpha"), make_tenant("beta")
    first = [an_event(alpha, key=f"a{n}") for n in range(3)]
    second = [an_event(beta, key=f"b{n}") for n in range(3)]
    emit(bus, alpha, *first)
    emit(bus, beta, *second)
    make_due(alpha)
    make_due(beta)

    # A limit shared by all tenants would handle one event in all, not one in each.
    assert bus.dispatch_pending(batch=1) == 2
    assert {e.id for e in probe.done} == {first[0].id, second[0].id}
    assert bus.dispatch_pending(batch=2) == 4
    assert bus.dispatch_pending(batch=2) == 0
    assert {e.id for e in probe.done} == {e.id for e in [*first, *second]}


# ---- retries, backoff and the dead letter ---------------------------------------


def test_a_handler_failing_twice_then_succeeding_ends_published_after_two_attempts(
    bus: OutboxEventBus, tenant_id: UUID
):
    flaky, steady = Probe(fail_times=2), Probe()
    register_subscriber(PING, flaky, consumer="flaky")
    register_subscriber(PING, steady, consumer="steady")
    event = an_event(tenant_id)
    emit(bus, tenant_id, event)
    make_due(tenant_id)

    assert bus.dispatch_pending() == 1
    row = stored(event)
    assert (row.status, row.attempts) == (OutboxStatus.PENDING, 1)
    assert row.last_error == "flaky: RuntimeError: boom"
    assert locked_for(event) <= 2  # backs off 2 ** 1 seconds at most
    # The failure did not stop the next consumer in the same pass.
    assert len(steady.calls) == 1
    assert consumed_by(event) == {"steady"}

    release_locks(tenant_id)
    assert bus.dispatch_pending() == 1
    row = stored(event)
    assert (row.status, row.attempts) == (OutboxStatus.PENDING, 2)
    assert locked_for(event) <= 4  # 2 ** 2 seconds at most

    release_locks(tenant_id)
    assert bus.dispatch_pending() == 1
    row = stored(event)
    assert (row.status, row.attempts) == (OutboxStatus.PUBLISHED, 2)
    assert row.locked_until is None
    assert row.last_error == "flaky: RuntimeError: boom"  # the history stays
    assert len(flaky.calls) == 3
    assert len(steady.calls) == 1  # the other consumer ran once, not three times
    assert consumed_by(event) == {"flaky", "steady"}


def test_every_failing_consumer_is_reported_but_the_attempt_counts_once(
    bus: OutboxEventBus, tenant_id: UUID
):
    register_subscriber(PING, Probe(fail_times=1), consumer="alpha")
    register_subscriber(PING, Probe(fail_times=1), consumer="beta")
    event = an_event(tenant_id)
    emit(bus, tenant_id, event)
    make_due(tenant_id)

    assert bus.dispatch_pending() == 1

    row = stored(event)
    assert row.attempts == 1
    assert row.last_error == "alpha: RuntimeError: boom; beta: RuntimeError: boom"


@pytest.mark.parametrize(
    ("before", "seconds"),
    [(3, 16), (4, 32), (5, 60), (8, 60)],
    ids=["16s", "32s", "capped-from-64s", "capped-from-512s"],
)
def test_the_backoff_doubles_with_each_attempt_up_to_sixty_seconds(
    engine: Engine, tenant_id: UUID, before: int, seconds: int
):
    bus = OutboxEventBus(get_sessionmaker(), max_attempts=100)
    register_subscriber(PING, Probe(fail_times=1), consumer="flaky")
    event = an_event(tenant_id)
    emit(bus, tenant_id, event)
    make_due(tenant_id)
    set_attempts(event, before)

    assert bus.dispatch_pending() == 1

    assert stored(event).attempts == before + 1
    # 2 ** attempts seconds, at most a minute. The margin below the bound is wider
    # than any pause between the failure and this read.
    assert 0.75 * seconds < locked_for(event) <= seconds


def test_an_event_in_backoff_is_not_claimed_until_the_backoff_is_over(
    bus: OutboxEventBus, tenant_id: UUID
):
    probe = Probe(fail_times=1)
    register_subscriber(PING, probe, consumer="flaky")
    event = an_event(tenant_id)
    emit(bus, tenant_id, event)
    make_due(tenant_id)
    set_attempts(event, 5)  # so its backoff is a minute, far longer than this test

    assert bus.dispatch_pending() == 1
    assert (stored(event).status, stored(event).attempts) == (OutboxStatus.PENDING, 6)

    assert bus.dispatch_pending() == 0  # backing off: not claimed
    assert len(probe.calls) == 1

    release_locks(tenant_id)  # the minute is over
    assert bus.dispatch_pending() == 1
    assert stored(event).status is OutboxStatus.PUBLISHED


def test_an_event_that_keeps_failing_is_dead_after_max_attempts(
    engine: Engine, tenant_id: UUID
):
    bus = OutboxEventBus(get_sessionmaker(), max_attempts=3)
    broken = Probe(fail_times=99)
    register_subscriber(PING, broken, consumer="broken")
    event = an_event(tenant_id)
    emit(bus, tenant_id, event)
    make_due(tenant_id)

    for attempt in (1, 2):
        assert bus.dispatch_pending() == 1
        row = stored(event)
        assert (row.status, row.attempts) == (OutboxStatus.PENDING, attempt)
        release_locks(tenant_id)

    assert bus.dispatch_pending() == 1  # a dead row counts as handled too

    row = stored(event)
    assert (row.status, row.attempts) == (OutboxStatus.DEAD, 3)
    assert row.last_error == "broken: RuntimeError: boom"
    assert row.locked_until is None
    assert row.published_at is None
    release_locks(tenant_id)
    assert bus.dispatch_pending() == 0
    assert len(broken.calls) == 3


def test_ten_attempts_are_allowed_by_default(bus: OutboxEventBus, tenant_id: UUID):
    broken = Probe(fail_times=99)
    register_subscriber(PING, broken, consumer="broken")
    event = an_event(tenant_id)
    emit(bus, tenant_id, event)
    make_due(tenant_id)

    for _ in range(9):
        assert bus.dispatch_pending() == 1
        release_locks(tenant_id)
    assert stored(event).status is OutboxStatus.PENDING

    assert bus.dispatch_pending() == 1
    row = stored(event)
    assert (row.status, row.attempts) == (OutboxStatus.DEAD, 10)


def test_database_error_text_never_reaches_last_error(
    bus: OutboxEventBus, tenant_id: UUID
):
    # A unique violation quotes the offending key values; those can be personal data.
    def duplicates_itself(session: Session, event: Event) -> None:
        session.add(
            OutboxRow(
                tenant_id=event.tenant_id, id=event.id, type=PING, key="k", payload={}
            )
        )
        session.flush()

    register_subscriber(PING, duplicates_itself, consumer="dup")
    event = an_event(tenant_id)
    emit(bus, tenant_id, event)
    make_due(tenant_id)

    assert bus.dispatch_pending() == 1

    assert stored(event).last_error == "dup: IntegrityError [sqlstate=23505]"


@pytest.mark.parametrize("link", ["cause", "context"])
def test_a_database_error_wrapped_by_the_handler_is_reduced_the_same_way(
    bus: OutboxEventBus, tenant_id: UUID, link: str
):
    def adds_a_duplicate_row(session: Session, event: Event) -> None:
        session.add(
            OutboxRow(
                tenant_id=event.tenant_id, id=event.id, type=PING, key="k", payload={}
            )
        )

    def wraps_as_cause(session: Session, event: Event) -> None:
        adds_a_duplicate_row(session, event)
        try:
            session.flush()
        except IntegrityError as error:
            caught = error
        else:
            pytest.fail("the duplicate row was accepted")
        msg = f"cannot store the event: {caught.orig}"  # quotes the key values
        raise RuntimeError(msg) from caught  # raised after the except: a cause only

    def wraps_as_context(session: Session, event: Event) -> None:
        adds_a_duplicate_row(session, event)
        try:
            session.flush()
        except IntegrityError as error:
            msg = f"cannot store the event: {error.orig}"
            raise RuntimeError(msg) from None  # raised while handling: a context only

    wraps = {"cause": wraps_as_cause, "context": wraps_as_context}[link]
    register_subscriber(PING, wraps, consumer="wrap")
    event = an_event(tenant_id)
    emit(bus, tenant_id, event)
    make_due(tenant_id)

    assert bus.dispatch_pending() == 1

    # The cause is a database error, so the message is dropped, whoever wrote it.
    assert stored(event).last_error == "wrap: RuntimeError [sqlstate=23505]"


def test_an_exception_chain_that_loops_is_walked_once(
    bus: OutboxEventBus, tenant_id: UUID
):
    def raises_a_loop(session: Session, event: Event) -> None:
        first, second = RuntimeError("first"), RuntimeError("second")
        first.__cause__ = second
        second.__cause__ = first  # only possible by assigning the links by hand
        raise first

    register_subscriber(PING, raises_a_loop, consumer="loop")
    event = an_event(tenant_id)
    emit(bus, tenant_id, event)
    make_due(tenant_id)

    assert bus.dispatch_pending() == 1

    assert stored(event).last_error == "loop: RuntimeError: first"


def test_a_swallowed_flush_error_does_not_leak_database_text(
    bus: OutboxEventBus, tenant_id: UUID
):
    # The session refuses further work with a PendingRollbackError whose message
    # repeats the driver's text; that must not reach the outbox row.
    def swallows(session: Session, event: Event) -> None:
        session.add(
            OutboxRow(
                tenant_id=event.tenant_id,
                id=event.id,
                type=PING,
                key="SECRET-KEY-VALUE",
                payload={},
            )
        )
        with contextlib.suppress(IntegrityError):
            session.flush()

    register_subscriber(PING, swallows, consumer="swallow")
    event = an_event(tenant_id)
    emit(bus, tenant_id, event)
    make_due(tenant_id)

    assert bus.dispatch_pending() == 1

    row = stored(event)
    assert (row.status, row.attempts) == (OutboxStatus.PENDING, 1)
    assert row.last_error == "swallow: PendingRollbackError"


@pytest.mark.parametrize(
    ("message", "stored_text"),
    [
        ("bad value 'a\x00b'", "bad value 'ab'"),
        ("bad name 'a\udcffb'", "bad name 'a?b'"),
    ],
    ids=["nul", "lone-surrogate"],
)
def test_text_postgres_cannot_store_cannot_wedge_a_key(
    bus: OutboxEventBus, tenant_id: UUID, message: str, stored_text: str
):
    first, second = an_event(tenant_id), an_event(tenant_id)  # one key
    attempts: list[UUID] = []

    def fails_once_with_odd_text(session: Session, event: Event) -> None:
        attempts.append(event.id)
        if len(attempts) == 1:
            raise RuntimeError(message)

    register_subscriber(PING, fails_once_with_odd_text, consumer="odd")
    emit(bus, tenant_id, first, second)
    make_due(tenant_id)

    assert bus.dispatch_pending() == 1  # the failure is recorded, not raised

    row = stored(first)
    assert (row.status, row.attempts) == (OutboxStatus.PENDING, 1)
    assert row.last_error == f"odd: RuntimeError: {stored_text}"
    release_locks(tenant_id)
    assert bus.dispatch_pending() == 1  # and the key moves on afterwards
    assert bus.dispatch_pending() == 1
    assert stored(first).status is OutboxStatus.PUBLISHED
    assert stored(second).status is OutboxStatus.PUBLISHED


def test_each_consumer_gets_its_own_copy_of_the_event(
    bus: OutboxEventBus, tenant_id: UUID
):
    seen: list[dict[str, object]] = []

    def tampers(session: Session, event: Event) -> None:
        event.data["n"] = 99  # a frozen model still has a mutable dict inside
        event.data["extra"] = "yes"

    def observes(session: Session, event: Event) -> None:
        seen.append(dict(event.data))

    register_subscriber(PING, tampers, consumer="a-tampers")
    register_subscriber(PING, observes, consumer="b-observes")
    event = an_event(tenant_id)
    emit(bus, tenant_id, event)
    make_due(tenant_id)

    assert bus.dispatch_pending() == 1

    assert seen == [{"n": 1}]
    assert stored(event).payload["data"] == {"n": 1}


def test_a_very_long_error_is_truncated(bus: OutboxEventBus, tenant_id: UUID):
    def rambles(session: Session, event: Event) -> None:
        raise RuntimeError("x" * 10_000)

    register_subscriber(PING, rambles, consumer="long")
    event = an_event(tenant_id)
    emit(bus, tenant_id, event)
    make_due(tenant_id)

    assert bus.dispatch_pending() == 1

    error = stored(event).last_error
    assert error is not None
    assert error.startswith("long: RuntimeError: xxx")
    # Each failure is cut by safe_exception_summary, like every stored error text.
    assert len(error) == len("long: ") + EXCEPTION_SUMMARY_LIMIT


def test_the_failures_of_several_consumers_together_are_cut_to_a_thousand(
    bus: OutboxEventBus, tenant_id: UUID
):
    def rambles(session: Session, event: Event) -> None:
        raise RuntimeError("x" * 10_000)

    for name in ("long-a", "long-b", "long-c"):
        register_subscriber(PING, rambles, consumer=name)
    event = an_event(tenant_id)
    emit(bus, tenant_id, event)
    make_due(tenant_id)

    assert bus.dispatch_pending() == 1

    error = stored(event).last_error
    assert error is not None
    assert error.startswith("long-a: RuntimeError: xxx")
    assert "; long-b: RuntimeError: xxx" in error
    assert len(error) == 1000


def test_a_database_error_deep_in_the_cause_chain_leaves_only_class_and_sqlstate(
    bus: OutboxEventBus, tenant_id: UUID
):
    # The driver error sits two links down, behind a ``raise ... from None`` that hides
    # it from tracebacks, and both wrappers quote it. What is stored is the outermost
    # class and the SQLSTATE: safe_exception_summary, the one way exception text
    # reaches a stored column.
    def look_up(session: Session) -> None:
        try:
            session.flush()
        except IntegrityError as error:
            msg = f"lookup failed: {error.orig}"
            raise ValueError(msg) from error

    def buries(session: Session, event: Event) -> None:
        session.add(
            OutboxRow(
                tenant_id=event.tenant_id,
                id=event.id,
                type=PING,
                key="SECRET-KEY-VALUE",
                payload={},
            )
        )
        try:
            look_up(session)
        except ValueError as middle:
            msg = f"could not record the event: {middle}"
            raise RuntimeError(msg) from None

    register_subscriber(PING, buries, consumer="deep")
    event = an_event(tenant_id)
    emit(bus, tenant_id, event)
    make_due(tenant_id)

    assert bus.dispatch_pending() == 1

    row = stored(event)
    assert (row.status, row.attempts) == (OutboxStatus.PENDING, 1)
    assert row.last_error == "deep: RuntimeError [sqlstate=23505]"


def test_a_validation_error_is_stored_by_its_error_count_not_its_input(
    bus: OutboxEventBus, tenant_id: UUID
):
    # Pydantic echoes the offending input in its message; that can be personal data.
    def validates(session: Session, event: Event) -> None:
        _ = Event.model_validate({"id": "ana.perez@example.mx"})

    register_subscriber(PING, validates, consumer="strict")
    event = an_event(tenant_id)
    emit(bus, tenant_id, event)
    make_due(tenant_id)

    assert bus.dispatch_pending() == 1

    error = stored(event).last_error
    assert error is not None
    assert error.startswith("strict: ValidationError (")
    assert "ana.perez" not in error


def test_a_stored_event_that_no_longer_validates_counts_as_a_failed_attempt(
    engine: Engine, tenant_id: UUID
):
    bus = OutboxEventBus(get_sessionmaker(), max_attempts=2)
    probe = Probe()
    register_subscriber(PING, probe, consumer="audit")
    broken = uuid7()
    with tenant_session(system_ctx(tenant_id)) as s:
        s.add(
            OutboxRow(
                tenant_id=tenant_id,
                id=broken,
                type=PING,
                key="k",
                payload={"unexpected": True},  # none of the envelope fields
            )
        )
    make_due(tenant_id)

    assert bus.dispatch_pending() == 1
    with tenant_session(system_ctx(tenant_id)) as s:
        row = s.get_one(OutboxRow, (tenant_id, broken))
    assert (row.status, row.attempts) == (OutboxStatus.PENDING, 1)
    assert row.last_error is not None
    assert row.last_error.startswith("invalid stored event")

    release_locks(tenant_id)
    assert bus.dispatch_pending() == 1
    with tenant_session(system_ctx(tenant_id)) as s:
        row = s.get_one(OutboxRow, (tenant_id, broken))
    assert (row.status, row.attempts) == (OutboxStatus.DEAD, 2)
    assert probe.calls == []  # a handler never sees a broken event


# ---- a handler's own transaction ------------------------------------------------


def test_a_failing_handler_leaves_no_trace_in_the_database(
    bus: OutboxEventBus, tenant_id: UUID
):
    def writes_then_fails(session: Session, event: Event) -> None:
        session.add(Notification(tenant_id=event.tenant_id, template="t", data={}))
        session.flush()
        msg = "too late"
        raise RuntimeError(msg)

    register_subscriber(PING, writes_then_fails, consumer="notifier")
    event = an_event(tenant_id)
    emit(bus, tenant_id, event)
    make_due(tenant_id)

    assert bus.dispatch_pending() == 1

    assert notification_count(tenant_id) == 0
    assert consumed_by(event) == set()
    assert stored(event).attempts == 1


def test_a_handlers_writes_commit_together_with_its_consumption(
    bus: OutboxEventBus, tenant_id: UUID
):
    def notifies(session: Session, event: Event) -> None:
        session.add(Notification(tenant_id=event.tenant_id, template="t", data={}))

    register_subscriber(PING, notifies, consumer="notifier")
    event = an_event(tenant_id)
    emit(bus, tenant_id, event)
    make_due(tenant_id)

    assert bus.dispatch_pending() == 1

    assert notification_count(tenant_id) == 1
    assert consumed_by(event) == {"notifier"}


# A statement that always fails with a unique violation.
def insert_the_same_notification_twice(session: Session, event: Event) -> None:
    _ = session.execute(
        text(
            """
            INSERT INTO notification (tenant_id, id, template, data)
            VALUES (:tenant, :id, 'x', '{}'), (:tenant, :id, 'x', '{}')
            """
        ),
        {"tenant": event.tenant_id, "id": event.id},
    )


def test_a_handler_that_swallows_a_database_error_is_retried_not_published(
    bus: OutboxEventBus, tenant_id: UUID
):
    # "Insert if absent": the error is expected and ignored, but the failed statement
    # leaves the transaction aborted, so its commit is a rollback that raises nothing.
    def swallows(session: Session, event: Event) -> None:
        with contextlib.suppress(DBAPIError):
            insert_the_same_notification_twice(session, event)

    register_subscriber(PING, swallows, consumer="swallow")
    event = an_event(tenant_id)
    emit(bus, tenant_id, event)
    make_due(tenant_id)

    assert bus.dispatch_pending() == 1

    row = stored(event)
    assert (row.status, row.attempts) == (OutboxStatus.PENDING, 1)
    assert row.last_error == "swallow: InternalError [sqlstate=25P02]"
    assert consumed_by(event) == set()  # nothing says this consumer ran
    assert notification_count(tenant_id) == 0

    # Once the handler is fixed, the retry delivers the event.
    register_subscriber(PING, a_handler(), consumer="swallow")
    release_locks(tenant_id)
    assert bus.dispatch_pending() == 1
    assert stored(event).status is OutboxStatus.PUBLISHED
    assert consumed_by(event) == {"swallow"}


def test_a_handler_that_rolls_back_is_retried_not_published(
    bus: OutboxEventBus, tenant_id: UUID
):
    def rolls_back(session: Session, event: Event) -> None:
        session.add(Notification(tenant_id=event.tenant_id, template="t", data={}))
        session.flush()
        session.rollback()  # to recover from something, then carry on

    register_subscriber(PING, rolls_back, consumer="rb")
    event = an_event(tenant_id)
    emit(bus, tenant_id, event)
    make_due(tenant_id)

    assert bus.dispatch_pending() == 1

    row = stored(event)
    assert (row.status, row.attempts) == (OutboxStatus.PENDING, 1)
    assert row.last_error is not None
    assert row.last_error.startswith("rb: ConsumptionLostError: ")
    assert consumed_by(event) == set()
    assert notification_count(tenant_id) == 0


def test_a_handler_that_recovers_with_a_savepoint_is_published(
    bus: OutboxEventBus, tenant_id: UUID
):
    # The right way to ignore an expected database error: the savepoint is rolled
    # back, the transaction and the consumption row survive.
    def recovers(session: Session, event: Event) -> None:
        try:
            with session.begin_nested():
                insert_the_same_notification_twice(session, event)
        except DBAPIError:
            pass
        session.add(Notification(tenant_id=event.tenant_id, template="t", data={}))

    register_subscriber(PING, recovers, consumer="careful")
    event = an_event(tenant_id)
    emit(bus, tenant_id, event)
    make_due(tenant_id)

    assert bus.dispatch_pending() == 1

    row = stored(event)
    assert (row.status, row.attempts, row.last_error) == (
        OutboxStatus.PUBLISHED,
        0,
        None,
    )
    assert consumed_by(event) == {"careful"}
    assert notification_count(tenant_id) == 1


# ---- per-key ordering -----------------------------------------------------------


def test_a_failing_event_holds_back_its_key_but_not_other_keys(
    bus: OutboxEventBus, tenant_id: UUID
):
    first, second, third = (an_event(tenant_id, key="case-1") for _ in range(3))
    elsewhere = an_event(tenant_id, key="case-2")
    probe = Probe(fail_once_for=[first.id])
    register_subscriber(PING, probe, consumer="audit")
    emit(bus, tenant_id, first, second, third, elsewhere)
    make_due(tenant_id)
    set_attempts(first, 5)  # so its backoff is a minute, far longer than this test

    # The first fails; its successors wait; the other key proceeds.
    assert bus.dispatch_pending() == 2
    assert [e.id for e in probe.calls] == [first.id, elsewhere.id]
    assert stored(first).status is OutboxStatus.PENDING
    assert stored(second).attempts == stored(third).attempts == 0
    assert stored(elsewhere).status is OutboxStatus.PUBLISHED

    assert bus.dispatch_pending() == 0  # the first is backing off, the rest blocked
    assert [e.id for e in probe.calls] == [first.id, elsewhere.id]

    # Once it succeeds the key moves on, one event per cycle, in publication order.
    release_locks(tenant_id)
    assert bus.dispatch_pending() == 1
    assert bus.dispatch_pending() == 1
    assert bus.dispatch_pending() == 1
    assert bus.dispatch_pending() == 0
    assert [e.id for e in probe.done] == [elsewhere.id, first.id, second.id, third.id]
    for event in (first, second, third, elsewhere):
        assert stored(event).status is OutboxStatus.PUBLISHED


def test_a_dead_event_no_longer_holds_back_its_key(engine: Engine, tenant_id: UUID):
    bus = OutboxEventBus(get_sessionmaker(), max_attempts=1)
    first, second = an_event(tenant_id, key="case-1"), an_event(tenant_id, key="case-1")
    probe = Probe(fail_once_for=[first.id])
    register_subscriber(PING, probe, consumer="audit")
    emit(bus, tenant_id, first, second)
    make_due(tenant_id)

    assert bus.dispatch_pending() == 1
    assert stored(first).status is OutboxStatus.DEAD
    assert bus.dispatch_pending() == 1

    assert [e.id for e in probe.done] == [second.id]
    assert stored(second).status is OutboxStatus.PUBLISHED


def test_events_waiting_behind_a_key_do_not_crowd_other_keys_out_of_a_batch(
    bus: OutboxEventBus, tenant_id: UUID
):
    stuck = [an_event(tenant_id, key="stuck") for _ in range(3)]
    later = an_event(tenant_id, key="later")
    probe = Probe()
    register_subscriber(PING, probe, consumer="audit")
    emit(bus, tenant_id, *stuck, later)
    make_due(tenant_id)
    set_lease(stuck[0], 3600)  # the head of the key is out of reach

    # The two events queued behind it are the oldest claimable rows. A claim that
    # took the oldest rows first and filtered afterwards would find only them.
    assert bus.dispatch_pending(batch=2) == 1

    assert [e.id for e in probe.done] == [later.id]


def test_the_cross_key_filter_ignores_other_tenants(engine: Engine):
    bus = OutboxEventBus(get_sessionmaker())
    alpha, beta = make_tenant("alpha"), make_tenant("beta")
    probe = Probe()
    register_subscriber(PING, probe, consumer="audit")
    stuck = an_event(alpha, key="shared-key")
    same_key_elsewhere = an_event(beta, key="shared-key")
    emit(bus, alpha, stuck)
    emit(bus, beta, same_key_elsewhere)
    make_due(alpha)
    make_due(beta)
    set_lease(stuck, 3600)

    assert bus.dispatch_pending() == 1

    assert [e.id for e in probe.done] == [same_key_elsewhere.id]


def test_the_claim_filters_by_tenant_without_help_from_row_level_security(
    admin_engine: Engine,
):
    # A superuser session ignores row-level security, so only the statement's own
    # tenant filters decide what it sees.
    bus = OutboxEventBus(get_sessionmaker())
    alpha, beta = make_tenant("alpha"), make_tenant("beta")
    older_in_alpha = an_event(alpha, key="shared-key")
    same_key_in_beta = an_event(beta, key="shared-key")
    emit(bus, alpha, older_in_alpha)
    emit(bus, beta, same_key_in_beta)
    make_due(alpha)
    make_due(beta)

    unrestricted = sessionmaker(bind=admin_engine, expire_on_commit=False)
    with tenant_session(system_ctx(beta), factory=unrestricted) as s:
        rows = s.scalars(claim_statement(beta, 200)).all()

    assert [row.id for row in rows] == [same_key_in_beta.id]


def test_a_backlog_of_ten_thousand_events_on_one_key_is_claimed_quickly(
    bus: OutboxEventBus, tenant_id: UUID
):
    # A consumer that was down leaves a long queue behind one key. Only the oldest
    # event of the key can be claimed, and finding it must not cost a comparison of
    # every event with every other one (seconds, and growing with the square).
    probe = Probe()
    register_subscriber(PING, probe, consumer="audit")
    seed_rows(tenant_id, rows=10_000, keys=1)
    with tenant_session(system_ctx(tenant_id)) as s:
        oldest = list(s.scalars(select(OutboxRow.id).order_by(OutboxRow.seq).limit(3)))

    timings: list[float] = []
    for _ in oldest:
        started = time.monotonic()
        assert bus.dispatch_pending() == 1  # the head of the key; the rest wait
        timings.append(time.monotonic() - started)

    assert [e.id for e in probe.done] == oldest  # one at a time, in order
    # Tens of milliseconds a call; the claim by NOT EXISTS took four seconds. The
    # best of three, so that one stalled call cannot fail the test.
    assert min(timings) < 0.5


@pytest.mark.parametrize("keys", [1, 100], ids=["one-key", "hundred-keys"])
def test_a_backlog_that_builds_after_the_table_was_analysed_is_claimed_quickly(
    engine: Engine, admin_engine: Engine, keys: int
):
    # The planner trusts the statistics of the last ANALYZE. Taken while nothing was
    # pending (here: only another tenant's published history), they promise a claim
    # that finds almost no pending row, and a plan that re-runs the per-key
    # aggregate for every candidate row then costs seconds once the backlog is there
    # (6 s for a claim written as a join to that aggregate).
    alpha, beta = make_tenant("alpha"), make_tenant("beta")
    seed_rows(beta, rows=20_000, keys=5_000, status=OutboxStatus.PUBLISHED)
    with admin_engine.begin() as conn:
        _ = conn.execute(text("ANALYZE outbox"))
    seed_rows(alpha, rows=10_000, keys=keys)

    timings: list[float] = []
    claimed: list[UUID] = []
    for _ in range(3):
        with tenant_session(system_ctx(alpha)) as s:
            started = time.monotonic()
            claimed = [row.id for row in s.scalars(claim_statement(alpha, 200))]
            timings.append(time.monotonic() - started)
    with tenant_session(system_ctx(alpha)) as s:
        oldest_of_each_key = set(
            s.scalars(
                text(
                    """
                    SELECT DISTINCT ON (key) id FROM outbox
                    WHERE status = 'PENDING' ORDER BY key, seq
                    """
                )
            )
        )

    assert len(claimed) == keys  # the oldest event of each key, and nothing else
    assert set(claimed) == oldest_of_each_key
    # Milliseconds when the claim does not depend on the statistics. The best of
    # three, so that one stalled call cannot fail the test.
    assert min(timings) < 0.5


# ---- exactly-once consumption ---------------------------------------------------


def test_redelivery_after_a_crash_before_the_status_update_skips_the_handlers(
    bus: OutboxEventBus, tenant_id: UUID, monkeypatch: pytest.MonkeyPatch
):
    audit, search = Probe(), Probe()
    register_subscriber(PING, audit, consumer="audit")
    register_subscriber(PING, search, consumer="search")
    event = an_event(tenant_id)
    emit(bus, tenant_id, event)
    make_due(tenant_id)

    # The process dies after both handlers committed and before the row is updated.
    def crash(*args: object, **kwargs: object) -> None:
        raise ProcessDiedError

    with monkeypatch.context() as patch:
        patch.setattr(OutboxEventBus, "_settle", crash)
        with pytest.raises(ProcessDiedError):
            _ = bus.dispatch_pending()
    assert len(audit.done) == len(search.done) == 1
    assert consumed_by(event) == {"audit", "search"}
    row = stored(event)
    assert (row.status, row.attempts) == (OutboxStatus.PENDING, 0)
    assert 30 < locked_for(event) <= 60  # still leased by the dead dispatcher

    # Its lease expires and another dispatcher picks the event up again.
    release_locks(tenant_id)
    assert bus.dispatch_pending() == 1

    assert len(audit.calls) == len(search.calls) == 1  # no handler ran again
    row = stored(event)
    assert (row.status, row.attempts) == (OutboxStatus.PUBLISHED, 0)
    assert row.published_at is not None


def test_a_handler_that_died_mid_run_is_redelivered_without_double_effects(
    bus: OutboxEventBus, tenant_id: UUID
):
    audit = Probe()
    runs: list[UUID] = []

    def notifies(session: Session, event: Event) -> None:
        session.add(Notification(tenant_id=event.tenant_id, template="t", data={}))
        session.flush()
        runs.append(event.id)
        if len(runs) == 1:  # the process dies on the first run only
            raise ProcessDiedError

    register_subscriber(PING, audit, consumer="audit")
    register_subscriber(PING, notifies, consumer="notifier")
    event = an_event(tenant_id)
    emit(bus, tenant_id, event)
    make_due(tenant_id)

    with pytest.raises(ProcessDiedError):
        _ = bus.dispatch_pending()  # the audit consumer finished, the notifier died
    assert consumed_by(event) == {"audit"}
    assert notification_count(tenant_id) == 0  # its half-done transaction rolled back

    release_locks(tenant_id)
    assert bus.dispatch_pending() == 1

    assert len(audit.calls) == 1
    assert notification_count(tenant_id) == 1
    assert consumed_by(event) == {"audit", "notifier"}
    assert stored(event).status is OutboxStatus.PUBLISHED


# ---- concurrent dispatchers -----------------------------------------------------


def pending_count(tenant_id: UUID) -> int:
    with tenant_session(system_ctx(tenant_id)) as s:
        return cast(
            "int",
            s.scalar(
                select(func.count())
                .select_from(OutboxRow)
                .where(OutboxRow.status == OutboxStatus.PENDING)
            ),
        )


# Dispatch until nothing is pending: a worker loop without the tick sleep.
def drain(bus: OutboxEventBus, tenant_id: UUID, *, deadline: float) -> int:
    handled = 0
    while pending_count(tenant_id) and time.monotonic() < deadline:
        step = bus.dispatch_pending()
        handled += step
        if not step:
            time.sleep(0.01)
    return handled


def skip_backoffs(tenant_id: UUID) -> None:
    """Expire the backoff of retrying events, not the lease of events being handled.

    A backoff here is a few seconds and a lease is sixty, so the cut-off tells them
    apart without touching an event a dispatcher is working on.
    """
    with tenant_session(system_ctx(tenant_id)) as s:
        _ = s.execute(
            text(
                """
                UPDATE outbox SET locked_until = NULL
                WHERE status = 'PENDING' AND attempts > 0
                  AND locked_until < now() + interval '30 seconds'
                """
            )
        )


def by_key(events: Iterable[Event]) -> dict[str, list[UUID]]:
    grouped: dict[str, list[UUID]] = defaultdict(list)
    for event in events:
        grouped[event.key].append(event.id)
    return dict(grouped)


# Wait until a dispatcher sits on a competing consumption insert. A unique-key insert
# waits for the other transaction to finish, which shows as an ungranted transactionid
# lock; pg_locks is readable by every role, unlike the query text of other sessions.
def wait_for_a_transaction_wait(engine: Engine, *, timeout: float = 15) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with engine.connect() as conn:
            waiting = conn.scalar(
                text(
                    """
                    SELECT count(*) FROM pg_locks
                    WHERE locktype = 'transactionid' AND NOT granted
                    """
                )
            )
        if waiting:
            return
        time.sleep(0.02)
    pytest.fail("no dispatcher ever waited on another transaction")


def test_two_dispatchers_consume_every_event_exactly_once_in_key_order(
    engine: Engine, tenant_id: UUID
):
    dispatchers = [OutboxEventBus(get_sessionmaker()) for _ in range(2)]
    audit, search = Probe(), Probe()
    register_subscriber(PING, audit, consumer="audit")
    register_subscriber(PING, search, consumer="search")
    events = [an_event(tenant_id, key=f"case-{n % 5}") for n in range(50)]
    emit(dispatchers[0], tenant_id, *events)
    make_due(tenant_id)

    deadline = time.monotonic() + 60
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(drain, d, tenant_id, deadline=deadline) for d in dispatchers
        ]
        handled = [future.result() for future in futures]

    assert sum(handled) == 50
    for probe in (audit, search):
        assert len(probe.calls) == 50  # each handler ran once per event
        assert by_key(probe.done) == by_key(events)  # and in publication order
    assert pending_count(tenant_id) == 0
    for event in events:
        assert stored(event).status is OutboxStatus.PUBLISHED


def test_two_dispatchers_keep_a_keys_order_while_its_first_event_retries(
    engine: Engine, tenant_id: UUID
):
    dispatchers = [OutboxEventBus(get_sessionmaker()) for _ in range(2)]
    events = [an_event(tenant_id, key=f"case-{n % 3}") for n in range(12)]
    heads = [next(e for e in events if e.key == f"case-{n}") for n in range(3)]
    audit, search = Probe(fail_once_for=[h.id for h in heads]), Probe()
    register_subscriber(PING, audit, consumer="audit")
    register_subscriber(PING, search, consumer="search")
    emit(dispatchers[0], tenant_id, *events)
    make_due(tenant_id)

    deadline = time.monotonic() + 60
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(drain, d, tenant_id, deadline=deadline) for d in dispatchers
        ]
        while not all(future.done() for future in futures):
            skip_backoffs(tenant_id)
            time.sleep(0.02)
        for future in futures:
            _ = future.result()

    expected = by_key(events)
    # Nothing behind a key's first event was even attempted before it succeeded.
    assert by_key(audit.calls) == {key: [ids[0], *ids] for key, ids in expected.items()}
    assert by_key(audit.done) == expected
    assert by_key(search.calls) == expected  # the other consumer ran each event once
    for head in heads:
        assert stored(head).attempts == 1
    assert pending_count(tenant_id) == 0


def test_a_lease_that_expires_during_a_slow_handler_does_not_run_the_consumer_twice(
    bus: OutboxEventBus, tenant_id: UUID, engine: Engine
):
    started, proceed = threading.Event(), threading.Event()
    calls: list[UUID] = []

    def slow(session: Session, event: Event) -> None:
        calls.append(event.id)
        started.set()
        assert proceed.wait(timeout=30)

    register_subscriber(PING, slow, consumer="slow")
    event = an_event(tenant_id)
    emit(bus, tenant_id, event)
    make_due(tenant_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(bus.dispatch_pending)
        assert started.wait(timeout=30)
        release_locks(tenant_id)  # its lease runs out while the handler still works
        second = pool.submit(bus.dispatch_pending)  # another dispatcher takes over
        wait_for_a_transaction_wait(engine)
        proceed.set()
        assert first.result() == second.result() == 1

    assert calls == [event.id]
    assert consumed_by(event) == {"slow"}
    assert stored(event).status is OutboxStatus.PUBLISHED


def test_a_row_locked_by_another_claimer_is_skipped_not_waited_for(
    bus: OutboxEventBus, tenant_id: UUID
):
    locked, free = an_event(tenant_id, key="a"), an_event(tenant_id, key="b")
    probe = Probe()
    register_subscriber(PING, probe, consumer="audit")
    emit(bus, tenant_id, locked, free)
    make_due(tenant_id)

    # The pool is the outer context, so a dispatcher that waits for the lock fails the
    # timeout, the claimer lets go, and only then does the pool wait for the thread.
    with (
        ThreadPoolExecutor(max_workers=1) as pool,
        tenant_session(system_ctx(tenant_id)) as claimer,
    ):
        # Another dispatcher is in the middle of claiming the first event.
        _ = claimer.execute(
            text("SELECT id FROM outbox WHERE id = :id FOR UPDATE"), {"id": locked.id}
        )
        handled = pool.submit(bus.dispatch_pending).result(timeout=15)

    assert handled == 1
    assert [e.id for e in probe.done] == [free.id]
    assert bus.dispatch_pending() == 1  # free again once the claimer let go
    assert [e.id for e in probe.done] == [free.id, locked.id]


def test_a_late_failure_from_an_expired_lease_does_not_touch_a_published_event(
    bus: OutboxEventBus, tenant_id: UUID, monkeypatch: pytest.MonkeyPatch
):
    # Dispatcher A runs both consumers, one fails, and A stalls before it records
    # the failure. Its lease expires, dispatcher B finishes the event, and only then
    # does A's failure arrive.
    steady, flaky = Probe(), Probe(fail_times=1)
    register_subscriber(PING, steady, consumer="steady")
    register_subscriber(PING, flaky, consumer="flaky")
    event = an_event(tenant_id)
    emit(bus, tenant_id, event)
    make_due(tenant_id)

    stalled, resume = threading.Event(), threading.Event()
    settle = cast("Callable[..., None]", vars(OutboxEventBus)["_settle"])

    def stall_failures(
        self: OutboxEventBus, ctx: TenantContext, event_id: UUID, failures: list[str]
    ) -> None:
        if failures:
            stalled.set()
            assert resume.wait(timeout=30)
        settle(self, ctx, event_id, failures)

    monkeypatch.setattr(OutboxEventBus, "_settle", stall_failures)
    with ThreadPoolExecutor(max_workers=1) as pool:
        late = pool.submit(bus.dispatch_pending)
        assert stalled.wait(timeout=30)
        release_locks(tenant_id)  # A's lease runs out
        assert bus.dispatch_pending() == 1  # B finishes the event
        assert stored(event).status is OutboxStatus.PUBLISHED
        resume.set()
        assert late.result() == 1  # A's failure arrives after the fact

    row = stored(event)
    assert (row.status, row.attempts, row.last_error) == (
        OutboxStatus.PUBLISHED,
        0,
        None,
    )
    assert row.locked_until is None
    assert len(steady.calls) == 1
    assert len(flaky.calls) == 2


# ---- replay ---------------------------------------------------------------------


def hour_ago() -> datetime:
    return clock.now() - timedelta(hours=1)


def settle_all(bus: OutboxEventBus, tenant_id: UUID, *events: Event) -> None:
    """Publish the events and dispatch until every one of them is published."""
    emit(bus, tenant_id, *events)
    make_due(tenant_id)
    while bus.dispatch_pending():
        pass


def test_replay_runs_only_the_named_consumer_again(
    bus: OutboxEventBus, tenant_id: UUID
):
    audit, search = Probe(), Probe()
    register_subscriber(PING, audit, consumer="audit")
    register_subscriber(PING, search, consumer="search")
    first, second = an_event(tenant_id, key="a"), an_event(tenant_id, key="b")
    settle_all(bus, tenant_id, first, second)
    before = {(e.id, c): consumed_at(e, c) for e in (first, second) for c in CONSUMERS}

    assert bus.replay(since=hour_ago(), types=[PING], consumer="audit") == 2

    assert [e.id for e in audit.done] == [first.id, second.id, first.id, second.id]
    assert audit.done[2:] == audit.done[:2]  # the same events, as stored
    assert [e.id for e in search.done] == [first.id, second.id]  # not run again
    for event in (first, second):
        assert consumed_by(event) == {"audit", "search"}
        assert stored(event).status is OutboxStatus.PUBLISHED
        # The replayed consumer's row was made again; the other's was not touched.
        assert consumed_at(event, "audit") > before[event.id, "audit"]
        assert consumed_at(event, "search") == before[event.id, "search"]


def test_replay_only_covers_the_requested_types(bus: OutboxEventBus, tenant_id: UUID):
    audit = Probe()
    register_subscriber("*", audit, consumer="audit")
    ping = an_event(tenant_id, type_=PING, key="a")
    pong = an_event(tenant_id, type_=PONG, key="b")
    settle_all(bus, tenant_id, ping, pong)

    assert bus.replay(since=hour_ago(), types=[PONG], consumer="audit") == 1
    assert bus.replay(since=hour_ago(), types=[], consumer="audit") == 0

    assert [e.id for e in audit.done] == [ping.id, pong.id, pong.id]


def test_replay_skips_events_of_types_the_consumer_does_not_subscribe_to(
    bus: OutboxEventBus, tenant_id: UUID
):
    pings, pongs = Probe(), Probe()
    register_subscriber(PING, pings, consumer="pings")
    register_subscriber(PONG, pongs, consumer="pongs")
    ping = an_event(tenant_id, type_=PING, key="a")
    pong = an_event(tenant_id, type_=PONG, key="b")
    settle_all(bus, tenant_id, ping, pong)

    assert bus.replay(since=hour_ago(), types=[PING, PONG], consumer="pings") == 1

    assert [e.id for e in pings.done] == [ping.id, ping.id]
    assert [e.id for e in pongs.done] == [pong.id]
    assert consumed_by(pong) == {"pongs"}


def test_replay_ignores_events_created_before_since(
    bus: OutboxEventBus, tenant_id: UUID
):
    audit = Probe()
    register_subscriber(PING, audit, consumer="audit")
    old, recent = an_event(tenant_id, key="a"), an_event(tenant_id, key="b")
    settle_all(bus, tenant_id, old, recent)
    set_age(old, 7200)
    set_age(recent, 60)

    assert bus.replay(since=hour_ago(), types=[PING], consumer="audit") == 1

    assert [e.id for e in audit.done] == [old.id, recent.id, recent.id]


def test_replay_covers_one_tenant_or_all_of_them(engine: Engine):
    bus = OutboxEventBus(get_sessionmaker())
    audit = Probe()
    register_subscriber(PING, audit, consumer="audit")
    alpha, beta = make_tenant("alpha"), make_tenant("beta")
    first, second = an_event(alpha), an_event(beta)
    settle_all(bus, alpha, first)
    settle_all(bus, beta, second)

    assert (
        bus.replay(since=hour_ago(), types=[PING], consumer="audit", tenant_id=beta)
        == 1
    )
    assert [e.id for e in audit.done] == [first.id, second.id, second.id]

    assert bus.replay(since=hour_ago(), types=[PING], consumer="audit") == 2
    assert sorted(e.id for e in audit.done[3:]) == sorted([first.id, second.id])


def test_replay_leaves_events_that_are_not_published_yet_alone(
    bus: OutboxEventBus, tenant_id: UUID
):
    audit = Probe()
    register_subscriber(PING, audit, consumer="audit")
    done, waiting = an_event(tenant_id, key="a"), an_event(tenant_id, key="b")
    settle_all(bus, tenant_id, done)
    emit(bus, tenant_id, waiting)  # still PENDING, not even due

    assert bus.replay(since=hour_ago(), types=[PING], consumer="audit") == 1

    assert [e.id for e in audit.done] == [done.id, done.id]
    assert stored(waiting).status is OutboxStatus.PENDING
    assert consumed_by(waiting) == set()


def test_replaying_the_failed_consumer_revives_a_dead_event(
    engine: Engine, tenant_id: UUID
):
    bus = OutboxEventBus(get_sessionmaker(), max_attempts=1, clock_fn=lambda: FROZEN)
    good, broken = Probe(), Probe(fail_times=1)  # the bug is gone by the replay
    register_subscriber(PING, good, consumer="good")
    register_subscriber(PING, broken, consumer="broken")
    event = an_event(tenant_id)
    emit(bus, tenant_id, event)
    make_due(tenant_id)
    assert bus.dispatch_pending() == 1
    row = stored(event)
    assert (row.status, row.attempts) == (OutboxStatus.DEAD, 1)
    assert consumed_by(event) == {"good"}

    assert bus.replay(since=hour_ago(), types=[PING], consumer="broken") == 1

    row = stored(event)
    assert (row.status, row.attempts) == (OutboxStatus.PUBLISHED, 1)
    assert row.published_at == FROZEN
    assert row.locked_until is None
    assert row.last_error == "broken: RuntimeError: boom"  # the history stays
    assert consumed_by(event) == {"good", "broken"}
    assert len(good.calls) == 1  # the healthy consumer did not run again


def test_a_dead_event_stays_dead_until_every_consumer_has_handled_it(
    engine: Engine, tenant_id: UUID
):
    bus = OutboxEventBus(get_sessionmaker(), max_attempts=1)
    register_subscriber(PING, Probe(fail_times=1), consumer="alpha")
    register_subscriber(PING, Probe(fail_times=1), consumer="beta")
    event = an_event(tenant_id)
    emit(bus, tenant_id, event)
    make_due(tenant_id)
    assert bus.dispatch_pending() == 1
    assert stored(event).status is OutboxStatus.DEAD
    assert consumed_by(event) == set()

    assert bus.replay(since=hour_ago(), types=[PING], consumer="alpha") == 1
    assert stored(event).status is OutboxStatus.DEAD  # beta has not handled it

    assert bus.replay(since=hour_ago(), types=[PING], consumer="beta") == 1
    assert stored(event).status is OutboxStatus.PUBLISHED


def test_a_replay_that_fails_stops_and_keeps_the_consumption(
    bus: OutboxEventBus, tenant_id: UUID
):
    events = [an_event(tenant_id, key=f"k{n}") for n in range(3)]
    register_subscriber(PING, Probe(), consumer="audit")
    settle_all(bus, tenant_id, *events)
    # The consumer is redeployed with a handler that chokes on the second event.
    redeployed = Probe(fail_once_for=[events[1].id])
    register_subscriber(PING, redeployed, consumer="audit")

    with pytest.raises(RuntimeError, match="boom"):
        _ = bus.replay(since=hour_ago(), types=[PING], consumer="audit")

    assert [e.id for e in redeployed.calls] == [events[0].id, events[1].id]
    for event in events:  # the failed replay did not erase the consumption
        assert consumed_by(event) == {"audit"}
        assert stored(event).status is OutboxStatus.PUBLISHED


def test_a_replayed_handler_that_rolls_back_fails_the_replay(
    bus: OutboxEventBus, tenant_id: UUID
):
    event = an_event(tenant_id)
    register_subscriber(PING, Probe(), consumer="audit")
    settle_all(bus, tenant_id, event)
    consumed_before = consumed_at(event, "audit")

    def rolls_back(session: Session, event: Event) -> None:
        session.rollback()

    register_subscriber(PING, rolls_back, consumer="audit")

    with pytest.raises(ConsumptionLostError):
        _ = bus.replay(since=hour_ago(), types=[PING], consumer="audit")

    assert consumed_at(event, "audit") == consumed_before  # the old row survived


def test_a_replayed_handler_that_swallows_an_error_cannot_revive_a_dead_event(
    engine: Engine, tenant_id: UUID
):
    bus = OutboxEventBus(get_sessionmaker(), max_attempts=1)
    register_subscriber(PING, Probe(fail_times=1), consumer="audit")
    event = an_event(tenant_id)
    emit(bus, tenant_id, event)
    make_due(tenant_id)
    assert bus.dispatch_pending() == 1
    assert stored(event).status is OutboxStatus.DEAD

    def swallows(session: Session, event: Event) -> None:
        with contextlib.suppress(DBAPIError):
            insert_the_same_notification_twice(session, event)

    register_subscriber(PING, swallows, consumer="audit")

    with pytest.raises(DBAPIError):  # the failed transaction rejects the check
        _ = bus.replay(since=hour_ago(), types=[PING], consumer="audit")

    assert stored(event).status is OutboxStatus.DEAD
    assert consumed_by(event) == set()


def test_replay_names_the_stored_event_that_no_longer_validates(
    bus: OutboxEventBus, tenant_id: UUID
):
    audit = Probe()
    register_subscriber(PING, audit, consumer="audit")
    healthy = an_event(tenant_id, key="a")
    settle_all(bus, tenant_id, healthy)
    broken = uuid7()
    with tenant_session(system_ctx(tenant_id)) as s:
        s.add(
            OutboxRow(
                tenant_id=tenant_id,
                id=broken,
                type=PING,
                key="b",
                payload={"unexpected": True},  # none of the envelope fields
                status=OutboxStatus.DEAD,
            )
        )

    with pytest.raises(InvalidStoredEventError, match=str(broken)):
        _ = bus.replay(since=hour_ago(), types=[PING], consumer="audit")

    # Events replayed before the broken one stay replayed.
    assert [e.id for e in audit.done] == [healthy.id, healthy.id]


def test_replay_reads_a_naive_since_as_utc(
    bus: OutboxEventBus, tokyo_bus: OutboxEventBus, tenant_id: UUID
):
    # Read in the session's own zone, a naive hour-ago in UTC would be nine hours
    # further back in a Tokyo session, and would take the old event in as well.
    replayed: list[UUID] = []
    zones: list[str] = []

    def audit(session: Session, event: Event) -> None:
        replayed.append(event.id)
        zones.append(cast("str", session.scalar(text("SHOW TimeZone"))))

    register_subscriber(PING, audit, consumer="audit")
    old, recent = an_event(tenant_id, key="a"), an_event(tenant_id, key="b")
    settle_all(bus, tenant_id, old, recent)
    set_age(old, 3 * 3600)
    set_age(recent, 10 * 60)
    replayed.clear()
    zones.clear()
    naive_hour_ago_in_utc = clock.now().replace(tzinfo=None) - timedelta(hours=1)

    count = tokyo_bus.replay(
        since=naive_hour_ago_in_utc, types=[PING], consumer="audit"
    )

    assert count == 1
    assert replayed == [recent.id]
    assert zones == ["Asia/Tokyo"]  # the replay really ran in that zone


def test_replay_of_an_unknown_consumer_is_refused(bus: OutboxEventBus, tenant_id: UUID):
    audit = Probe()
    register_subscriber(PING, audit, consumer="audit")
    settle_all(bus, tenant_id, an_event(tenant_id))

    with pytest.raises(ValidationFailedError, match="serach"):
        _ = bus.replay(since=hour_ago(), types=[PING], consumer="serach")

    assert len(audit.calls) == 1


def test_replayed_handlers_run_as_the_replay_actor_in_the_events_tenant(
    bus: OutboxEventBus, tenant_id: UUID
):
    seen: list[tuple[UUID | None, ActorType, str]] = []

    def spy(session: Session, event: Event) -> None:
        ctx = bound_context(session)
        seen.append((ctx.tenant_id, ctx.actor_type, ctx.actor_id))

    register_subscriber(PING, spy, consumer="spy")
    settle_all(bus, tenant_id, an_event(tenant_id))
    seen.clear()

    assert bus.replay(since=hour_ago(), types=[PING], consumer="spy") == 1

    assert seen == [(tenant_id, ActorType.SYSTEM, "replay")]
