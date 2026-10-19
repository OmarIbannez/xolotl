"""The tenant's own record and the idempotency exercise endpoint (spec §6.2)."""

from typing import Annotated

from fastapi import Depends

from xolotl.modules.tenants.models import Tenant
from xolotl.modules.tenants.schemas import PingRequest, PingResponse, TenantResponse
from xolotl.platform import clock
from xolotl.platform.api.router import v1_router
from xolotl.platform.auth.actor import Actor
from xolotl.platform.auth.principal import SessionDep, require_access
from xolotl.platform.errors import NotFoundError

router = v1_router(tags=["tenants"])

TenantReader = Annotated[Actor, Depends(require_access(scope="tenants:read"))]
TenantWriter = Annotated[
    Actor, Depends(require_access(scope="tenants:write", write=True))
]


@router.get("/tenants/me")
def tenants_me(actor: TenantReader, session: SessionDep) -> TenantResponse:
    tenant = session.get(Tenant, actor.context.require_tenant_id())
    if tenant is None:
        raise NotFoundError
    return TenantResponse.model_validate(tenant)


@router.post("/ping")
def ping(body: PingRequest, actor: TenantWriter) -> PingResponse:
    return PingResponse(
        pong=body.message, actor_id=actor.context.actor_id, at=clock.now()
    )
