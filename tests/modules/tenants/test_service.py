import re
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from sqlalchemy import Engine, select

from tests.helpers import (
    auth_session,
    backdate_password_change,
    new_tenant,
    platform_ctx,
    staff_session,
    system_ctx,
)
from xolotl.modules.tenants import service
from xolotl.modules.tenants.models import (
    ApiKey,
    ApiKeyLookup,
    StaffCredential,
    StaffUser,
    Tenant,
)
from xolotl.platform import clock
from xolotl.platform.audit.models import AuditEventRow
from xolotl.platform.auth.tokens import decode_token
from xolotl.platform.auth.totp import current_code
from xolotl.platform.db import get_sessionmaker, tenant_session
from xolotl.platform.enums import (
    ActorType,
    ApiKeyStatus,
    Role,
    StaffStatus,
    TenantEnv,
    TenantStatus,
)
from xolotl.platform.errors import (
    DomainError,
    ForbiddenError,
    UnauthorizedError,
    ValidationFailedError,
)


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
    assert _actions(created.tenant_id) == ["tenant.created", "staff.created"]


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
