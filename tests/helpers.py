"""Small helpers shared by test modules."""

from collections.abc import Generator, Iterable
from contextlib import contextmanager
from datetime import timedelta
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from xolotl.modules.tenants import service
from xolotl.modules.tenants.models import StaffCredential, Tenant
from xolotl.platform import clock
from xolotl.platform.db import get_sessionmaker, tenant_session
from xolotl.platform.enums import ActorType, Role, TenantEnv, TenantStatus
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
