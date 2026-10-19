"""TOTP across key rotation and destruction, through the real app (plan 1b, B7).

An operator rotates or destroys a tenant's keys with ``xolotl keys`` while staff keep
signing in. The command line is a process of its own, so every command here runs with
a key provider of its own and leaves the server's, cache and all, as it was: that is
the topology of a deployment, and the one in which a rotation or a destruction can be
missed by a cache. The sign-ins go over HTTP, the audit trail is read over HTTP, and
the database is the real one.
"""

import json
from collections.abc import Generator
from contextlib import contextmanager
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from typer.testing import CliRunner

from tests.conftest import ADMIN_PASSWORD
from tests.helpers import (
    audit_events,
    first_login,
    key_states,
    leaked,
    login_token,
    staff_session,
    wrapped_keys,
)
from xolotl.cli import app as cli
from xolotl.modules.tenants import service
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform.auth.totp import current_code
from xolotl.platform.config import get_settings
from xolotl.platform.crypto import (
    LocalKeyProvider,
    get_key_provider,
    parse_header,
    set_key_provider,
)
from xolotl.platform.db import get_sessionmaker
from xolotl.platform.enums import KeyPurpose, Role

DATA = KeyPurpose.DATA
OTHER_KEK = bytes(range(64, 96))
runner = CliRunner()


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@contextmanager
def _another_process() -> Generator[None]:
    """Run the block as a second process would, with a key provider of its own.

    The server keeps the provider it built, so what it has cached stays cached.
    """
    server = get_key_provider()
    set_key_provider(
        LocalKeyProvider(get_settings().master_key_bytes, get_sessionmaker())
    )
    try:
        yield
    finally:
        set_key_provider(server)


def _keys(*args: str) -> None:
    with _another_process():
        result = runner.invoke(cli, ["keys", *args])
    assert result.exit_code == 0, result.output


def _enrol(client: TestClient, headers: dict[str, str]) -> str:
    secret = client.post("/v1/auth/totp/enroll", headers=headers).json()["secret"]
    confirmed = client.post(
        "/v1/auth/totp/confirm", json={"code": current_code(secret)}, headers=headers
    )
    assert confirmed.status_code == 204
    return secret


def _password_step(client: TestClient, email: str, password: str):
    return client.post(
        "/v1/auth/login",
        json={"tenant_slug": "acme", "email": email, "password": password},
    )


def _totp_step(client: TestClient, mfa_token: str, code: str):
    return client.post(
        "/v1/auth/login/totp", json={"mfa_token": mfa_token, "code": code}
    )


# The column as the database holds it, past every type decorator.
def _stored_secret(admin_engine: Engine, user_id: UUID) -> bytes | None:
    with admin_engine.connect() as conn:
        stored = conn.scalar(
            text("SELECT totp_secret FROM staff_credential WHERE staff_user_id = :u"),
            {"u": user_id},
        )
    return None if stored is None else bytes(stored)


def _key_version_of(sealed: bytes | None) -> int:
    assert sealed is not None
    return parse_header(sealed)


def _failed_attempts(admin_engine: Engine, user_id: UUID) -> int:
    with admin_engine.connect() as conn:
        count = conn.scalar(
            text(
                "SELECT failed_attempts FROM staff_credential WHERE staff_user_id = :u"
            ),
            {"u": user_id},
        )
    assert count is not None
    return int(count)


# ---- rotation -------------------------------------------------------------------


def test_an_enrolled_user_still_signs_in_with_totp_after_a_key_rotation(
    client: TestClient, tenant: CreatedTenant, admin_headers, admin_engine: Engine
):
    secret = _enrol(client, admin_headers)
    sealed = _stored_secret(admin_engine, tenant.admin_user_id)
    assert _key_version_of(sealed) == 1
    _keys("rotate", "--tenant", "acme", "--purpose", "DATA")
    assert key_states(admin_engine, tenant.tenant_id) == [
        ("BIOMETRIC", 1, "current"),
        ("DATA", 1, "retired"),
        ("DATA", 2, "current"),
        ("INDEX", 1, "current"),
    ]
    pending = _password_step(client, tenant.admin_email, ADMIN_PASSWORD)
    assert pending.json()["mfa_required"] is True
    signed_in = _totp_step(client, pending.json()["mfa_token"], current_code(secret))
    assert signed_in.status_code == 200
    token = signed_in.json()["access_token"]
    assert client.get("/v1/auth/me", headers=_bearer(token)).status_code == 200
    # Nothing was re-encrypted: the secret still opens under the version it names.
    assert _stored_secret(admin_engine, tenant.admin_user_id) == sealed


def test_a_secret_enrolled_after_a_rotation_is_sealed_under_the_new_version(
    client: TestClient, tenant: CreatedTenant, admin_headers, admin_engine: Engine
):
    # The server has read the tenant's key, so its cache says version 1 is the current
    # one when the other process rotates. An enrolment does not go by the cache.
    _ = get_key_provider().data_key(tenant.tenant_id, DATA)
    _keys("rotate", "--tenant", "acme", "--purpose", "DATA")
    _keys("rotate", "--tenant", "acme", "--purpose", "DATA")
    secret = _enrol(client, admin_headers)
    assert _key_version_of(_stored_secret(admin_engine, tenant.admin_user_id)) == 3
    pending = _password_step(client, tenant.admin_email, ADMIN_PASSWORD)
    signed_in = _totp_step(client, pending.json()["mfa_token"], current_code(secret))
    assert signed_in.status_code == 200


def test_a_rotation_of_another_purpose_leaves_totp_alone(
    client: TestClient, tenant: CreatedTenant, admin_headers, admin_engine: Engine
):
    secret = _enrol(client, admin_headers)
    _keys("rotate", "--tenant", "acme", "--purpose", "BIOMETRIC")
    assert _key_version_of(_stored_secret(admin_engine, tenant.admin_user_id)) == 1
    pending = _password_step(client, tenant.admin_email, ADMIN_PASSWORD)
    signed_in = _totp_step(client, pending.json()["mfa_token"], current_code(secret))
    assert signed_in.status_code == 200


# ---- destruction ----------------------------------------------------------------


def test_after_a_key_destruction_the_totp_step_fails_closed_with_a_410(
    client: TestClient, tenant: CreatedTenant, admin_headers, admin_engine: Engine
):
    secret = _enrol(client, admin_headers)
    pending = _password_step(client, tenant.admin_email, ADMIN_PASSWORD)
    mfa_token = pending.json()["mfa_token"]
    # The server has read the tenant's key, so it holds it in memory when the other
    # process destroys it. The TOTP step does not rely on that copy.
    _ = get_key_provider().data_key(tenant.tenant_id, DATA)
    _keys("destroy", "--tenant", "acme", "--yes")
    for code in (current_code(secret), "000000"):  # a right code and a wrong one
        gone = _totp_step(client, mfa_token, code)
        assert gone.status_code == 410
        assert gone.headers["content-type"] == "application/problem+json"
        body = gone.json()
        assert (body["code"], body["title"]) == ("KEY_DESTROYED", "Gone")
        assert "access_token" not in body
    # A shredded tenant is not a wrong code: nothing was counted, nothing is locked.
    assert _failed_attempts(admin_engine, tenant.admin_user_id) == 0
    assert "auth.login_failed" not in {e.action for e in audit_events(tenant.tenant_id)}
    # The password step needs no key, and a session that already exists carries on.
    assert _password_step(client, tenant.admin_email, ADMIN_PASSWORD).status_code == 200
    assert client.get("/v1/auth/me", headers=admin_headers).status_code == 200


def test_after_a_key_destruction_a_user_without_totp_signs_in_with_a_password(
    client: TestClient, tenant: CreatedTenant, admin_headers, admin_engine: Engine
):
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        ana = service.create_staff(
            s, email="ana@acme.mx", display_name="Ana", roles=[Role.ANALYST]
        )
    password = "ana-long-new-password"
    _ = first_login(client, "acme", "ana@acme.mx", ana.initial_password, password)
    _ = _enrol(client, admin_headers)  # the admin has TOTP, Ana does not
    _keys("destroy", "--tenant", "acme", "--yes")
    token = login_token(client, "acme", "ana@acme.mx", password)
    me = client.get("/v1/auth/me", headers=_bearer(token))
    assert me.status_code == 200
    assert me.json()["totp_enabled"] is False
    # A secret nobody could ever open is not stored: enrolling fails closed.
    enrol = client.post("/v1/auth/totp/enroll", headers=_bearer(token))
    assert enrol.status_code == 410
    assert enrol.json()["code"] == "KEY_DESTROYED"
    assert _stored_secret(admin_engine, ana.id) is None
    # The admin, who does have TOTP, is the one who cannot finish signing in.
    pending = _password_step(client, tenant.admin_email, ADMIN_PASSWORD)
    assert pending.json()["mfa_required"] is True


# ---- the audit trail ------------------------------------------------------------


def test_the_audit_chain_records_a_rotation_and_a_destruction_with_ids_only(
    client: TestClient, tenant: CreatedTenant, admin_headers, admin_engine: Engine
):
    secret = _enrol(client, admin_headers)
    provider = get_key_provider()
    raw = [bytes(provider.data_key(tenant.tenant_id, p)[1]) for p in KeyPurpose]
    wrapped = wrapped_keys(admin_engine, tenant.tenant_id)
    _keys("rotate", "--tenant", "acme", "--purpose", "DATA")
    _keys("destroy", "--tenant", "acme", "--yes")
    listed = client.get("/v1/audit", params={"limit": 200}, headers=admin_headers)
    assert listed.status_code == 200
    events = listed.json()["items"]
    (rotated,) = (e for e in events if e["action"] == "tenant.key_rotated")
    (destroyed,) = (e for e in events if e["action"] == "tenant.keys_destroyed")
    for event in (rotated, destroyed):
        assert (event["entity_type"], event["entity_id"]) == (
            "tenant",
            str(tenant.tenant_id),
        )
        assert event["actor_type"] == "PLATFORM_OPERATOR"
        assert event["actor_id"].startswith("cli:")
    assert rotated["detail"] == {"purpose": "DATA", "version": 2}
    assert destroyed["detail"] is None
    assert rotated["seq"] < destroyed["seq"]
    # Ids only: a tenant, a purpose and a version, never a key or the secret it sealed.
    trail = json.dumps(events)
    assert secret not in trail
    assert leaked(trail, *raw, *wrapped) == []
    verified = client.get("/v1/audit/verify", headers=admin_headers).json()
    assert verified["valid"] is True


# ---- a server with another master key -------------------------------------------


def test_a_server_with_another_master_key_signs_nobody_in_and_counts_no_attempt(
    client: TestClient, tenant: CreatedTenant, admin_headers, admin_engine: Engine
):
    secret = _enrol(client, admin_headers)
    pending = _password_step(client, tenant.admin_email, ADMIN_PASSWORD)  # needs no key
    server = get_key_provider()
    set_key_provider(LocalKeyProvider(OTHER_KEK, get_sessionmaker()))
    try:
        refused = _totp_step(client, pending.json()["mfa_token"], current_code(secret))
    finally:
        set_key_provider(server)
    assert refused.status_code == 503
    assert refused.json()["code"] == "KEK_MISMATCH"
    assert "access_token" not in refused.json()
    assert _failed_attempts(admin_engine, tenant.admin_user_id) == 0
    # The same sign-in works again once the server has the right master key.
    again = _totp_step(client, pending.json()["mfa_token"], current_code(secret))
    assert again.status_code == 200
