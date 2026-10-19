"""Delivering a webhook: the request, the outcome, retries, dead letters (§9.2).

Real bus, queue and runner against the real database; the receiver is mocked with
``respx``. The URL policy runs as it does in production (the host is resolved and the
request goes to the address that was checked), so the mocked receiver sits at that
address, ``PINNED``, and the tests look at the ``Host`` header to see the name.
"""

import gzip
import io
import itertools
import json
import logging
import threading
from collections.abc import Generator, Iterator
from concurrent.futures import ThreadPoolExecutor, wait
from contextlib import contextmanager
from datetime import timedelta
from typing import NoReturn, Self, override
from uuid import UUID, uuid4

import httpx
import pytest
import respx
from pydantic import JsonValue
from sqlalchemy import Engine, select, text, update

from tests.helpers import (
    everything_stored,
    make_tenant_with_keys,
    system_ctx,
)
from tests.platform.jobs_support import T0, FakeClock
from tests.platform.webhooks_support import (
    PINNED,
    PUBLIC_IP,
    Hook,
    Resolves,
    Stack,
    StackFactory,
    an_event,
    delivery_for,
    delivery_jobs,
    make_endpoint,
    stack_factory,
)
from xolotl.platform.config import get_settings
from xolotl.platform.crypto.encrypted import encrypt
from xolotl.platform.crypto.provider import (
    LocalKeyProvider,
    get_key_provider,
    set_key_provider,
)
from xolotl.platform.db import get_sessionmaker, tenant_session
from xolotl.platform.enums import DeliveryStatus, EndpointStatus, JobStatus
from xolotl.platform.errors import DomainError, NotFoundError
from xolotl.platform.events import webhooks
from xolotl.platform.events.event import Event
from xolotl.platform.events.models import OutboxRow, WebhookDelivery, WebhookEndpoint
from xolotl.platform.events.registry import reset_subscribers
from xolotl.platform.events.url_policy import UrlPolicy
from xolotl.platform.events.webhooks import (
    DELIVER_KIND,
    MAX_BACKOFF,
    RETRY_WINDOW,
    WebhookService,
    backoff,
    enqueue_delivery,
    generate_webhook_secret,
    give_up,
    retry_delivery,
    sign,
    verify_signature,
)
from xolotl.platform.jobs.models import JobRow
from xolotl.platform.jobs.queue import Job, job_handler, register_job_handler
from xolotl.platform.logging import configure_logging, safe_exception_summary

MINUTES_2 = timedelta(minutes=2)
# Not the master key the suite wraps its tenants' keys with.
OTHER_MASTER_KEY = bytes(range(64, 96))


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def make_stack(engine: Engine, fake_clock: FakeClock) -> Generator[StackFactory]:
    reset_subscribers()
    yield stack_factory(fake_clock)
    reset_subscribers()


@pytest.fixture
def stack(make_stack: StackFactory) -> Stack:
    return make_stack()


@pytest.fixture
def receiver() -> Generator[respx.MockRouter]:
    # Anything the test has not mocked raises instead of reaching the network.
    with respx.mock(assert_all_called=False) as router:
        yield router


@pytest.fixture
def tenant_id(engine: Engine) -> UUID:
    return make_tenant_with_keys("alpha")


@pytest.fixture
def production_logging() -> Generator[io.StringIO]:
    """Log the way the application does, into a buffer.

    Yields:
        The buffer the application's log handler writes to. The process is left as
        it was found.

    """
    root = logging.getLogger()
    names = ("httpx", "httpcore", "sqlalchemy.engine")
    handlers, level = list(root.handlers), root.level
    levels = {name: logging.getLogger(name).level for name in names}
    configure_logging("DEBUG")
    (handler,) = root.handlers
    assert isinstance(handler, logging.StreamHandler)
    stream = io.StringIO()
    _ = handler.setStream(stream)
    yield stream
    root.handlers[:] = handlers
    root.setLevel(level)
    for name, own in levels.items():
        logging.getLogger(name).setLevel(own)


# ---- helpers ---------------------------------------------------------------------


def arrange(
    stack: Stack,
    receiver: respx.MockRouter,
    tenant_id: UUID,
    response: httpx.Response | None = None,
) -> tuple[Hook, Event, respx.Route]:
    """Give the tenant an endpoint, a mocked receiver and a fanned-out event.

    Returns:
        The endpoint, the event, and the route that stands for the receiver.

    """
    hook = make_endpoint(tenant_id)
    route = receiver.post(PINNED).mock(return_value=response or httpx.Response(200))
    event = an_event(tenant_id, type_="party.created.v1")
    assert stack.fan_out(tenant_id, event) == 1
    return hook, event, route


@contextmanager
def another_master_key() -> Generator[None]:
    """Run as a process started with a different ``XOLOTL_MASTER_KEY``.

    The keys of every tenant were wrapped by the suite's own key, so reading one now is
    a ``KekMismatchError``. The right provider is back when the block ends.

    Yields:
        Nothing; the wrong provider is installed until the block ends.

    """
    right = get_key_provider()
    set_key_provider(LocalKeyProvider(OTHER_MASTER_KEY, get_sessionmaker()))
    try:
        yield
    finally:
        set_key_provider(right)


def row_of(tenant_id: UUID, event: Event, hook: Hook) -> WebhookDelivery:
    return delivery_for(tenant_id, event, hook)


def job_states(tenant_id: UUID) -> list[JobStatus]:
    return [job.status for job in delivery_jobs(tenant_id)]


def verified(secret: str, request: httpx.Request, clock: FakeClock) -> bool:
    return verify_signature(
        secret,
        request.headers["x-xolotl-signature"],
        request.headers["x-xolotl-timestamp"],
        request.content,
        now=clock,
    )


# ---- the request -----------------------------------------------------------------


def test_a_delivery_posts_the_event_with_the_headers_and_a_signature_to_verify(
    stack: Stack,
    receiver: respx.MockRouter,
    tenant_id: UUID,
    fake_clock: FakeClock,
):
    hook, event, route = arrange(stack, receiver, tenant_id)

    assert stack.run_jobs() == 1

    (call,) = route.calls
    request = call.request
    stamp = int(T0.timestamp())
    assert request.method == "POST"
    assert request.headers["content-type"] == "application/json"
    assert request.headers["x-xolotl-event-id"] == str(event.id)
    assert request.headers["x-xolotl-event-type"] == "party.created.v1"
    assert request.headers["x-xolotl-timestamp"] == str(stamp)
    assert request.headers["x-xolotl-signature"] == sign(
        hook.secret, stamp, request.content
    )
    assert verified(hook.secret, request, fake_clock)
    assert not verified(generate_webhook_secret(), request, fake_clock)


def test_the_four_xolotl_headers_are_the_only_ones_a_receiver_is_told_about(
    stack: Stack, receiver: respx.MockRouter, tenant_id: UUID
):
    _, _, route = arrange(stack, receiver, tenant_id)
    _ = stack.run_jobs()
    names = {name for name in route.calls.last.request.headers if name.startswith("x-")}
    assert names == {
        "x-xolotl-event-id",
        "x-xolotl-event-type",
        "x-xolotl-timestamp",
        "x-xolotl-signature",
    }


def test_the_body_is_the_event_as_json(
    stack: Stack, receiver: respx.MockRouter, tenant_id: UUID
):
    _, event, route = arrange(stack, receiver, tenant_id)
    _ = stack.run_jobs()

    body = route.calls.last.request.content
    assert body == event.model_dump_json().encode()
    assert json.loads(body) == {
        "id": str(event.id),
        "type": "party.created.v1",
        "source": "xolotl.tests",
        "tenant_id": str(tenant_id),
        "subject": "test/k",
        "time": "2026-01-01T00:00:00Z",
        "key": "k",
        "correlation_id": None,
        "policy_snapshot_id": None,
        "data": {"n": 1},
    }


def test_the_signature_is_over_the_bytes_on_the_wire_not_over_a_reserialisation(
    stack: Stack,
    receiver: respx.MockRouter,
    tenant_id: UUID,
    fake_clock: FakeClock,
):
    hook, _, route = arrange(stack, receiver, tenant_id)
    _ = stack.run_jobs()
    request = route.calls.last.request

    reserialised = json.dumps(json.loads(request.content)).encode()
    assert reserialised != request.content  # same JSON, other bytes
    assert verified(hook.secret, request, fake_clock)
    assert not verify_signature(
        hook.secret,
        request.headers["x-xolotl-signature"],
        request.headers["x-xolotl-timestamp"],
        reserialised,
        now=fake_clock,
    )


def test_the_request_goes_to_the_address_that_was_checked_under_the_hosts_name(
    stack: Stack, receiver: respx.MockRouter, tenant_id: UUID
):
    _, _, route = arrange(stack, receiver, tenant_id)
    _ = stack.run_jobs()

    request = route.calls.last.request
    assert request.url.host == PUBLIC_IP
    assert request.headers["host"] == "hooks.example.com"
    assert request.extensions["sni_hostname"] == "hooks.example.com"
    assert stack.resolver.calls == ["hooks.example.com"]


def test_outside_production_the_url_is_called_as_written(
    make_stack: StackFactory, receiver: respx.MockRouter, tenant_id: UUID
):
    stack = make_stack(env="test")
    _ = make_endpoint(tenant_id, url="http://localhost:9000/hook?x=1")
    route = receiver.post("http://localhost:9000/hook").mock(
        return_value=httpx.Response(200)
    )
    assert stack.fan_out(tenant_id, an_event(tenant_id)) == 1

    assert stack.run_jobs() == 1

    request = route.calls.last.request
    assert str(request.url) == "http://localhost:9000/hook?x=1"
    assert request.headers["host"] == "localhost:9000"
    assert "sni_hostname" not in request.extensions
    assert stack.resolver.calls == []


def test_the_request_times_out_after_the_configured_seconds(
    make_stack: StackFactory, receiver: respx.MockRouter, tenant_id: UUID
):
    stack = make_stack(timeout=9)
    _, _, route = arrange(stack, receiver, tenant_id)
    _ = stack.run_jobs()
    assert route.calls.last.request.extensions["timeout"] == {
        "connect": 9.0,
        "read": 9.0,
        "write": 9.0,
        "pool": 9.0,
    }


def test_the_client_follows_no_redirects_and_trusts_no_proxy_settings(
    stack: Stack,
    receiver: respx.MockRouter,
    tenant_id: UUID,
    monkeypatch: pytest.MonkeyPatch,
):
    options: list[dict[str, object]] = []

    class Spy(httpx.Client):
        # Exactly the options the delivery passes, so a new one is noticed here.
        def __init__(
            self, *, timeout: httpx.Timeout, follow_redirects: bool, trust_env: bool
        ) -> None:
            options.append({
                "timeout": timeout,
                "follow_redirects": follow_redirects,
                "trust_env": trust_env,
            })
            super().__init__(
                timeout=timeout, follow_redirects=follow_redirects, trust_env=trust_env
            )

    monkeypatch.setattr(httpx, "Client", Spy)
    _ = arrange(stack, receiver, tenant_id)
    _ = stack.run_jobs()

    (used,) = options
    # A proxy from the environment would resolve the name itself and undo the pinning.
    assert used["follow_redirects"] is False
    assert used["trust_env"] is False


def test_a_redirect_is_a_failed_attempt_and_is_not_followed(
    stack: Stack, receiver: respx.MockRouter, tenant_id: UUID
):
    metadata = "http://169.254.169.254/latest/meta-data/"
    target = receiver.get(metadata).mock(return_value=httpx.Response(200))
    other = receiver.post(metadata).mock(return_value=httpx.Response(200))
    hook, event, route = arrange(
        stack,
        receiver,
        tenant_id,
        httpx.Response(302, headers={"location": metadata}),
    )

    assert stack.run_jobs() == 1

    assert route.call_count == 1
    assert target.call_count == other.call_count == 0
    row = row_of(tenant_id, event, hook)
    assert (row.status, row.response_code, row.last_error) == (
        DeliveryStatus.PENDING,
        302,
        "HTTP 302",
    )


# ---- the outcome -----------------------------------------------------------------


@pytest.mark.parametrize("status", [200, 201, 202, 204, 206, 299])
def test_any_2xx_marks_the_delivery_delivered(
    stack: Stack, receiver: respx.MockRouter, tenant_id: UUID, status: int
):
    hook, event, _ = arrange(stack, receiver, tenant_id, httpx.Response(status))

    assert stack.run_jobs() == 1

    row = row_of(tenant_id, event, hook)
    assert row.status is DeliveryStatus.DELIVERED
    assert (row.attempt, row.response_code) == (1, status)
    assert row.delivered_at == T0
    assert (row.next_attempt_at, row.last_error) == (None, None)
    assert job_states(tenant_id) == [JobStatus.DONE]


def test_a_delivered_event_is_not_sent_again(
    stack: Stack, receiver: respx.MockRouter, tenant_id: UUID
):
    _, _, route = arrange(stack, receiver, tenant_id)
    _ = stack.run_jobs()
    assert stack.run_jobs() == 0
    assert route.call_count == 1


@pytest.mark.parametrize(
    "status",
    [300, 301, 302, 307, 308, 400, 401, 403, 404, 408, 410, 422, 429, 500, 502, 503],
)
def test_anything_but_2xx_is_a_failed_attempt_retried_in_two_minutes(
    stack: Stack, receiver: respx.MockRouter, tenant_id: UUID, status: int
):
    hook, event, _ = arrange(stack, receiver, tenant_id, httpx.Response(status))

    assert stack.run_jobs() == 1

    row = row_of(tenant_id, event, hook)
    assert row.status is DeliveryStatus.PENDING
    assert (row.attempt, row.response_code) == (1, status)
    assert row.last_error == f"HTTP {status}"
    assert row.delivered_at is None
    assert row.next_attempt_at == T0 + backoff(1) == T0 + MINUTES_2
    first, retry = delivery_jobs(tenant_id)
    assert first.status is JobStatus.DONE
    assert retry.status is JobStatus.PENDING
    assert retry.run_after == T0 + MINUTES_2
    assert retry.payload == {"delivery_id": str(row.id)}


def test_the_retry_waits_for_its_time(
    stack: Stack, receiver: respx.MockRouter, tenant_id: UUID, fake_clock: FakeClock
):
    _, _, route = arrange(stack, receiver, tenant_id, httpx.Response(500))
    _ = stack.run_jobs()
    _ = fake_clock.advance(MINUTES_2 - timedelta(seconds=1))
    assert stack.run_jobs() == 0
    _ = fake_clock.advance(timedelta(seconds=1))
    assert stack.run_jobs() == 1
    assert route.call_count == 2


def test_a_retry_succeeds_when_the_receiver_recovers(
    stack: Stack, receiver: respx.MockRouter, tenant_id: UUID, fake_clock: FakeClock
):
    hook, event, route = arrange(stack, receiver, tenant_id, httpx.Response(503))
    _ = stack.run_jobs()
    _ = route.mock(return_value=httpx.Response(200))
    _ = fake_clock.advance(MINUTES_2)

    assert stack.run_jobs() == 1

    row = row_of(tenant_id, event, hook)
    assert row.status is DeliveryStatus.DELIVERED
    assert (row.attempt, row.response_code) == (2, 200)
    assert row.delivered_at == T0 + MINUTES_2
    assert row.last_error is None  # the earlier failure is history, not a flaw
    assert job_states(tenant_id) == [JobStatus.DONE, JobStatus.DONE]


def test_every_attempt_carries_a_new_timestamp_and_signature(
    stack: Stack, receiver: respx.MockRouter, tenant_id: UUID, fake_clock: FakeClock
):
    hook, _, route = arrange(stack, receiver, tenant_id, httpx.Response(500))
    _ = stack.run_jobs()
    _ = fake_clock.advance(MINUTES_2)
    _ = stack.run_jobs()

    first, second = (call.request for call in route.calls)
    assert first.content == second.content
    assert first.headers["x-xolotl-timestamp"] != second.headers["x-xolotl-timestamp"]
    assert first.headers["x-xolotl-signature"] != second.headers["x-xolotl-signature"]
    assert second.headers["x-xolotl-timestamp"] == str(
        int((T0 + MINUTES_2).timestamp())
    )
    assert verified(hook.secret, first, FakeClock(T0))
    assert verified(hook.secret, second, fake_clock)


# ---- retries until 24 hours have passed ------------------------------------------


def test_a_failing_endpoint_is_retried_on_the_schedule_until_24_hours_have_passed(
    stack: Stack, receiver: respx.MockRouter, tenant_id: UUID, fake_clock: FakeClock
):
    hook, event, route = arrange(stack, receiver, tenant_id, httpx.Response(500))
    created = fake_clock.now
    minutes: list[int] = []
    while True:
        assert stack.run_jobs() == 1
        minutes.append(int((fake_clock.now - created).total_seconds() // 60))
        row = row_of(tenant_id, event, hook)
        if row.status is DeliveryStatus.DEAD:
            break
        assert row.status is DeliveryStatus.PENDING
        assert row.next_attempt_at is not None
        fake_clock.now = row.next_attempt_at

    # 2, 4, 8, 16 and 32 minutes apart, then hourly; the last attempt is the first one
    # made 24 hours or more after the event reached the endpoint.
    assert minutes[:7] == [0, 2, 6, 14, 30, 62, 122]
    assert minutes[-3:] == [1322, 1382, 1442]
    assert len(minutes) == route.call_count == 29
    assert [b - a for a, b in itertools.pairwise(minutes[5:])] == [60] * 23
    assert minutes[-2] < 24 * 60 <= minutes[-1]
    assert (row.attempt, row.response_code, row.last_error) == (29, 500, "HTTP 500")
    assert row.next_attempt_at is None
    assert set(job_states(tenant_id)) == {JobStatus.DONE}


@pytest.mark.parametrize(
    ("age", "status"),
    [
        (timedelta(hours=24) - timedelta(seconds=1), DeliveryStatus.PENDING),
        (timedelta(hours=24), DeliveryStatus.DEAD),
        (timedelta(hours=24, seconds=1), DeliveryStatus.DEAD),
        (timedelta(days=3), DeliveryStatus.DEAD),
    ],
)
def test_a_failure_24_hours_after_the_event_is_the_last(
    stack: Stack,
    receiver: respx.MockRouter,
    tenant_id: UUID,
    fake_clock: FakeClock,
    age: timedelta,
    status: DeliveryStatus,
):
    hook, event, _ = arrange(stack, receiver, tenant_id, httpx.Response(500))
    _ = fake_clock.advance(age)

    assert stack.run_jobs() == 1

    row = row_of(tenant_id, event, hook)
    assert (row.status, row.attempt, row.response_code) == (status, 1, 500)
    assert row.last_error == "HTTP 500"
    if status is DeliveryStatus.DEAD:
        assert row.next_attempt_at is None
        assert job_states(tenant_id) == [JobStatus.DONE]
    else:
        assert row.next_attempt_at == fake_clock.now + MINUTES_2


def test_the_window_is_24_hours():
    assert timedelta(hours=24) == RETRY_WINDOW


def test_a_late_success_still_counts(
    stack: Stack, receiver: respx.MockRouter, tenant_id: UUID, fake_clock: FakeClock
):
    # The window limits retrying, not delivering: a worker that was down for a day
    # still sends what it owes.
    hook, event, _ = arrange(stack, receiver, tenant_id)
    _ = fake_clock.advance(timedelta(days=2))

    assert stack.run_jobs() == 1

    assert row_of(tenant_id, event, hook).status is DeliveryStatus.DELIVERED


# ---- exceptions ------------------------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectError("[Errno 111] Connection refused"),
        httpx.ConnectTimeout("timed out"),
        httpx.ReadTimeout("timed out"),
        httpx.WriteTimeout("timed out"),
        httpx.RemoteProtocolError("Server disconnected without sending a response."),
        httpx.ConnectError(
            "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed"
        ),
        httpx.TooManyRedirects("Exceeded maximum allowed redirects."),
        OSError("Network is unreachable"),
    ],
    ids=lambda error: type(error).__name__,
)
def test_a_transport_failure_is_a_failed_attempt_with_a_safe_summary(
    stack: Stack, receiver: respx.MockRouter, tenant_id: UUID, error: Exception
):
    hook, event, route = arrange(stack, receiver, tenant_id)
    _ = route.mock(side_effect=error)

    assert stack.run_jobs() == 1

    row = row_of(tenant_id, event, hook)
    assert row.status is DeliveryStatus.PENDING
    assert (row.attempt, row.response_code) == (1, None)
    assert row.last_error == safe_exception_summary(error)
    assert row.last_error == f"{type(error).__name__}: {error}"
    assert row.next_attempt_at == T0 + MINUTES_2
    assert job_states(tenant_id) == [JobStatus.DONE, JobStatus.PENDING]


def test_an_error_that_is_not_a_transport_error_is_not_swallowed(
    stack: Stack, receiver: respx.MockRouter, tenant_id: UUID
):
    # A bug in our own code must fail the job, not pass for a receiver that is down.
    hook, event, route = arrange(stack, receiver, tenant_id)
    _ = route.mock(side_effect=ZeroDivisionError("bug"))

    assert stack.run_jobs() == 1

    row = row_of(tenant_id, event, hook)
    assert (row.status, row.attempt) == (DeliveryStatus.PENDING, 0)
    (job,) = delivery_jobs(tenant_id)
    assert job.status is JobStatus.PENDING
    assert job.last_error == "ZeroDivisionError: bug"


# ---- what is kept of the answer --------------------------------------------------


@pytest.mark.parametrize(
    ("response", "stored"),
    [
        (
            httpx.Response(
                500, headers={"content-type": "text/plain"}, content=b"upstream is down"
            ),
            "HTTP 500: upstream is down",
        ),
        (
            httpx.Response(
                400,
                headers={"content-type": "application/json; charset=utf-8"},
                content=b'{"error":"bad signature"}',
            ),
            'HTTP 400: {"error":"bad signature"}',
        ),
        (
            httpx.Response(
                422,
                headers={"content-type": "application/problem+json"},
                content=b'{"title":"nope"}',
            ),
            'HTTP 422: {"title":"nope"}',
        ),
        (
            httpx.Response(
                500,
                headers={"content-type": "text/plain"},
                content=b"a\x00b\nc\td\r\n  e\x1b[31m",
            ),
            "HTTP 500: ab c d e[31m",
        ),
        (
            httpx.Response(
                500, headers={"content-type": "text/plain"}, content=b"caf\xc3\xa9 \xff"
            ),
            "HTTP 500: café �",
        ),
        (
            httpx.Response(
                500, headers={"content-type": "text/plain"}, content=b"y" * 500
            ),
            "HTTP 500: " + "y" * 200,
        ),
        (
            httpx.Response(
                500, headers={"content-type": "text/plain"}, content=b"  \n\t "
            ),
            "HTTP 500",
        ),
        (
            httpx.Response(
                500, headers={"content-type": "text/html"}, content=b"<h1>oops</h1>"
            ),
            "HTTP 500",
        ),
        (httpx.Response(500, content=b"no content type"), "HTTP 500"),
        (
            httpx.Response(
                502,
                headers={"content-type": "application/octet-stream"},
                content=b"\x00\x01",
            ),
            "HTTP 502",
        ),
        (
            httpx.Response(
                500,
                headers={"content-type": "text/plain", "content-encoding": "gzip"},
                content=gzip.compress(b"compressed reason"),
            ),
            "HTTP 500",
        ),
        (httpx.Response(503), "HTTP 503"),
    ],
    ids=[
        "text",
        "json",
        "problem-json",
        "controls",
        "bytes",
        "truncated",
        "blank",
        "html",
        "untyped",
        "binary",
        "compressed",
        "empty",
    ],
)
def test_a_failed_attempt_keeps_the_status_and_a_short_plain_reason_only(
    stack: Stack,
    receiver: respx.MockRouter,
    tenant_id: UUID,
    response: httpx.Response,
    stored: str,
):
    hook, event, _ = arrange(stack, receiver, tenant_id, response)
    _ = stack.run_jobs()
    assert row_of(tenant_id, event, hook).last_error == stored


class Endless(httpx.SyncByteStream):
    """A response body that never ends, and counts how much of it was asked for."""

    def __init__(self) -> None:
        self.chunks = 0

    @override
    def __iter__(self) -> Iterator[bytes]:
        while self.chunks < 10_000:
            self.chunks += 1
            yield b"x" * 1024
        msg = "the body was read to its end"
        raise AssertionError(msg)


class Untouchable(httpx.SyncByteStream):
    """A response body that must never be read."""

    @override
    def __iter__(self) -> NoReturn:
        msg = "the body was read"
        raise AssertionError(msg)


def test_an_endless_body_is_read_only_as_far_as_a_reason_needs(
    stack: Stack, receiver: respx.MockRouter, tenant_id: UUID
):
    body = Endless()
    response = httpx.Response(500, headers={"content-type": "text/plain"}, stream=body)
    hook, event, _ = arrange(stack, receiver, tenant_id, response)

    _ = stack.run_jobs()

    assert body.chunks <= 2
    assert row_of(tenant_id, event, hook).last_error == "HTTP 500: " + "x" * 200


def test_the_body_of_a_success_is_never_read(
    stack: Stack, receiver: respx.MockRouter, tenant_id: UUID
):
    response = httpx.Response(
        200, headers={"content-type": "text/plain"}, stream=Untouchable()
    )
    hook, event, _ = arrange(stack, receiver, tenant_id, response)
    _ = stack.run_jobs()
    row = row_of(tenant_id, event, hook)
    assert (row.status, row.last_error) == (DeliveryStatus.DELIVERED, None)


def test_a_body_that_fails_to_arrive_does_not_change_the_outcome(
    stack: Stack, receiver: respx.MockRouter, tenant_id: UUID
):
    class Broken(httpx.SyncByteStream):
        @override
        def __iter__(self) -> NoReturn:
            msg = "connection reset"
            raise httpx.ReadError(msg)

    response = httpx.Response(
        500, headers={"content-type": "text/plain"}, stream=Broken()
    )
    hook, event, _ = arrange(stack, receiver, tenant_id, response)
    _ = stack.run_jobs()
    row = row_of(tenant_id, event, hook)
    assert (row.status, row.response_code, row.last_error) == (
        DeliveryStatus.PENDING,
        500,
        "HTTP 500",
    )


class Seconds:
    """A monotonic clock that only moves when the test says so."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_a_body_that_dribbles_in_is_not_waited_for(
    stack: Stack, receiver: respx.MockRouter, tenant_id: UUID, fake_clock: FakeClock
):
    # One byte a second never trips the per-read timeout, so only the total counts.
    seconds = Seconds()

    class Dribble(httpx.SyncByteStream):
        sent = 0

        @override
        def __iter__(self) -> Iterator[bytes]:
            while self.sent < 100:
                seconds.now += 1.0
                self.sent += 1
                yield b"x"
            msg = "the body was read to its end"
            raise AssertionError(msg)

    body = Dribble()
    service = WebhookService(
        stack.queue,
        policy=UrlPolicy(strict=True, resolver=stack.resolver),
        timeout_seconds=1,
        clock_fn=fake_clock,
        monotonic=seconds,
    )
    register_job_handler(DELIVER_KIND, service.deliver)
    response = httpx.Response(500, headers={"content-type": "text/plain"}, stream=body)
    hook, event, _ = arrange(stack, receiver, tenant_id, response)

    _ = stack.run_jobs()

    assert body.sent == 2  # the deadline passed while the second byte was arriving
    assert row_of(tenant_id, event, hook).last_error == "HTTP 500: xx"


# ---- where a delivery may go -----------------------------------------------------


@pytest.mark.parametrize(
    "address", ["10.0.0.5", "127.0.0.1", "169.254.169.254", "::1", "fd00:ec2::254"]
)
def test_a_host_that_now_resolves_to_a_non_public_address_is_not_called(
    make_stack: StackFactory,
    receiver: respx.MockRouter,
    tenant_id: UUID,
    address: str,
):
    stack = make_stack(resolver=Resolves({"hooks.example.com": [address]}))
    hook, event, route = arrange(stack, receiver, tenant_id)

    assert stack.run_jobs() == 1

    assert route.call_count == 0
    assert receiver.calls.call_count == 0
    row = row_of(tenant_id, event, hook)
    assert row.status is DeliveryStatus.PENDING
    assert (row.attempt, row.response_code) == (1, None)
    assert row.last_error == "webhook url points at a non-public address"
    assert row.next_attempt_at == T0 + MINUTES_2


def test_the_host_is_looked_up_again_before_every_attempt(
    stack: Stack, receiver: respx.MockRouter, tenant_id: UUID, fake_clock: FakeClock
):
    hook, event, route = arrange(stack, receiver, tenant_id)
    answers = stack.resolver.answers
    answers["hooks.example.com"] = ["10.0.0.5"]  # DNS now points inside
    _ = stack.run_jobs()
    assert route.call_count == 0

    answers["hooks.example.com"] = [PUBLIC_IP]  # and back
    _ = fake_clock.advance(MINUTES_2)
    _ = stack.run_jobs()

    assert route.call_count == 1
    assert stack.resolver.calls == ["hooks.example.com"] * 2
    assert row_of(tenant_id, event, hook).status is DeliveryStatus.DELIVERED


def test_a_host_that_does_not_resolve_is_a_failed_attempt(
    make_stack: StackFactory, receiver: respx.MockRouter, tenant_id: UUID
):
    stack = make_stack(resolver=Resolves())
    hook, event, route = arrange(stack, receiver, tenant_id)
    _ = stack.run_jobs()
    assert route.call_count == 0
    row = row_of(tenant_id, event, hook)
    assert row.last_error == "webhook host does not resolve to an address"
    assert row.status is DeliveryStatus.PENDING


def test_a_stored_url_that_breaks_the_rules_is_not_called(
    stack: Stack, receiver: respx.MockRouter, tenant_id: UUID
):
    # Rows can predate a rule, or be written by hand.
    hook = make_endpoint(tenant_id, url="http://hooks.example.com/xolotl")
    event = an_event(tenant_id)
    _ = stack.fan_out(tenant_id, event)
    _ = stack.run_jobs()
    row = row_of(tenant_id, event, hook)
    assert row.last_error == "webhook url must use https"
    assert receiver.calls.call_count == 0


# ---- jobs that have nothing to do ------------------------------------------------


def test_a_second_job_for_the_same_delivery_does_not_send_it_twice(
    stack: Stack, receiver: respx.MockRouter, tenant_id: UUID
):
    hook, event, route = arrange(stack, receiver, tenant_id)
    row = row_of(tenant_id, event, hook)
    with tenant_session(system_ctx(tenant_id)) as s:
        enqueue_delivery(stack.queue, s, row.id, run_after=T0)

    assert stack.run_jobs() == 2

    assert route.call_count == 1
    assert job_states(tenant_id) == [JobStatus.DONE, JobStatus.DONE]


def test_a_job_that_runs_before_its_delivery_is_due_does_nothing(
    stack: Stack, receiver: respx.MockRouter, tenant_id: UUID
):
    hook, event, route = arrange(stack, receiver, tenant_id)
    later = T0 + timedelta(minutes=10)
    with tenant_session(system_ctx(tenant_id)) as s:
        _ = s.execute(update(WebhookDelivery).values(next_attempt_at=later))

    assert stack.run_jobs() == 1

    assert route.call_count == 0
    row = row_of(tenant_id, event, hook)
    assert (row.status, row.attempt, row.next_attempt_at) == (
        DeliveryStatus.PENDING,
        0,
        later,
    )
    # A stale job neither sends nor reschedules: the job for next_attempt_at exists.
    assert job_states(tenant_id) == [JobStatus.DONE]


def test_a_job_for_a_dead_delivery_does_nothing(
    stack: Stack, receiver: respx.MockRouter, tenant_id: UUID
):
    hook, event, route = arrange(stack, receiver, tenant_id)
    with tenant_session(system_ctx(tenant_id)) as s:
        _ = s.execute(update(WebhookDelivery).values(status=DeliveryStatus.DEAD))
    assert stack.run_jobs() == 1
    assert route.call_count == 0
    assert row_of(tenant_id, event, hook).status is DeliveryStatus.DEAD


def test_a_job_for_a_delivery_that_does_not_exist_does_nothing(
    stack: Stack, receiver: respx.MockRouter, tenant_id: UUID
):
    with tenant_session(system_ctx(tenant_id)) as s:
        enqueue_delivery(stack.queue, s, uuid4(), run_after=T0)
    assert stack.run_jobs() == 1
    assert receiver.calls.call_count == 0
    assert job_states(tenant_id) == [JobStatus.DONE]


@pytest.mark.parametrize("payload", [{}, {"delivery_id": 7}, {"delivery_id": "nope"}])
def test_a_job_without_a_usable_delivery_id_fails(
    stack: Stack, tenant_id: UUID, payload: dict[str, JsonValue]
):
    with tenant_session(system_ctx(tenant_id)) as s:
        _ = stack.queue.enqueue(s, kind=DELIVER_KIND, payload=payload)
    assert stack.run_jobs() == 1
    (job,) = delivery_jobs(tenant_id)
    assert job.status is JobStatus.PENDING
    assert job.last_error is not None
    assert job.last_error.startswith("ValueError: webhook.deliver job")


# ---- deliveries that cannot be sent ----------------------------------------------


def test_a_disabled_endpoint_receives_nothing_more(
    stack: Stack, receiver: respx.MockRouter, tenant_id: UUID
):
    hook, event, route = arrange(stack, receiver, tenant_id)
    with tenant_session(system_ctx(tenant_id)) as s:
        _ = s.execute(update(WebhookEndpoint).values(status=EndpointStatus.DISABLED))

    assert stack.run_jobs() == 1

    assert route.call_count == 0
    row = row_of(tenant_id, event, hook)
    assert (row.status, row.attempt) == (DeliveryStatus.DEAD, 0)
    assert row.last_error == "endpoint disabled"
    assert row.next_attempt_at is None
    assert job_states(tenant_id) == [JobStatus.DONE]


def test_an_event_that_is_gone_ends_the_delivery(
    stack: Stack, receiver: respx.MockRouter, tenant_id: UUID
):
    hook, event, route = arrange(stack, receiver, tenant_id)
    with tenant_session(system_ctx(tenant_id)) as s:
        s.delete(s.get_one(OutboxRow, (tenant_id, event.id)))

    assert stack.run_jobs() == 1

    assert route.call_count == 0
    row = row_of(tenant_id, event, hook)
    assert (row.status, row.last_error) == (
        DeliveryStatus.DEAD,
        "event no longer available",
    )


def test_an_event_that_no_longer_validates_ends_the_delivery(
    stack: Stack, receiver: respx.MockRouter, tenant_id: UUID
):
    hook, event, route = arrange(stack, receiver, tenant_id)
    with tenant_session(system_ctx(tenant_id)) as s:
        _ = s.execute(update(OutboxRow).values(payload={}))

    assert stack.run_jobs() == 1

    assert route.call_count == 0
    row = row_of(tenant_id, event, hook)
    assert (row.status, row.last_error) == (
        DeliveryStatus.DEAD,
        "event no longer valid",
    )


def test_destroyed_tenant_keys_end_the_delivery_instead_of_crashing_the_job(
    stack: Stack, receiver: respx.MockRouter, tenant_id: UUID
):
    hook, event, route = arrange(stack, receiver, tenant_id)
    get_key_provider().destroy(tenant_id)

    assert stack.run_jobs() == 1

    assert route.call_count == 0
    row = row_of(tenant_id, event, hook)
    assert (row.status, row.attempt) == (DeliveryStatus.DEAD, 0)
    assert row.last_error == "signing secret unavailable (KeyDestroyedError)"
    assert job_states(tenant_id) == [JobStatus.DONE]


@pytest.mark.parametrize(
    ("stored", "error"),
    [
        (b"\x00\x01" + b"x" * 40, "DecryptionError"),  # fails to authenticate
        (b"\x00\x63" + b"x" * 40, "KeyNotFoundError"),  # a key version never made
        (b"\x00", "DecryptionError"),  # too short to name a version at all
    ],
)
def test_a_secret_that_cannot_be_opened_ends_the_delivery(
    stack: Stack,
    receiver: respx.MockRouter,
    tenant_id: UUID,
    admin_engine: Engine,
    stored: bytes,
    error: str,
):
    hook, event, route = arrange(stack, receiver, tenant_id)
    with admin_engine.begin() as conn:
        _ = conn.execute(
            text("UPDATE webhook_endpoint SET secret = :garbage"), {"garbage": stored}
        )

    assert stack.run_jobs() == 1

    assert route.call_count == 0
    row = row_of(tenant_id, event, hook)
    assert row.status is DeliveryStatus.DEAD
    assert row.last_error == f"signing secret unavailable ({error})"
    assert job_states(tenant_id) == [JobStatus.DONE]


# A master key that does not fit is the process's fault, not the delivery's: the
# keys are fine and another process, or this one restarted with the right key, opens
# them. Only a failure of the secret itself ends a delivery, and that is the case above
# (a secret that does not authenticate is a DecryptionError under the right key too).


def test_a_master_key_mismatch_retries_the_job_and_leaves_the_delivery_pending(
    stack: Stack, receiver: respx.MockRouter, tenant_id: UUID, fake_clock: FakeClock
):
    hook, event, route = arrange(stack, receiver, tenant_id)

    with another_master_key():
        assert stack.run_jobs() == 1

    # Nothing was sent and nothing is held against the delivery: it is as it was.
    assert route.call_count == 0
    row = row_of(tenant_id, event, hook)
    assert (row.status, row.attempt, row.last_error) == (
        DeliveryStatus.PENDING,
        0,
        None,
    )
    # The queue's retry, an infrastructure failure's: the job waits its turn.
    (job,) = delivery_jobs(tenant_id)
    assert (job.status, job.attempts) == (JobStatus.PENDING, 1)
    assert job.last_error is not None
    assert job.last_error.startswith("KekMismatchError")

    # A process that holds the right key takes the job up and sends the delivery.
    _ = fake_clock.advance(MINUTES_2)
    assert stack.run_jobs() == 1
    assert route.call_count == 1
    assert row_of(tenant_id, event, hook).status is DeliveryStatus.DELIVERED


def test_a_master_key_mismatch_on_the_jobs_last_try_parks_the_delivery_not_buries_it(
    stack: Stack, receiver: respx.MockRouter, tenant_id: UUID, fake_clock: FakeClock
):
    hook, event, route = arrange(stack, receiver, tenant_id)
    with tenant_session(system_ctx(tenant_id)) as s:
        _ = s.execute(
            update(JobRow).values(attempts=JobRow.max_attempts - 1, run_after=T0)
        )

    with another_master_key():
        assert stack.run_jobs() == 1

    # Another failure ends a delivery on the job's last try; a mismatch only waits.
    row = row_of(tenant_id, event, hook)
    assert (row.status, row.attempt, row.last_error) == (
        DeliveryStatus.PENDING,
        0,
        None,
    )
    old, parked = delivery_jobs(tenant_id)
    assert (old.status, parked.status) == (JobStatus.DONE, JobStatus.PENDING)
    assert parked.run_after == fake_clock.now + MAX_BACKOFF
    assert parked.attempts == 0

    # Not due yet: nothing runs. When it is, the right key sends the delivery.
    assert stack.run_jobs() == 0
    _ = fake_clock.advance(MAX_BACKOFF)
    assert stack.run_jobs() == 1
    assert route.call_count == 1
    assert row_of(tenant_id, event, hook).status is DeliveryStatus.DELIVERED


def test_the_secret_is_read_at_delivery_so_a_rotation_takes_effect(
    stack: Stack, receiver: respx.MockRouter, tenant_id: UUID, fake_clock: FakeClock
):
    hook, _, route = arrange(stack, receiver, tenant_id, httpx.Response(500))
    _ = stack.run_jobs()
    rotated = generate_webhook_secret()
    with tenant_session(system_ctx(tenant_id)) as s:
        endpoint = s.get_one(WebhookEndpoint, (tenant_id, hook.id))
        endpoint.secret = encrypt(rotated, tenant_id=tenant_id, session=s)
    _ = fake_clock.advance(MINUTES_2)
    _ = stack.run_jobs()

    first, second = (call.request for call in route.calls)
    assert verified(hook.secret, first, FakeClock(T0))
    assert verified(rotated, second, fake_clock)
    assert not verified(hook.secret, second, fake_clock)


def test_the_secret_is_stored_encrypted_under_the_tenants_key(
    tenant_id: UUID, admin_engine: Engine
):
    hook = make_endpoint(tenant_id)
    with admin_engine.connect() as conn:
        stored = conn.execute(text("SELECT secret FROM webhook_endpoint")).scalar_one()
    assert hook.secret.encode() not in bytes(stored)
    assert hook.secret[6:].encode() not in bytes(stored)
    with tenant_session(system_ctx(tenant_id)) as s:
        endpoint = s.get_one(WebhookEndpoint, (tenant_id, hook.id))
        assert endpoint.secret.reveal(session=s) == hook.secret


# ---- infrastructure failures follow the queue ------------------------------------


class Exploding:
    """An HTTP client that fails the way a bug would."""

    def __init__(self, **_options: object) -> None:
        pass

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    @staticmethod
    def stream(*_args: object, **_options: object) -> NoReturn:
        msg = "boom"
        raise RuntimeError(msg)


def test_an_infrastructure_failure_retries_the_job_and_the_last_try_ends_the_delivery(
    stack: Stack,
    receiver: respx.MockRouter,
    tenant_id: UUID,
    fake_clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
):
    hook, event, _ = arrange(stack, receiver, tenant_id)
    monkeypatch.setattr(httpx, "Client", Exploding)

    assert stack.run_jobs() == 1

    # The queue's retry: nothing about the delivery changed, the job waits its turn.
    row = row_of(tenant_id, event, hook)
    assert (row.status, row.attempt, row.last_error) == (
        DeliveryStatus.PENDING,
        0,
        None,
    )
    (job,) = delivery_jobs(tenant_id)
    assert (job.status, job.attempts, job.last_error) == (
        JobStatus.PENDING,
        1,
        "RuntimeError: boom",
    )

    # On its last attempt the job gives the delivery up, in a job that completes.
    with tenant_session(system_ctx(tenant_id)) as s:
        _ = s.execute(
            update(JobRow).values(attempts=JobRow.max_attempts - 1, run_after=T0)
        )
    _ = fake_clock.advance(timedelta(hours=1))
    assert stack.run_jobs() == 1

    row = row_of(tenant_id, event, hook)
    assert (row.status, row.attempt) == (DeliveryStatus.DEAD, 0)
    assert row.last_error == "RuntimeError: boom"
    assert row.next_attempt_at is None
    (job,) = delivery_jobs(tenant_id)
    assert (job.status, job.attempts) == (JobStatus.DONE, job.max_attempts)


def test_a_failed_database_transaction_does_not_stop_the_last_try_from_giving_up(
    stack: Stack,
    receiver: respx.MockRouter,
    tenant_id: UUID,
    fake_clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
):
    hook, event, _ = arrange(stack, receiver, tenant_id)

    def broken(session, _delivery):
        _ = session.execute(text("SELECT 1 / 0"))
        raise AssertionError

    monkeypatch.setattr(webhooks, "_prepare", broken)
    assert stack.run_jobs() == 1
    (job,) = delivery_jobs(tenant_id)
    # The database's own words (a literal can be personal data) never reach the row.
    assert job.last_error == "DataError [sqlstate=22012]"
    assert row_of(tenant_id, event, hook).status is DeliveryStatus.PENDING

    with tenant_session(system_ctx(tenant_id)) as s:
        _ = s.execute(
            update(JobRow).values(attempts=JobRow.max_attempts - 1, run_after=T0)
        )
    _ = fake_clock.advance(timedelta(hours=1))
    assert stack.run_jobs() == 1

    row = row_of(tenant_id, event, hook)
    assert (row.status, row.last_error) == (
        DeliveryStatus.DEAD,
        "DataError [sqlstate=22012]",
    )
    (job,) = delivery_jobs(tenant_id)
    assert job.status is JobStatus.DONE


def test_an_attempt_in_flight_holds_the_delivery_against_other_jobs_and_retries(
    stack: Stack, receiver: respx.MockRouter, tenant_id: UUID
):
    hook, event, route = arrange(stack, receiver, tenant_id)
    row = row_of(tenant_id, event, hook)
    entered, release = threading.Event(), threading.Event()

    def slow(_request: httpx.Request) -> httpx.Response:
        entered.set()
        assert release.wait(10)
        return httpx.Response(200)

    _ = route.mock(side_effect=slow)
    handler = job_handler(DELIVER_KIND)

    def run_job() -> None:
        job = Job(
            id=uuid4(),
            tenant_id=tenant_id,
            kind=DELIVER_KIND,
            key=None,
            payload={"delivery_id": str(row.id)},
            attempts=1,
            max_attempts=8,
            lock_token=uuid4(),
        )
        with tenant_session(system_ctx(tenant_id)) as s:
            handler(s, job)

    def run_retry() -> None:
        with tenant_session(system_ctx(tenant_id)) as s:
            _ = retry_delivery(s, stack.queue, row.id, clock_fn=stack.clock)

    with ThreadPoolExecutor(max_workers=3) as pool:
        first = pool.submit(run_job)
        assert entered.wait(10)
        second, retried = pool.submit(run_job), pool.submit(run_retry)
        # Both wait on the row the first one holds.
        assert not wait([second, retried], timeout=1.0).done
        release.set()
        first.result(10)
        second.result(10)
        # The retry then sees what the attempt left: delivered, nothing to retry.
        with pytest.raises(DomainError, match="delivered"):
            retried.result(10)

    assert route.call_count == 1
    assert row_of(tenant_id, event, hook).status is DeliveryStatus.DELIVERED


# ---- sending a dead delivery again -----------------------------------------------


def retry(stack: Stack, tenant_id: UUID, delivery_id: UUID) -> WebhookDelivery:
    with tenant_session(system_ctx(tenant_id)) as s:
        return retry_delivery(s, stack.queue, delivery_id, clock_fn=stack.clock)


def kill(stack: Stack, receiver: respx.MockRouter, tenant_id: UUID, clock: FakeClock):
    """Fail a delivery until it is dead.

    Returns:
        The endpoint, the event and the receiver's route.

    """
    hook, event, route = arrange(stack, receiver, tenant_id, httpx.Response(500))
    _ = clock.advance(timedelta(hours=25))
    _ = stack.run_jobs()
    assert row_of(tenant_id, event, hook).status is DeliveryStatus.DEAD
    return hook, event, route


def test_retrying_a_dead_delivery_sends_it_again_with_a_new_timestamp_and_signature(
    stack: Stack, receiver: respx.MockRouter, tenant_id: UUID, fake_clock: FakeClock
):
    hook, event, route = kill(stack, receiver, tenant_id, fake_clock)
    dead = row_of(tenant_id, event, hook)
    _ = fake_clock.advance(timedelta(minutes=7))
    _ = route.mock(return_value=httpx.Response(200))

    reset = retry(stack, tenant_id, dead.id)

    assert reset.status is DeliveryStatus.PENDING
    assert reset.next_attempt_at == fake_clock.now
    *_, queued = delivery_jobs(tenant_id)
    assert (queued.status, queued.run_after) == (JobStatus.PENDING, fake_clock.now)
    assert queued.payload == {"delivery_id": str(dead.id)}

    assert stack.run_jobs() == 1

    first, second = (call.request for call in route.calls)
    assert second.content == first.content
    assert second.headers["x-xolotl-timestamp"] != first.headers["x-xolotl-timestamp"]
    assert second.headers["x-xolotl-signature"] != first.headers["x-xolotl-signature"]
    assert verified(hook.secret, second, fake_clock)
    row = row_of(tenant_id, event, hook)
    assert row.status is DeliveryStatus.DELIVERED
    assert row.attempt == dead.attempt + 1
    assert row.delivered_at == fake_clock.now


def test_a_retried_delivery_that_fails_again_is_dead_again_after_one_attempt(
    stack: Stack, receiver: respx.MockRouter, tenant_id: UUID, fake_clock: FakeClock
):
    hook, event, route = kill(stack, receiver, tenant_id, fake_clock)
    dead = row_of(tenant_id, event, hook)
    _ = retry(stack, tenant_id, dead.id)

    assert stack.run_jobs() == 1

    row = row_of(tenant_id, event, hook)
    assert (row.status, row.attempt) == (DeliveryStatus.DEAD, dead.attempt + 1)
    assert route.call_count == 2


def test_retrying_a_pending_delivery_brings_its_next_attempt_forward(
    stack: Stack, receiver: respx.MockRouter, tenant_id: UUID, fake_clock: FakeClock
):
    hook, event, route = arrange(stack, receiver, tenant_id, httpx.Response(500))
    _ = stack.run_jobs()
    waiting = row_of(tenant_id, event, hook)
    assert waiting.next_attempt_at == T0 + MINUTES_2
    _ = route.mock(return_value=httpx.Response(200))

    _ = retry(stack, tenant_id, waiting.id)
    assert stack.run_jobs() == 1  # the new job, due now; the old one is not due yet

    assert row_of(tenant_id, event, hook).status is DeliveryStatus.DELIVERED
    # The job that was waiting for the backoff finds nothing left to do.
    _ = fake_clock.advance(MINUTES_2)
    assert stack.run_jobs() == 1
    assert route.call_count == 2


def test_a_delivered_delivery_cannot_be_retried(
    stack: Stack, receiver: respx.MockRouter, tenant_id: UUID
):
    hook, event, _ = arrange(stack, receiver, tenant_id)
    _ = stack.run_jobs()
    with pytest.raises(DomainError, match="delivered"):
        _ = retry(stack, tenant_id, row_of(tenant_id, event, hook).id)


def test_a_delivery_of_a_disabled_endpoint_cannot_be_retried(
    stack: Stack, receiver: respx.MockRouter, tenant_id: UUID, fake_clock: FakeClock
):
    hook, event, _ = kill(stack, receiver, tenant_id, fake_clock)
    with tenant_session(system_ctx(tenant_id)) as s:
        _ = s.execute(update(WebhookEndpoint).values(status=EndpointStatus.DISABLED))
    with pytest.raises(DomainError, match="disabled"):
        _ = retry(stack, tenant_id, row_of(tenant_id, event, hook).id)
    assert row_of(tenant_id, event, hook).status is DeliveryStatus.DEAD


def test_only_the_tenants_own_deliveries_can_be_retried(
    stack: Stack, receiver: respx.MockRouter, tenant_id: UUID, fake_clock: FakeClock
):
    hook, event, _ = kill(stack, receiver, tenant_id, fake_clock)
    beta = make_tenant_with_keys("beta")
    dead = row_of(tenant_id, event, hook)
    with pytest.raises(NotFoundError):
        _ = retry(stack, beta, dead.id)
    with pytest.raises(NotFoundError):
        _ = retry(stack, tenant_id, uuid4())
    assert row_of(tenant_id, event, hook).status is DeliveryStatus.DEAD


def test_a_key_destruction_made_by_another_process_is_seen_at_once(
    stack: Stack, receiver: respx.MockRouter, tenant_id: UUID
):
    # This process has the key cached; the destruction happens through a provider with
    # a cache of its own, as another process's would. Only a read made inside the
    # delivery's own transaction sees it before the cache expires.
    hook, event, route = arrange(stack, receiver, tenant_id)
    with tenant_session(system_ctx(tenant_id)) as s:
        _ = s.get_one(WebhookEndpoint, (tenant_id, hook.id)).secret.reveal()
    elsewhere = LocalKeyProvider(get_settings().master_key_bytes, get_sessionmaker())
    elsewhere.destroy(tenant_id)

    assert stack.run_jobs() == 1

    assert route.call_count == 0
    row = row_of(tenant_id, event, hook)
    assert row.last_error == "signing secret unavailable (KeyDestroyedError)"


def test_giving_up_ends_a_waiting_delivery_with_a_safe_summary(
    stack: Stack, receiver: respx.MockRouter, tenant_id: UUID
):
    hook, event, _ = arrange(stack, receiver, tenant_id)
    row = row_of(tenant_id, event, hook)
    with tenant_session(system_ctx(tenant_id)) as s:
        give_up(s, tenant_id, row.id, RuntimeError("boom"))
    ended = row_of(tenant_id, event, hook)
    assert (ended.status, ended.last_error) == (
        DeliveryStatus.DEAD,
        "RuntimeError: boom",
    )
    assert ended.next_attempt_at is None


@pytest.mark.parametrize("status", [DeliveryStatus.DELIVERED, DeliveryStatus.DEAD])
def test_giving_up_leaves_a_finished_delivery_as_it_is(
    stack: Stack,
    receiver: respx.MockRouter,
    tenant_id: UUID,
    status: DeliveryStatus,
):
    # Another job may have finished it in the moment between the failure and this.
    hook, event, _ = arrange(stack, receiver, tenant_id)
    row = row_of(tenant_id, event, hook)
    with tenant_session(system_ctx(tenant_id)) as s:
        _ = s.execute(update(WebhookDelivery).values(status=status, last_error="kept"))
        give_up(s, tenant_id, row.id, RuntimeError("boom"))
    after = row_of(tenant_id, event, hook)
    assert (after.status, after.last_error) == (status, "kept")


def test_giving_up_a_delivery_that_is_not_there_does_nothing(tenant_id: UUID):
    with tenant_session(system_ctx(tenant_id)) as s:
        give_up(s, tenant_id, uuid4(), RuntimeError("boom"))


# ---- what must never be shown ----------------------------------------------------


def test_the_secret_is_in_no_log_line_no_stored_text_and_no_error(
    stack: Stack,
    receiver: respx.MockRouter,
    tenant_id: UUID,
    fake_clock: FakeClock,
    admin_engine: Engine,
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.DEBUG)
    hook, event, route = arrange(stack, receiver, tenant_id, httpx.Response(500))
    _ = stack.run_jobs()  # a failed attempt
    _ = route.mock(side_effect=httpx.ConnectError("refused"))
    _ = fake_clock.advance(MINUTES_2)
    _ = stack.run_jobs()  # a transport failure
    _ = route.mock(return_value=httpx.Response(200))
    _ = fake_clock.advance(timedelta(minutes=4))
    _ = stack.run_jobs()  # and success

    assert row_of(tenant_id, event, hook).status is DeliveryStatus.DELIVERED
    secret = hook.secret
    logged = caplog.text + "\n".join(repr(vars(record)) for record in caplog.records)
    assert secret not in logged
    assert secret[6:] not in logged
    assert secret not in everything_stored(admin_engine)
    assert secret[6:] not in everything_stored(admin_engine)
    for request in (call.request for call in route.calls):
        assert secret.encode() not in request.content
        assert secret not in repr(dict(request.headers))


def test_the_token_in_a_webhook_url_is_not_logged_by_a_delivery(
    stack: Stack,
    receiver: respx.MockRouter,
    tenant_id: UUID,
    production_logging: io.StringIO,
):
    # The HTTP client logs the URL of every request; the application keeps it quiet.
    _ = make_endpoint(
        tenant_id, url="https://hooks.example.com/services/tok_s3cr3t?key=k_s3cr3t"
    )
    route = receiver.post(f"https://{PUBLIC_IP}/services/tok_s3cr3t").mock(
        return_value=httpx.Response(500)
    )
    assert stack.fan_out(tenant_id, an_event(tenant_id)) == 1

    assert stack.run_jobs() == 1

    assert route.call_count == 1
    logged = production_logging.getvalue()
    assert "webhook delivery failed, will retry" in logged  # the delivery did log
    assert "tok_s3cr3t" not in logged
    assert "k_s3cr3t" not in logged


def test_the_stored_endpoint_row_masks_its_secret_everywhere_it_is_printed(
    tenant_id: UUID,
):
    hook = make_endpoint(tenant_id)
    with tenant_session(system_ctx(tenant_id)) as s:
        endpoint = s.scalars(select(WebhookEndpoint)).one()
        printed = f"{endpoint.secret} {endpoint.secret!r} {vars(endpoint)!r}"
    assert hook.secret not in printed
    assert "Encrypted(***)" in printed
