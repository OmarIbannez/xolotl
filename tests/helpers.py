"""Small helpers shared by test modules."""

import base64
import traceback
from collections.abc import Callable, Generator, Iterable
from contextlib import contextmanager
from datetime import timedelta
from typing import cast, override
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, literal_column, select, table, text
from sqlalchemy.orm import Session

from xolotl.models import metadata
from xolotl.modules.tenants import service
from xolotl.modules.tenants.models import StaffCredential, Tenant
from xolotl.platform import clock
from xolotl.platform.audit.models import AuditEventRow
from xolotl.platform.config import get_settings
from xolotl.platform.crypto.models import TenantKey
from xolotl.platform.crypto.primitives import unwrap_dek
from xolotl.platform.crypto.provider import (
    KeyProvider,
    LocalKeyProvider,
    get_key_provider,
)
from xolotl.platform.db import get_sessionmaker, tenant_session
from xolotl.platform.enums import ActorType, KeyPurpose, Role, TenantEnv, TenantStatus
from xolotl.platform.errors import XolotlError
from xolotl.platform.tenancy.context import TenantContext


def platform_ctx(actor_id: str = "cli:test") -> TenantContext:
    return TenantContext.platform(actor_id)


def staff_ctx(
    tenant_id: UUID, *, actor_id: str = "staff:test", roles: Iterable[Role] = ()
) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        actor_type=ActorType.STAFF,
        actor_id=actor_id,
        roles=frozenset(roles),
    )


def system_ctx(tenant_id: UUID, actor_id: str = "job:test") -> TenantContext:
    return TenantContext.system(tenant_id, actor_id)


def make_tenant(
    slug: str,
    *,
    env: TenantEnv = TenantEnv.SANDBOX,
    status: TenantStatus = TenantStatus.ACTIVE,
) -> UUID:
    with tenant_session(platform_ctx()) as s:
        tenant = Tenant(
            slug=slug,
            legal_name=slug.replace("-", " ").title(),
            country_code="MX",
            env=env,
            status=status,
        )
        s.add(tenant)
        s.flush()
        return tenant.id


def make_tenant_with_keys(
    slug: str,
    *,
    provider: KeyProvider | None = None,
    env: TenantEnv = TenantEnv.SANDBOX,
    status: TenantStatus = TenantStatus.ACTIVE,
) -> UUID:
    """Create a tenant and give it its data keys; the configured provider by default.

    Returns:
        The id of the new tenant.

    """
    tenant_id = make_tenant(slug, env=env, status=status)
    (provider or get_key_provider()).create_tenant_keys(tenant_id)
    return tenant_id


@contextmanager
def auth_session() -> Generator[Session]:
    with tenant_session(service.auth_context()) as s:
        yield s


@contextmanager
def staff_session(
    tenant_id: UUID, user_id: UUID, roles: Iterable[Role] = (Role.ADMIN,)
) -> Generator[Session]:
    with tenant_session(staff_ctx(tenant_id, actor_id=str(user_id), roles=roles)) as s:
        yield s


def new_tenant(
    slug: str = "acme", email: str = "admin@acme.mx"
) -> service.CreatedTenant:
    return service.create_tenant(
        get_sessionmaker(),
        slug=slug,
        legal_name=f"{slug.title()} SA de CV",
        country_code="MX",
        admin_email=email,
    )


def login_token(client: TestClient, slug: str, email: str, password: str) -> str:
    resp = client.post(
        "/v1/auth/login",
        json={"tenant_slug": slug, "email": email, "password": password},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def first_login(
    client: TestClient, slug: str, email: str, password: str, new_password: str
) -> dict[str, str]:
    """Sign a new user in and replace the generated password.

    The change ends every session that started before it, so the headers carry the
    token the change returns, not the login token.

    Returns:
        A mapping holding the user's ``Authorization`` header.

    """
    token = login_token(client, slug, email, password)
    changed = client.post(
        "/v1/auth/password",
        json={"current_password": password, "new_password": new_password},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert changed.status_code == 200, changed.text
    return {"Authorization": f"Bearer {changed.json()['access_token']}"}


def backdate_password_change(tenant_id: UUID, user_id: UUID, *, seconds: int) -> None:
    # Tokens issued in an earlier second than password_changed_at are refused, so a
    # test that needs a token from before a change backdates the credential first.
    with tenant_session(system_ctx(tenant_id)) as s:
        credential = s.scalars(
            select(StaffCredential).where(StaffCredential.staff_user_id == user_id)
        ).one()
        credential.password_changed_at = clock.now() - timedelta(seconds=seconds)


# Every spelling a byte secret could leak in: hex, base64 and the bytes repr. The copy
# is plain bytes because a SecretBytes would give its mask as the repr, and a mask is
# not a leak.
def secret_forms(secret: bytes) -> list[str]:
    plain = bytes(secret)
    forms = [
        plain.hex(),
        plain.hex().upper(),
        base64.b64encode(plain).decode(),
        base64.urlsafe_b64encode(plain).decode(),
        repr(plain)[2:-1],
    ]
    return forms + [form.rstrip("=") for form in forms]


# The spellings of ``secrets`` found in ``haystack``; an empty list means no leak.
def leaked(haystack: str, *secrets: bytes) -> list[str]:
    return [
        form for secret in secrets for form in secret_forms(secret) if form in haystack
    ]


# Every row of every table as text, read past row-level security and every column
# type, for a search for what must never be stored in the clear. The partitions of the
# audit log come with their parent.
def everything_stored(admin_engine: Engine) -> str:
    with admin_engine.connect() as conn:
        dumps = [
            repr(
                conn.execute(select(literal_column("*")).select_from(table(name))).all()
            )
            for name in metadata().tables
        ]
    return "\n".join(dumps)


# A tenant's DATA key, unwrapped from ``tenant_key`` with ``kek`` (the configured master
# key by default) and not by the key provider, so that a test can tell which
# key-encryption key wrapped it: another one fails with ``DecryptionError``.
def data_key_of(
    engine: Engine, tenant_id: UUID, version: int = 1, *, kek: bytes | None = None
) -> bytes:
    with engine.connect() as conn:
        wrapped = cast(
            "bytes",
            conn.scalar(
                text(
                    """
                    SELECT wrapped_dek FROM tenant_key
                    WHERE tenant_id = :tenant_id AND purpose = 'DATA'
                      AND version = :version
                    """
                ),
                {"tenant_id": tenant_id, "version": version},
            ),
        )
    return bytes(
        unwrap_dek(
            kek or get_settings().master_key_bytes,
            wrapped,
            tenant_id=tenant_id,
            purpose=KeyPurpose.DATA,
            version=version,
        )
    )


# Run ``call``, which must raise, and render its traceback the way an error tracker that
# captures local variables does (Sentry's default, rich, pytest -l): every frame with
# the repr of each local. The frame of this helper is left out, since it holds the
# caller's own variables and not the code under test.
def locals_report(call: Callable[[], object]) -> str:
    with pytest.raises((XolotlError, ValueError)) as caught:
        _ = call()
    error = caught.value
    assert error.__traceback__ is not None
    rendered = traceback.TracebackException(
        type(error), error, error.__traceback__.tb_next, capture_locals=True
    )
    return "".join(rendered.format())


# The tenant's audit chain in order, read as the tenant's own system actor.
def audit_events(tenant_id: UUID) -> list[AuditEventRow]:
    with tenant_session(system_ctx(tenant_id)) as s:
        return list(s.scalars(select(AuditEventRow).order_by(AuditEventRow.seq)))


# (purpose, version, state) of every key row of the tenant, read as the owner, outside
# row-level security. The state is "current", "retired" or "destroyed"; "inconsistent"
# means half a destruction: the key gone without a timestamp, or the other way round.
def key_states(admin_engine: Engine, tenant_id: UUID) -> list[tuple[str, int, str]]:
    with admin_engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT purpose, version,
                       CASE
                         WHEN wrapped_dek IS NULL AND destroyed_at IS NOT NULL
                           THEN 'destroyed'
                         WHEN wrapped_dek IS NULL OR destroyed_at IS NOT NULL
                           THEN 'inconsistent'
                         WHEN retired_at IS NOT NULL THEN 'retired'
                         ELSE 'current'
                       END
                FROM tenant_key WHERE tenant_id = :tenant_id
                ORDER BY purpose, version
                """
            ),
            {"tenant_id": tenant_id},
        ).all()
    return [(row[0], row[1], row[2]) for row in rows]


# The wrapped keys still stored for the tenant, as the raw bytes of the column.
def wrapped_keys(admin_engine: Engine, tenant_id: UUID) -> list[bytes]:
    with admin_engine.connect() as conn:
        return list(
            conn.scalars(
                text(
                    """
                    SELECT wrapped_dek FROM tenant_key
                    WHERE tenant_id = :tenant_id AND wrapped_dek IS NOT NULL
                    """
                ),
                {"tenant_id": tenant_id},
            )
        )


# A local key provider that will not rotate or destroy outside the caller's session.
# The tenant service holds row locks in its own transaction when it calls these two; a
# call that opened a second session instead would wait for those locks for ever
# (Postgres sees no cycle between a connection and the thread that is blocked on it), so
# a service that forgot to pass its session would hang the run. Here it fails at once.
class SessionBoundProvider(LocalKeyProvider):
    @override
    def rotate(
        self, tenant_id: UUID, purpose: KeyPurpose, *, session: Session | None = None
    ) -> int:
        assert session is not None, "rotate was called without the caller's session"
        return super().rotate(tenant_id, purpose, session=session)

    @override
    def destroy(self, tenant_id: UUID, *, session: Session | None = None) -> None:
        assert session is not None, "destroy was called without the caller's session"
        super().destroy(tenant_id, session=session)


# Another transaction that has the tenant's key rows locked, the way a rotation under
# way has them. It is released when the block ends.
@contextmanager
def holding_key_rows(tenant_id: UUID) -> Generator[Session]:
    with tenant_session(TenantContext.platform("cli:holder", tenant_id=tenant_id)) as s:
        _ = s.execute(
            select(TenantKey.id)
            .where(TenantKey.tenant_id == tenant_id)
            .with_for_update()
        ).all()
        yield s
