from fastapi.testclient import TestClient

from tests.helpers import first_login, login_token
from xolotl.modules.tenants.service import CreatedTenant


def _create(client: TestClient, headers, key: str, **body):
    payload = {
        "email": "ana@acme.mx",
        "display_name": "Ana",
        "roles": ["ANALYST"],
        **body,
    }
    return client.post(
        "/v1/staff", json=payload, headers={**headers, "Idempotency-Key": key}
    )


# Sign a new hire in and replace the generated password. Until they do, every
# guarded route answers PASSWORD_CHANGE_REQUIRED, which would mask the role rule.
def _first_login(client: TestClient, email: str, password: str) -> dict[str, str]:
    return first_login(client, "acme", email, password, "a-long-enough-password")


def test_admin_creates_lists_and_disables_staff(
    client: TestClient, tenant: CreatedTenant, admin_headers
):
    created = _create(client, admin_headers, "s1", roles=["ANALYST", "MLRO"])
    assert created.status_code == 201
    body = created.json()
    assert body["roles"] == ["ANALYST", "MLRO"]
    assert len(body["initial_password"]) >= 20
    assert login_token(client, "acme", "ana@acme.mx", body["initial_password"])

    listed = client.get("/v1/staff", headers=admin_headers).json()
    assert {item["email"] for item in listed["items"]} == {
        "admin@acme.mx",
        "ana@acme.mx",
    }
    assert all("initial_password" not in item for item in listed["items"])
    page = client.get("/v1/staff", params={"limit": 1}, headers=admin_headers).json()
    assert len(page["items"]) == 1
    assert page["next_cursor"]

    duplicate = _create(client, admin_headers, "s2", email="ANA@acme.mx")
    assert (duplicate.status_code, duplicate.json()["code"]) == (409, "CONFLICT")
    no_roles = _create(client, admin_headers, "s3", email="b@acme.mx", roles=[])
    assert no_roles.status_code == 422

    disabled = client.post(
        f"/v1/staff/{body['id']}/disable",
        headers={**admin_headers, "Idempotency-Key": "d1"},
    )
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "DISABLED"
    denied = client.post(
        "/v1/auth/login",
        json={
            "tenant_slug": "acme",
            "email": "ana@acme.mx",
            "password": body["initial_password"],
        },
    )
    assert denied.status_code == 401
    me = client.post(
        f"/v1/staff/{tenant.admin_user_id}/disable",
        headers={**admin_headers, "Idempotency-Key": "d2"},
    )
    assert (me.status_code, me.json()["code"]) == (409, "CONFLICT")
    missing = client.post(
        "/v1/staff/00000000-0000-7000-8000-000000000000/disable",
        headers={**admin_headers, "Idempotency-Key": "d3"},
    )
    assert missing.status_code == 404


def test_non_admins_cannot_manage_staff(
    client: TestClient, tenant: CreatedTenant, admin_headers
):
    auditor = _create(client, admin_headers, "s4", roles=["AUDITOR_RO"]).json()
    auditor_headers = _first_login(client, "ana@acme.mx", auditor["initial_password"])
    listed = client.get("/v1/staff", headers=auditor_headers)
    assert (listed.status_code, listed.json()["code"]) == (403, "ROLE_FORBIDDEN")
    denied = _create(client, auditor_headers, "s5", email="c@acme.mx")
    assert (denied.status_code, denied.json()["code"]) == (403, "ROLE_FORBIDDEN")

    # An MLRO is not an auditor, so the read-only ban that stops AUDITOR_RO on every
    # POST never applies to them: only the ADMIN-only rule can refuse these calls.
    mlro = _create(
        client, admin_headers, "s6", email="mlro@acme.mx", roles=["MLRO"]
    ).json()
    mlro_headers = _first_login(client, "mlro@acme.mx", mlro["initial_password"])
    listed = client.get("/v1/staff", headers=mlro_headers)
    assert (listed.status_code, listed.json()["code"]) == (403, "ROLE_FORBIDDEN")
    promoted = _create(
        client, mlro_headers, "s7", email="root@acme.mx", roles=["ADMIN"]
    )
    assert (promoted.status_code, promoted.json()["code"]) == (403, "ROLE_FORBIDDEN")
    disabled = client.post(
        f"/v1/staff/{tenant.admin_user_id}/disable",
        headers={**mlro_headers, "Idempotency-Key": "d4"},
    )
    assert (disabled.status_code, disabled.json()["code"]) == (403, "ROLE_FORBIDDEN")
