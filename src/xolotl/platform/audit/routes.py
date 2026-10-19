"""Read and verify the tenant's audit chain (spec §8.3)."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Query
from pydantic import BaseModel, JsonValue
from sqlalchemy import select

from xolotl.platform.api.pagination import Page, PageDep, paginate
from xolotl.platform.api.router import v1_router
from xolotl.platform.audit import ChainVerification, verify_chain
from xolotl.platform.audit.models import AuditEventRow
from xolotl.platform.auth.actor import Actor
from xolotl.platform.auth.principal import SessionDep, require_access
from xolotl.platform.enums import Role

router = v1_router(prefix="/audit", tags=["audit"])

AuditReader = Annotated[
    Actor, Depends(require_access(roles=(Role.MLRO, Role.ADMIN, Role.AUDITOR_RO)))
]


class AuditEventResponse(BaseModel):
    id: UUID
    seq: int
    at: datetime
    actor_type: str
    actor_id: str
    action: str
    entity_type: str | None
    entity_id: str | None
    detail: dict[str, JsonValue] | None
    prev_hash: str
    hash: str


def _to_response(row: AuditEventRow) -> AuditEventResponse:
    return AuditEventResponse.model_validate({
        "id": row.id,
        "seq": row.seq,
        "at": row.at,
        "actor_type": row.actor_type,
        "actor_id": row.actor_id,
        "action": row.action,
        "entity_type": row.entity_type,
        "entity_id": row.entity_id,
        "detail": row.detail,
        "prev_hash": row.prev_hash.hex(),
        "hash": row.hash.hex(),
    })


@router.get("")
def list_audit(
    _reader: AuditReader,
    session: SessionDep,
    page: PageDep,
    action: Annotated[str | None, Query(max_length=120)] = None,
) -> Page[AuditEventResponse]:
    stmt = select(AuditEventRow)
    if action is not None:
        stmt = stmt.where(AuditEventRow.action == action)
    rows, next_cursor = paginate(
        session,
        stmt,
        ts_col=AuditEventRow.at,
        id_col=AuditEventRow.id,
        key=lambda row: (row.at, row.id),
        page=page,
    )
    return Page(items=[_to_response(row) for row in rows], next_cursor=next_cursor)


@router.get("/verify")
def verify_audit(_reader: AuditReader, session: SessionDep) -> ChainVerification:
    return verify_chain(session)
