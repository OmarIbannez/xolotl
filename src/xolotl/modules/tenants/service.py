"""Tenant lifecycle, staff authentication, staff management and API keys (spec §5-§6).

Two session shapes are used:

* ``auth`` sessions (``auth_context()``): a platform-operator session with no
  tenant, used by login before anyone is authenticated. ``login`` resolves the
  tenant and switches the session into that tenant's scope with
  ``apply_context``. Failure bookkeeping (counters, audit) is committed before
  the error is raised so a rollback cannot erase it.
* staff sessions: bound by ``get_session`` to the authenticated staff actor;
  ``actor_id`` is the ``staff_user.id``.
"""

import hashlib
import hmac
import re
import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import select
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
from xolotl.platform.db import (
    SessionFactory,
    apply_context,
    bound_context,
    tenant_session,
)
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
    NotFoundError,
    UnauthorizedError,
    ValidationFailedError,
)
from xolotl.platform.tenancy.context import TenantContext

AUTH_ACTOR = "auth"
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
                action="staff.created",
                entity_type="staff_user",
                entity_id=str(admin.id),
                detail={"roles": ["ADMIN"]},
            ),
        )
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
    apply_context(s, TenantContext.system(tenant.id, AUTH_ACTOR))
    user, cred = _load_staff(s, email.strip().lower())
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
    apply_context(s, TenantContext.system(claims.tenant_id, AUTH_ACTOR))
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
    if not verify_code(cred.totp_secret.decode(), code):
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
    cred.totp_secret = secret.encode()
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
    if not verify_code(cred.totp_secret.decode(), code):
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
