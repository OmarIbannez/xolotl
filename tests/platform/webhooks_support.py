"""Helpers shared by the webhook tests: the platform's and the routes'."""

import socket
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol
from uuid import UUID

from sqlalchemy import select, text

from tests.helpers import system_ctx
from tests.platform.jobs_support import FakeClock
from xolotl.platform.config import Settings
from xolotl.platform.crypto.encrypted import encrypt
from xolotl.platform.db import get_sessionmaker, tenant_session
from xolotl.platform.enums import EndpointStatus
from xolotl.platform.events.bus import OutboxEventBus
from xolotl.platform.events.event import Event
from xolotl.platform.events.models import WebhookDelivery, WebhookEndpoint
from xolotl.platform.events.webhooks import (
    DELIVER_KIND,
    ClockFn,
    generate_webhook_secret,
    register_webhooks,
)
from xolotl.platform.ids import uuid7
from xolotl.platform.jobs.models import JobRow
from xolotl.platform.jobs.queue import PostgresJobQueue
from xolotl.platform.jobs.runner import JobRunner
from xolotl.worker import EMBEDDED_THREAD_NAME

PUBLIC_IP = "93.184.216.34"
HOOK = "https://hooks.example.com/xolotl"
# Where the mocked receiver sees the request: the address that was checked.
PINNED = f"https://{PUBLIC_IP}/xolotl"
PING = "test.ping.v1"


class Resolves:
    """A resolver with canned answers that remembers what it was asked."""

    def __init__(self, answers: dict[str, Sequence[str]] | None = None) -> None:
        self.answers = answers or {}
        self.calls: list[str] = []

    def __call__(self, host: str) -> Sequence[str]:
        self.calls.append(host)
        if host not in self.answers:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        return self.answers[host]


Env = Literal["dev", "test", "prod"]


def assert_no_embedded_worker() -> None:
    """Fail when the API's worker loop runs on a thread of this process.

    The suite builds its apps without it (``XOLOTL_EMBEDDED_WORKER=false``, set in
    ``tests/conftest.py``); the tests of the embedded worker turn it on for themselves
    and stop it before they end. A test that drives the dispatcher and the job runner
    by hand shares the process-wide subscriber and job-handler tables, and the database,
    with such a thread, which would take its events and jobs and make it flaky. This
    makes that fail loudly instead.
    """
    running = [
        thread.name
        for thread in threading.enumerate()
        if thread.name == EMBEDDED_THREAD_NAME
    ]
    assert not running, "an embedded worker runs beside a test that drives the queue"


class Stack:
    """The real bus, queue and runner on one fake clock, webhooks registered.

    ``env`` is the deployment the webhook policy is built for: ``prod`` resolves and
    pins every host, anything else calls URLs as written. The stack is driven by hand
    (``fan_out``, ``run_jobs``), so no embedded worker may run beside it.
    """

    def __init__(
        self,
        clock: ClockFn,
        *,
        env: Env = "prod",
        resolver: Resolves | None = None,
        timeout: int = 5,
    ) -> None:
        assert_no_embedded_worker()
        self.clock = clock
        self.resolver = resolver or Resolves({"hooks.example.com": [PUBLIC_IP]})
        factory = get_sessionmaker()
        self.bus = OutboxEventBus(factory)
        self.queue = PostgresJobQueue(factory, clock_fn=clock)
        self.runner = JobRunner(self.queue, factory, worker_id="w-test")
        self.settings = Settings.model_construct(
            env=env, webhook_timeout_seconds=timeout
        )
        register_webhooks(
            self.bus,
            self.queue,
            settings=self.settings,
            resolver=self.resolver,
            clock_fn=clock,
        )

    def fan_out(self, tenant_id: UUID, *events: Event) -> int:
        """Publish the events and let the dispatcher hand them to the fan-out.

        Returns:
            How many events the dispatcher handled.

        """
        emit(self.bus, tenant_id, *events)
        return self.bus.dispatch_pending()

    def settle(self, tenant_id: UUID) -> None:
        """Dispatch what already waits in the tenant's outbox, before it has endpoints.

        A tenant made through the application has its creation events there; left
        alone, they would reach the endpoints a test makes next and add deliveries and
        jobs it does not expect.
        """
        _ = self.fan_out(tenant_id)

    def run_jobs(self) -> int:
        """Run every job that is due on the fake clock.

        Returns:
            How many handlers ran.

        """
        return self.runner.run_once()


@dataclass(frozen=True, slots=True)
class Hook:
    """An endpoint row and the secret that signs its deliveries."""

    id: UUID
    secret: str


class StackFactory(Protocol):
    def __call__(
        self, *, env: Env = "prod", resolver: Resolves | None = None, timeout: int = 5
    ) -> Stack: ...


def stack_factory(clock: FakeClock) -> StackFactory:
    def build(
        *, env: Env = "prod", resolver: Resolves | None = None, timeout: int = 5
    ) -> Stack:
        return Stack(clock, env=env, resolver=resolver, timeout=timeout)

    return build


def make_endpoint(
    tenant_id: UUID,
    *,
    url: str = HOOK,
    event_types: Sequence[str] = ("*",),
    status: EndpointStatus = EndpointStatus.ACTIVE,
    secret: str | None = None,
) -> Hook:
    plain = secret or generate_webhook_secret()
    with tenant_session(system_ctx(tenant_id)) as s:
        endpoint = WebhookEndpoint(
            tenant_id=tenant_id,
            url=url,
            secret=encrypt(plain, tenant_id=tenant_id, session=s),
            event_types=list(event_types),
            status=status,
            created_by=uuid7(),
        )
        s.add(endpoint)
        s.flush()
        return Hook(endpoint.id, plain)


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
    """Publish in one transaction and age the events past the dispatcher's grace."""
    with tenant_session(system_ctx(tenant_id)) as s:
        for event in events:
            bus.publish(s, event)
    with tenant_session(system_ctx(tenant_id)) as s:
        _ = s.execute(
            text("UPDATE outbox SET created_at = created_at - interval '1 minute'")
        )


def deliveries(tenant_id: UUID) -> list[WebhookDelivery]:
    with tenant_session(system_ctx(tenant_id)) as s:
        return list(
            s.scalars(
                select(WebhookDelivery).order_by(
                    WebhookDelivery.created_at, WebhookDelivery.id
                )
            )
        )


def delivery_for(tenant_id: UUID, event: Event, hook: Hook) -> WebhookDelivery:
    with tenant_session(system_ctx(tenant_id)) as s:
        return s.scalars(
            select(WebhookDelivery).where(
                WebhookDelivery.event_id == event.id,
                WebhookDelivery.endpoint_id == hook.id,
            )
        ).one()


def delivery_jobs(tenant_id: UUID) -> list[JobRow]:
    with tenant_session(system_ctx(tenant_id)) as s:
        return list(
            s.scalars(
                select(JobRow)
                .where(JobRow.kind == DELIVER_KIND)
                .order_by(JobRow.created_at, JobRow.id)
            )
        )
