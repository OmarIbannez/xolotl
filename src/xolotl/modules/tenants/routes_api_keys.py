"""Scoped API keys managed by tenant admins (spec §6.2)."""

from typing import Annotated
from uuid import UUID

from fastapi import Depends
from sqlalchemy import select

from xolotl.modules.tenants import service
from xolotl.modules.tenants.models import ApiKey
from xolotl.modules.tenants.schemas import (
    ApiKeyCreatedResponse,
    ApiKeyCreateRequest,
    ApiKeyResponse,
)
from xolotl.platform.api.pagination import Page, PageDep, paginate
from xolotl.platform.api.router import v1_router
from xolotl.platform.auth.actor import Actor
from xolotl.platform.auth.principal import SessionDep, require_access
from xolotl.platform.enums import Role

router = v1_router(prefix="/api-keys", tags=["api-keys"])

AdminWriter = Annotated[Actor, Depends(require_access(roles=(Role.ADMIN,), write=True))]
AdminReader = Annotated[Actor, Depends(require_access(roles=(Role.ADMIN,)))]


@router.post("", status_code=201)
def create_api_key(
    body: ApiKeyCreateRequest, _admin: AdminWriter, session: SessionDep
) -> ApiKeyCreatedResponse:
    return service.create_api_key(
        session, name=body.name, scopes=body.scopes, env=body.env
    )


@router.get("")
def list_api_keys(
    _admin: AdminReader, session: SessionDep, page: PageDep
) -> Page[ApiKeyResponse]:
    rows, next_cursor = paginate(
        session,
        select(ApiKey),
        ts_col=ApiKey.created_at,
        id_col=ApiKey.id,
        key=lambda row: (row.created_at, row.id),
        page=page,
    )
    return Page(
        items=[ApiKeyResponse.model_validate(row) for row in rows],
        next_cursor=next_cursor,
    )


@router.post("/{api_key_id}/revoke")
def revoke_api_key(
    api_key_id: UUID, _admin: AdminWriter, session: SessionDep
) -> ApiKeyResponse:
    return service.revoke_api_key(session, api_key_id)
