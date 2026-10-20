"""The screening API for staff and API clients (SP-03 spec §9, §12).

Checks, runs and party views are shared: API keys with ``screening:read`` /
``screening:write`` get status fields only (FR-SC-013), staff the full typed
explanation. The match queue, its detail, the reveal and dispositions are staff only;
revoking a whitelist row is the MLRO's. Re-screen batch progress is staff only (spec
§12). Another tenant's rows are ``NOT_FOUND``
(row-level security). Routes hold no business rule.
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Query, Response
from sqlalchemy import select

from xolotl.modules.screening import api
from xolotl.modules.screening.api import CheckRequest
from xolotl.modules.screening.engine.enums import (
    BatchKind,
    BatchStatus,
    Decision,
    HitBand,
    MatchStatus,
)
from xolotl.modules.screening.engine.models import ScreeningBatch, ScreeningMatch
from xolotl.modules.screening.enums import ListKind
from xolotl.modules.screening.review.dispositions import disposition_match
from xolotl.modules.screening.review.whitelist import revoke_whitelist
from xolotl.modules.screening.schemas import (
    BatchResponse,
    CheckResponse,
    CheckStaffResponse,
    DispositionRequest,
    DispositionResponse,
    MatchDetail,
    MatchReveal,
    MatchSummary,
    PartyScreeningResponse,
    PartyScreeningStaffResponse,
    RunResponse,
    RunStaffResponse,
)
from xolotl.platform.api.pagination import Page, PageDep, paginate
from xolotl.platform.api.router import v1_router
from xolotl.platform.auth.actor import Actor
from xolotl.platform.auth.principal import SessionDep, require_access
from xolotl.platform.enums import Role

router = v1_router(prefix="/screening", tags=["screening"])

ScreeningReader = Annotated[Actor, Depends(require_access(scope="screening:read"))]
ScreeningWriter = Annotated[
    Actor, Depends(require_access(scope="screening:write", write=True))
]
# Staff only: a route without a scope refuses every API client.
ScreeningStaff = Annotated[Actor, Depends(require_access())]
ScreeningStaffWriter = Annotated[Actor, Depends(require_access(write=True))]
WhitelistRevoker = Annotated[Actor, Depends(require_access(roles=(Role.MLRO,)))]


@router.post("/checks", status_code=201)
def create_check(
    body: CheckRequest, actor: ScreeningWriter, session: SessionDep
) -> CheckStaffResponse | CheckResponse:
    result = api.run_check(session, body)
    return api.check_response(result, staff=actor.is_staff)


@router.get("/runs/{run_id}")
def get_run(
    run_id: UUID, actor: ScreeningReader, session: SessionDep
) -> RunStaffResponse | RunResponse:
    return api.run_view(session, run_id, staff=actor.is_staff)


@router.get("/parties/{party_id}")
def get_party_screening(
    party_id: UUID, actor: ScreeningReader, session: SessionDep
) -> PartyScreeningStaffResponse | PartyScreeningResponse:
    return api.party_view(session, party_id, staff=actor.is_staff)


@router.get("/matches")
def list_matches(
    _staff: ScreeningStaff,
    session: SessionDep,
    page: PageDep,
    status: MatchStatus | None = None,
    band: HitBand | None = None,
    list_code: Annotated[str | None, Query(max_length=100)] = None,
    list_kind: ListKind | None = None,
    created_before: datetime | None = None,
) -> Page[MatchSummary]:
    query = api.matches_query(
        status=status,
        band=band,
        list_code=list_code,
        list_kind=list_kind,
        created_before=created_before,
    )
    rows, next_cursor = paginate(
        session,
        query,
        ts_col=ScreeningMatch.created_at,
        id_col=ScreeningMatch.id,
        key=lambda row: (row.created_at, row.id),
        page=page,
    )
    return Page(items=api.match_summaries(session, rows), next_cursor=next_cursor)


@router.get("/matches/{match_id}")
def get_match(
    match_id: UUID, _staff: ScreeningStaff, session: SessionDep
) -> MatchDetail:
    return api.match_detail(session, match_id)


@router.get("/matches/{match_id}/reveal")
def reveal_match(
    match_id: UUID, _staff: ScreeningStaff, session: SessionDep
) -> MatchReveal:
    return api.reveal_match(session, match_id)


@router.post("/matches/{match_id}/dispositions", status_code=201)
def create_disposition(
    match_id: UUID,
    body: DispositionRequest,
    _staff: ScreeningStaffWriter,
    session: SessionDep,
) -> DispositionResponse:
    disposition = disposition_match(
        session, match_id, decision=Decision(body.decision), rationale=body.rationale
    )
    # The server-side decided_at is read back before the response is built.
    session.refresh(disposition)
    status = session.scalars(
        select(ScreeningMatch.status).where(ScreeningMatch.id == match_id)
    ).one()
    return DispositionResponse(
        id=disposition.id,
        match_id=match_id,
        decision=disposition.decision,
        match_status=status,
        decided_at=disposition.decided_at,
    )


@router.delete("/whitelist/{whitelist_id}", status_code=204)
def revoke_whitelist_entry(
    whitelist_id: UUID, _mlro: WhitelistRevoker, session: SessionDep
) -> Response:
    _ = revoke_whitelist(session, whitelist_id)
    return Response(status_code=204)


@router.get("/batches")
def list_screening_batches(
    _staff: ScreeningStaff,
    session: SessionDep,
    page: PageDep,
    kind: BatchKind | None = None,
    status: BatchStatus | None = None,
) -> Page[BatchResponse]:
    rows, next_cursor = paginate(
        session,
        api.batches_query(kind=kind, status=status),
        ts_col=ScreeningBatch.started_at,
        id_col=ScreeningBatch.id,
        key=lambda row: (row.started_at, row.id),
        page=page,
    )
    return Page(
        items=[api.batch_response(row) for row in rows], next_cursor=next_cursor
    )
