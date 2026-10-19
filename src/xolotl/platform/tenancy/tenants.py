"""The tenants the background loops visit (plan 1c pre-flight ruling).

The dispatcher, the job runner and the scheduler work one tenant at a time, each
inside that tenant's own row-level security scope. None of them can ask "which
tenants have work?": a session without a tenant sees no row of any tenant table. The
``tenant`` policy admits the platform operator, so the loops list the tenants through
a platform-operator read of ``tenant`` and then open a tenant session for each one.
This module is that read, shared by all three loops.
"""

from uuid import UUID

from sqlalchemy import Uuid, column, select, table

from xolotl.platform.db import SessionFactory, tenant_session
from xolotl.platform.tenancy.context import TenantContext

# ``tenant`` lives in the tenants module, which the platform must not import.
_TENANT = table("tenant", column("id", Uuid))


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
