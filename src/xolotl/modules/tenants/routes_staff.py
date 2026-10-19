"""Staff management by tenant admins (spec §6.4)."""

from typing import Annotated
from uuid import UUID

from fastapi import Depends
from sqlalchemy import select

from xolotl.modules.tenants import service
from xolotl.modules.tenants.models import StaffUser
from xolotl.modules.tenants.schemas import (
    StaffCreatedResponse,
    StaffCreateRequest,
    StaffResponse,
)
from xolotl.platform.api.pagination import Page, PageDep, paginate
from xolotl.platform.api.router import v1_router
from xolotl.platform.auth.actor import Actor
from xolotl.platform.auth.principal import SessionDep, require_access
from xolotl.platform.enums import Role

router = v1_router(prefix="/staff", tags=["staff"])

AdminWriter = Annotated[Actor, Depends(require_access(roles=(Role.ADMIN,), write=True))]
AdminReader = Annotated[Actor, Depends(require_access(roles=(Role.ADMIN,)))]


@router.post("", status_code=201)
def create_staff(
    body: StaffCreateRequest, _admin: AdminWriter, session: SessionDep
) -> StaffCreatedResponse:
    return service.create_staff(
        session, email=body.email, display_name=body.display_name, roles=body.roles
    )


@router.get("")
def list_staff(
    _admin: AdminReader, session: SessionDep, page: PageDep
) -> Page[StaffResponse]:
    rows, next_cursor = paginate(
        session,
        select(StaffUser),
        ts_col=StaffUser.created_at,
        id_col=StaffUser.id,
        key=lambda row: (row.created_at, row.id),
        page=page,
    )
    return Page(
        items=[StaffResponse.model_validate(row) for row in rows],
        next_cursor=next_cursor,
    )


@router.post("/{staff_user_id}/disable")
def disable_staff(
    staff_user_id: UUID, _admin: AdminWriter, session: SessionDep
) -> StaffResponse:
    return service.disable_staff(session, staff_user_id)
