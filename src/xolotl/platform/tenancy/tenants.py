"""The tenants the background loops visit (plan 1c pre-flight ruling).

The dispatcher, the job runner and the scheduler work one tenant at a time, each
inside that tenant's own row-level security scope. None of them can ask "which
tenants have work?": a session without a tenant sees no row of any tenant table. The
``tenant`` policy admits the platform operator, so the loops list the tenants through
a platform-operator read of ``tenant`` and then open a tenant session for each one.
This module is that read, shared by all three loops, and the read a connector makes
of its own tenant's ``env`` and ``cell``.
"""

from dataclasses import dataclass
from typing import cast
from uuid import UUID

from sqlalchemy import String, Text, Uuid, column, select, table
from sqlalchemy.orm import Session

from xolotl.platform.db import SessionFactory, tenant_session
from xolotl.platform.enums import TenantEnv
from xolotl.platform.errors import NotFoundError
from xolotl.platform.tenancy.context import TenantContext

# ``tenant`` lives in the tenants module, which the platform must not import.
_TENANT = table(
    "tenant", column("id", Uuid), column("env", String(32)), column("cell", Text)
)


@dataclass(frozen=True, slots=True)
class TenantRuntime:
    """Where a tenant runs: its environment and its cell."""

    env: TenantEnv
    cell: str


def tenant_ids(session_factory: SessionFactory, *, actor_id: str) -> list[UUID]:
    """List every tenant, oldest first, for a loop that works tenant by tenant.

    The read is a short platform-operator session of its own, named after the loop
    by ``actor_id``. Suspended and offboarded tenants are listed too: events and jobs
    queued before a tenant's status changed still run to their end.

    Returns:
        The tenant ids in creation order (they are UUIDv7).

    """
    with tenant_session(
        TenantContext.platform(actor_id), factory=session_factory
    ) as session:
        return list(session.scalars(select(_TENANT.c.id).order_by(_TENANT.c.id)))


def tenant_runtime(session: Session, tenant_id: UUID) -> TenantRuntime:
    """Read the ``env`` and ``cell`` of a tenant in the session's own scope.

    A tenant-bound session sees its own ``tenant`` row only (the ``tenant`` policy
    admits ``id = app.tenant_id``), so another tenant's id is not found.

    Returns:
        The tenant's environment and cell.

    Raises:
        NotFoundError: When the session cannot see a tenant with that id.

    """
    row = session.execute(
        select(_TENANT.c.env, _TENANT.c.cell).where(_TENANT.c.id == tenant_id)
    ).one_or_none()
    if row is None:
        msg = "tenant not found"
        raise NotFoundError(msg)
    # The lightweight table's columns carry no Python type of their own.
    return TenantRuntime(
        env=TenantEnv(cast("str", row.env)), cell=cast("str", row.cell)
    )
