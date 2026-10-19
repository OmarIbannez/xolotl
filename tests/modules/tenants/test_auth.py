from datetime import timedelta

from fastapi.testclient import TestClient

from tests.conftest import ADMIN_PASSWORD
from tests.helpers import (
    backdate_password_change,
    login_token,
    platform_ctx,
    staff_session,
)
from xolotl.modules.tenants import service
from xolotl.modules.tenants.models import Tenant
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform import clock
from xolotl.platform.auth.tokens import issue_token
from xolotl.platform.auth.totp import current_code
from xolotl.platform.db import tenant_session
from xolotl.platform.enums import Role, TenantStatus


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_login_returns_a_bearer_token(client: TestClient, tenant: CreatedTenant):
    resp = client.post(
        "/v1/auth/login",
        json={
            "tenant_slug": "acme",
            "email": tenant.admin_email,
            "password": tenant.admin_password,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == 28_800
    assert body["must_change_password"] is True
    me = client.get("/v1/auth/me", headers=_bearer(body["access_token"]))
    assert me.status_code == 200
    assert me.json()["tenant_slug"] == "acme"
    assert me.json()["roles"] == ["ADMIN"]
    assert me.json()["totp_enabled"] is False


def test_login_failures_are_401_problems_without_detail_leaks(
    client: TestClient, tenant: CreatedTenant
):
    for payload in (
        {"tenant_slug": "acme", "email": tenant.admin_email, "password": "wrong"},
        {"tenant_slug": "acme", "email": "ghost@acme.mx", "password": "wrong"},
        {
            "tenant_slug": "nope",
            "email": tenant.admin_email,
            "password": tenant.admin_password,
        },
    ):
        resp = client.post("/v1/auth/login", json=payload)
        assert resp.status_code == 401
        assert resp.json()["code"] == "UNAUTHENTICATED"
        assert resp.json()["detail"] == "invalid credentials"
    assert client.post("/v1/auth/login", json={"email": "x"}).status_code == 422


def test_me_requires_a_valid_unexpired_untampered_token(
    client: TestClient, tenant: CreatedTenant
):
    assert client.get("/v1/auth/me").status_code == 401
    token = login_token(client, "acme", tenant.admin_email, tenant.admin_password)
    assert (
        client.get("/v1/auth/me", headers=_bearer(token[:-3] + "xyz")).status_code
        == 401
    )
    expired = issue_token(
        subject=str(tenant.admin_user_id),
        tenant_id=tenant.tenant_id,
        tenant_slug="acme",
        roles=[Role.ADMIN],
        purpose="access",
        ttl=timedelta(seconds=-1),
    )
    assert client.get("/v1/auth/me", headers=_bearer(expired)).status_code == 401


def test_forced_password_change_gates_everything_but_the_auth_routes(
    client: TestClient, tenant: CreatedTenant, monkeypatch
):
    # Sign in half a minute "ago" against a credential stamped a minute ago, so the
    # change below lands in a later second than the login token's iat.
    backdate_password_change(tenant.tenant_id, tenant.admin_user_id, seconds=60)
    with monkeypatch.context() as earlier:
        then = clock.now() - timedelta(seconds=30)
        earlier.setattr(clock, "now", lambda: then)
        token = login_token(client, "acme", tenant.admin_email, tenant.admin_password)
    blocked = client.get("/v1/tenants/me", headers=_bearer(token))
    assert (blocked.status_code, blocked.json()["code"]) == (
        403,
        "PASSWORD_CHANGE_REQUIRED",
    )
    weak = client.post(
        "/v1/auth/password",
        json={"current_password": tenant.admin_password, "new_password": "short"},
        headers=_bearer(token),
    )
    assert weak.status_code == 422
    changed = client.post(
        "/v1/auth/password",
        json={
            "current_password": tenant.admin_password,
            "new_password": ADMIN_PASSWORD,
        },
        headers=_bearer(token),
    )
    assert changed.status_code == 200
    body = changed.json()
    assert (body["token_type"], body["expires_in"], body["must_change_password"]) == (
        "Bearer",
        28_800,
        False,
    )
    # The change ends the session it was made in, and every other earlier one...
    stale = client.get("/v1/tenants/me", headers=_bearer(token))
    assert (stale.status_code, stale.json()["code"]) == (401, "UNAUTHENTICATED")
    # ...while the token it returned carries on, with the gate lifted.
    fresh = _bearer(body["access_token"])
    assert client.get("/v1/tenants/me", headers=fresh).status_code == 200
    me = client.get("/v1/auth/me", headers=fresh)
    assert me.json()["must_change_password"] is False
    assert login_token(client, "acme", tenant.admin_email, ADMIN_PASSWORD)


def test_totp_enrolment_and_two_step_login_over_http(client: TestClient, admin_headers):
    enrolled = client.post("/v1/auth/totp/enroll", headers=admin_headers)
    assert enrolled.status_code == 200
    secret = enrolled.json()["secret"]
    assert enrolled.json()["otpauth_uri"].startswith("otpauth://totp/")
    bad = client.post(
        "/v1/auth/totp/confirm", json={"code": "000000"}, headers=admin_headers
    )
    assert bad.status_code == 422
    ok = client.post(
        "/v1/auth/totp/confirm",
        json={"code": current_code(secret)},
        headers=admin_headers,
    )
    assert ok.status_code == 204
    step1 = client.post(
        "/v1/auth/login",
        json={
            "tenant_slug": "acme",
            "email": "admin@acme.mx",
            "password": ADMIN_PASSWORD,
        },
    )
    assert step1.json()["mfa_required"] is True
    mfa_token = step1.json()["mfa_token"]
    wrong = client.post(
        "/v1/auth/login/totp", json={"mfa_token": mfa_token, "code": "000000"}
    )
    assert wrong.status_code == 401
    step2 = client.post(
        "/v1/auth/login/totp",
        json={"mfa_token": mfa_token, "code": current_code(secret)},
    )
    assert step2.status_code == 200
    access = step2.json()["access_token"]
    assert client.get("/v1/auth/me", headers=_bearer(access)).status_code == 200
    # An mfa token is not an access token.
    assert client.get("/v1/auth/me", headers=_bearer(mfa_token)).status_code == 401


def test_disabled_user_and_suspended_tenant_are_rejected_with_live_tokens(
    client: TestClient, tenant: CreatedTenant, admin_headers
):
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        analyst = service.create_staff(
            s, email="ana@acme.mx", display_name="Ana", roles=[Role.ANALYST]
        )
    token = login_token(client, "acme", "ana@acme.mx", analyst.initial_password)
    assert client.get("/v1/auth/me", headers=_bearer(token)).status_code == 200
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        _ = service.disable_staff(s, analyst.id)
    assert client.get("/v1/auth/me", headers=_bearer(token)).status_code == 401
    with tenant_session(platform_ctx()) as s:
        row = s.get(Tenant, tenant.tenant_id)
        assert row is not None
        row.status = TenantStatus.SUSPENDED
    suspended = client.get("/v1/tenants/me", headers=admin_headers)
    assert (suspended.status_code, suspended.json()["code"]) == (
        403,
        "TENANT_SUSPENDED",
    )


def test_tenants_me_and_ping(client: TestClient, tenant: CreatedTenant, admin_headers):
    me = client.get("/v1/tenants/me", headers=admin_headers)
    assert me.status_code == 200
    assert me.json()["slug"] == "acme"
    assert me.json()["env"] == "SANDBOX"
    assert me.json()["id"] == str(tenant.tenant_id)
    pong = client.post(
        "/v1/ping",
        json={"message": "hola"},
        headers={**admin_headers, "Idempotency-Key": "k-1"},
    )
    assert pong.status_code == 200
    assert pong.json()["pong"] == "hola"
    assert pong.json()["actor_id"] == str(tenant.admin_user_id)
