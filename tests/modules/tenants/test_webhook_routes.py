"""Webhook endpoints and deliveries as a tenant's administrators manage them (§9.2).

Through the real application, with the real bus, queue and runner behind it and the
receiver mocked by ``respx``. The URL rules of production are switched on with the
``strict`` fixture; without it the application runs as the suite does, outside
production, and calls a URL as written.
"""

import logging
import re
from collections.abc import Generator
from datetime import timedelta
from uuid import UUID

import httpx
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, literal_column, select, table, update

from tests.conftest import ADMIN_PASSWORD
from tests.helpers import (
    audit_events,
    first_login,
    new_tenant,
    staff_session,
    system_ctx,
)
from tests.platform.webhooks_support import (
    HOOK,
    PINNED,
    PUBLIC_IP,
    Resolves,
    Stack,
    an_event,
    deliveries,
    delivery_jobs,
)
from xolotl.models import metadata
from xolotl.modules.tenants import routes_webhooks, service
from xolotl.modules.tenants.routes_webhooks import webhook_url_policy
from xolotl.modules.tenants.schemas import (
    WebhookEndpointCreatedResponse,
    WebhookSecretResponse,
)
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform import clock
from xolotl.platform.config import Settings
from xolotl.platform.db import tenant_session
from xolotl.platform.enums import DeliveryStatus, EndpointStatus, Role
from xolotl.platform.events.models import WebhookDelivery, WebhookEndpoint
from xolotl.platform.events.registry import reset_subscribers
from xolotl.platform.events.url_policy import UrlPolicy
from xolotl.platform.events.webhooks import verify_signature

ENDPOINTS = "/v1/webhooks/endpoints"
DELIVERIES = "/v1/webhooks/deliveries"
SECRET_SHAPE = r"whsec_[0-9a-f]{32}"


@pytest.fixture
def stack(
    engine: Engine, client: TestClient, tenant: CreatedTenant
) -> Generator[Stack]:
    # Built after the application, which registers its own webhooks when it is composed
    # (the composition root's wire_platform): this stack's registration must be the
    # last one. The routes read the real clock, so the stack behind them does too. The
    # tenant's creation events pass before any test gives it an endpoint.
    reset_subscribers()
    started = Stack(clock.now)
    started.settle(tenant.tenant_id)
    yield started
    reset_subscribers()


@pytest.fixture
def receiver() -> Generator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        yield router


@pytest.fixture
def strict(app: FastAPI) -> Resolves:
    """Switch on production's rules for endpoint URLs.

    Returns:
        The resolver they use, which resolves ``hooks.example.com`` to a public address.

    """
    resolver = Resolves({"hooks.example.com": [PUBLIC_IP]})
    app.dependency_overrides[webhook_url_policy] = lambda: UrlPolicy(
        strict=True, resolver=resolver
    )
    return resolver


# ---- helpers ---------------------------------------------------------------------


def create(client: TestClient, headers: dict[str, str], key: str, **body: object):
    payload = {"url": HOOK, "event_types": ["*"], **body}
    return client.post(
        ENDPOINTS, json=payload, headers={**headers, "Idempotency-Key": key}
    )


def post(client: TestClient, headers: dict[str, str], path: str, key: str):
    return client.post(path, headers={**headers, "Idempotency-Key": key})


def endpoint_rows(tenant_id: UUID) -> list[WebhookEndpoint]:
    with tenant_session(system_ctx(tenant_id)) as s:
        return list(s.scalars(select(WebhookEndpoint).order_by(WebhookEndpoint.id)))


def revealed(tenant_id: UUID, endpoint_id: str) -> str:
    with tenant_session(system_ctx(tenant_id)) as s:
        row = s.get_one(WebhookEndpoint, (tenant_id, UUID(endpoint_id)))
        return row.secret.reveal(session=s)


def audited(tenant_id: UUID, action: str):
    return [event for event in audit_events(tenant_id) if event.action == action]


def make_dead(
    stack: Stack,
    receiver: respx.MockRouter,
    tenant_id: UUID,
    endpoint_id: str,
    *,
    answer: int = 500,
    event_type: str = "party.created.v1",
) -> tuple[WebhookDelivery, respx.Route]:
    """Fan an event out to the endpoint and fail it until it is dead.

    Returns:
        The dead delivery and the route that stands for the receiver.

    """
    route = receiver.post(PINNED).mock(return_value=httpx.Response(answer))
    _ = stack.fan_out(tenant_id, an_event(tenant_id, type_=event_type))
    with tenant_session(system_ctx(tenant_id)) as s:
        # The event reached the endpoint 25 hours ago: this failure is the last.
        _ = s.execute(
            update(WebhookDelivery)
            .where(WebhookDelivery.endpoint_id == UUID(endpoint_id))
            .values(created_at=clock.now() - timedelta(hours=25))
        )
    assert stack.run_jobs() == 1
    (dead,) = [d for d in deliveries(tenant_id) if d.endpoint_id == UUID(endpoint_id)]
    assert dead.status is DeliveryStatus.DEAD
    return dead, route


@pytest.mark.parametrize(
    ("env", "strict"), [("prod", True), ("test", False), ("dev", False)]
)
def test_the_url_rules_follow_the_configured_environment(
    monkeypatch: pytest.MonkeyPatch, env, strict
):
    monkeypatch.setattr(
        routes_webhooks, "get_settings", lambda: Settings.model_construct(env=env)
    )
    assert webhook_url_policy().strict is strict


# ---- creating an endpoint --------------------------------------------------------


def test_an_admin_creates_an_endpoint_and_is_shown_its_secret_once(
    client: TestClient, tenant: CreatedTenant, admin_headers
):
    created = create(client, admin_headers, "k1", event_types=["party.created.v1"])

    assert created.status_code == 201
    body = created.json()
    assert set(body) == {"id", "url", "event_types", "status", "created_at", "secret"}
    assert body["url"] == HOOK
    assert body["event_types"] == ["party.created.v1"]
    assert body["status"] == "ACTIVE"
    assert re.fullmatch(SECRET_SHAPE, body["secret"])
    (row,) = endpoint_rows(tenant.tenant_id)
    assert str(row.id) == body["id"]
    assert row.created_by == tenant.admin_user_id
    assert row.status is EndpointStatus.ACTIVE
    # Stored encrypted, under the tenant's key; this is the only reading of it.
    assert revealed(tenant.tenant_id, body["id"]) == body["secret"]


def test_event_types_are_kept_once_and_in_order(client: TestClient, admin_headers):
    created = create(
        client,
        admin_headers,
        "k1",
        event_types=["b.created.v1", "a.created.v1", "b.created.v1", "*"],
    )
    assert created.json()["event_types"] == ["*", "a.created.v1", "b.created.v1"]


def test_the_same_request_with_the_same_key_creates_one_endpoint(
    client: TestClient, tenant: CreatedTenant, admin_headers
):
    first = create(client, admin_headers, "same")
    again = create(client, admin_headers, "same")
    other = create(client, admin_headers, "other")

    assert again.headers["Idempotent-Replay"] == "true"
    assert again.json() == first.json()
    assert other.json()["id"] != first.json()["id"]
    assert other.json()["secret"] != first.json()["secret"]
    assert len(endpoint_rows(tenant.tenant_id)) == 2


def test_creating_an_endpoint_needs_an_idempotency_key(
    client: TestClient, admin_headers
):
    response = client.post(
        ENDPOINTS, json={"url": HOOK, "event_types": ["*"]}, headers=admin_headers
    )
    assert (response.status_code, response.json()["code"]) == (
        400,
        "IDEMPOTENCY_KEY_REQUIRED",
    )


def test_creating_an_endpoint_is_audited_with_ids_only(
    client: TestClient, tenant: CreatedTenant, admin_headers
):
    body = create(client, admin_headers, "k1", url=f"{HOOK}?token=abc123").json()

    (event,) = audited(tenant.tenant_id, "webhook_endpoint.created")
    assert (event.entity_type, event.entity_id) == ("webhook_endpoint", body["id"])
    assert event.actor_id == str(tenant.admin_user_id)
    assert event.detail is None
    text_of_event = repr((event.detail, event.entity_id, event.entity_type))
    assert body["secret"] not in text_of_event
    assert "abc123" not in text_of_event


@pytest.mark.parametrize(
    "url",
    [
        "http://hooks.example.com/xolotl",
        "https://user:pw@hooks.example.com/xolotl",
        "https://hooks.example.com:99999/xolotl",
        "https://127.0.0.1/xolotl",
        "https://[::1]/xolotl",
        "https://169.254.169.254/latest/meta-data/",
        "https://10.0.0.5/xolotl",
        "https://localhost/xolotl",
        "ftp://hooks.example.com/xolotl",
        "hooks.example.com/xolotl",
        "https://nowhere.example.com/xolotl",
    ],
)
def test_in_production_an_endpoint_url_must_be_https_and_public(
    client: TestClient, tenant: CreatedTenant, admin_headers, strict: Resolves, url: str
):
    response = create(client, admin_headers, "k1", url=url)

    assert (response.status_code, response.json()["code"]) == (422, "VALIDATION_FAILED")
    assert url not in response.text
    assert endpoint_rows(tenant.tenant_id) == []
    assert audited(tenant.tenant_id, "webhook_endpoint.created") == []


def test_in_production_a_host_that_resolves_inside_is_refused_and_one_outside_is_not(
    client: TestClient, tenant: CreatedTenant, admin_headers, strict: Resolves
):
    strict.answers["inside.example.com"] = ["10.1.2.3"]
    refused = create(client, admin_headers, "k1", url="https://inside.example.com/x")
    assert refused.status_code == 422
    assert "non-public" in refused.json()["detail"]

    accepted = create(client, admin_headers, "k2")
    assert accepted.status_code == 201
    assert accepted.json()["url"] == HOOK
    assert strict.calls == ["inside.example.com", "hooks.example.com"]


def test_outside_production_any_http_or_https_url_is_accepted(
    client: TestClient, admin_headers
):
    for n, url in enumerate(["http://localhost:9000/hook", "https://10.0.0.5/hook"]):
        assert create(client, admin_headers, f"k{n}", url=url).status_code == 201
    bad = create(client, admin_headers, "k9", url="ftp://localhost/hook")
    assert (bad.status_code, bad.json()["code"]) == (422, "VALIDATION_FAILED")


def test_a_url_is_stored_as_given_without_the_blanks_around_it(
    client: TestClient, tenant: CreatedTenant, admin_headers
):
    created = create(client, admin_headers, "k1", url=f"  {HOOK}?x=1  ")
    assert created.json()["url"] == f"{HOOK}?x=1"
    assert endpoint_rows(tenant.tenant_id)[0].url == f"{HOOK}?x=1"


def test_a_url_over_the_limit_is_refused_by_the_schema_with_the_field_named(
    client: TestClient, admin_headers
):
    url = "https://hooks.example.com/" + "a" * 2100
    response = create(client, admin_headers, "k1", url=url)
    assert (response.status_code, response.json()["code"]) == (422, "VALIDATION_FAILED")
    assert response.json()["errors"][0]["loc"] == ["body", "url"]
    assert url not in response.text


@pytest.mark.parametrize(
    "body",
    [
        {"url": "", "event_types": ["*"]},
        {"url": HOOK, "event_types": []},
        {"url": HOOK, "event_types": ["Party.Created"]},
        {"url": HOOK, "event_types": ["party created"]},
        {"url": HOOK, "event_types": ["party.*"]},
        {"url": HOOK, "event_types": [""]},
        {"url": HOOK, "event_types": ["a" * 101]},
        {"url": HOOK, "event_types": [f"t{n}.v1" for n in range(51)]},
        {"url": HOOK},
        {"event_types": ["*"]},
        {"url": 7, "event_types": ["*"]},
    ],
)
def test_a_malformed_body_is_refused(
    client: TestClient, tenant: CreatedTenant, admin_headers, body: dict[str, object]
):
    response = client.post(
        ENDPOINTS, json=body, headers={**admin_headers, "Idempotency-Key": "k1"}
    )
    assert (response.status_code, response.json()["code"]) == (422, "VALIDATION_FAILED")
    assert endpoint_rows(tenant.tenant_id) == []


# ---- listing endpoints -----------------------------------------------------------


def test_endpoints_are_listed_newest_first_and_never_with_their_secret(
    client: TestClient, admin_headers
):
    ids = [
        create(client, admin_headers, f"k{n}", event_types=[f"t{n}.v1"]).json()["id"]
        for n in range(3)
    ]

    listed = client.get(ENDPOINTS, headers=admin_headers)

    assert listed.status_code == 200
    page = listed.json()
    assert [item["id"] for item in page["items"]] == ids[::-1]
    assert page["next_cursor"] is None
    for item in page["items"]:
        assert set(item) == {"id", "url", "event_types", "status", "created_at"}
    assert "whsec_" not in listed.text


def test_the_endpoint_list_pages_by_cursor(client: TestClient, admin_headers):
    ids = [create(client, admin_headers, f"k{n}").json()["id"] for n in range(3)]

    first = client.get(ENDPOINTS, params={"limit": 2}, headers=admin_headers).json()
    assert [item["id"] for item in first["items"]] == ids[::-1][:2]
    assert first["next_cursor"] is not None
    second = client.get(
        ENDPOINTS,
        params={"limit": 2, "cursor": first["next_cursor"]},
        headers=admin_headers,
    ).json()
    assert [item["id"] for item in second["items"]] == ids[::-1][2:]
    assert second["next_cursor"] is None


def test_disabled_endpoints_stay_in_the_list_marked_disabled(
    client: TestClient, admin_headers
):
    created = create(client, admin_headers, "k1").json()
    assert (
        client.delete(f"{ENDPOINTS}/{created['id']}", headers=admin_headers).status_code
        == 204
    )
    (item,) = client.get(ENDPOINTS, headers=admin_headers).json()["items"]
    assert (item["id"], item["status"]) == (created["id"], "DISABLED")


# ---- disabling an endpoint -------------------------------------------------------


def test_deleting_an_endpoint_disables_it_and_keeps_its_deliveries(
    client: TestClient,
    tenant: CreatedTenant,
    admin_headers,
    stack: Stack,
    receiver: respx.MockRouter,
):
    created = create(client, admin_headers, "k1").json()
    _ = receiver.post(PINNED).mock(return_value=httpx.Response(200))
    _ = stack.fan_out(tenant.tenant_id, an_event(tenant.tenant_id))
    (before,) = deliveries(tenant.tenant_id)

    response = client.delete(f"{ENDPOINTS}/{created['id']}", headers=admin_headers)

    assert response.status_code == 204
    assert response.content == b""
    (row,) = endpoint_rows(tenant.tenant_id)
    assert row.status is EndpointStatus.DISABLED
    (kept,) = deliveries(tenant.tenant_id)  # kept, and still waiting
    assert (kept.id, kept.status) == (before.id, DeliveryStatus.PENDING)
    (event,) = audited(tenant.tenant_id, "webhook_endpoint.deleted")
    assert (event.entity_type, event.entity_id) == ("webhook_endpoint", created["id"])
    assert event.detail is None


def test_a_disabled_endpoint_gets_no_new_deliveries_and_sends_what_was_waiting_nowhere(
    client: TestClient,
    tenant: CreatedTenant,
    admin_headers,
    stack: Stack,
    receiver: respx.MockRouter,
):
    created = create(client, admin_headers, "k1").json()
    route = receiver.post(PINNED).mock(return_value=httpx.Response(200))
    _ = stack.fan_out(tenant.tenant_id, an_event(tenant.tenant_id, key="a"))
    _ = client.delete(f"{ENDPOINTS}/{created['id']}", headers=admin_headers)

    _ = stack.fan_out(tenant.tenant_id, an_event(tenant.tenant_id, key="b"))
    assert stack.run_jobs() == 1

    assert route.call_count == 0
    (waiting,) = deliveries(tenant.tenant_id)
    assert (waiting.status, waiting.last_error) == (
        DeliveryStatus.DEAD,
        "endpoint disabled",
    )


def test_deleting_twice_is_not_an_error_and_is_audited_once(
    client: TestClient, tenant: CreatedTenant, admin_headers
):
    created = create(client, admin_headers, "k1").json()
    for _ in range(2):
        response = client.delete(f"{ENDPOINTS}/{created['id']}", headers=admin_headers)
        assert response.status_code == 204
    assert len(audited(tenant.tenant_id, "webhook_endpoint.deleted")) == 1


def test_deleting_an_endpoint_that_does_not_exist_is_a_404(
    client: TestClient, admin_headers
):
    response = client.delete(
        f"{ENDPOINTS}/00000000-0000-7000-8000-000000000000", headers=admin_headers
    )
    assert (response.status_code, response.json()["code"]) == (404, "NOT_FOUND")
    assert client.delete(f"{ENDPOINTS}/nope", headers=admin_headers).status_code == 422


# ---- rotating the secret ---------------------------------------------------------


def test_rotating_gives_a_new_secret_shown_once_and_audited_by_id(
    client: TestClient, tenant: CreatedTenant, admin_headers
):
    created = create(client, admin_headers, "k1").json()

    rotated = post(
        client, admin_headers, f"{ENDPOINTS}/{created['id']}/rotate-secret", "r1"
    )

    assert rotated.status_code == 200
    body = rotated.json()
    assert set(body) == {"id", "secret"}
    assert body["id"] == created["id"]
    assert body["secret"] != created["secret"]
    assert revealed(tenant.tenant_id, created["id"]) == body["secret"]
    (event,) = audited(tenant.tenant_id, "webhook_endpoint.secret_rotated")
    assert event.entity_id == created["id"]
    assert event.detail is None
    listed = client.get(ENDPOINTS, headers=admin_headers)
    assert body["secret"] not in listed.text
    assert created["secret"] not in listed.text


def test_after_a_rotation_deliveries_are_signed_with_the_new_secret(
    client: TestClient,
    tenant: CreatedTenant,
    admin_headers,
    stack: Stack,
    receiver: respx.MockRouter,
):
    created = create(client, admin_headers, "k1").json()
    new = post(
        client, admin_headers, f"{ENDPOINTS}/{created['id']}/rotate-secret", "r1"
    ).json()["secret"]
    route = receiver.post(PINNED).mock(return_value=httpx.Response(200))
    _ = stack.fan_out(tenant.tenant_id, an_event(tenant.tenant_id))

    assert stack.run_jobs() == 1

    request = route.calls.last.request
    stamp, signature = (
        request.headers["x-xolotl-timestamp"],
        request.headers["x-xolotl-signature"],
    )
    assert verify_signature(new, signature, stamp, request.content)
    assert not verify_signature(created["secret"], signature, stamp, request.content)


def test_a_disabled_or_unknown_endpoint_cannot_be_rotated(
    client: TestClient, admin_headers
):
    created = create(client, admin_headers, "k1").json()
    _ = client.delete(f"{ENDPOINTS}/{created['id']}", headers=admin_headers)

    disabled = post(
        client, admin_headers, f"{ENDPOINTS}/{created['id']}/rotate-secret", "r1"
    )
    assert (disabled.status_code, disabled.json()["code"]) == (409, "CONFLICT")
    unknown = post(
        client,
        admin_headers,
        f"{ENDPOINTS}/00000000-0000-7000-8000-000000000000/rotate-secret",
        "r2",
    )
    assert (unknown.status_code, unknown.json()["code"]) == (404, "NOT_FOUND")


# ---- listing deliveries ----------------------------------------------------------


def arrange_deliveries(
    client: TestClient,
    tenant: CreatedTenant,
    admin_headers,
    stack: Stack,
    receiver: respx.MockRouter,
):
    """Two endpoints and two events: three delivered, one dead.

    Returns:
        The two endpoints' ids.

    """
    first = create(
        client,
        admin_headers,
        "k1",
        event_types=["party.created.v1", "party.deleted.v1"],
    ).json()["id"]
    second = create(
        client, admin_headers, "k2", event_types=["party.created.v1"]
    ).json()["id"]
    _ = receiver.post(PINNED).mock(return_value=httpx.Response(200))
    _ = stack.fan_out(
        tenant.tenant_id, an_event(tenant.tenant_id, type_="party.created.v1")
    )
    _ = stack.fan_out(
        tenant.tenant_id, an_event(tenant.tenant_id, type_="party.deleted.v1")
    )
    assert stack.run_jobs() == 3
    return first, second


def test_deliveries_are_listed_newest_first_with_their_state(
    client: TestClient,
    tenant: CreatedTenant,
    admin_headers,
    stack: Stack,
    receiver: respx.MockRouter,
):
    first, second = arrange_deliveries(client, tenant, admin_headers, stack, receiver)

    listed = client.get(DELIVERIES, headers=admin_headers)

    assert listed.status_code == 200
    items = listed.json()["items"]
    assert len(items) == 3
    assert [item["created_at"] for item in items] == sorted(
        (item["created_at"] for item in items), reverse=True
    )
    assert {item["endpoint_id"] for item in items} == {first, second}
    assert set(items[0]) == {
        "id",
        "endpoint_id",
        "event_id",
        "attempt",
        "status",
        "response_code",
        "last_error",
        "next_attempt_at",
        "delivered_at",
        "created_at",
    }
    assert {
        (item["status"], item["attempt"], item["response_code"]) for item in items
    } == {("DELIVERED", 1, 200)}


def test_deliveries_can_be_filtered_by_status_and_by_endpoint(
    client: TestClient,
    tenant: CreatedTenant,
    admin_headers,
    stack: Stack,
    receiver: respx.MockRouter,
):
    first, second = arrange_deliveries(client, tenant, admin_headers, stack, receiver)
    third = create(client, admin_headers, "k3", event_types=["party.merged.v1"]).json()[
        "id"
    ]
    dead, _ = make_dead(
        stack, receiver, tenant.tenant_id, third, event_type="party.merged.v1"
    )

    by_status = client.get(DELIVERIES, params={"status": "DEAD"}, headers=admin_headers)
    assert [item["id"] for item in by_status.json()["items"]] == [str(dead.id)]
    by_endpoint = client.get(
        DELIVERIES, params={"endpoint_id": first}, headers=admin_headers
    ).json()["items"]
    assert len(by_endpoint) == 2
    assert {item["endpoint_id"] for item in by_endpoint} == {first}
    both = client.get(
        DELIVERIES,
        params={"status": "DELIVERED", "endpoint_id": second},
        headers=admin_headers,
    ).json()["items"]
    assert len(both) == 1
    assert (both[0]["endpoint_id"], both[0]["status"]) == (second, "DELIVERED")
    none = client.get(DELIVERIES, params={"status": "PENDING"}, headers=admin_headers)
    assert none.json() == {"items": [], "next_cursor": None}


def test_a_dead_delivery_shows_why_it_died(
    client: TestClient,
    tenant: CreatedTenant,
    admin_headers,
    stack: Stack,
    receiver: respx.MockRouter,
):
    endpoint_id = create(client, admin_headers, "k1").json()["id"]
    dead, _ = make_dead(stack, receiver, tenant.tenant_id, endpoint_id, answer=503)

    (item,) = client.get(
        DELIVERIES, params={"status": "DEAD"}, headers=admin_headers
    ).json()["items"]

    assert item["id"] == str(dead.id)
    assert (item["attempt"], item["response_code"], item["last_error"]) == (
        1,
        503,
        "HTTP 503",
    )
    assert item["next_attempt_at"] is None
    assert item["delivered_at"] is None


def test_the_delivery_list_pages_by_cursor(
    client: TestClient,
    tenant: CreatedTenant,
    admin_headers,
    stack: Stack,
    receiver: respx.MockRouter,
):
    _ = arrange_deliveries(client, tenant, admin_headers, stack, receiver)
    seen: list[str] = []
    cursor = None
    while True:
        params = {"limit": 2} | ({"cursor": cursor} if cursor else {})
        page = client.get(DELIVERIES, params=params, headers=admin_headers).json()
        seen.extend(item["id"] for item in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == len(set(seen)) == 3


@pytest.mark.parametrize(
    "params",
    [
        {"status": "LOST"},
        {"endpoint_id": "nope"},
        {"limit": 0},
        {"limit": 201},
        {"cursor": "!"},
    ],
)
def test_the_delivery_list_refuses_bad_filters(
    client: TestClient, admin_headers, params: dict[str, str | int]
):
    response = client.get(DELIVERIES, params=params, headers=admin_headers)
    assert (response.status_code, response.json()["code"]) == (422, "VALIDATION_FAILED")


# ---- retrying a delivery ---------------------------------------------------------


def test_retrying_a_dead_delivery_sends_it_again(
    client: TestClient,
    tenant: CreatedTenant,
    admin_headers,
    stack: Stack,
    receiver: respx.MockRouter,
):
    endpoint_id = create(client, admin_headers, "k1").json()["id"]
    dead, route = make_dead(stack, receiver, tenant.tenant_id, endpoint_id)
    _ = route.mock(return_value=httpx.Response(200))
    before = len(delivery_jobs(tenant.tenant_id))

    response = post(client, admin_headers, f"{DELIVERIES}/{dead.id}/retry", "retry-1")

    assert response.status_code == 200
    body = response.json()
    assert (body["id"], body["status"]) == (str(dead.id), "PENDING")
    assert body["next_attempt_at"] is not None
    assert len(delivery_jobs(tenant.tenant_id)) == before + 1
    (event,) = audited(tenant.tenant_id, "webhook_delivery.retried")
    assert (event.entity_type, event.entity_id) == ("webhook_delivery", str(dead.id))
    assert event.detail == {
        "endpoint_id": endpoint_id,
        "event_id": str(dead.event_id),
    }

    assert stack.run_jobs() == 1
    delivered = client.get(
        DELIVERIES, params={"status": "DELIVERED"}, headers=admin_headers
    ).json()["items"]
    assert [item["id"] for item in delivered] == [str(dead.id)]
    first, second = (call.request for call in route.calls)
    assert second.content == first.content
    assert verify_signature(
        revealed(tenant.tenant_id, endpoint_id),
        second.headers["x-xolotl-signature"],
        second.headers["x-xolotl-timestamp"],
        second.content,
    )


def test_a_retry_with_the_same_idempotency_key_queues_one_job(
    client: TestClient,
    tenant: CreatedTenant,
    admin_headers,
    stack: Stack,
    receiver: respx.MockRouter,
):
    endpoint_id = create(client, admin_headers, "k1").json()["id"]
    dead, _ = make_dead(stack, receiver, tenant.tenant_id, endpoint_id)
    before = len(delivery_jobs(tenant.tenant_id))

    first = post(client, admin_headers, f"{DELIVERIES}/{dead.id}/retry", "same")
    again = post(client, admin_headers, f"{DELIVERIES}/{dead.id}/retry", "same")

    assert again.headers["Idempotent-Replay"] == "true"
    assert again.json() == first.json()
    assert len(delivery_jobs(tenant.tenant_id)) == before + 1
    assert len(audited(tenant.tenant_id, "webhook_delivery.retried")) == 1


def test_retrying_needs_an_idempotency_key(client: TestClient, admin_headers):
    response = client.post(
        f"{DELIVERIES}/00000000-0000-7000-8000-000000000000/retry",
        headers=admin_headers,
    )
    assert (response.status_code, response.json()["code"]) == (
        400,
        "IDEMPOTENCY_KEY_REQUIRED",
    )


def test_a_delivered_delivery_cannot_be_retried(
    client: TestClient,
    tenant: CreatedTenant,
    admin_headers,
    stack: Stack,
    receiver: respx.MockRouter,
):
    _ = arrange_deliveries(client, tenant, admin_headers, stack, receiver)
    (item, *_) = client.get(DELIVERIES, headers=admin_headers).json()["items"]

    response = post(client, admin_headers, f"{DELIVERIES}/{item['id']}/retry", "r1")

    assert (response.status_code, response.json()["code"]) == (409, "CONFLICT")
    assert audited(tenant.tenant_id, "webhook_delivery.retried") == []


def test_a_delivery_of_a_disabled_endpoint_cannot_be_retried(
    client: TestClient,
    tenant: CreatedTenant,
    admin_headers,
    stack: Stack,
    receiver: respx.MockRouter,
):
    endpoint_id = create(client, admin_headers, "k1").json()["id"]
    dead, _ = make_dead(stack, receiver, tenant.tenant_id, endpoint_id)
    _ = client.delete(f"{ENDPOINTS}/{endpoint_id}", headers=admin_headers)

    response = post(client, admin_headers, f"{DELIVERIES}/{dead.id}/retry", "r1")

    assert (response.status_code, response.json()["code"]) == (409, "CONFLICT")


def test_retrying_a_delivery_that_does_not_exist_is_a_404(
    client: TestClient, admin_headers
):
    response = post(
        client,
        admin_headers,
        f"{DELIVERIES}/00000000-0000-7000-8000-000000000000/retry",
        "r1",
    )
    assert (response.status_code, response.json()["code"]) == (404, "NOT_FOUND")


# ---- who may do this -------------------------------------------------------------


def login_as(client: TestClient, tenant: CreatedTenant, email: str, role: Role):
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        staff = service.create_staff(s, email=email, display_name="Staff", roles=[role])
    return first_login(
        client, "acme", email, staff.initial_password, "another-long-password"
    )


MANAGEMENT_CALLS = [
    ("post", ENDPOINTS, {"url": HOOK, "event_types": ["*"]}),
    ("get", ENDPOINTS, None),
    ("delete", f"{ENDPOINTS}/00000000-0000-7000-8000-000000000000", None),
    ("post", f"{ENDPOINTS}/00000000-0000-7000-8000-000000000000/rotate-secret", None),
    ("get", DELIVERIES, None),
    ("post", f"{DELIVERIES}/00000000-0000-7000-8000-000000000000/retry", None),
]


def call(client: TestClient, spec, headers: dict[str, str]):
    method, path, body = spec
    extra = {"Idempotency-Key": "k"} if method == "post" else {}
    return client.request(method, path, json=body, headers={**headers, **extra})


@pytest.mark.parametrize(
    "spec", MANAGEMENT_CALLS, ids=lambda spec: f"{spec[0]} {spec[1][12:]}"
)
@pytest.mark.parametrize(
    "role", [Role.ANALYST, Role.SENIOR_ANALYST, Role.MLRO, Role.AUDITOR_RO]
)
def test_only_admins_manage_webhooks(
    client: TestClient, tenant: CreatedTenant, spec, role: Role
):
    headers = login_as(client, tenant, "someone@acme.mx", role)
    response = call(client, spec, headers)
    assert (response.status_code, response.json()["code"]) == (403, "ROLE_FORBIDDEN")


@pytest.mark.parametrize(
    "spec", MANAGEMENT_CALLS, ids=lambda spec: f"{spec[0]} {spec[1][12:]}"
)
def test_an_api_key_cannot_manage_webhooks(
    client: TestClient, tenant: CreatedTenant, spec
):
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        key = service.create_api_key(
            s, name="x", scopes=["tenants:read", "tenants:write"]
        )
    response = call(client, spec, {"Authorization": f"Bearer {key.key}"})
    assert (response.status_code, response.json()["code"]) == (403, "ROLE_FORBIDDEN")


@pytest.mark.parametrize(
    "spec", MANAGEMENT_CALLS, ids=lambda spec: f"{spec[0]} {spec[1][12:]}"
)
def test_webhooks_need_a_signed_in_caller(client: TestClient, spec):
    response = call(client, spec, {})
    assert response.status_code == 401


# ---- tenants ---------------------------------------------------------------------


def test_one_tenant_cannot_see_or_touch_the_webhooks_of_another(
    client: TestClient,
    tenant: CreatedTenant,
    admin_headers,
    stack: Stack,
    receiver: respx.MockRouter,
):
    mine = create(client, admin_headers, "k1").json()["id"]
    dead, _ = make_dead(stack, receiver, tenant.tenant_id, mine)
    other = new_tenant(slug="beta", email="admin@beta.mx")
    theirs = first_login(
        client, "beta", other.admin_email, other.admin_password, ADMIN_PASSWORD
    )

    assert client.get(ENDPOINTS, headers=theirs).json()["items"] == []
    assert client.get(DELIVERIES, headers=theirs).json()["items"] == []
    for response in (
        client.delete(f"{ENDPOINTS}/{mine}", headers=theirs),
        post(client, theirs, f"{ENDPOINTS}/{mine}/rotate-secret", "r1"),
        post(client, theirs, f"{DELIVERIES}/{dead.id}/retry", "r2"),
    ):
        assert (response.status_code, response.json()["code"]) == (404, "NOT_FOUND")
    (row,) = endpoint_rows(tenant.tenant_id)
    assert row.status is EndpointStatus.ACTIVE
    assert audited(other.tenant_id, "webhook_endpoint.deleted") == []


# ---- the whole path --------------------------------------------------------------


def test_an_event_reaches_a_receiver_with_a_signature_made_from_the_secret_it_was_shown(
    client: TestClient,
    tenant: CreatedTenant,
    admin_headers,
    stack: Stack,
    receiver: respx.MockRouter,
    strict: Resolves,
):
    created = create(
        client, admin_headers, "k1", event_types=["party.created.v1"]
    ).json()
    route = receiver.post(PINNED).mock(return_value=httpx.Response(204))
    event = an_event(tenant.tenant_id, type_="party.created.v1")

    assert stack.fan_out(tenant.tenant_id, event) == 1
    assert stack.run_jobs() == 1

    request = route.calls.last.request
    assert verify_signature(
        created["secret"],
        request.headers["x-xolotl-signature"],
        request.headers["x-xolotl-timestamp"],
        request.content,
    )
    assert request.headers["x-xolotl-event-id"] == str(event.id)
    (item,) = client.get(DELIVERIES, headers=admin_headers).json()["items"]
    assert (item["status"], item["response_code"]) == ("DELIVERED", 204)


def test_a_failing_receiver_lands_in_the_dead_letter_view_and_a_retry_recovers_it(
    client: TestClient,
    tenant: CreatedTenant,
    admin_headers,
    stack: Stack,
    receiver: respx.MockRouter,
    strict: Resolves,
):
    endpoint_id = create(client, admin_headers, "k1").json()["id"]
    dead, route = make_dead(stack, receiver, tenant.tenant_id, endpoint_id)
    listed = client.get(DELIVERIES, params={"status": "DEAD"}, headers=admin_headers)
    assert [item["id"] for item in listed.json()["items"]] == [str(dead.id)]

    _ = route.mock(return_value=httpx.Response(200))
    assert (
        post(client, admin_headers, f"{DELIVERIES}/{dead.id}/retry", "r1").status_code
        == 200
    )
    assert stack.run_jobs() == 1

    assert (
        client.get(DELIVERIES, params={"status": "DEAD"}, headers=admin_headers).json()[
            "items"
        ]
        == []
    )
    (item,) = client.get(DELIVERIES, headers=admin_headers).json()["items"]
    assert item["status"] == "DELIVERED"
    assert item["attempt"] == 2


# ---- what must never be shown ----------------------------------------------------


def test_the_secret_appears_only_in_the_response_that_creates_or_rotates_it(
    client: TestClient,
    tenant: CreatedTenant,
    admin_headers,
    admin_engine: Engine,
    stack: Stack,
    receiver: respx.MockRouter,
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.DEBUG)
    created = create(client, admin_headers, "k1")
    endpoint_id = created.json()["id"]
    secret = created.json()["secret"]
    route = receiver.post(PINNED).mock(return_value=httpx.Response(500))
    _ = stack.fan_out(tenant.tenant_id, an_event(tenant.tenant_id))
    _ = stack.run_jobs()
    rotated = post(
        client, admin_headers, f"{ENDPOINTS}/{endpoint_id}/rotate-secret", "r1"
    )
    responses = [
        client.get(ENDPOINTS, headers=admin_headers),
        client.get(DELIVERIES, headers=admin_headers),
        client.delete(f"{ENDPOINTS}/{endpoint_id}", headers=admin_headers),
        client.get("/v1/audit", headers=admin_headers),
        client.get("/v1/openapi.json"),
    ]

    assert route.call_count == 1
    for response in responses:
        assert secret not in response.text
        assert "whsec_" not in response.text.replace("whsec_<", "")
    new_secret = rotated.json()["secret"]
    assert new_secret != secret
    logged = caplog.text + "\n".join(repr(vars(record)) for record in caplog.records)
    for leaked_secret in (secret, new_secret):
        assert leaked_secret not in logged
        assert leaked_secret[6:] not in logged
    with admin_engine.connect() as conn:
        for name in metadata().tables:
            # The idempotency record of the creating request is the response it
            # replays: the secret is in it, for 24 hours (see the report).
            if name == "idempotency_key":
                continue
            rows = conn.execute(select(literal_column("*")).select_from(table(name)))
            dump = repr(rows.all())
            assert secret not in dump, name
            assert new_secret not in dump, name


def test_a_response_model_never_prints_the_secret(client: TestClient, admin_headers):
    created = create(client, admin_headers, "k1").json()
    shown = WebhookEndpointCreatedResponse.model_validate(created)
    assert shown.secret not in repr(shown)
    assert shown.secret not in str(shown)
    rotated = post(
        client, admin_headers, f"{ENDPOINTS}/{created['id']}/rotate-secret", "r1"
    )
    again = WebhookSecretResponse.model_validate(rotated.json())
    assert again.secret not in repr(again)
    assert again.secret not in str(again)


def test_only_the_two_responses_that_show_a_secret_have_one_in_the_openapi_document(
    client: TestClient,
):
    schemas = client.get("/v1/openapi.json").json()["components"]["schemas"]
    with_secret = {
        name
        for name, schema in schemas.items()
        if "secret" in schema.get("properties", {})
    }
    assert {"WebhookEndpointCreatedResponse", "WebhookSecretResponse"} <= with_secret
    assert "WebhookEndpointResponse" not in with_secret
    assert "WebhookDeliveryResponse" not in with_secret
