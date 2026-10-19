"""Webhooks in the composed platform: the API and ``xolotl worker`` both deliver.

``wire_platform`` registers the webhook fan-out (consumer ``webhooks``) and the
``webhook.deliver`` job for every process that composes the platform, so an event
reaches a tenant's endpoints whichever process dispatches it: the embedded worker of
the API, or a process of its own. These tests go through the two composition roots
(``build_app``, ``build_worker``) and the real worker loop, with the receiver mocked
by ``respx``. The routes take their queue from the platform the application was
composed with.
"""

import threading
from collections.abc import Callable, Generator
from contextlib import contextmanager
from typing import TYPE_CHECKING, cast
from uuid import UUID

import httpx
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session
from starlette.requests import Request

from tests.helpers import first_login, make_tenant_with_keys, new_tenant, system_ctx
from tests.platform.jobs_support import FakeClock, eventually
from tests.platform.webhooks_support import (
    HOOK,
    PING,
    Stack,
    an_event,
    deliveries,
    delivery_jobs,
    emit,
    make_endpoint,
)
from xolotl.app import build_app, build_worker, worker_loop
from xolotl.modules.tenants.routes_webhooks import webhook_queue
from xolotl.platform.api.app import create_app
from xolotl.platform.config import get_settings
from xolotl.platform.crypto.provider import (
    LocalKeyProvider,
    get_key_provider,
    set_key_provider,
)
from xolotl.platform.db import get_sessionmaker, tenant_session
from xolotl.platform.enums import DeliveryStatus, JobStatus
from xolotl.platform.events import OutboxEventBus
from xolotl.platform.events.registry import (
    consumers,
    reset_subscribers,
    subscribers_for,
)
from xolotl.platform.events.webhooks import (
    CONSUMER,
    DELIVER_KIND,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    verify_signature,
)
from xolotl.platform.jobs.queue import Job, job_handler, register_job_handler
from xolotl.worker import EMBEDDED_THREAD_NAME

if TYPE_CHECKING:
    from xolotl.platform.api.app import AppState

PASSWORD = "a-long-enough-password"


@pytest.fixture
def clean_registry() -> Generator[None]:
    # The registry is process-wide: only a composition made inside the test may fill it.
    reset_subscribers()
    yield
    reset_subscribers()


pytestmark = pytest.mark.usefixtures("clean_registry")


@pytest.fixture
def embedded_on() -> Generator[None]:
    with pytest.MonkeyPatch.context() as patched:
        patched.setenv("XOLOTL_EMBEDDED_WORKER", "true")
        patched.setenv("XOLOTL_WORKER_TICK_SECONDS", "0.02")
        get_settings.cache_clear()
        yield
    get_settings.cache_clear()


@pytest.fixture
def receiver() -> Generator[respx.MockRouter]:
    # Anything the test has not mocked raises instead of reaching the network.
    with respx.mock(assert_all_called=False) as router:
        yield router


@pytest.fixture
def tenant_id(engine: Engine) -> UUID:
    return make_tenant_with_keys("alpha")


def age_outbox(tenant_id: UUID) -> None:
    # Past the dispatcher's one-second grace window.
    with tenant_session(system_ctx(tenant_id)) as s:
        _ = s.execute(
            text("UPDATE outbox SET created_at = created_at - interval '1 minute'")
        )


@contextmanager
def fresh_process() -> Generator[None]:
    """Run as a process that has installed no key provider yet, as a worker starts.

    Yields:
        Nothing; no provider is installed until the block ends, when the suite's own
        is back.

    """
    installed = get_key_provider()
    set_key_provider(None)
    try:
        yield
    finally:
        set_key_provider(installed)


def state_of(app: FastAPI) -> "AppState":
    return cast("AppState", app.state.xolotl)


def request_for(app: FastAPI) -> Request:
    return Request({"type": "http", "app": app, "headers": []})


def is_signed_by(secret: str, request: httpx.Request) -> bool:
    return verify_signature(
        secret,
        request.headers[SIGNATURE_HEADER],
        request.headers[TIMESTAMP_HEADER],
        request.content,
    )


# ---- both processes register the fan-out and the delivery job -----------------------


@pytest.mark.parametrize(
    "compose", [build_app, build_worker], ids=["api process", "xolotl worker"]
)
def test_composing_the_platform_registers_the_fanout_and_the_delivery_job(
    engine: Engine, compose: Callable[[], object]
):
    def stale(_session: Session, _job: Job) -> None:
        raise AssertionError

    register_job_handler(DELIVER_KIND, stale)
    assert consumers() == frozenset()

    _ = compose()

    assert CONSUMER in consumers()
    assert CONSUMER in {name for name, _ in subscribers_for("party.created.v1")}
    assert job_handler(DELIVER_KIND) is not stale


# ---- an event reaches an endpoint through each composition --------------------------


def test_xolotl_worker_delivers_a_published_event_to_a_matching_endpoint(
    tenant_id: UUID, receiver: respx.MockRouter
):
    hook = make_endpoint(tenant_id, event_types=[PING])
    route = receiver.post(HOOK).mock(return_value=httpx.Response(200))
    event = an_event(tenant_id)
    emit(OutboxEventBus(get_sessionmaker()), tenant_id, event)

    assert build_worker().run(max_ticks=1) == 1

    (delivery,) = deliveries(tenant_id)
    assert (delivery.status, delivery.response_code) == (DeliveryStatus.DELIVERED, 200)
    (job,) = delivery_jobs(tenant_id)
    assert job.status is JobStatus.DONE
    (call,) = route.calls
    assert call.request.headers["x-xolotl-event-id"] == str(event.id)
    assert is_signed_by(hook.secret, call.request)


def test_a_standalone_worker_opens_the_secret_it_signs_with(
    tenant_id: UUID, receiver: respx.MockRouter
):
    hook = make_endpoint(tenant_id, event_types=[PING])
    route = receiver.post(HOOK).mock(return_value=httpx.Response(200))
    emit(OutboxEventBus(get_sessionmaker()), tenant_id, an_event(tenant_id))

    with fresh_process():
        with pytest.raises(RuntimeError):
            _ = get_key_provider()  # nothing yet, as in a process that builds no app
        worker = build_worker()
        assert isinstance(get_key_provider(), LocalKeyProvider)
        assert worker.run(max_ticks=1) == 1

    (delivery,) = deliveries(tenant_id)
    assert delivery.status is DeliveryStatus.DELIVERED
    (call,) = route.calls
    assert is_signed_by(hook.secret, call.request)


def test_the_api_process_fans_the_event_out_and_its_worker_loop_delivers_it(
    tenant_id: UUID, receiver: respx.MockRouter
):
    app = build_app()
    services = state_of(app).services
    assert services is not None
    hook = make_endpoint(tenant_id, event_types=[PING])
    route = receiver.post(HOOK).mock(return_value=httpx.Response(200))
    emit(services.bus, tenant_id, an_event(tenant_id))

    # The dispatcher alone: the fan-out ran and queued the delivery job.
    assert services.bus.dispatch_pending() == 1
    (job,) = delivery_jobs(tenant_id)
    assert job.status is JobStatus.PENDING
    assert route.call_count == 0

    # The loop the embedded worker runs, over the same services: the job is sent.
    assert worker_loop(services, get_settings()).run(max_ticks=1) == 1

    (delivery,) = deliveries(tenant_id)
    assert delivery.status is DeliveryStatus.DELIVERED
    (call,) = route.calls
    assert is_signed_by(hook.secret, call.request)


def test_an_event_that_no_endpoint_wants_makes_no_delivery_job(
    tenant_id: UUID, receiver: respx.MockRouter
):
    _ = make_endpoint(tenant_id, event_types=["party.created.v1"])
    route = receiver.post(HOOK).mock(return_value=httpx.Response(200))
    emit(OutboxEventBus(get_sessionmaker()), tenant_id, an_event(tenant_id))

    assert build_worker().run(max_ticks=1) == 1

    assert deliveries(tenant_id) == []
    assert delivery_jobs(tenant_id) == []
    assert route.call_count == 0


def test_an_endpoint_made_through_the_api_is_signed_to_by_the_embedded_worker(
    engine: Engine, embedded_on: None, receiver: respx.MockRouter
):
    created = new_tenant()
    route = receiver.post(HOOK).mock(return_value=httpx.Response(204))

    with TestClient(build_app()) as client:
        headers = first_login(
            client, created.slug, created.admin_email, created.admin_password, PASSWORD
        )
        made = client.post(
            "/v1/webhooks/endpoints",
            json={"url": HOOK, "event_types": [PING]},
            headers={**headers, "Idempotency-Key": "w1"},
        )
        assert made.status_code == 201, made.text
        secret = made.json()["secret"]
        pong = client.post(
            "/v1/ping", json={}, headers={**headers, "Idempotency-Key": "w2"}
        )
        assert pong.status_code == 200, pong.text
        age_outbox(created.tenant_id)
        assert eventually(lambda: route.call_count == 1)

    # Shutdown joined the worker, so its last transaction has committed.
    (delivery,) = deliveries(created.tenant_id)
    assert delivery.status is DeliveryStatus.DELIVERED
    (call,) = route.calls
    assert call.request.headers["x-xolotl-event-type"] == PING
    assert is_signed_by(secret, call.request)


# ---- tests that drive the queue by hand never share it with a worker thread ---------


def test_a_stack_driven_by_hand_refuses_to_run_beside_an_embedded_worker(
    engine: Engine,
):
    release = threading.Event()
    stray = threading.Thread(
        target=release.wait, name=EMBEDDED_THREAD_NAME, daemon=True
    )
    stray.start()
    try:
        with pytest.raises(AssertionError, match="embedded worker"):
            _ = Stack(FakeClock())
    finally:
        release.set()
        stray.join(10)
    _ = Stack(FakeClock())  # with the thread gone, it builds


# ---- the routes use the platform's queue --------------------------------------------


def test_the_webhook_routes_queue_is_the_one_the_platform_was_composed_with(
    engine: Engine,
):
    app = build_app()
    services = state_of(app).services
    assert services is not None

    assert webhook_queue(request_for(app)) is services.queue


def test_the_webhook_routes_fail_loudly_when_the_app_has_no_platform(engine: Engine):
    bare = create_app(session_factory=get_sessionmaker())

    with pytest.raises(RuntimeError, match="platform services"):
        _ = webhook_queue(request_for(bare))
