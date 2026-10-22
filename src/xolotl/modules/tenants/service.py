"""Tenant lifecycle, staff authentication, staff management and API keys (spec §5-§6).

Two session shapes are used:

* ``auth`` sessions (``auth_context()``): a platform-operator session with no
  tenant, used by login before anyone is authenticated. ``login`` and
  ``login_totp`` resolve the tenant and switch the session into that tenant's scope
  with ``_in_tenant``, which applies the context to the session and also binds it as
  the current context (loading the encrypted TOTP secret needs that). Failure
  bookkeeping (counters, audit) is committed before the error is raised so a
  rollback cannot erase it.
* staff sessions: bound by ``get_session`` to the authenticated staff actor;
  ``actor_id`` is the ``staff_user.id``.

Writes publish their kernel event (``tenant.created.v1``, ``staff.created.v1``,
``api_key.created.v1``, ``api_key.revoked.v1``) in their own transaction, next to
their audit event: keyed by the entity's id, with ids and codes as data and never
personal data or secrets (program spec D8).
"""

import hashlib
import hmac
import re
import secrets
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import cast
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from xolotl.modules.tenants.models import (
    ApiKey,
    ApiKeyLookup,
    StaffCredential,
    StaffUser,
    Tenant,
)
from xolotl.modules.tenants.schemas import (
    ApiKeyCreatedResponse,
    ApiKeyResponse,
    MeResponse,
    StaffCreatedResponse,
    StaffResponse,
    TotpEnrollResponse,
)
from xolotl.platform import clock
from xolotl.platform.api.idempotency import PURGE_KIND
from xolotl.platform.audit import AuditEvent, record
from xolotl.platform.auth.actor import Actor
from xolotl.platform.auth.passwords import (
    check_password_strength,
    generate_password,
    hash_password,
    verify_dummy,
    verify_password,
)
from xolotl.platform.auth.tokens import TokenClaims, decode_token, issue_token
from xolotl.platform.auth.totp import new_secret, otpauth_uri, verify_code
from xolotl.platform.config import get_settings
from xolotl.platform.crypto import KeyNotFoundError, TenantKey, get_key_provider
from xolotl.platform.crypto.encrypted import encrypt
from xolotl.platform.db import (
    SessionFactory,
    apply_context,
    bound_context,
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
    DomainError,
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
    ValidationFailedError,
)
from xolotl.platform.events import Event, publish
from xolotl.platform.ids import uuid7
from xolotl.platform.jobs.queue import PostgresJobQueue
from xolotl.platform.tenancy.context import TenantContext, bind_context
from xolotl.platform.tenancy.tenants import tenant_ids
from xolotl.platform.units import UNITS_FETCH_KIND
from xolotl.platform.workflows import SWEEP_KIND

AUTH_ACTOR = "auth"
SCHEDULES_ACTOR = "platform-schedules"
EVENT_SOURCE = "xolotl.tenants"
# The platform's own jobs that every tenant runs hourly, in UTC, from schedules made
# when the tenant is created: the idempotency purge and the stranded-workflow sweep.
TENANT_JOBS = (PURGE_KIND, SWEEP_KIND)
HOURLY = "0 * * * *"
# The daily fetch of the Banxico units runs on XOLOTL_UNITS_FETCH_CRON in Mexico City.
UNITS_FETCH_TZ = "America/Mexico_City"
# The party module's daily retention sweep, named by its job kind (a literal: this
# module never imports the party module).
RETENTION_SWEEP_SCHEDULE = "party.retention_sweep"
# The screening catalog's poll of the shared lists, on XOLOTL_LISTS_POLL_CRON in UTC
# (a literal: this module never imports the screening module).
LISTS_POLL_SCHEDULE = "lists.poll"
# The screening module's monthly full re-screen, on XOLOTL_SCREENING_PERIODIC_CRON in
# Mexico City, named by its job kind (a literal, as above).
SCREENING_PERIODIC_SCHEDULE = "screening.periodic_rescreen"
# The monitoring module's per-minute sweep and daily partition maintenance, on
# XOLOTL_MONITORING_SWEEP_CRON and XOLOTL_MONITORING_PARTITIONS_CRON in UTC, named by
# their job kinds (literals, as above).
MONITORING_SWEEP_SCHEDULE = "monitoring.sweep"
MONITORING_PARTITIONS_SCHEDULE = "monitoring.partitions"
# The monitoring module's monthly retention, on XOLOTL_MONITORING_RETENTION_CRON in
# UTC, named by its job kind (a literal, as above).
MONITORING_RETENTION_SCHEDULE = "monitoring.retention"
MAX_FAILED_ATTEMPTS = 10
LOCKOUT = timedelta(minutes=15)
LAST_USED_GRANULARITY = timedelta(minutes=1)
KEY_PREFIX_LEN = 15
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,39}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
SCOPE_RE = re.compile(r"^[a-z][a-z_]*:(read|write)$")
API_KEY_RE = re.compile(r"^xk_(sbx|prd)_[0-9a-f]{8}_[0-9a-f]{32}$")
_ENV_TAG = {TenantEnv.SANDBOX: "sbx", TenantEnv.PROD: "prd"}
_INVALID_CREDENTIALS = "invalid credentials"


def auth_context() -> TenantContext:
    return TenantContext.platform(AUTH_ACTOR)


@contextmanager
def _in_tenant(s: Session, tenant_id: UUID) -> Generator[None]:
    """Move an auth session into one tenant's scope, as the system actor ``auth``.

    ``apply_context`` points the session's row-level security settings at the tenant.
    It does not set the current context, and that is where a result processor takes the
    tenant from, having no session: loading the credential's encrypted TOTP secret
    would fail for a session that only had ``apply_context``. So the context is bound
    as well, for as long as the tenant's rows are being worked on.

    Yields:
        Nothing; the session and the current context are both in the tenant's scope.

    """
    ctx = TenantContext.system(tenant_id, AUTH_ACTOR)
    with bind_context(ctx):
        apply_context(s, ctx)
        yield


# --- validation ----------------------------------------------------------------


def normalize_slug(slug: str) -> str:
    value = slug.strip().lower()
    if not SLUG_RE.match(value):
        msg = "slug must match ^[a-z0-9][a-z0-9-]{2,39}$"
        raise ValidationFailedError(msg)
    return value


def normalize_email(email: str) -> str:
    value = email.strip().lower()
    if not EMAIL_RE.match(value):
        msg = "invalid email address"
        raise ValidationFailedError(msg)
    return value


def validate_scopes(scopes: Sequence[str]) -> list[str]:
    cleaned = sorted({scope.strip() for scope in scopes})
    for scope in cleaned:
        if not SCOPE_RE.match(scope):
            msg = f"scope {scope!r} must look like <area>:<read|write>"
            raise ValidationFailedError(msg)
    return cleaned


def _roles(user: StaffUser) -> list[Role]:
    return sorted(Role(value) for value in user.roles)


def _ensure_tenant_active(tenant: Tenant) -> None:
    if tenant.status is not TenantStatus.ACTIVE:
        msg = "tenant is not active"
        raise ForbiddenError(msg, code="TENANT_SUSPENDED")


# --- kernel events and platform schedules --------------------------------------


def _publish(
    s: Session,
    event_type: str,
    *,
    entity: str,
    entity_id: UUID | str,
    data: dict[str, JsonValue],
) -> Event:
    """Publish a kernel event about one entity in the caller's transaction.

    Keyed by the entity's id, so each consumer gets one entity's events in order;
    the subject names the entity. Inside an HTTP request the request id becomes the
    correlation id.

    Returns:
        The event as published.

    """
    ctx = bound_context(s)
    event = Event(
        id=uuid7(),
        type=event_type,
        source=EVENT_SOURCE,
        tenant_id=ctx.require_tenant_id(),
        subject=f"{entity}/{entity_id}",
        time=clock.now(),
        key=str(entity_id),
        correlation_id=ctx.request_id or None,
        data=data,
    )
    publish(s, event)
    return event


def _publish_staff_created(s: Session, user: StaffUser) -> None:
    _ = _publish(
        s,
        "staff.created.v1",
        entity="staff_user",
        entity_id=user.id,
        data={"staff_user_id": str(user.id), "roles": list(user.roles)},
    )


def schedule_platform_jobs(s: Session, queue: PostgresJobQueue) -> None:
    """Declare the platform's jobs for the session's tenant.

    The hourly ones run in UTC; ``units.fetch`` runs on ``XOLOTL_UNITS_FETCH_CRON``
    in Mexico City, ``party.retention_sweep`` on ``XOLOTL_RETENTION_PURGE_CRON`` in
    UTC, ``lists.poll`` on ``XOLOTL_LISTS_POLL_CRON`` in UTC and the monthly
    ``screening.periodic_rescreen`` on ``XOLOTL_SCREENING_PERIODIC_CRON`` in Mexico
    City, ``monitoring.sweep`` on ``XOLOTL_MONITORING_SWEEP_CRON`` and
    ``monitoring.partitions`` on ``XOLOTL_MONITORING_PARTITIONS_CRON``, both in UTC.

    Each schedule is named after the job it runs. ``schedule`` is an upsert on the
    tenant and the name that keeps the next slot of an unchanged schedule, so
    declaring them again never postpones nor duplicates them.
    """
    for kind in TENANT_JOBS:
        _ = queue.schedule(s, name=kind, cron=HOURLY, tz="UTC", kind=kind, payload={})
    _ = queue.schedule(
        s,
        name=UNITS_FETCH_KIND,
        cron=get_settings().units_fetch_cron,
        tz=UNITS_FETCH_TZ,
        kind=UNITS_FETCH_KIND,
        payload={},
    )
    _ = queue.schedule(
        s,
        name=RETENTION_SWEEP_SCHEDULE,
        cron=get_settings().retention_purge_cron,
        tz="UTC",
        kind=RETENTION_SWEEP_SCHEDULE,
        payload={},
    )
    _ = queue.schedule(
        s,
        name=LISTS_POLL_SCHEDULE,
        cron=get_settings().lists_poll_cron,
        tz="UTC",
        kind=LISTS_POLL_SCHEDULE,
        payload={},
    )
    _ = queue.schedule(
        s,
        name=SCREENING_PERIODIC_SCHEDULE,
        cron=get_settings().screening_periodic_cron,
        tz=UNITS_FETCH_TZ,
        kind=SCREENING_PERIODIC_SCHEDULE,
        payload={},
    )
    _ = queue.schedule(
        s,
        name=MONITORING_SWEEP_SCHEDULE,
        cron=get_settings().monitoring_sweep_cron,
        tz="UTC",
        kind=MONITORING_SWEEP_SCHEDULE,
        payload={},
    )
    _ = queue.schedule(
        s,
        name=MONITORING_PARTITIONS_SCHEDULE,
        cron=get_settings().monitoring_partitions_cron,
        tz="UTC",
        kind=MONITORING_PARTITIONS_SCHEDULE,
        payload={},
    )
    _ = queue.schedule(
        s,
        name=MONITORING_RETENTION_SCHEDULE,
        cron=get_settings().monitoring_retention_cron,
        tz="UTC",
        kind=MONITORING_RETENTION_SCHEDULE,
        payload={},
    )


def ensure_platform_schedules(
    session_factory: SessionFactory, queue: PostgresJobQueue
) -> int:
    """Declare the platform's jobs for every tenant, each in its own session.

    Tenant creation declares them; this reaches the tenants created before a
    schedule existed, and does nothing to the others (see
    ``schedule_platform_jobs``). A worker loop runs it as it starts.

    Returns:
        How many tenants were covered.

    """
    covered = tenant_ids(session_factory, actor_id=SCHEDULES_ACTOR)
    for tenant_id in covered:
        with tenant_session(
            TenantContext.system(tenant_id, SCHEDULES_ACTOR), factory=session_factory
        ) as s:
            schedule_platform_jobs(s, queue)
    return len(covered)


def ping(s: Session) -> datetime:
    """Publish ``test.ping.v1`` for the session's actor, keyed by the actor's id.

    Returns:
        The event's time, which ``POST /v1/ping`` reports.

    """
    ctx = bound_context(s)
    return _publish(
        s,
        "test.ping.v1",
        entity="actor",
        entity_id=ctx.actor_id,
        data={"actor_id": ctx.actor_id, "actor_type": ctx.actor_type.value},
    ).time


# --- tenants -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CreatedTenant:
    tenant_id: UUID
    slug: str
    admin_user_id: UUID
    admin_email: str
    admin_password: str


def _add_staff(
    s: Session,
    tenant_id: UUID,
    *,
    email: str,
    display_name: str,
    roles: Sequence[Role],
    password: str,
) -> StaffUser:
    if s.scalar(select(StaffUser.id).where(StaffUser.email == email)) is not None:
        msg = f"staff user {email} already exists"
        raise DomainError(msg)
    user = StaffUser(
        tenant_id=tenant_id,
        email=email,
        display_name=display_name.strip(),
        roles=sorted({role.value for role in roles}),
        status=StaffStatus.ACTIVE,
        must_change_password=True,
    )
    s.add(user)
    s.flush()
    s.add(
        StaffCredential(
            tenant_id=tenant_id,
            staff_user_id=user.id,
            password_hash=hash_password(password),
            # Stamped from the clock that stamps token iat, not the database's
            # now(), so a token issued right after creation is never "older".
            password_changed_at=clock.now(),
        )
    )
    s.flush()
    return user


def create_tenant(
    session_factory: SessionFactory,
    *,
    slug: str,
    legal_name: str,
    country_code: str,
    admin_email: str,
    env: TenantEnv = TenantEnv.SANDBOX,
    cell: str = "mx-1",
    actor_id: str = "cli:xolotl",
) -> CreatedTenant:
    slug = normalize_slug(slug)
    email = normalize_email(admin_email)
    country = country_code.strip().upper()
    if len(country) != 2 or not country.isalpha():  # ruff: ignore[magic-value-comparison]
        msg = "country_code must be an ISO 3166-1 alpha-2 code"
        raise ValidationFailedError(msg)
    if not legal_name.strip():
        msg = "legal_name is required"
        raise ValidationFailedError(msg)
    with tenant_session(TenantContext.platform(actor_id), factory=session_factory) as s:
        if s.scalar(select(Tenant.id).where(Tenant.slug == slug)) is not None:
            msg = f"tenant slug {slug!r} already exists"
            raise DomainError(msg)
        tenant = Tenant(
            slug=slug,
            legal_name=legal_name.strip(),
            country_code=country,
            cell=cell,
            env=env,
            status=TenantStatus.ACTIVE,
        )
        s.add(tenant)
        s.flush()
        # Same transaction, now scoped to the new tenant (still as the operator).
        apply_context(s, TenantContext.platform(actor_id, tenant_id=tenant.id))
        # In this transaction too: there is no tenant without its keys, and nothing
        # that would encrypt for it before they exist.
        get_key_provider().create_tenant_keys(tenant.id, session=s)
        password = generate_password()
        admin = _add_staff(
            s,
            tenant.id,
            email=email,
            display_name="Administrator",
            roles=[Role.ADMIN],
            password=password,
        )
        _ = record(
            s,
            AuditEvent(
                action="tenant.created",
                entity_type="tenant",
                entity_id=str(tenant.id),
                detail={"slug": slug, "env": env.value},
            ),
        )
        _ = record(
            s,
            AuditEvent(
                action="tenant.keys_created",
                entity_type="tenant",
                entity_id=str(tenant.id),
                detail={"purposes": [purpose.value for purpose in KeyPurpose]},
            ),
        )
        _ = record(
            s,
            AuditEvent(
                action="staff.created",
                entity_type="staff_user",
                entity_id=str(admin.id),
                detail={"roles": ["ADMIN"]},
            ),
        )
        _ = _publish(
            s,
            "tenant.created.v1",
            entity="tenant",
            entity_id=tenant.id,
            data={"tenant_id": str(tenant.id), "slug": slug, "env": env.value},
        )
        _publish_staff_created(s, admin)
        schedule_platform_jobs(s, PostgresJobQueue(session_factory))
        return CreatedTenant(tenant.id, slug, admin.id, email, password)


def list_tenants(s: Session) -> list[Tenant]:
    return list(s.scalars(select(Tenant).order_by(Tenant.created_at, Tenant.id)))


# --- login ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LoginOutcome:
    mfa_required: bool
    token: str
    expires_in: int
    must_change_password: bool


def _load_staff(
    s: Session, email: str
) -> tuple[StaffUser | None, StaffCredential | None]:
    user = s.scalar(select(StaffUser).where(StaffUser.email == email))
    if user is None:
        return None, None
    # FOR UPDATE: attempts on one account run one at a time, so the lock check, the
    # verification and the counter update all see committed state.
    cred = s.scalar(
        select(StaffCredential)
        .where(StaffCredential.staff_user_id == user.id)
        .with_for_update()
    )
    return user, cred


def _fail_and_commit(
    s: Session, user: StaffUser | None, reason: str, *, attempt: str
) -> UnauthorizedError:
    # An early exit would answer faster than a real check and so reveal that the
    # email is unknown or the account disabled or locked: spend one check first.
    verify_dummy(attempt)
    if user is not None:
        _ = record(
            s,
            AuditEvent(
                action="auth.login_failed",
                entity_type="staff_user",
                entity_id=str(user.id),
                detail={"reason": reason},
            ),
        )
    s.commit()
    return UnauthorizedError(_INVALID_CREDENTIALS)


def _register_failure(
    s: Session, user: StaffUser, cred: StaffCredential, now: datetime, *, stage: str
) -> UnauthorizedError:
    cred.failed_attempts += 1
    _ = record(
        s,
        AuditEvent(
            action="auth.login_failed",
            entity_type="staff_user",
            entity_id=str(user.id),
            detail={"reason": "bad_" + stage, "failed_attempts": cred.failed_attempts},
        ),
    )
    if cred.failed_attempts >= MAX_FAILED_ATTEMPTS:
        locked_until = now + LOCKOUT
        cred.locked_until = locked_until
        cred.failed_attempts = 0
        _ = record(
            s,
            AuditEvent(
                action="auth.locked",
                entity_type="staff_user",
                entity_id=str(user.id),
                detail={"locked_until": locked_until.isoformat()},
            ),
        )
    s.commit()
    return UnauthorizedError(_INVALID_CREDENTIALS)


def _is_locked(cred: StaffCredential, now: datetime) -> bool:
    return cred.locked_until is not None and cred.locked_until > now


def _issued_before_password_change(claims: TokenClaims, cred: StaffCredential) -> bool:
    # A password change ends every token issued before it, access and MFA alike.
    # iat has whole seconds, so a token from the same second as the change stays valid.
    return claims.iat < int(cred.password_changed_at.timestamp())


def _access_outcome(tenant: Tenant, user: StaffUser) -> LoginOutcome:
    ttl = timedelta(minutes=get_settings().access_token_ttl_minutes)
    token = issue_token(
        subject=str(user.id),
        tenant_id=tenant.id,
        tenant_slug=tenant.slug,
        roles=_roles(user),
        purpose="access",
        ttl=ttl,
    )
    return LoginOutcome(
        mfa_required=False,
        token=token,
        expires_in=int(ttl.total_seconds()),
        must_change_password=user.must_change_password,
    )


def _succeed(
    s: Session, tenant: Tenant, user: StaffUser, cred: StaffCredential
) -> LoginOutcome:
    cred.failed_attempts = 0
    cred.locked_until = None
    _ = record(
        s,
        AuditEvent(
            action="auth.login_succeeded",
            entity_type="staff_user",
            entity_id=str(user.id),
        ),
    )
    return _access_outcome(tenant, user)


def login(s: Session, *, tenant_slug: str, email: str, password: str) -> LoginOutcome:
    # Password step. ``s`` is bound to ``auth_context()``. Every failure costs one
    # argon2id verification, real or dummy, and answers the same generic 401.
    tenant = s.scalar(select(Tenant).where(Tenant.slug == tenant_slug.strip().lower()))
    if tenant is None:
        verify_dummy(password)  # see _fail_and_commit
        raise UnauthorizedError(_INVALID_CREDENTIALS)
    with _in_tenant(s, tenant.id):
        return _password_step(s, tenant, email=email.strip().lower(), password=password)


def _password_step(
    s: Session, tenant: Tenant, *, email: str, password: str
) -> LoginOutcome:
    user, cred = _load_staff(s, email)
    if user is None or cred is None or user.status is not StaffStatus.ACTIVE:
        raise _fail_and_commit(s, user, "unknown_or_disabled", attempt=password)
    now = clock.now()
    if _is_locked(cred, now):
        raise _fail_and_commit(s, user, "locked", attempt=password)
    if not verify_password(cred.password_hash, password):
        raise _register_failure(s, user, cred, now, stage="password")
    _ensure_tenant_active(tenant)
    if cred.totp_enabled:
        ttl = timedelta(minutes=get_settings().mfa_token_ttl_minutes)
        token = issue_token(
            subject=str(user.id),
            tenant_id=tenant.id,
            tenant_slug=tenant.slug,
            roles=_roles(user),
            purpose="mfa",
            ttl=ttl,
        )
        return LoginOutcome(
            mfa_required=True,
            token=token,
            expires_in=int(ttl.total_seconds()),
            must_change_password=user.must_change_password,
        )
    return _succeed(s, tenant, user, cred)


def login_totp(s: Session, *, mfa_token: str, code: str) -> LoginOutcome:
    # TOTP step. ``s`` is bound to ``auth_context()``.
    claims = decode_token(mfa_token, purpose="mfa")
    with _in_tenant(s, claims.tenant_id):
        return _totp_step(s, claims, code=code)


def _totp_step(s: Session, claims: TokenClaims, *, code: str) -> LoginOutcome:
    tenant = s.get(Tenant, claims.tenant_id)
    user = s.get(StaffUser, (claims.tenant_id, UUID(claims.sub)))
    # FOR UPDATE: see _load_staff.
    cred = (
        None
        if user is None
        else s.scalar(
            select(StaffCredential)
            .where(StaffCredential.staff_user_id == user.id)
            .with_for_update()
        )
    )
    if tenant is None or user is None or cred is None:
        raise _fail_and_commit(s, user, "mfa_not_available", attempt=code)
    if (
        user.status is not StaffStatus.ACTIVE
        or not cred.totp_enabled
        or cred.totp_secret is None
    ):
        raise _fail_and_commit(s, user, "mfa_not_available", attempt=code)
    if _issued_before_password_change(claims, cred):
        raise _fail_and_commit(s, user, "password_changed", attempt=code)
    now = clock.now()
    if _is_locked(cred, now):
        raise _fail_and_commit(s, user, "locked", attempt=code)
    if not verify_code(cred.totp_secret.reveal(session=s), code):
        # The early exits above spend a dummy verification, so a wrong code does too:
        # every failure of this step costs the same.
        verify_dummy(code)
        raise _register_failure(s, user, cred, now, stage="totp")
    _ensure_tenant_active(tenant)
    return _succeed(s, tenant, user, cred)


# --- staff-session operations --------------------------------------------------


def _current_rows(
    s: Session, *, for_update: bool = False
) -> tuple[Tenant, StaffUser, StaffCredential]:
    ctx = bound_context(s)
    tenant_id = ctx.require_tenant_id()
    tenant = s.get(Tenant, tenant_id)
    user = s.get(StaffUser, (tenant_id, UUID(ctx.actor_id)))
    cred = None
    if user is not None:
        statement = select(StaffCredential).where(
            StaffCredential.staff_user_id == user.id
        )
        # Callers that change the credential lock its row first, as logins do.
        cred = s.scalar(statement.with_for_update() if for_update else statement)
    if tenant is None or user is None or cred is None:
        raise UnauthorizedError
    return tenant, user, cred


def me(s: Session) -> MeResponse:
    tenant, user, cred = _current_rows(s)
    return MeResponse(
        id=user.id,
        tenant_id=tenant.id,
        tenant_slug=tenant.slug,
        email=user.email,
        display_name=user.display_name,
        roles=_roles(user),
        must_change_password=user.must_change_password,
        totp_enabled=cred.totp_enabled,
    )


def enroll_totp(s: Session) -> TotpEnrollResponse:
    tenant, user, cred = _current_rows(s, for_update=True)
    if cred.totp_enabled:
        msg = "TOTP is already enabled"
        raise DomainError(msg)
    secret = new_secret()
    # Sealed here, for this tenant and in this transaction, rather than left to the
    # column: an Encrypted is stored as it is, and the plain text never sits on the row.
    cred.totp_secret = encrypt(secret, tenant_id=tenant.id, session=s)
    s.flush()
    return TotpEnrollResponse(
        secret=secret,
        otpauth_uri=otpauth_uri(secret, email=user.email, tenant_slug=tenant.slug),
    )


def confirm_totp(s: Session, *, code: str) -> None:
    _, user, cred = _current_rows(s, for_update=True)
    if cred.totp_secret is None:
        msg = "enrol TOTP before confirming it"
        raise DomainError(msg)
    if cred.totp_enabled:
        msg = "TOTP is already enabled"
        raise DomainError(msg)
    if not verify_code(cred.totp_secret.reveal(session=s), code):
        msg = "invalid TOTP code"
        raise ValidationFailedError(msg)
    cred.totp_enabled = True
    _ = record(
        s,
        AuditEvent(
            action="auth.totp_enrolled",
            entity_type="staff_user",
            entity_id=str(user.id),
        ),
    )
    s.flush()


def change_password(
    s: Session, *, current_password: str, new_password: str
) -> LoginOutcome:
    # Ends every session that started before the change, including the caller's own
    # (see _resolve_staff_token), and returns a fresh access token to carry on with.
    tenant, user, cred = _current_rows(s, for_update=True)
    if not verify_password(cred.password_hash, current_password):
        msg = "current password is incorrect"
        raise ValidationFailedError(msg)
    check_password_strength(new_password)
    if new_password == current_password:
        msg = "new password must differ from the current password"
        raise ValidationFailedError(msg)
    cred.password_hash = hash_password(new_password)
    cred.password_changed_at = clock.now()
    user.must_change_password = False
    _ = record(
        s,
        AuditEvent(
            action="auth.password_changed",
            entity_type="staff_user",
            entity_id=str(user.id),
        ),
    )
    s.flush()
    return _access_outcome(tenant, user)


def create_staff(
    s: Session, *, email: str, display_name: str, roles: Sequence[Role]
) -> StaffCreatedResponse:
    tenant_id = bound_context(s).require_tenant_id()
    password = generate_password()
    user = _add_staff(
        s,
        tenant_id,
        email=normalize_email(email),
        display_name=display_name,
        roles=roles,
        password=password,
    )
    _ = record(
        s,
        AuditEvent(
            action="staff.created",
            entity_type="staff_user",
            entity_id=str(user.id),
            detail={"roles": list(user.roles)},
        ),
    )
    _publish_staff_created(s, user)
    return StaffCreatedResponse(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        roles=_roles(user),
        initial_password=password,
    )


def disable_staff(s: Session, staff_user_id: UUID) -> StaffResponse:
    ctx = bound_context(s)
    user = s.get(StaffUser, (ctx.require_tenant_id(), staff_user_id))
    if user is None:
        raise NotFoundError
    if str(user.id) == ctx.actor_id:
        msg = "you cannot disable your own account"
        raise DomainError(msg)
    if user.status is not StaffStatus.DISABLED:
        user.status = StaffStatus.DISABLED
        _ = record(
            s,
            AuditEvent(
                action="staff.disabled",
                entity_type="staff_user",
                entity_id=str(user.id),
            ),
        )
        s.flush()
    return StaffResponse.model_validate(user)


# --- api keys ------------------------------------------------------------------


def generate_api_key(env: TenantEnv) -> tuple[str, str, bytes]:
    """Return ``(key, key_prefix, sha256(key))`` for a new key in ``env``.

    Returns:
        The key (shown once), its 15-character prefix and the SHA-256 digest
        of the key.

    """
    key = f"xk_{_ENV_TAG[env]}_{secrets.token_hex(4)}_{secrets.token_hex(16)}"
    return key, key[:KEY_PREFIX_LEN], hashlib.sha256(key.encode()).digest()


def create_api_key(
    s: Session, *, name: str, scopes: Sequence[str], env: TenantEnv | None = None
) -> ApiKeyCreatedResponse:
    ctx = bound_context(s)
    tenant = s.get(Tenant, ctx.require_tenant_id())
    if tenant is None:
        raise NotFoundError
    key_env = env or tenant.env
    if key_env is not tenant.env:
        msg = f"api key env must match the tenant env {tenant.env.value}"
        raise DomainError(msg)
    cleaned = validate_scopes(scopes)
    key, prefix, digest = generate_api_key(key_env)
    row = ApiKey(
        tenant_id=tenant.id,
        name=name.strip(),
        key_prefix=prefix,
        key_hash=digest,
        scopes=cleaned,
        env=key_env,
        status=ApiKeyStatus.ACTIVE,
        created_by=UUID(ctx.actor_id),
    )
    s.add(row)
    s.add(ApiKeyLookup(key_prefix=prefix, tenant_id=tenant.id))
    s.flush()
    _ = record(
        s,
        AuditEvent(
            action="api_key.created",
            entity_type="api_key",
            entity_id=str(row.id),
            detail={"name": row.name, "scopes": list(cleaned), "env": key_env.value},
        ),
    )
    _ = _publish(
        s,
        "api_key.created.v1",
        entity="api_key",
        entity_id=row.id,
        data={"api_key_id": str(row.id), "scopes": list(cleaned), "env": key_env.value},
    )
    return ApiKeyCreatedResponse(
        id=row.id,
        name=row.name,
        key_prefix=prefix,
        scopes=cleaned,
        env=key_env,
        key=key,
    )


def revoke_api_key(s: Session, api_key_id: UUID) -> ApiKeyResponse:
    row = s.get(ApiKey, (bound_context(s).require_tenant_id(), api_key_id))
    if row is None:
        raise NotFoundError
    if row.status is ApiKeyStatus.ACTIVE:
        row.status = ApiKeyStatus.REVOKED
        row.revoked_at = clock.now()
        _ = record(
            s,
            AuditEvent(
                action="api_key.revoked", entity_type="api_key", entity_id=str(row.id)
            ),
        )
        _ = _publish(
            s,
            "api_key.revoked.v1",
            entity="api_key",
            entity_id=row.id,
            data={"api_key_id": str(row.id)},
        )
        s.flush()
    return ApiKeyResponse.model_validate(row)


# --- bearer resolution ---------------------------------------------------------


def _resolve_staff_token(session_factory: SessionFactory, token: str) -> Actor:
    claims = decode_token(token, purpose="access")
    probe = TenantContext(
        tenant_id=claims.tenant_id,
        actor_type=ActorType.STAFF,
        actor_id=claims.sub,
        roles=frozenset(claims.roles),
    )
    with tenant_session(probe, factory=session_factory) as s:
        tenant = s.get(Tenant, claims.tenant_id)
        user = s.get(StaffUser, (claims.tenant_id, UUID(claims.sub)))
        if tenant is None or user is None or user.status is not StaffStatus.ACTIVE:
            raise UnauthorizedError
        cred = s.scalar(
            select(StaffCredential).where(StaffCredential.staff_user_id == user.id)
        )
        if cred is None or _issued_before_password_change(claims, cred):
            raise UnauthorizedError
        _ensure_tenant_active(tenant)
        roles = frozenset(_roles(user))  # the row is authoritative, not the token
        must_change = user.must_change_password
    return Actor(
        context=TenantContext(
            tenant_id=claims.tenant_id,
            actor_type=ActorType.STAFF,
            actor_id=claims.sub,
            roles=roles,
        ),
        must_change_password=must_change,
    )


def _resolve_api_key(session_factory: SessionFactory, token: str) -> Actor:
    if not API_KEY_RE.match(token):
        raise UnauthorizedError
    prefix = token[:KEY_PREFIX_LEN]
    digest = hashlib.sha256(token.encode()).digest()
    with tenant_session(auth_context(), factory=session_factory) as s:
        lookup = s.get(
            ApiKeyLookup, prefix
        )  # shared table, readable before any tenant scope
        if lookup is None:
            raise UnauthorizedError
        tenant_id = lookup.tenant_id
        apply_context(
            s,
            TenantContext(
                tenant_id=tenant_id,
                actor_type=ActorType.API_CLIENT,
                actor_id=f"key:{prefix}",
            ),
        )
        row = s.scalar(select(ApiKey).where(ApiKey.key_prefix == prefix))
        if (
            row is None
            or not hmac.compare_digest(row.key_hash, digest)
            or row.status is not ApiKeyStatus.ACTIVE
        ):
            raise UnauthorizedError
        tenant = s.get(Tenant, tenant_id)
        if tenant is None:
            raise UnauthorizedError
        _ensure_tenant_active(tenant)
        now = clock.now()
        if row.last_used_at is None or now - row.last_used_at >= LAST_USED_GRANULARITY:
            row.last_used_at = now
        return Actor(
            context=TenantContext(
                tenant_id=tenant_id,
                actor_type=ActorType.API_CLIENT,
                actor_id=str(row.id),
            ),
            scopes=frozenset(row.scopes),
        )


def resolve_bearer(session_factory: SessionFactory, token: str) -> Actor:
    """Resolve a staff JWT or an ``xk_`` API key; the kernel's ``ActorResolver``.

    Returns:
        The staff or API-client actor the token identifies.

    """
    if token.startswith("xk_"):
        return _resolve_api_key(session_factory, token)
    return _resolve_staff_token(session_factory, token)


# --- tenant keys (platform operator) -------------------------------------------

# Rotating and destroying keys lock the tenant's key rows and its audit chain. An
# operation that cannot get a lock within this long gives up with a conflict instead of
# waiting for ever, for instance behind a transaction that someone left open. Same
# setting and value as the audit partitions use.
KEY_LOCK_TIMEOUT = "5s"
_LOCK_NOT_AVAILABLE = "55P03"
_KEYS_BUSY = "this tenant's keys or audit trail are in use by another transaction"
_LOCK_TIMEOUT_SQL = text("SELECT current_setting('lock_timeout')")
_SET_LOCK_TIMEOUT_SQL = text("SELECT set_config('lock_timeout', :value, true)")


@contextmanager
def _bounded_lock_waits(session: Session) -> Generator[None]:
    # Bounds every lock wait of the block by KEY_LOCK_TIMEOUT and turns a timeout into a
    # DomainError (409). The setting is made in a savepoint. A give-up rolls the
    # savepoint back, which undoes the setting and leaves the caller's transaction
    # usable. On success the caller's own value is put back, because a setting made in
    # a savepoint outlives its release.
    callers_timeout = cast("str", session.scalar(_LOCK_TIMEOUT_SQL))
    try:
        with session.begin_nested():
            _ = session.execute(_SET_LOCK_TIMEOUT_SQL, {"value": KEY_LOCK_TIMEOUT})
            yield
            _ = session.execute(_SET_LOCK_TIMEOUT_SQL, {"value": callers_timeout})
    except OperationalError as exc:
        sqlstate = cast("str | None", getattr(exc.orig, "sqlstate", None))
        if sqlstate != _LOCK_NOT_AVAILABLE:
            raise
        msg = f"{_KEYS_BUSY}; try again shortly"
        raise DomainError(msg) from exc


def rotate_tenant_key(session: Session, purpose: KeyPurpose) -> int:
    """Add a new version of one of the session's tenant's keys and audit it.

    The process that rotates seals every value it writes from now on under the new
    version; another process does so once the key it cached expires (the provider's
    cache TTL). Values sealed earlier stay readable: the old version is retired, not
    destroyed, and nothing is re-encrypted. INDEX keys are never rotated (every blind
    index would need recomputing), and a tenant whose keys were destroyed cannot
    rotate: the provider raises DomainError, KeyNotFoundError or KeyDestroyedError
    (410) and nothing is audited. The event and the new key are one transaction: the
    caller's.

    A lock wait (the purpose's key rows, then the tenant's audit lock) lasts at most
    KEY_LOCK_TIMEOUT. After that it gives up with a DomainError (409), and the caller's
    transaction is still usable. To compose it with other work, call it before
    anything else in the transaction records an audit event: that lock is held until
    the commit, so an event recorded earlier would put it before the key rows again,
    the order that deadlocks with a destroy under way.

    The session is bound to the tenant. The CLI runs it as the platform operator.

    Returns:
        The new version number.

    """
    tenant_id = bound_context(session).require_tenant_id()
    with _bounded_lock_waits(session):
        version = get_key_provider().rotate(tenant_id, purpose, session=session)
        _ = record(
            session,
            AuditEvent(
                action="tenant.key_rotated",
                entity_type="tenant",
                entity_id=str(tenant_id),
                detail={"purpose": purpose.value, "version": version},
            ),
        )
    return version


def destroy_tenant_keys(session: Session) -> None:
    """Destroy every key of the session's tenant for good (crypto-shredding).

    Irreversible: every value sealed for the tenant, under any purpose or version,
    becomes unreadable, and nothing creates its keys again. Destroying twice is
    harmless (the first destruction time stands) but is audited each time.

    The event is recorded before the keys are destroyed, in the same transaction, so
    a failure on either side rolls both back and the destruction never goes unrecorded.
    The key rows are locked first, in the order rotation takes them, and the audit
    lock comes second: taking them the other way round could deadlock with a rotation
    that is under way. That holds only if nothing earlier in the transaction recorded
    an audit event (that lock is held until the commit), so call it before any other
    record() of the same transaction. A lock wait lasts at most KEY_LOCK_TIMEOUT;
    after that it gives up with a DomainError (409), and the caller's transaction is
    still usable. This process forgets the keys it cached when the transaction
    commits; another process serves what it cached for up to the provider's cache TTL.

    The session is bound to the tenant. The CLI runs it as the platform operator.

    Raises:
        KeyNotFoundError: When the tenant has no keys, so nothing is destroyed and
            nothing is audited.

    """
    tenant_id = bound_context(session).require_tenant_id()
    with _bounded_lock_waits(session):
        held = session.scalars(
            select(TenantKey.id)
            .where(TenantKey.tenant_id == tenant_id)
            .order_by(TenantKey.id)
            .with_for_update()
        ).all()
        if not held:
            msg = "this tenant has no encryption keys to destroy"
            raise KeyNotFoundError(msg)
        _ = record(
            session,
            AuditEvent(
                action="tenant.keys_destroyed",
                entity_type="tenant",
                entity_id=str(tenant_id),
            ),
        )
        get_key_provider().destroy(tenant_id, session=session)
