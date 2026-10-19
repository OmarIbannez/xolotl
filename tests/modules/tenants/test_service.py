import json
import logging
import re
import time
from collections.abc import Callable, Generator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from datetime import timedelta
from functools import partial
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from tests.helpers import (
    SessionBoundProvider,
    audit_events,
    auth_session,
    backdate_password_change,
    first_login,
    holding_key_rows,
    key_states,
    leaked,
    login_token,
    make_tenant,
    make_tenant_with_keys,
    new_tenant,
    platform_ctx,
    staff_session,
    system_ctx,
    wrapped_keys,
)
from xolotl.app import build_app, build_key_provider
from xolotl.modules.tenants import service
from xolotl.modules.tenants.models import (
    ApiKey,
    ApiKeyLookup,
    StaffCredential,
    StaffUser,
    Tenant,
)
from xolotl.platform import clock
from xolotl.platform.audit import AuditEvent, partitions, record, verify_chain
from xolotl.platform.audit.models import AuditEventRow
from xolotl.platform.auth import passwords
from xolotl.platform.auth.principal import StaffDep
from xolotl.platform.auth.tokens import decode_token
from xolotl.platform.auth.totp import current_code
from xolotl.platform.config import get_settings
from xolotl.platform.crypto import (
    KeyDestroyedError,
    KeyNotFoundError,
    KeyProvider,
    LocalKeyProvider,
    TenantKey,
    aad_for,
    decrypt_value,
    encrypt_value,
    get_key_provider,
    parse_header,
    set_key_provider,
)
from xolotl.platform.crypto.encrypted import Encrypted, encrypt
from xolotl.platform.crypto.errors import DecryptionError
from xolotl.platform.crypto.primitives import HEADER_LEN, NONCE_LEN, TAG_LEN
from xolotl.platform.db import (
    apply_context,
    get_engine,
    get_sessionmaker,
    tenant_session,
)
from xolotl.platform.enums import (
    ActorType,
    ApiKeyStatus,
    KeyPurpose,
    Role,
    StaffStatus,
    TenantEnv,
    TenantStatus,
)
from xolotl.platform.errors import (
    ContextMissingError,
    DomainError,
    ForbiddenError,
    UnauthorizedError,
    ValidationFailedError,
)
from xolotl.platform.tenancy.context import TenantContext, bind_context


def _login(slug: str, email: str, password: str) -> service.LoginOutcome:
    with auth_session() as s:
        return service.login(s, tenant_slug=slug, email=email, password=password)


def _actions(tenant_id) -> list[str]:
    with tenant_session(system_ctx(tenant_id)) as s:
        return list(s.scalars(select(AuditEventRow.action).order_by(AuditEventRow.seq)))


def _credential(tenant_id, user_id) -> StaffCredential:
    with tenant_session(system_ctx(tenant_id)) as s:
        return s.scalars(
            select(StaffCredential).where(StaffCredential.staff_user_id == user_id)
        ).one()


# --- tenants -----------------------------------------------------------------


def test_create_tenant_creates_an_admin_with_a_one_time_password(engine: Engine):
    created = new_tenant()
    assert len(created.admin_password) >= 20
    with tenant_session(platform_ctx()) as s:
        tenant = s.get(Tenant, created.tenant_id)
        assert tenant is not None
        assert (tenant.slug, tenant.env, tenant.status) == (
            "acme",
            TenantEnv.SANDBOX,
            TenantStatus.ACTIVE,
        )
    with tenant_session(system_ctx(created.tenant_id)) as s:
        admin = s.get(StaffUser, (created.tenant_id, created.admin_user_id))
        assert admin is not None
        assert (admin.email, admin.roles, admin.must_change_password) == (
            "admin@acme.mx",
            ["ADMIN"],
            True,
        )
    assert _actions(created.tenant_id) == [
        "tenant.created",
        "tenant.keys_created",
        "staff.created",
    ]


def test_create_tenant_normalises_slug_and_email_and_rejects_duplicates(engine: Engine):
    created = service.create_tenant(
        get_sessionmaker(),
        slug=" Acme-Fintech ",
        legal_name="Acme",
        country_code="mx",
        admin_email=" Admin@Acme.MX ",
    )
    assert (created.slug, created.admin_email) == ("acme-fintech", "admin@acme.mx")
    with pytest.raises(DomainError, match="already exists"):
        _ = new_tenant(slug="acme-fintech")
    with pytest.raises(ValidationFailedError):
        _ = new_tenant(slug="a")
    with pytest.raises(ValidationFailedError):
        _ = new_tenant(slug="ok-slug", email="not-an-email")


# --- tenant keys -------------------------------------------------------------


def test_create_tenant_gives_the_tenant_its_keys(engine: Engine):
    created = new_tenant()
    with tenant_session(system_ctx(created.tenant_id)) as s:
        keys = s.scalars(select(TenantKey).order_by(TenantKey.purpose)).all()
        assert [(key.purpose, key.version) for key in keys] == [
            (KeyPurpose.BIOMETRIC, 1),
            (KeyPurpose.DATA, 1),
            (KeyPurpose.INDEX, 1),
        ]
        # Each names the master key that wrapped it (test_kek_ref.py pins which).
        assert all(re.fullmatch(r"local:v1:[0-9a-f]{16}", key.kek_ref) for key in keys)
        assert all(key.wrapped_dek is not None for key in keys)
        assert all(key.retired_at is None and key.destroyed_at is None for key in keys)
    # They are the tenant's working keys, not placeholders.
    value = encrypt("hola", tenant_id=created.tenant_id)
    assert value.reveal() == "hola"
    assert (value.purpose, value.version) == (KeyPurpose.DATA, 1)


def test_create_tenant_audits_its_keys_without_naming_them(engine: Engine):
    created = new_tenant()
    provider = get_key_provider()
    key_bytes = [
        provider.data_key(created.tenant_id, purpose)[1] for purpose in KeyPurpose
    ]
    with tenant_session(system_ctx(created.tenant_id)) as s:
        events = s.scalars(select(AuditEventRow).order_by(AuditEventRow.seq)).all()
        assert [(e.seq, e.action) for e in events] == [
            (1, "tenant.created"),
            (2, "tenant.keys_created"),
            (3, "staff.created"),
        ]
        keyed = events[1]
        assert (keyed.entity_type, keyed.entity_id) == (
            "tenant",
            str(created.tenant_id),
        )
        assert keyed.detail == {"purposes": ["DATA", "INDEX", "BIOMETRIC"]}
        assert (keyed.actor_type, keyed.actor_id) == ("PLATFORM_OPERATOR", "cli:xolotl")
        chain = verify_chain(s)
        assert (chain.valid, chain.entries_checked) == (True, 3)
        wrapped = [key.wrapped_dek for key in s.scalars(select(TenantKey))]
    trail = json.dumps([
        (e.action, e.entity_id, e.detail, e.hash.hex()) for e in events
    ])
    assert leaked(trail, *key_bytes, *(w for w in wrapped if w is not None)) == []


@pytest.fixture
def broken_keys(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    """Leave the installed provider unable to create keys."""

    def refuse(tenant_id, *, session=None):
        raise KeyNotFoundError

    monkeypatch.setattr(get_key_provider(), "create_tenant_keys", refuse)


COUNT_ROWS = {
    "tenant": "SELECT count(*) FROM tenant",
    "tenant_key": "SELECT count(*) FROM tenant_key",
    "staff_user": "SELECT count(*) FROM staff_user",
    "staff_credential": "SELECT count(*) FROM staff_credential",
    "audit_event": "SELECT count(*) FROM audit_event",
}


# Every row the creation of a tenant writes, counted past row-level security.
def _rows_in_the_database(admin_engine: Engine) -> dict[str, int]:
    with admin_engine.connect() as conn:
        return {
            table: cast("int", conn.scalar(text(statement)))
            for table, statement in COUNT_ROWS.items()
        }


def test_a_tenant_is_not_created_when_its_keys_cannot_be(
    engine: Engine, admin_engine: Engine, broken_keys: None
):
    with pytest.raises(KeyNotFoundError):
        _ = new_tenant()
    assert set(_rows_in_the_database(admin_engine).values()) == {0}


def test_a_tenant_is_not_created_without_a_key_provider(
    engine: Engine, admin_engine: Engine, no_key_provider: None
):
    with pytest.raises(RuntimeError, match="key provider not configured"):
        _ = new_tenant()
    assert set(_rows_in_the_database(admin_engine).values()) == {0}


# --- login -------------------------------------------------------------------


def test_login_success_issues_an_access_token_with_db_roles(engine: Engine):
    created = new_tenant()
    outcome = _login("acme", "admin@acme.mx", created.admin_password)
    assert outcome.mfa_required is False
    assert outcome.must_change_password is True
    assert outcome.expires_in == 480 * 60
    claims = decode_token(outcome.token, purpose="access")
    assert (claims.sub, claims.tenant_slug, claims.roles) == (
        str(created.admin_user_id),
        "acme",
        [Role.ADMIN],
    )
    assert _actions(created.tenant_id)[-1] == "auth.login_succeeded"


def test_login_normalises_slug_and_email(engine: Engine):
    created = new_tenant()
    assert _login(" ACME ", "Admin@ACME.mx ", created.admin_password).token


def test_login_failures_are_generic_and_persist_bookkeeping(engine: Engine):
    created = new_tenant()
    for slug, email, password in (
        ("nope", "admin@acme.mx", created.admin_password),
        ("acme", "ghost@acme.mx", created.admin_password),
        ("acme", "admin@acme.mx", "wrong-password"),
    ):
        with pytest.raises(UnauthorizedError, match="invalid credentials"):
            _ = _login(slug, email, password)
    assert _credential(created.tenant_id, created.admin_user_id).failed_attempts == 1
    # Only the attempt that matched a user is attributable; unknown slugs and
    # unknown emails leave no audit row because there is nobody to attribute to.
    assert _actions(created.tenant_id).count("auth.login_failed") == 1


def test_ten_failures_lock_the_account_for_fifteen_minutes(engine: Engine, monkeypatch):
    created = new_tenant()
    for _ in range(service.MAX_FAILED_ATTEMPTS):
        with pytest.raises(UnauthorizedError):
            _ = _login("acme", "admin@acme.mx", "wrong")
    cred = _credential(created.tenant_id, created.admin_user_id)
    assert cred.locked_until is not None
    assert cred.failed_attempts == 0
    assert "auth.locked" in _actions(created.tenant_id)
    with pytest.raises(UnauthorizedError):  # right password, still locked
        _ = _login("acme", "admin@acme.mx", created.admin_password)
    real_now = clock.now()
    monkeypatch.setattr(
        clock, "now", lambda: real_now + service.LOCKOUT + timedelta(seconds=1)
    )
    assert _login("acme", "admin@acme.mx", created.admin_password).token


def test_concurrent_failures_are_counted_exactly(engine: Engine):
    created = new_tenant()
    attempts = 6  # fewer than MAX_FAILED_ATTEMPTS, so the account must not lock

    def wrong_login(_attempt: int) -> None:
        with pytest.raises(UnauthorizedError):
            _ = _login("acme", "admin@acme.mx", "wrong")

    with ThreadPoolExecutor(max_workers=attempts) as pool:
        _ = list(pool.map(wrong_login, range(attempts)))
    cred = _credential(created.tenant_id, created.admin_user_id)
    assert cred.failed_attempts == attempts
    assert cred.locked_until is None


def _fastest_refusals(*attempts: Callable[[], object], rounds: int = 3) -> list[float]:
    # The fastest of a few refused attempts per path: the floor of what each path
    # costs. Every round times each path once, so a load burst slows them alike.
    timings: list[list[float]] = [[] for _ in attempts]
    for _ in range(rounds):
        for attempt, taken in zip(attempts, timings, strict=True):
            start = time.perf_counter()
            with pytest.raises(UnauthorizedError, match="invalid credentials"):
                _ = attempt()
            taken.append(time.perf_counter() - start)
    return [min(taken) for taken in timings]


@pytest.mark.parametrize(
    "path", ["unknown-slug", "unknown-email", "disabled-user", "locked-account"]
)
def test_early_login_failures_cost_about_as_much_as_a_wrong_password(
    engine: Engine, path: str
):
    created = new_tenant()
    t, u = created.tenant_id, created.admin_user_id
    with staff_session(t, u) as s:
        ana = service.create_staff(
            s, email="ana@acme.mx", display_name="Ana", roles=[Role.ANALYST]
        )
    if path == "disabled-user":
        with staff_session(t, u) as s:
            _ = service.disable_staff(s, ana.id)
    if path == "locked-account":
        with tenant_session(system_ctx(t)) as s:
            credential = s.scalars(
                select(StaffCredential).where(StaffCredential.staff_user_id == ana.id)
            ).one()
            credential.locked_until = clock.now() + timedelta(hours=1)
    slug, email = {
        "unknown-slug": ("nope", "ana@acme.mx"),
        "unknown-email": ("acme", "ghost@acme.mx"),
    }.get(path, ("acme", "ana@acme.mx"))
    early, wrong = _fastest_refusals(
        lambda: _login(slug, email, ana.initial_password),
        lambda: _login("acme", "admin@acme.mx", "wrong-pass"),
    )
    # A loose bound that still catches an early return: without the dummy argon2id
    # verification an early exit costs a few round trips, a fraction of a real one.
    assert early >= 0.5 * wrong, f"{path}: {early:.4f}s vs wrong password {wrong:.4f}s"


def test_login_on_suspended_tenant_is_forbidden(engine: Engine):
    created = new_tenant()
    with tenant_session(platform_ctx()) as s:
        tenant = s.get(Tenant, created.tenant_id)
        assert tenant is not None
        tenant.status = TenantStatus.SUSPENDED
    with pytest.raises(ForbiddenError) as exc:
        _ = _login("acme", "admin@acme.mx", created.admin_password)
    assert exc.value.code == "TENANT_SUSPENDED"


# --- totp --------------------------------------------------------------------


def test_totp_enrolment_then_two_step_login(engine: Engine):
    created = new_tenant()
    t, u = created.tenant_id, created.admin_user_id
    with staff_session(t, u) as s:
        enrolled = service.enroll_totp(s)
        assert "otpauth://totp/" in enrolled.otpauth_uri
        with pytest.raises(ValidationFailedError):
            service.confirm_totp(s, code="000000")
        service.confirm_totp(s, code=current_code(enrolled.secret))
        assert service.me(s).totp_enabled is True
    first = _login("acme", "admin@acme.mx", created.admin_password)
    assert first.mfa_required is True
    assert decode_token(first.token, purpose="mfa").purpose == "mfa"
    with auth_session() as s, pytest.raises(UnauthorizedError):
        _ = service.login_totp(s, mfa_token=first.token, code="000000")
    assert _credential(t, u).failed_attempts == 1
    with auth_session() as s:
        final = service.login_totp(
            s, mfa_token=first.token, code=current_code(enrolled.secret)
        )
    assert final.mfa_required is False
    assert decode_token(final.token, purpose="access").sub == str(u)
    assert _credential(t, u).failed_attempts == 0
    with (
        auth_session() as s,
        pytest.raises(UnauthorizedError),
    ):  # access token is not an mfa token
        _ = service.login_totp(
            s, mfa_token=final.token, code=current_code(enrolled.secret)
        )
    assert "auth.totp_enrolled" in _actions(t)


# The totp_secret column as the database holds it, past every type decorator.
def _stored_totp_secret(admin_engine: Engine, user_id) -> bytes | None:
    with admin_engine.connect() as conn:
        return cast(
            "bytes | None",
            conn.scalar(
                text(
                    """
                    SELECT totp_secret FROM staff_credential
                    WHERE staff_user_id = :user_id
                    """
                ),
                {"user_id": user_id},
            ),
        )


def _enrolled_and_confirmed(created: service.CreatedTenant) -> str:
    with staff_session(created.tenant_id, created.admin_user_id) as s:
        secret = service.enroll_totp(s).secret
        service.confirm_totp(s, code=current_code(secret))
    return secret


def test_the_totp_secret_is_stored_encrypted_under_the_tenants_data_key(
    engine: Engine, admin_engine: Engine
):
    created = new_tenant()
    t, u = created.tenant_id, created.admin_user_id
    with staff_session(t, u) as s:
        secret = service.enroll_totp(s).secret
    stored = _stored_totp_secret(admin_engine, u)
    assert stored is not None
    # Version header, nonce, ciphertext and tag: none of the base32 text is there.
    assert stored[:HEADER_LEN] == b"\x00\x01"
    assert len(stored) == HEADER_LEN + NONCE_LEN + len(secret) + TAG_LEN
    assert secret.encode() not in stored
    # Read raw through the tenant's own session, past the column type, it is the same.
    with tenant_session(system_ctx(t)) as s:
        raw = s.scalar(
            text("SELECT totp_secret FROM staff_credential WHERE staff_user_id = :u"),
            {"u": u},
        )
    assert raw is not None
    assert bytes(raw) == stored
    # It opens with the tenant's DATA key, for this tenant and purpose only.
    _, dek = get_key_provider().data_key(t, KeyPurpose.DATA)
    opened = decrypt_value(dek, ciphertext=stored, aad=aad_for(t, KeyPurpose.DATA))
    assert opened == secret.encode()
    other = new_tenant(slug="beta", email="admin@beta.mx").tenant_id
    _, other_dek = get_key_provider().data_key(other, KeyPurpose.DATA)
    with pytest.raises(DecryptionError):  # another tenant's key does not open it
        _ = decrypt_value(
            other_dek, ciphertext=stored, aad=aad_for(other, KeyPurpose.DATA)
        )
    with pytest.raises(DecryptionError):  # nor does its own key, for another tenant
        _ = decrypt_value(dek, ciphertext=stored, aad=aad_for(other, KeyPurpose.DATA))
    # The application holds an opaque value that reveals to the same secret.
    loaded = _credential(t, u).totp_secret
    assert isinstance(loaded, Encrypted)
    assert repr(loaded) == "Encrypted(***)"
    assert loaded.reveal() == secret


def test_enrolling_again_replaces_the_pending_secret(
    engine: Engine, admin_engine: Engine
):
    created = new_tenant()
    t, u = created.tenant_id, created.admin_user_id
    with staff_session(t, u) as s:
        first = service.enroll_totp(s).secret
    before = _stored_totp_secret(admin_engine, u)
    with staff_session(t, u) as s:
        second = service.enroll_totp(s).secret
    assert second != first
    assert _stored_totp_secret(admin_engine, u) != before
    loaded = _credential(t, u).totp_secret
    assert loaded is not None
    assert loaded.reveal() == second  # and only the new one counts


def test_enrolment_stores_nothing_for_a_tenant_without_keys(
    engine: Engine, admin_engine: Engine
):
    t = make_tenant("no-keys")
    with staff_session(t, uuid4()) as s:
        member = service.create_staff(
            s, email="ana@nokeys.mx", display_name="Ana", roles=[Role.ADMIN]
        )
    with staff_session(t, member.id) as s, pytest.raises(KeyNotFoundError):
        _ = service.enroll_totp(s)
    assert _stored_totp_secret(admin_engine, member.id) is None


def test_confirming_a_totp_code_needs_the_tenants_key(engine: Engine):
    created = new_tenant()
    t, u = created.tenant_id, created.admin_user_id
    with staff_session(t, u) as s:
        secret = service.enroll_totp(s).secret
    get_key_provider().destroy(t)
    # A destroyed key is not a wrong code: the error says so and nothing is enabled.
    with staff_session(t, u) as s, pytest.raises(KeyDestroyedError):
        service.confirm_totp(s, code=current_code(secret))
    assert _credential(t, u).totp_enabled is False


def test_a_destroyed_key_ends_the_second_login_step_without_counting_an_attempt(
    engine: Engine,
):
    created = new_tenant()
    t, u = created.tenant_id, created.admin_user_id
    secret = _enrolled_and_confirmed(created)
    pending = _login("acme", "admin@acme.mx", created.admin_password)
    get_key_provider().destroy(t)
    with auth_session() as s, pytest.raises(KeyDestroyedError):
        _ = service.login_totp(s, mfa_token=pending.token, code=current_code(secret))
    assert _credential(t, u).failed_attempts == 0
    assert "auth.login_failed" not in _actions(t)


# Another process destroys a tenant's keys. It has a provider, and so a key cache, of
# its own: the provider installed here keeps whatever it had cached.
def _elsewhere_destroys(tenant_id) -> None:
    other = LocalKeyProvider(get_settings().master_key_bytes, get_sessionmaker())
    other.destroy(tenant_id)


# The TOTP steps read the tenant's key through the session that is working on the
# credential, so a destruction committed by another process stops them at once. Read
# through the provider's cache instead, a shredded tenant would go on verifying codes
# until its entry expired.
def test_the_second_login_step_sees_a_destruction_committed_elsewhere(engine: Engine):
    created = new_tenant()
    t = created.tenant_id
    secret = _enrolled_and_confirmed(created)
    pending = _login("acme", "admin@acme.mx", created.admin_password)
    _ = get_key_provider().data_key(t, KeyPurpose.DATA)  # warm this process's cache
    _elsewhere_destroys(t)
    assert get_key_provider().data_key(t, KeyPurpose.DATA)[0] == 1  # the premise: stale
    with auth_session() as s, pytest.raises(KeyDestroyedError):
        _ = service.login_totp(s, mfa_token=pending.token, code=current_code(secret))


def test_confirming_totp_sees_a_destruction_committed_elsewhere(engine: Engine):
    created = new_tenant()
    t, u = created.tenant_id, created.admin_user_id
    with staff_session(t, u) as s:
        secret = service.enroll_totp(s).secret
    _ = get_key_provider().data_key(t, KeyPurpose.DATA)  # warm this process's cache
    _elsewhere_destroys(t)
    assert get_key_provider().data_key(t, KeyPurpose.DATA)[0] == 1  # the premise: stale
    with staff_session(t, u) as s, pytest.raises(KeyDestroyedError):
        service.confirm_totp(s, code=current_code(secret))
    assert _credential(t, u).totp_enabled is False


# A provider that has cached nothing: a key read that is not handed a session opens one
# of its own, and so takes a second connection while the request holds the first.
@pytest.fixture
def cold_key_cache(engine: Engine) -> Generator[None]:
    previous = get_key_provider()
    set_key_provider(build_key_provider(get_settings(), get_sessionmaker()))
    yield
    set_key_provider(previous)


# The connections checked out of the application's pool inside the block.
@contextmanager
def _checkouts() -> Generator[list[int]]:
    taken: list[int] = []

    def counting(*_args: object) -> None:
        taken.append(1)

    event.listen(get_engine(), "checkout", counting)
    try:
        yield taken
    finally:
        event.remove(get_engine(), "checkout", counting)


# Every request holds a connection while it works; a second one per login would starve
# the pool under load (each request holding one and waiting for another).
def test_the_second_login_step_takes_one_connection(
    engine: Engine, cold_key_cache: None
):
    created = new_tenant()
    secret = _enrolled_and_confirmed(created)
    pending = _login("acme", "admin@acme.mx", created.admin_password)
    with _checkouts() as taken, auth_session() as s:
        done = service.login_totp(s, mfa_token=pending.token, code=current_code(secret))
    assert done.mfa_required is False
    assert len(taken) == 1


def test_confirming_totp_takes_one_connection(engine: Engine, cold_key_cache: None):
    created = new_tenant()
    t, u = created.tenant_id, created.admin_user_id
    with staff_session(t, u) as s:
        secret = service.enroll_totp(s).secret
    with _checkouts() as taken, staff_session(t, u) as s:
        service.confirm_totp(s, code=current_code(secret))
    assert len(taken) == 1
    assert _credential(t, u).totp_enabled is True


def test_the_login_steps_record_their_events_as_the_system_actor_auth(engine: Engine):
    created = new_tenant()
    t = created.tenant_id
    secret = _enrolled_and_confirmed(created)
    with pytest.raises(UnauthorizedError):
        _ = _login("acme", "admin@acme.mx", "wrong-password")
    pending = _login("acme", "admin@acme.mx", created.admin_password)
    with auth_session() as s, pytest.raises(UnauthorizedError):
        _ = service.login_totp(s, mfa_token=pending.token, code="000000")
    with auth_session() as s:
        _ = service.login_totp(s, mfa_token=pending.token, code=current_code(secret))
    with tenant_session(system_ctx(t)) as s:
        events = s.execute(
            select(
                AuditEventRow.action, AuditEventRow.actor_type, AuditEventRow.actor_id
            )
            .where(
                AuditEventRow.action.in_(["auth.login_failed", "auth.login_succeeded"])
            )
            .order_by(AuditEventRow.seq)
        ).all()
    # Not the platform operator that opened the session: the system actor of ``auth``.
    assert [tuple(event) for event in events] == [
        ("auth.login_failed", "SYSTEM", "auth"),  # the wrong password
        ("auth.login_failed", "SYSTEM", "auth"),  # the wrong code
        ("auth.login_succeeded", "SYSTEM", "auth"),
    ]


def test_a_wrong_totp_code_spends_a_dummy_verification_like_every_other_refusal(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
):
    created = new_tenant()
    secret = _enrolled_and_confirmed(created)
    pending = _login("acme", "admin@acme.mx", created.admin_password)
    spent: list[str] = []
    real = passwords.verify_dummy

    def counting(attempt: str) -> None:
        spent.append(attempt)
        real(attempt)

    monkeypatch.setattr(service, "verify_dummy", counting)
    with auth_session() as s, pytest.raises(UnauthorizedError):
        _ = service.login_totp(s, mfa_token=pending.token, code="000000")
    # Without it this refusal would answer faster than the others and tell a wrong
    # code from a wrong password, an unknown user or a locked account.
    assert spent == ["000000"]
    with auth_session() as s:
        _ = service.login_totp(s, mfa_token=pending.token, code=current_code(secret))
    assert spent == ["000000"]  # a right code spends none


@contextmanager
def _auth_session_prepared(*, bound: bool) -> Generator[Session]:
    # What the route's auth session does: the platform context goes onto the session
    # with apply_context, and nothing binds it as the current context. The other
    # shape binds a context that names no tenant, as tenant_session does.
    session = get_sessionmaker()()
    try:
        with bind_context(service.auth_context()) if bound else nullcontext():
            apply_context(session, service.auth_context())
            yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


@pytest.mark.parametrize(
    "bound", [False, True], ids=["applied-only", "bound-no-tenant"]
)
def test_both_login_steps_load_the_encrypted_secret_however_the_session_is_bound(
    engine: Engine, *, bound: bool
):
    # Loading an encrypted value needs the tenant as the *current* context, which
    # apply_context does not set: the steps bind it once they know the tenant.
    created = new_tenant()
    secret = _enrolled_and_confirmed(created)
    with _auth_session_prepared(bound=bound) as s:
        pending = service.login(
            s,
            tenant_slug="acme",
            email="admin@acme.mx",
            password=created.admin_password,
        )
    assert pending.mfa_required is True
    with _auth_session_prepared(bound=bound) as s:
        done = service.login_totp(s, mfa_token=pending.token, code=current_code(secret))
    assert done.mfa_required is False


# --- password change ---------------------------------------------------------


def test_change_password_rules(engine: Engine):
    created = new_tenant()
    t, u = created.tenant_id, created.admin_user_id
    with staff_session(t, u) as s:
        with pytest.raises(ValidationFailedError, match="current password"):
            _ = service.change_password(
                s, current_password="nope", new_password="a-long-new-password"
            )
        with pytest.raises(ValidationFailedError, match="12 characters"):
            _ = service.change_password(
                s, current_password=created.admin_password, new_password="short"
            )
        outcome = service.change_password(
            s,
            current_password=created.admin_password,
            new_password="a-long-new-password",
        )
        assert service.me(s).must_change_password is False
    # The change hands back a fresh access token for the session that made it.
    assert (outcome.mfa_required, outcome.must_change_password) == (False, False)
    assert outcome.expires_in == 480 * 60
    claims = decode_token(outcome.token, purpose="access")
    assert (claims.sub, claims.roles) == (str(u), [Role.ADMIN])
    assert (
        _login("acme", "admin@acme.mx", "a-long-new-password").must_change_password
        is False
    )
    with pytest.raises(UnauthorizedError):
        _ = _login("acme", "admin@acme.mx", created.admin_password)
    assert "auth.password_changed" in _actions(t)


def test_a_password_change_ends_every_earlier_session(engine: Engine, monkeypatch):
    created = new_tenant()
    t, u = created.tenant_id, created.admin_user_id
    # Sign in half a minute "ago" against a credential stamped a minute ago, so the
    # change below lands in a later second than the old token's iat.
    backdate_password_change(t, u, seconds=60)
    with monkeypatch.context() as earlier:
        then = clock.now() - timedelta(seconds=30)
        earlier.setattr(clock, "now", lambda: then)
        old = _login("acme", "admin@acme.mx", created.admin_password).token
    assert service.resolve_bearer(get_sessionmaker(), old).context.actor_id == str(u)
    with staff_session(t, u) as s:
        outcome = service.change_password(
            s,
            current_password=created.admin_password,
            new_password="a-long-new-password",
        )
    with pytest.raises(UnauthorizedError):  # still signed and unexpired, but stale
        _ = service.resolve_bearer(get_sessionmaker(), old)
    actor = service.resolve_bearer(get_sessionmaker(), outcome.token)
    assert (actor.context.actor_id, actor.must_change_password) == (str(u), False)


def test_a_password_change_ends_a_pending_mfa_step(engine: Engine, monkeypatch):
    created = new_tenant()
    t, u = created.tenant_id, created.admin_user_id
    with staff_session(t, u) as s:
        secret = service.enroll_totp(s).secret
        service.confirm_totp(s, code=current_code(secret))
    # The password step half a minute "ago", against a credential stamped a minute
    # ago, so the change below lands in a later second than the MFA token's iat.
    backdate_password_change(t, u, seconds=60)
    with monkeypatch.context() as earlier:
        then = clock.now() - timedelta(seconds=30)
        earlier.setattr(clock, "now", lambda: then)
        pending = _login("acme", "admin@acme.mx", created.admin_password)
    assert pending.mfa_required is True
    with staff_session(t, u) as s:
        _ = service.change_password(
            s,
            current_password=created.admin_password,
            new_password="a-long-new-password",
        )
    with (
        auth_session() as s,
        pytest.raises(UnauthorizedError, match="invalid credentials"),
    ):  # signed and unexpired, but issued before the change
        _ = service.login_totp(s, mfa_token=pending.token, code=current_code(secret))
    with tenant_session(system_ctx(t)) as s:
        last = s.scalars(
            select(AuditEventRow).order_by(AuditEventRow.seq.desc()).limit(1)
        ).one()
        assert (last.action, last.detail) == (
            "auth.login_failed",
            {"reason": "password_changed"},
        )
    fresh = _login("acme", "admin@acme.mx", "a-long-new-password")
    with auth_session() as s:
        done = service.login_totp(s, mfa_token=fresh.token, code=current_code(secret))
    assert done.mfa_required is False


# --- staff -------------------------------------------------------------------


def test_create_and_disable_staff(engine: Engine):
    created = new_tenant()
    t, u = created.tenant_id, created.admin_user_id
    with staff_session(t, u) as s:
        analyst = service.create_staff(
            s, email="Ana@Acme.MX", display_name="Ana", roles=[Role.ANALYST, Role.MLRO]
        )
        assert analyst.email == "ana@acme.mx"
        assert analyst.roles == [Role.ANALYST, Role.MLRO]
        assert len(analyst.initial_password) >= 20
        with pytest.raises(DomainError, match="already exists"):
            _ = service.create_staff(
                s, email="ANA@acme.mx", display_name="Dup", roles=[Role.ANALYST]
            )
        with pytest.raises(DomainError, match="own account"):
            _ = service.disable_staff(s, u)
        disabled = service.disable_staff(s, analyst.id)
        assert disabled.status is StaffStatus.DISABLED
        assert (
            service.disable_staff(s, analyst.id).status is StaffStatus.DISABLED
        )  # idempotent
    assert _actions(t)[-2:] == ["staff.created", "staff.disabled"]
    with pytest.raises(UnauthorizedError):
        _ = _login("acme", "ana@acme.mx", analyst.initial_password)


def test_resolve_bearer_rechecks_user_and_tenant_status(engine: Engine):
    created = new_tenant()
    t, u = created.tenant_id, created.admin_user_id
    with staff_session(t, u) as s:
        analyst = service.create_staff(
            s, email="ana@acme.mx", display_name="Ana", roles=[Role.ANALYST]
        )
    token = _login("acme", "ana@acme.mx", analyst.initial_password).token
    actor = service.resolve_bearer(get_sessionmaker(), token)
    assert actor.context.actor_type is ActorType.STAFF
    assert actor.context.roles == frozenset({Role.ANALYST})
    assert actor.must_change_password is True
    with staff_session(t, u) as s:
        _ = service.disable_staff(s, analyst.id)
    with pytest.raises(UnauthorizedError):  # still-valid JWT, disabled user
        _ = service.resolve_bearer(get_sessionmaker(), token)
    admin_token = _login("acme", "admin@acme.mx", created.admin_password).token
    with tenant_session(platform_ctx()) as s:
        tenant = s.get(Tenant, t)
        assert tenant is not None
        tenant.status = TenantStatus.SUSPENDED
    with pytest.raises(ForbiddenError) as exc:
        _ = service.resolve_bearer(get_sessionmaker(), admin_token)
    assert exc.value.code == "TENANT_SUSPENDED"
    with pytest.raises(UnauthorizedError):
        _ = service.resolve_bearer(get_sessionmaker(), "not-a-token")


def test_resolve_bearer_takes_roles_from_the_row_not_the_token(engine: Engine):
    created = new_tenant()
    t, u = created.tenant_id, created.admin_user_id
    with staff_session(t, u) as s:
        analyst = service.create_staff(
            s, email="ana@acme.mx", display_name="Ana", roles=[Role.ANALYST]
        )
    token = _login("acme", "ana@acme.mx", analyst.initial_password).token
    assert decode_token(token, purpose="access").roles == [Role.ANALYST]
    with tenant_session(system_ctx(t)) as s:
        row = s.get(StaffUser, (t, analyst.id))
        assert row is not None
        row.roles = ["ADMIN", "MLRO"]
    # The token still claims ANALYST; the actor follows the row.
    actor = service.resolve_bearer(get_sessionmaker(), token)
    assert actor.context.roles == frozenset({Role.ADMIN, Role.MLRO})


# --- api keys ----------------------------------------------------------------


def test_api_key_lifecycle(engine: Engine, monkeypatch):
    created = new_tenant()
    t, u = created.tenant_id, created.admin_user_id
    with staff_session(t, u) as s:
        issued = service.create_api_key(
            s, name="ingest", scopes=["tenants:write", "tenants:read"]
        )
        with pytest.raises(DomainError, match="env"):
            _ = service.create_api_key(
                s, name="x", scopes=["tenants:read"], env=TenantEnv.PROD
            )
        with pytest.raises(ValidationFailedError):
            _ = service.create_api_key(s, name="x", scopes=["Tenants:Read"])
    assert re.fullmatch(r"xk_sbx_[0-9a-f]{8}_[0-9a-f]{32}", issued.key)
    assert len(issued.key) == 48
    assert issued.key_prefix == issued.key[:15]
    assert issued.scopes == ["tenants:read", "tenants:write"]
    with tenant_session(platform_ctx()) as s:
        lookup = s.get(ApiKeyLookup, issued.key_prefix)
        assert lookup is not None
        assert lookup.tenant_id == t

    actor = service.resolve_bearer(get_sessionmaker(), issued.key)
    assert actor.context.actor_type is ActorType.API_CLIENT
    assert actor.context.tenant_id == t
    assert actor.context.actor_id == str(issued.id)
    assert actor.scopes == frozenset({"tenants:read", "tenants:write"})

    def last_used():
        with tenant_session(system_ctx(t)) as s:
            row = s.get(ApiKey, (t, issued.id))
            assert row is not None
            return row.last_used_at

    first_seen = last_used()
    assert first_seen is not None
    _ = service.resolve_bearer(get_sessionmaker(), issued.key)
    assert last_used() == first_seen  # not bumped within a minute
    monkeypatch.setattr(clock, "now", lambda: first_seen + timedelta(minutes=2))
    _ = service.resolve_bearer(get_sessionmaker(), issued.key)
    assert last_used() == first_seen + timedelta(minutes=2)

    forged = issued.key[:-4] + ("0000" if not issued.key.endswith("0000") else "1111")
    with pytest.raises(UnauthorizedError):
        _ = service.resolve_bearer(get_sessionmaker(), forged)
    with pytest.raises(UnauthorizedError):
        _ = service.resolve_bearer(get_sessionmaker(), "xk_sbx_short")

    with staff_session(t, u) as s:
        revoked = service.revoke_api_key(s, issued.id)
        assert revoked.status is ApiKeyStatus.REVOKED
        assert revoked.revoked_at is not None
    before = last_used()
    with pytest.raises(UnauthorizedError):
        _ = service.resolve_bearer(get_sessionmaker(), issued.key)
    assert last_used() == before
    assert _actions(t)[-2:] == ["api_key.created", "api_key.revoked"]


# --- tenant keys -------------------------------------------------------------
# Rotation and destruction run as the CLI runs them: a platform-operator session bound
# to the tenant. TOTP secrets are still plaintext at this point of plan 1b, so a value
# sealed under the tenant's DATA key stands in for one, sealed and opened the way
# Encrypted does it: the current version to seal, the version in the header to open.

DATA, INDEX, BIOMETRIC = KeyPurpose.DATA, KeyPurpose.INDEX, KeyPurpose.BIOMETRIC


@pytest.fixture
def installed_provider(engine: Engine) -> Generator[LocalKeyProvider]:
    # The service reads the process-wide provider, as the app and the CLI install it.
    try:
        before: KeyProvider | None = get_key_provider()
    except RuntimeError:
        before = None
    # It refuses to rotate or destroy outside the service's session, so a service that
    # forgot to pass it fails at once instead of waiting for its own row locks.
    provider = SessionBoundProvider(get_settings().master_key_bytes, get_sessionmaker())
    set_key_provider(provider)
    yield provider
    set_key_provider(before)


def _operator(tenant_id: UUID):
    return tenant_session(TenantContext.platform("cli:test", tenant_id=tenant_id))


def _rotate(tenant_id: UUID, purpose: KeyPurpose) -> int:
    with _operator(tenant_id) as s:
        return service.rotate_tenant_key(s, purpose)


def _destroy(tenant_id: UUID) -> None:
    with _operator(tenant_id) as s:
        service.destroy_tenant_keys(s)


def _destroyed_stamps(admin_engine: Engine, tenant_id: UUID) -> list[object]:
    with admin_engine.connect() as conn:
        return list(
            conn.scalars(
                text(
                    """
                    SELECT destroyed_at FROM tenant_key
                    WHERE tenant_id = :tenant_id ORDER BY purpose, version
                    """
                ),
                {"tenant_id": tenant_id},
            )
        )


def _seal(tenant_id: UUID, plaintext: bytes) -> bytes:
    version, dek = get_key_provider().data_key(tenant_id, DATA)
    return encrypt_value(
        dek, version=version, plaintext=plaintext, aad=aad_for(tenant_id, DATA)
    )


def _open(tenant_id: UUID, sealed: bytes) -> bytes:
    _, dek = get_key_provider().data_key(tenant_id, DATA, parse_header(sealed))
    return decrypt_value(dek, ciphertext=sealed, aad=aad_for(tenant_id, DATA))


def test_rotating_a_key_adds_the_next_version_and_audits_it(
    installed_provider: LocalKeyProvider, admin_engine: Engine
):
    tenant_id = make_tenant_with_keys("acme")
    assert _rotate(tenant_id, DATA) == 2
    (event,) = audit_events(tenant_id)
    assert (event.action, event.entity_type, event.entity_id) == (
        "tenant.key_rotated",
        "tenant",
        str(tenant_id),
    )
    assert event.detail == {"purpose": "DATA", "version": 2}
    assert (event.actor_type, event.actor_id) == ("PLATFORM_OPERATOR", "cli:test")
    assert key_states(admin_engine, tenant_id) == [
        ("BIOMETRIC", 1, "current"),
        ("DATA", 1, "retired"),
        ("DATA", 2, "current"),
        ("INDEX", 1, "current"),
    ]
    with _operator(tenant_id) as s:
        assert verify_chain(s).valid


def test_values_sealed_before_a_rotation_still_open_and_later_ones_use_the_new_key(
    installed_provider: LocalKeyProvider,
):
    tenant_id = make_tenant_with_keys("acme")
    before = _seal(tenant_id, b"enrolled before")
    assert parse_header(before) == 1
    _ = _rotate(tenant_id, DATA)
    after = _seal(tenant_id, b"enrolled after")
    assert parse_header(after) == 2  # the cached version 1 did not outlive the commit
    assert _open(tenant_id, before) == b"enrolled before"
    assert _open(tenant_id, after) == b"enrolled after"


def test_rotation_is_per_purpose_and_every_rotation_is_audited(
    installed_provider: LocalKeyProvider,
):
    tenant_id = make_tenant_with_keys("acme")
    assert [_rotate(tenant_id, p) for p in (DATA, DATA, BIOMETRIC)] == [2, 3, 2]
    assert [event.detail for event in audit_events(tenant_id)] == [
        {"purpose": "DATA", "version": 2},
        {"purpose": "DATA", "version": 3},
        {"purpose": "BIOMETRIC", "version": 2},
    ]
    assert installed_provider.data_key(tenant_id, DATA)[0] == 3
    assert installed_provider.data_key(tenant_id, BIOMETRIC)[0] == 2
    assert installed_provider.data_key(tenant_id, INDEX)[0] == 1


def test_index_keys_are_never_rotated(
    installed_provider: LocalKeyProvider, admin_engine: Engine
):
    tenant_id = make_tenant_with_keys("acme")
    with pytest.raises(DomainError) as refused:
        _ = _rotate(tenant_id, INDEX)
    assert refused.value.code == "CONFLICT"
    assert "INDEX keys are never rotated" in refused.value.detail
    assert audit_events(tenant_id) == []
    assert ("INDEX", 1, "current") in key_states(admin_engine, tenant_id)
    assert len(key_states(admin_engine, tenant_id)) == len(KeyPurpose)


def test_a_tenant_without_keys_has_nothing_to_rotate(
    installed_provider: LocalKeyProvider,
):
    bare = make_tenant("bare")
    with pytest.raises(KeyNotFoundError):
        _ = _rotate(bare, DATA)
    assert audit_events(bare) == []


def test_destroying_keys_audits_it_and_makes_every_key_unreadable_for_good(
    installed_provider: LocalKeyProvider, admin_engine: Engine
):
    tenant_id = make_tenant_with_keys("acme")
    bystander = make_tenant_with_keys("other")
    _ = _rotate(tenant_id, DATA)
    sealed = _seal(tenant_id, b"secret")  # reads the keys, so they are cached
    assert [installed_provider.data_key(tenant_id, p)[0] for p in KeyPurpose] == [
        2,
        1,
        1,
    ]
    _destroy(tenant_id)
    events = audit_events(tenant_id)
    assert [(e.action, e.entity_type, e.entity_id) for e in events] == [
        ("tenant.key_rotated", "tenant", str(tenant_id)),
        ("tenant.keys_destroyed", "tenant", str(tenant_id)),
    ]
    assert events[-1].detail is None
    states = key_states(admin_engine, tenant_id)
    assert len(states) == len(KeyPurpose) + 1  # the retired version 1 of DATA too
    assert {state for _, _, state in states} == {"destroyed"}
    assert wrapped_keys(admin_engine, tenant_id) == []
    for purpose in KeyPurpose:  # the cached keys went with the commit
        with pytest.raises(KeyDestroyedError):
            _ = installed_provider.data_key(tenant_id, purpose)
    with pytest.raises(KeyDestroyedError):
        _ = installed_provider.data_key(tenant_id, DATA, 1)
    with pytest.raises(KeyDestroyedError):
        _ = _open(tenant_id, sealed)
    with _operator(tenant_id) as s:
        assert verify_chain(s).valid
    # Another tenant keeps everything.
    assert {s for _, _, s in key_states(admin_engine, bystander)} == {"current"}
    assert installed_provider.data_key(bystander, DATA)[0] == 1
    assert audit_events(bystander) == []


def test_the_audit_event_is_recorded_before_the_keys_are_destroyed(
    installed_provider: LocalKeyProvider, monkeypatch: pytest.MonkeyPatch
):
    tenant_id = make_tenant_with_keys("acme")
    seen: list[list[str]] = []
    destroy = installed_provider.destroy

    def watching(tenant: UUID, *, session: Session | None = None) -> None:
        assert session is not None  # the caller's transaction, not one of its own
        seen.append(list(session.scalars(select(AuditEventRow.action))))
        destroy(tenant, session=session)

    monkeypatch.setattr(installed_provider, "destroy", watching)
    _destroy(tenant_id)
    assert seen == [["tenant.keys_destroyed"]]


def test_a_failed_destruction_rolls_back_its_audit_event_too(
    installed_provider: LocalKeyProvider,
    admin_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
):
    tenant_id = make_tenant_with_keys("acme")
    destroy = installed_provider.destroy

    def failing(tenant: UUID, *, session: Session | None = None) -> None:
        destroy(tenant, session=session)  # the keys are gone inside the transaction
        msg = "the connection was lost"
        raise RuntimeError(msg)

    monkeypatch.setattr(installed_provider, "destroy", failing)
    with pytest.raises(RuntimeError, match="connection was lost"):
        _destroy(tenant_id)
    assert audit_events(tenant_id) == []
    assert {s for _, _, s in key_states(admin_engine, tenant_id)} == {"current"}
    assert installed_provider.data_key(tenant_id, DATA)[0] == 1


@pytest.mark.parametrize("operation", ["rotate", "destroy"])
def test_an_operation_whose_audit_fails_leaves_the_keys_as_they_were(
    installed_provider: LocalKeyProvider,
    admin_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
):
    tenant_id = make_tenant_with_keys("acme")

    def failing(_session: Session, _event: AuditEvent) -> None:
        msg = "the audit trail is unavailable"
        raise RuntimeError(msg)

    monkeypatch.setattr(service, "record", failing)
    with pytest.raises(RuntimeError, match="audit trail is unavailable"):
        if operation == "rotate":
            _ = _rotate(tenant_id, DATA)
        else:
            _destroy(tenant_id)
    assert key_states(admin_engine, tenant_id) == [
        ("BIOMETRIC", 1, "current"),
        ("DATA", 1, "current"),
        ("INDEX", 1, "current"),
    ]
    assert installed_provider.data_key(tenant_id, DATA)[0] == 1


def test_a_destroyed_tenant_cannot_rotate_and_the_refusal_is_a_410(
    installed_provider: LocalKeyProvider,
):
    tenant_id = make_tenant_with_keys("acme")
    _destroy(tenant_id)
    for purpose in (DATA, BIOMETRIC):
        with pytest.raises(KeyDestroyedError) as refused:
            _ = _rotate(tenant_id, purpose)
        assert (refused.value.status, refused.value.code) == (410, "KEY_DESTROYED")
    assert [e.action for e in audit_events(tenant_id)] == ["tenant.keys_destroyed"]


def test_a_tenant_without_keys_has_nothing_to_destroy_and_nothing_is_audited(
    installed_provider: LocalKeyProvider,
):
    bare = make_tenant("bare")
    with pytest.raises(KeyNotFoundError, match="no encryption keys"):
        _destroy(bare)
    assert audit_events(bare) == []


def test_destroying_twice_is_harmless_and_each_request_is_audited(
    installed_provider: LocalKeyProvider, admin_engine: Engine
):
    tenant_id = make_tenant_with_keys("acme")
    _destroy(tenant_id)
    first = _destroyed_stamps(admin_engine, tenant_id)
    assert len(first) == len(KeyPurpose)
    assert None not in first
    _destroy(tenant_id)
    assert _destroyed_stamps(admin_engine, tenant_id) == first  # the first one stands
    assert [event.action for event in audit_events(tenant_id)] == [
        "tenant.keys_destroyed"
    ] * 2


@pytest.mark.parametrize("status", list(TenantStatus))
def test_keys_can_be_rotated_and_destroyed_whatever_the_tenant_status(
    installed_provider: LocalKeyProvider, admin_engine: Engine, status: TenantStatus
):
    tenant_id = make_tenant_with_keys("acme", status=status)
    assert _rotate(tenant_id, BIOMETRIC) == 2
    _destroy(tenant_id)
    assert {s for _, _, s in key_states(admin_engine, tenant_id)} == {"destroyed"}


def test_both_operations_need_a_session_bound_to_a_tenant(
    installed_provider: LocalKeyProvider,
):
    with pytest.raises(ContextMissingError), tenant_session(platform_ctx()) as s:
        _ = service.rotate_tenant_key(s, DATA)
    with pytest.raises(ContextMissingError), tenant_session(platform_ctx()) as s:
        service.destroy_tenant_keys(s)


def _wait_for_a_blocked_statement(admin_engine: Engine) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        with admin_engine.connect() as conn:
            blocked = conn.scalar(
                text(
                    """
                    SELECT count(*) FROM pg_stat_activity
                    WHERE wait_event_type = 'Lock' AND datname = current_database()
                    """
                )
            )
        if blocked:
            return
        time.sleep(0.02)
    pytest.fail("no statement is waiting for a lock")


def test_destroy_takes_the_key_rows_before_the_audit_lock_like_rotate_does(
    installed_provider: LocalKeyProvider, admin_engine: Engine
):
    # Rotate locks the purpose's rows first and takes the tenant's audit lock when it
    # records. A destroy that took the audit lock first would wait on those rows while
    # holding what rotate needs next: a deadlock between two operators.
    tenant_id = make_tenant_with_keys("acme")
    with ThreadPoolExecutor(max_workers=1) as pool:
        with _operator(tenant_id) as rotating:
            _ = rotating.execute(
                select(TenantKey.id).where(TenantKey.purpose == DATA).with_for_update()
            ).all()
            destroying = pool.submit(_destroy, tenant_id)
            _wait_for_a_blocked_statement(admin_engine)
            _ = rotating.execute(text("SET LOCAL lock_timeout = '5s'"))
            _ = record(rotating, AuditEvent(action="test.rotating"))
        destroying.result(timeout=30)
    assert [event.action for event in audit_events(tenant_id)] == [
        "test.rotating",
        "tenant.keys_destroyed",
    ]


def test_destroy_locks_the_key_rows_in_id_order(
    installed_provider: LocalKeyProvider, caplog: pytest.LogCaptureFixture
):
    # The provider locks a tenant's rows in id order, so a destroy that locked them in
    # another order could end up waiting on a backfill that waits on it.
    tenant_id = make_tenant_with_keys("acme")
    with caplog.at_level(logging.INFO, logger="sqlalchemy.engine"):
        _destroy(tenant_id)
    statements = [record.getMessage() for record in caplog.records]
    locking = [sql for sql in statements if "FOR UPDATE" in sql]
    assert locking
    assert all("ORDER BY tenant_key.id FOR UPDATE" in sql for sql in locking)


@contextmanager
def _holding(tenant_id: UUID, what: str) -> Generator[None]:
    # Another transaction that has the tenant's key rows, or its audit chain, locked.
    if what == "key rows":
        with holding_key_rows(tenant_id):
            yield
    else:
        with _operator(tenant_id) as holder:
            _ = record(holder, AuditEvent(action="test.holder"))
            yield


@pytest.mark.parametrize("what", ["key rows", "audit chain"])
@pytest.mark.parametrize("operation", ["rotate", "destroy"])
def test_an_operation_that_cannot_get_its_locks_gives_up_with_a_conflict(
    installed_provider: LocalKeyProvider,
    admin_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    what: str,
):
    monkeypatch.setattr(service, "KEY_LOCK_TIMEOUT", "300ms")
    tenant_id = make_tenant_with_keys("acme")
    attempt = (
        partial(_rotate, tenant_id, DATA)
        if operation == "rotate"
        else partial(_destroy, tenant_id)
    )
    with ThreadPoolExecutor(max_workers=1) as pool, _holding(tenant_id, what):
        # A wait without a limit would fail here after 15 s instead of blocking the run.
        failure = pool.submit(attempt).exception(timeout=15)
    assert isinstance(failure, DomainError)
    assert (failure.status, failure.code) == (409, "CONFLICT")
    assert "in use by another transaction" in failure.detail
    # It left nothing behind: no event of its own, and every key as it was (a rotation
    # that had added its version before the audit lock timed out was rolled back).
    assert [e.action for e in audit_events(tenant_id)] == (
        ["test.holder"] if what == "audit chain" else []
    )
    states = key_states(admin_engine, tenant_id)
    assert {state for _, _, state in states} == {"current"}
    assert len(states) == len(KeyPurpose)
    _ = attempt()  # the locks are free now, and the same call goes through
    assert audit_events(tenant_id)[-1].action == (
        "tenant.key_rotated" if operation == "rotate" else "tenant.keys_destroyed"
    )


def test_a_give_up_leaves_the_callers_transaction_usable_and_its_timeout_alone(
    installed_provider: LocalKeyProvider, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(service, "KEY_LOCK_TIMEOUT", "300ms")
    tenant_id = make_tenant_with_keys("acme")

    def give_up_in_a_transaction() -> tuple[str, int]:
        with _operator(tenant_id) as s:
            _ = s.execute(text("SET LOCAL lock_timeout = '42s'"))
            with pytest.raises(DomainError):
                service.destroy_tenant_keys(s)
            # The transaction is still usable, and the setting is the caller's again.
            return s.scalar(text("SHOW lock_timeout")), s.scalar(text("SELECT 1"))

    with ThreadPoolExecutor(max_workers=1) as pool, holding_key_rows(tenant_id):
        outcome = pool.submit(give_up_in_a_transaction).result(timeout=15)
    assert outcome == ("42s", 1)


def test_a_database_error_that_is_no_lock_timeout_is_not_reported_as_one(
    installed_provider: LocalKeyProvider, monkeypatch: pytest.MonkeyPatch
):
    tenant_id = make_tenant_with_keys("acme")

    def failing(tenant: UUID, purpose: KeyPurpose, *, session: Session | None = None):
        statement = "SELECT 1"
        driver_error = Exception("the server went away")
        raise OperationalError(statement, {}, driver_error)

    monkeypatch.setattr(installed_provider, "rotate", failing)
    with pytest.raises(OperationalError, match="the server went away"):
        _ = _rotate(tenant_id, DATA)
    assert audit_events(tenant_id) == []


def test_the_key_lock_timeout_is_the_one_the_audit_partitions_use():
    assert service.KEY_LOCK_TIMEOUT == partitions.LOCK_TIMEOUT


def test_the_callers_lock_timeout_is_left_as_it_was(
    installed_provider: LocalKeyProvider,
):
    tenant_id = make_tenant_with_keys("acme")
    with _operator(tenant_id) as s:
        assert s.scalar(text("SHOW lock_timeout")) == "0"  # none, until set
        _ = service.rotate_tenant_key(s, DATA)
        assert s.scalar(text("SHOW lock_timeout")) == "0"
        _ = s.execute(text("SET LOCAL lock_timeout = '42s'"))
        service.destroy_tenant_keys(s)
        assert s.scalar(text("SHOW lock_timeout")) == "42s"


@contextmanager
def _debug_logging(caplog: pytest.LogCaptureFixture) -> Generator[None]:
    # SQLAlchemy logs every result row at DEBUG, so the scan below sees what the code
    # under test would leak. The test's own raw queries stay outside the window.
    with (
        caplog.at_level(logging.DEBUG),
        caplog.at_level(logging.DEBUG, logger="sqlalchemy.engine"),
    ):
        yield


def test_no_key_material_reaches_the_logs_or_the_audit_trail(
    installed_provider: LocalKeyProvider,
    admin_engine: Engine,
    caplog: pytest.LogCaptureFixture,
):
    tenant_id = make_tenant_with_keys("acme")
    with _debug_logging(caplog):
        _ = _rotate(tenant_id, DATA)
        raw = [bytes(installed_provider.data_key(tenant_id, p)[1]) for p in KeyPurpose]
        raw += [bytes(installed_provider.data_key(tenant_id, DATA, 1)[1])]
    wrapped = wrapped_keys(admin_engine, tenant_id)
    assert len(wrapped) == len(KeyPurpose) + 1
    with _debug_logging(caplog):
        _destroy(tenant_id)
    events = audit_events(tenant_id)
    trail = json.dumps([
        (e.action, e.entity_type, e.entity_id, e.detail) for e in events
    ])
    assert "Row (" in caplog.text  # result rows are logged, so the scan bites
    assert leaked(caplog.text + trail, *raw, *wrapped) == []


def _key_probe_router() -> APIRouter:
    router = APIRouter()

    @router.get("/probe/key")
    def key_version(staff: StaffDep) -> dict[str, int]:
        tenant_id = staff.context.require_tenant_id()
        return {"version": get_key_provider().data_key(tenant_id, DATA)[0]}

    return router


def test_after_a_destruction_sessions_still_work_but_nothing_can_be_decrypted(
    installed_provider: LocalKeyProvider,
):
    # Stands in for "login_totp fails with a 410 while GET /v1/auth/me works": the
    # probe route needs the tenant's DATA key the way a TOTP verification will.
    created = new_tenant()
    installed_provider.create_tenant_keys(created.tenant_id)
    app = build_app()
    app.include_router(_key_probe_router(), prefix="/v1")
    with TestClient(app) as client:
        headers = first_login(
            client,
            "acme",
            created.admin_email,
            created.admin_password,
            "a-long-new-secret-phrase",
        )
        assert client.get("/v1/probe/key", headers=headers).json() == {"version": 1}
        _destroy(created.tenant_id)
        assert client.get("/v1/auth/me", headers=headers).status_code == 200
        gone = client.get("/v1/probe/key", headers=headers)
        assert gone.status_code == 410
        assert gone.headers["content-type"] == "application/problem+json"
        assert gone.json()["code"] == "KEY_DESTROYED"
        fresh = login_token(
            client, "acme", created.admin_email, "a-long-new-secret-phrase"
        )
        assert fresh
