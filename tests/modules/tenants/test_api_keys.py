import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from tests.helpers import first_login, new_tenant, staff_session, system_ctx
from xolotl.modules.tenants import service
from xolotl.modules.tenants.models import ApiKey
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform.audit.models import AuditEventRow
from xolotl.platform.db import tenant_session
from xolotl.platform.enums import Role


def _create(client: TestClient, headers, key: str, **body):
    payload = {"name": "ingest", "scopes": ["tenants:read"], **body}
    return client.post(
        "/v1/api-keys", json=payload, headers={**headers, "Idempotency-Key": key}
    )


def test_admin_creates_lists_and_revokes_keys(client: TestClient, admin_headers):
    created = _create(client, admin_headers, "k1")
    assert created.status_code == 201
    body = created.json()
    assert body["key"].startswith("xk_sbx_")
    assert body["key_prefix"] == body["key"][:15]
    listed = client.get("/v1/api-keys", headers=admin_headers).json()
    assert [item["key_prefix"] for item in listed["items"]] == [body["key_prefix"]]
    assert "key" not in listed["items"][0]
    assert listed["items"][0]["status"] == "ACTIVE"
    revoked = client.post(
        f"/v1/api-keys/{body['id']}/revoke",
        headers={**admin_headers, "Idempotency-Key": "r1"},
    )
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "REVOKED"
    again = client.post(
        f"/v1/api-keys/{body['id']}/revoke",
        headers={**admin_headers, "Idempotency-Key": "r2"},
    )
    assert again.status_code == 200
    missing = client.post(
        "/v1/api-keys/00000000-0000-7000-8000-000000000000/revoke",
        headers={**admin_headers, "Idempotency-Key": "r3"},
    )
    assert missing.status_code == 404


def test_validation_and_env_rules(client: TestClient, admin_headers):
    bad_scope = _create(client, admin_headers, "k2", scopes=["Tenants:Read"])
    assert (bad_scope.status_code, bad_scope.json()["code"]) == (
        422,
        "VALIDATION_FAILED",
    )
    wrong_env = _create(client, admin_headers, "k3", env="PROD")
    assert (wrong_env.status_code, wrong_env.json()["code"]) == (409, "CONFLICT")
    empty = _create(client, admin_headers, "k4", scopes=[])
    assert empty.status_code == 422


def test_only_admins_manage_keys(
    client: TestClient, tenant: CreatedTenant, admin_headers
):
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        analyst = service.create_staff(
            s, email="ana@acme.mx", display_name="Ana", roles=[Role.ANALYST]
        )
    headers = first_login(
        client, "acme", "ana@acme.mx", analyst.initial_password, "another-long-password"
    )
    denied = _create(client, headers, "k5")
    assert (denied.status_code, denied.json()["code"]) == (403, "ROLE_FORBIDDEN")
    assert client.get("/v1/api-keys", headers=headers).status_code == 403


def test_keys_authenticate_machines_within_their_scope(
    client: TestClient, admin_headers
):
    read_key = _create(client, admin_headers, "k6").json()["key"]
    machine = {"Authorization": f"Bearer {read_key}"}
    assert client.get("/v1/tenants/me", headers=machine).status_code == 200
    denied = client.post(
        "/v1/ping", json={}, headers={**machine, "Idempotency-Key": "p1"}
    )
    assert (denied.status_code, denied.json()["code"]) == (403, "SCOPE_MISSING")
    staff_only = client.get("/v1/api-keys", headers=machine)
    assert (staff_only.status_code, staff_only.json()["code"]) == (
        403,
        "ROLE_FORBIDDEN",
    )
    write_key = _create(client, admin_headers, "k7", scopes=["tenants:write"]).json()[
        "key"
    ]
    pong = client.post(
        "/v1/ping",
        json={},
        headers={"Authorization": f"Bearer {write_key}", "Idempotency-Key": "p2"},
    )
    assert pong.status_code == 200


def test_revoked_and_foreign_keys_are_rejected_and_do_not_touch_last_used(
    client: TestClient, tenant: CreatedTenant, admin_headers
):
    issued = _create(client, admin_headers, "k8").json()
    machine = {"Authorization": f"Bearer {issued['key']}"}
    assert client.get("/v1/tenants/me", headers=machine).status_code == 200
    _ = client.post(
        f"/v1/api-keys/{issued['id']}/revoke",
        headers={**admin_headers, "Idempotency-Key": "r9"},
    )

    def last_used():
        with tenant_session(system_ctx(tenant.tenant_id)) as s:
            return s.scalars(
                select(ApiKey.last_used_at).where(
                    ApiKey.key_prefix == issued["key_prefix"]
                )
            ).one()

    before = last_used()
    assert before is not None
    assert client.get("/v1/tenants/me", headers=machine).status_code == 401
    assert last_used() == before

    other = new_tenant(slug="beta", email="admin@beta.mx")
    with staff_session(other.tenant_id, other.admin_user_id) as s:
        foreign = service.create_api_key(s, name="x", scopes=["tenants:read"])
    seen = client.get(
        "/v1/tenants/me", headers={"Authorization": f"Bearer {foreign.key}"}
    )
    assert seen.status_code == 200
    assert seen.json()["slug"] == "beta"  # a key only ever sees its own tenant
    unknown_prefix = "xk_sbx_00000000_" + "0" * 32
    assert (
        client.get(
            "/v1/tenants/me", headers={"Authorization": f"Bearer {unknown_prefix}"}
        ).status_code
        == 401
    )


def test_list_pagination_bounds_and_cursors(client: TestClient, admin_headers):
    for n in range(3):
        assert (
            _create(client, admin_headers, f"pk{n}", name=f"key-{n}").status_code == 201
        )
    assert (
        client.get(
            "/v1/api-keys", params={"limit": 0}, headers=admin_headers
        ).status_code
        == 422
    )
    assert (
        client.get(
            "/v1/api-keys", params={"limit": 201}, headers=admin_headers
        ).status_code
        == 422
    )
    first = client.get(
        "/v1/api-keys", params={"limit": 2}, headers=admin_headers
    ).json()
    assert len(first["items"]) == 2
    assert first["next_cursor"]
    second = client.get(
        "/v1/api-keys",
        params={"limit": 2, "cursor": first["next_cursor"]},
        headers=admin_headers,
    ).json()
    assert len(second["items"]) == 1
    assert second["next_cursor"] is None
    names = [i["name"] for i in first["items"] + second["items"]]
    assert sorted(names) == ["key-0", "key-1", "key-2"]
    bad = client.get(
        "/v1/api-keys", params={"cursor": "garbage"}, headers=admin_headers
    )
    assert (bad.status_code, bad.json()["code"]) == (422, "VALIDATION_FAILED")


@pytest.fixture
def analyst_headers(client: TestClient, tenant: CreatedTenant) -> dict[str, str]:
    """Bearer headers for an ANALYST once the forced password change is done.

    Returns:
        A mapping holding the analyst's ``Authorization`` header.

    """
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        analyst = service.create_staff(
            s, email="ana@acme.mx", display_name="Ana", roles=[Role.ANALYST]
        )
    return first_login(
        client, "acme", "ana@acme.mx", analyst.initial_password, "another-long-password"
    )


def test_machine_keys_and_analysts_are_refused_on_every_key_route(
    client: TestClient, admin_headers, analyst_headers
):
    # Create and revoke need the ADMIN role as much as list does: a route guarded by
    # a scope instead would let a leaked write key mint or revoke keys.
    issued = _create(
        client, admin_headers, "k9", scopes=["tenants:read", "tenants:write"]
    ).json()
    machine = {"Authorization": f"Bearer {issued['key']}"}
    for who, headers in (("machine", machine), ("analyst", analyst_headers)):
        created = _create(client, headers, f"{who}-create")
        listed = client.get("/v1/api-keys", headers=headers)
        revoked = client.post(
            f"/v1/api-keys/{issued['id']}/revoke",
            headers={**headers, "Idempotency-Key": f"{who}-revoke"},
        )
        outcomes = [
            (r.status_code, r.json()["code"]) for r in (created, listed, revoked)
        ]
        assert outcomes == [(403, "ROLE_FORBIDDEN")] * 3, who
    # The refusals had no effect: the key still works and no other key was minted.
    assert client.get("/v1/tenants/me", headers=machine).status_code == 200
    keys = client.get("/v1/api-keys", headers=admin_headers).json()["items"]
    assert [(key["id"], key["status"]) for key in keys] == [(issued["id"], "ACTIVE")]


def test_revoking_twice_changes_nothing_the_second_time(
    client: TestClient, tenant: CreatedTenant, admin_headers
):
    issued = _create(client, admin_headers, "k10").json()
    revoke = f"/v1/api-keys/{issued['id']}/revoke"
    first = client.post(revoke, headers={**admin_headers, "Idempotency-Key": "r10"})
    second = client.post(revoke, headers={**admin_headers, "Idempotency-Key": "r11"})
    assert first.json()["revoked_at"] is not None
    assert second.json() == first.json()  # not stamped again
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        actions = list(
            s.scalars(
                select(AuditEventRow.action)
                .where(AuditEventRow.entity_id == issued["id"])
                .order_by(AuditEventRow.seq)
            )
        )
    assert actions == ["api_key.created", "api_key.revoked"]  # audited once
