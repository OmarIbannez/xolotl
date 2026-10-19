"""The connector routes: list, usage and credentials, for administrators only."""

import logging
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from tests.helpers import (
    audit_events,
    everything_stored,
    first_login,
    leaked,
    new_tenant,
    staff_session,
    system_ctx,
)
from tests.platform.connectors_support import FakeConnector, make_deps
from xolotl.modules.tenants import service
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform.connectors import (
    CallOutcome,
    CheckType,
    ConnectorMode,
    ConnectorRoute,
    ConnectorRouter,
)
from xolotl.platform.connectors.models import ConnectorCall
from xolotl.platform.db import tenant_session
from xolotl.platform.enums import Role

if TYPE_CHECKING:
    from xolotl.platform.api.app import AppState


@pytest.fixture
def fake_router(app: FastAPI, tmp_path: Path) -> ConnectorRouter:
    deps, _, _ = make_deps(tmp_path)
    router = ConnectorRouter(
        [FakeConnector(deps, name="primary"), FakeConnector(deps, name="fallback")],
        {(CheckType.RFC_VALIDATION, "MX"): ("primary", "fallback")},
        default_mode=ConnectorMode.LIVE,
    )
    state = cast("AppState", app.state.xolotl)
    assert state.services is not None
    state.services = replace(state.services, connectors=router)
    return router


def _staff_headers(
    client: TestClient, tenant: CreatedTenant, *, roles: list[Role]
) -> dict[str, str]:
    email = f"{roles[0].value.lower()}@acme.mx"
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        created = service.create_staff(s, email=email, display_name=email, roles=roles)
    return first_login(
        client, "acme", email, created.initial_password, "a-long-enough-password"
    )


def _api_key(tenant: CreatedTenant, *, scopes: list[str]) -> str:
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        return service.create_api_key(s, name="machine", scopes=scopes).key


def seed(
    tenant_id: UUID, connector: str, outcome: CallOutcome, *, cost: int, at: datetime
) -> None:
    with tenant_session(system_ctx(tenant_id)) as s:
        s.add(
            ConnectorCall(
                tenant_id=tenant_id,
                connector=connector,
                check_type=CheckType.RFC_VALIDATION,
                mode=ConnectorMode.LIVE,
                route=ConnectorRoute.PRIMARY,
                outcome=outcome,
                duration_ms=5,
                cost_micros=cost,
                created_at=at,
            )
        )


def test_admin_lists_connectors_with_mode_routes_circuit_and_credential_modes(
    client: TestClient, admin_headers, fake_router
):
    _ = client.put(
        "/v1/connectors/primary/credentials/LIVE",
        json={"fields": {"token": "t"}},
        headers=admin_headers,
    )
    resp = client.get("/v1/connectors", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "items": [
            {
                "name": "primary",
                "check_types": ["RFC_VALIDATION"],
                "mode": "SANDBOX",
                "routes": [
                    {
                        "check_type": "RFC_VALIDATION",
                        "country": "MX",
                        "position": "PRIMARY",
                    }
                ],
                "circuit_state": "CLOSED",
                "credential_modes": ["LIVE"],
            },
            {
                "name": "fallback",
                "check_types": ["RFC_VALIDATION"],
                "mode": "SANDBOX",
                "routes": [
                    {
                        "check_type": "RFC_VALIDATION",
                        "country": "MX",
                        "position": "FALLBACK",
                    }
                ],
                "circuit_state": "CLOSED",
                "credential_modes": [],
            },
        ]
    }


def test_the_list_shows_this_cells_open_circuit(
    client: TestClient, admin_headers, fake_router: ConnectorRouter
):
    for _ in range(5):
        fake_router.connector("primary").breaker("mx-1").record_failure()
    items = client.get("/v1/connectors", headers=admin_headers).json()["items"]
    assert items[0]["circuit_state"] == "OPEN"
    assert items[1]["circuit_state"] == "CLOSED"


def test_setting_credentials_answers_without_the_values_and_audits_the_field_names(
    client: TestClient,
    admin_headers,
    tenant: CreatedTenant,
    admin_engine: Engine,
    caplog: pytest.LogCaptureFixture,
    fake_router,
):
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.DEBUG, logger="sqlalchemy.engine")
    token = f"tok-{uuid4().hex}"
    resp = client.put(
        "/v1/connectors/primary/credentials/LIVE",
        json={"fields": {"token": token, "user": "ana"}},
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (
        body["connector"],
        body["mode"],
        body["field_names"],
        body["rotated_at"],
    ) == ("primary", "LIVE", ["token", "user"], None)
    assert body["created_at"]
    assert token not in resp.text
    assert leaked(caplog.text, token.encode()) == []
    assert leaked(everything_stored(admin_engine), token.encode()) == []
    assert [
        (e.action, e.detail)
        for e in audit_events(tenant.tenant_id)
        if e.action == "connector.credential_set"
    ] == [
        (
            "connector.credential_set",
            {
                "connector": "primary",
                "mode": "LIVE",
                "field_names": ["token", "user"],
                "rotated": False,
            },
        )
    ]
    again = client.put(
        "/v1/connectors/primary/credentials/LIVE",
        json={"fields": {"token": "new"}},
        headers=admin_headers,
    )
    assert again.status_code == 200, again.text
    assert again.json()["rotated_at"] is not None
    assert again.json()["field_names"] == ["token"]


def test_a_sandbox_credential_is_stored_and_listed(
    client: TestClient, admin_headers, fake_router
):
    resp = client.put(
        "/v1/connectors/fallback/credentials/SANDBOX",
        json={"fields": {"token": "t"}},
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["mode"] == "SANDBOX"
    items = client.get("/v1/connectors", headers=admin_headers).json()["items"]
    assert [i["credential_modes"] for i in items] == [[], ["SANDBOX"]]


def test_an_unknown_connector_is_a_404(client: TestClient, admin_headers):
    resp = client.put(
        "/v1/connectors/nobody/credentials/LIVE",
        json={"fields": {"token": "t"}},
        headers=admin_headers,
    )
    assert (resp.status_code, resp.json()["code"]) == (404, "NOT_FOUND")


@pytest.mark.parametrize(
    "body",
    [{"fields": {}}, {"fields": {"Bad Key": "x"}}, {"fields": {"token": ""}}, {}],
)
def test_invalid_credential_bodies_are_422(
    client: TestClient, admin_headers, fake_router, body
):
    resp = client.put(
        "/v1/connectors/primary/credentials/LIVE", json=body, headers=admin_headers
    )
    assert (resp.status_code, resp.json()["code"]) == (422, "VALIDATION_FAILED")


def test_usage_aggregates_per_connector_per_utc_day_for_this_tenant_only(
    client: TestClient, admin_headers, tenant: CreatedTenant
):
    other = new_tenant("beta", "admin@beta.mx")
    seed(
        tenant.tenant_id,
        "sat_rfc",
        CallOutcome.PASS,
        cost=1000,
        at=datetime(2026, 10, 18, 23, 59, 59, tzinfo=UTC),
    )
    seed(
        tenant.tenant_id,
        "sat_rfc",
        CallOutcome.ERROR,
        cost=0,
        at=datetime(2026, 10, 19, 0, 0, 1, tzinfo=UTC),
    )
    seed(
        tenant.tenant_id,
        "sat_rfc",
        CallOutcome.PASS,
        cost=1000,
        at=datetime(2026, 10, 19, 12, 0, tzinfo=UTC),
    )
    seed(
        tenant.tenant_id,
        "banxico_sie",
        CallOutcome.PASS,
        cost=0,
        at=datetime(2026, 10, 19, 12, 0, tzinfo=UTC),
    )
    seed(
        other.tenant_id,
        "sat_rfc",
        CallOutcome.PASS,
        cost=5000,
        at=datetime(2026, 10, 19, 12, 0, tzinfo=UTC),
    )
    # Outside the range on both sides: the bounds are whole UTC days.
    seed(
        tenant.tenant_id,
        "sat_rfc",
        CallOutcome.PASS,
        cost=9,
        at=datetime(2026, 10, 17, 23, 59, 59, tzinfo=UTC),
    )
    seed(
        tenant.tenant_id,
        "sat_rfc",
        CallOutcome.PASS,
        cost=9,
        at=datetime(2026, 10, 20, 0, 0, 0, tzinfo=UTC),
    )
    resp = client.get(
        "/v1/connectors/usage",
        params={"from": "2026-10-18", "to": "2026-10-19"},
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "start": "2026-10-18",
        "end": "2026-10-19",
        "items": [
            {
                "day": "2026-10-18",
                "connector": "sat_rfc",
                "calls": 1,
                "cost_micros": 1000,
                "outcomes": {"PASS": 1},
            },
            {
                "day": "2026-10-19",
                "connector": "banxico_sie",
                "calls": 1,
                "cost_micros": 0,
                "outcomes": {"PASS": 1},
            },
            {
                "day": "2026-10-19",
                "connector": "sat_rfc",
                "calls": 2,
                "cost_micros": 1000,
                "outcomes": {"ERROR": 1, "PASS": 1},
            },
        ],
    }


@pytest.mark.parametrize(
    "params",
    [
        {"from": "2026-10-19", "to": "2026-10-18"},
        {"from": "2025-01-01", "to": "2026-12-31"},
        {"to": "2026-10-19"},
        {"from": "soon", "to": "2026-10-19"},
    ],
)
def test_bad_usage_ranges_are_422(client: TestClient, admin_headers, params):
    resp = client.get("/v1/connectors/usage", params=params, headers=admin_headers)
    assert (resp.status_code, resp.json()["code"]) == (422, "VALIDATION_FAILED")


def test_a_range_of_exactly_366_days_is_accepted(client: TestClient, admin_headers):
    resp = client.get(
        "/v1/connectors/usage",
        params={"from": "2025-10-19", "to": "2026-10-19"},
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["items"] == []


def test_every_connector_route_is_admin_and_staff_only(
    client: TestClient, admin_headers, tenant: CreatedTenant
):
    analyst = _staff_headers(client, tenant, roles=[Role.ANALYST])
    auditor = _staff_headers(client, tenant, roles=[Role.AUDITOR_RO])
    machine = {"Authorization": f"Bearer {_api_key(tenant, scopes=['tenants:read'])}"}
    for headers in (analyst, auditor, machine):
        assert (
            client.get("/v1/connectors", headers=headers).json()["code"]
            == "ROLE_FORBIDDEN"
        )
        assert (
            client.get(
                "/v1/connectors/usage",
                params={"from": "2026-10-01", "to": "2026-10-02"},
                headers=headers,
            ).json()["code"]
            == "ROLE_FORBIDDEN"
        )
        assert (
            client.put(
                "/v1/connectors/sat_rfc/credentials/LIVE",
                json={"fields": {"token": "t"}},
                headers=headers,
            ).json()["code"]
            == "ROLE_FORBIDDEN"
        )
