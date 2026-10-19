"""Who is acting and for which tenant (spec §4.1)."""

from collections.abc import Generator, Iterable
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Self
from uuid import UUID

from xolotl.platform.enums import ActorType, Role
from xolotl.platform.errors import ContextMissingError


@dataclass(frozen=True, slots=True)
class TenantContext:
    tenant_id: UUID | None  # None only for PLATFORM_OPERATOR
    actor_type: ActorType
    actor_id: str  # staff_user.id, api_key.id, job id, or "cli:<user>"
    roles: frozenset[Role] = field(default_factory=frozenset)
    request_id: str = ""

    def __post_init__(self) -> None:
        if (
            self.tenant_id is None
            and self.actor_type is not ActorType.PLATFORM_OPERATOR
        ):
            msg = "tenant_id may be None only for PLATFORM_OPERATOR"
            raise ValueError(msg)

    @classmethod
    def platform(cls, actor_id: str, *, tenant_id: UUID | None = None) -> Self:
        return cls(
            tenant_id=tenant_id,
            actor_type=ActorType.PLATFORM_OPERATOR,
            actor_id=actor_id,
        )

    @classmethod
    def system(cls, tenant_id: UUID, actor_id: str) -> Self:
        return cls(tenant_id=tenant_id, actor_type=ActorType.SYSTEM, actor_id=actor_id)

    def require_tenant_id(self) -> UUID:
        if self.tenant_id is None:
            msg = "this operation needs a tenant-scoped context"
            raise ContextMissingError(msg)
        return self.tenant_id


current_context: ContextVar[TenantContext | None] = ContextVar(
    "xolotl_tenant_context", default=None
)


@contextmanager
def bind_context(ctx: TenantContext) -> Generator[None]:
    token = current_context.set(ctx)
    try:
        yield
    finally:
        current_context.reset(token)


def require_context() -> TenantContext:
    ctx = current_context.get()
    if ctx is None:
        raise ContextMissingError
    return ctx


def roles_guc(roles: Iterable[Role]) -> str:
    """Comma-delimited roles with sentinels, e.g. ``,ADMIN,MLRO,`` (empty: ``""``).

    Returns:
        The sorted role names between sentinel commas, or ``""`` when there are none.

    """
    names = sorted(role.value for role in roles)
    return f",{','.join(names)}," if names else ""


def tenant_guc(ctx: TenantContext) -> str:
    return "" if ctx.tenant_id is None else str(ctx.tenant_id)
