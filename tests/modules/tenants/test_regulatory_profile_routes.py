"""The regulatory profile and the branch catalogue: encrypted, masked, audited."""

import logging
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select

from tests.helpers import audit_events, everything_stored, leaked, system_ctx
from tests.modules.tenants.policy_support import staff_headers
from xolotl.modules.tenants.models import TenantRegulatoryProfile
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform.db import tenant_session
from xolotl.platform.enums import Role

PROFILE = "/v1/tenants/me/regulatory-profile"
BRANCHES = "/v1/tenants/me/branches"
ACME = {
    "rfc": "acm010101ab1",
    "legal_name": "Acme SA de CV",
    "casfim_supervisor": "CNBV",
    "entidad_colegiada": False,
}
MATRIZ = {
    "code": "MAT",
    "name": "Matriz",
    "address": "Av. Reforma 1, CDMX",
    "state_code": "09",
    "municipality_code": "015",
}


def _profile_count(tenant_id: UUID) -> int:
    with tenant_session(system_ctx(tenant_id)) as s:
        return s.scalar(select(func.count()).select_from(TenantRegulatoryProfile)) or 0


def _details(tenant_id: UUID, action: str) -> list[object]:
    return [event.detail for event in audit_events(tenant_id) if event.action == action]


def test_the_profile_is_set_masked_and_replaced_in_place(
    client: TestClient,
    tenant: CreatedTenant,
    admin_headers,
    admin_engine: Engine,
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.DEBUG, logger="sqlalchemy.engine")
    assert client.get(PROFILE, headers=admin_headers).status_code == 404

    resp = client.put(PROFILE, json=ACME, headers=admin_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["rfc_masked"] == "****1AB1"
    assert "rfc" not in body
    assert body["legal_name"] == "Acme SA de CV"
    assert body["entidad_colegiada"] is False

    read = client.get(PROFILE, headers=admin_headers)
    assert read.status_code == 200
    assert read.json() == body

    renamed = client.put(
        PROFILE,
        json={**ACME, "legal_name": "Acme Pagos SA de CV"},
        headers=admin_headers,
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["id"] == body["id"]
    assert renamed.json()["legal_name"] == "Acme Pagos SA de CV"
    assert renamed.json()["updated_at"] > body["updated_at"]
    assert renamed.json()["created_at"] == body["created_at"]
    assert _profile_count(tenant.tenant_id) == 1

    assert _details(tenant.tenant_id, "tenant.regulatory_profile_set")[0] == {
        "fields": ["casfim_supervisor", "legal_name"]
    }
    assert leaked(everything_stored(admin_engine), b"ACM010101AB1") == []
    assert "ACM010101AB1" not in caplog.text
    assert "acm010101ab1" not in caplog.text


@pytest.mark.parametrize(
    "change", [{"rfc": "nope"}, {"compliance_officer_user_id": str(uuid4())}]
)
def test_a_bad_rfc_or_an_unknown_officer_is_422(
    client: TestClient, admin_headers, change: dict[str, str]
):
    resp = client.put(PROFILE, json={**ACME, **change}, headers=admin_headers)
    assert (resp.status_code, resp.json()["code"]) == (422, "VALIDATION_FAILED")


def test_the_admin_can_be_the_compliance_officer(
    client: TestClient, tenant: CreatedTenant, admin_headers
):
    officer = str(tenant.admin_user_id)
    resp = client.put(
        PROFILE,
        json={**ACME, "compliance_officer_user_id": officer},
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["compliance_officer_user_id"] == officer


def test_only_an_admin_sets_the_profile_or_adds_a_branch(
    client: TestClient, tenant: CreatedTenant, admin_headers
):
    analyst = staff_headers(client, tenant, role=Role.ANALYST)
    put = client.put(PROFILE, json=ACME, headers=analyst)
    assert (put.status_code, put.json()["code"]) == (403, "ROLE_FORBIDDEN")
    post = client.post(
        BRANCHES, json=MATRIZ, headers={**analyst, "Idempotency-Key": "b1"}
    )
    assert (post.status_code, post.json()["code"]) == (403, "ROLE_FORBIDDEN")
    _ = client.put(PROFILE, json=ACME, headers=admin_headers)
    assert client.get(PROFILE, headers=analyst).status_code == 200
    assert client.get(BRANCHES, headers=analyst).status_code == 200


def test_branches_are_created_without_showing_the_address(
    client: TestClient, tenant: CreatedTenant, admin_headers, admin_engine: Engine
):
    resp = client.post(
        BRANCHES, json=MATRIZ, headers={**admin_headers, "Idempotency-Key": "b1"}
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "ACTIVE"
    assert "address" not in body
    assert (body["code"], body["state_code"], body["municipality_code"]) == (
        "MAT",
        "09",
        "015",
    )

    again = client.post(
        BRANCHES, json=MATRIZ, headers={**admin_headers, "Idempotency-Key": "b2"}
    )
    assert (again.status_code, again.json()["code"]) == (409, "CONFLICT")

    listed = client.get(BRANCHES, headers=admin_headers).json()
    assert [item["code"] for item in listed["items"]] == ["MAT"]
    assert "address" not in listed["items"][0]

    assert leaked(everything_stored(admin_engine), b"Av. Reforma 1") == []
    assert _details(tenant.tenant_id, "tenant.branch_created") == [{"code": "MAT"}]
