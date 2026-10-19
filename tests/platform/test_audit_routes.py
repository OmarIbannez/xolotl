from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from tests.helpers import first_login, staff_session
from xolotl.modules.tenants import service
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform.enums import Role


def _staff_headers(
    client: TestClient, tenant: CreatedTenant, email: str, roles: list[Role]
) -> dict[str, str]:
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        created = service.create_staff(s, email=email, display_name=email, roles=roles)
    return first_login(
        client, "acme", email, created.initial_password, "a-long-enough-password"
    )


def test_audit_list_is_newest_first_and_filterable(
    client: TestClient, tenant: CreatedTenant, admin_headers
):
    resp = client.get("/v1/audit", headers=admin_headers)
    assert resp.status_code == 200
    items = resp.json()["items"]
    seqs = [item["seq"] for item in items]
    assert seqs == sorted(seqs, reverse=True)
    assert items[0]["action"] == "auth.password_changed"
    assert len(items[0]["hash"]) == 64
    assert len(items[0]["prev_hash"]) == 64
    assert items[-1]["action"] == "tenant.created"
    filtered = client.get(
        "/v1/audit", params={"action": "auth.login_succeeded"}, headers=admin_headers
    ).json()
    assert {item["action"] for item in filtered["items"]} == {"auth.login_succeeded"}
    paged = client.get("/v1/audit", params={"limit": 2}, headers=admin_headers).json()
    assert len(paged["items"]) == 2
    assert paged["next_cursor"]


def test_audit_roles(client: TestClient, tenant: CreatedTenant, admin_headers):
    auditor = _staff_headers(client, tenant, "aud@acme.mx", [Role.AUDITOR_RO])
    mlro = _staff_headers(client, tenant, "mlro@acme.mx", [Role.MLRO])
    analyst = _staff_headers(client, tenant, "ana@acme.mx", [Role.ANALYST])
    assert client.get("/v1/audit", headers=auditor).status_code == 200
    assert client.get("/v1/audit/verify", headers=mlro).status_code == 200
    denied = client.get("/v1/audit", headers=analyst)
    assert (denied.status_code, denied.json()["code"]) == (403, "ROLE_FORBIDDEN")
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        key = service.create_api_key(s, name="x", scopes=["audit:read"])
    machine = client.get("/v1/audit", headers={"Authorization": f"Bearer {key.key}"})
    assert machine.status_code == 403


def test_verify_reports_a_tampered_row(
    client: TestClient, tenant: CreatedTenant, admin_headers, admin_engine: Engine
):
    ok = client.get("/v1/audit/verify", headers=admin_headers).json()
    assert ok["valid"] is True
    assert ok["entries_checked"] >= 3
    with admin_engine.begin() as conn:
        _ = conn.execute(text("SET LOCAL session_replication_role = replica"))
        _ = conn.execute(
            text(
                """
                UPDATE audit_event SET action = 'forged'
                WHERE tenant_id = :t AND seq = 1
                """
            ),
            {"t": tenant.tenant_id},
        )
    broken = client.get("/v1/audit/verify", headers=admin_headers).json()
    assert broken["valid"] is False
    assert broken["first_broken_seq"] == 1
