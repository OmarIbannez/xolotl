"""Policy packs, the effective policy, the regulatory profile and branches (SP-02 §9).

Staff of any role read; only an ``ADMIN`` assigns packs, tightens the policy with an
override, sets the regulatory profile or adds a branch. API keys have no scope here.
"""

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import Depends
from sqlalchemy import select

from xolotl.modules.tenants import policy_service
from xolotl.modules.tenants.models import TenantBranch
from xolotl.modules.tenants.schemas_policy import (
    AssignmentCreateRequest,
    AssignmentResponse,
    BranchCreateRequest,
    BranchResponse,
    EffectivePolicyResponse,
    OverrideResponse,
    PackVersionResponse,
    RegulatoryProfileRequest,
    RegulatoryProfileResponse,
)
from xolotl.platform import clock
from xolotl.platform.api.pagination import Page, PageDep, paginate
from xolotl.platform.api.router import v1_router
from xolotl.platform.auth.actor import Actor
from xolotl.platform.auth.principal import SessionDep, require_access
from xolotl.platform.enums import Role
from xolotl.platform.policy import (
    PolicyPack,
    PolicyPackVersion,
    TenantOverride,
    TenantOverrideVersion,
    TenantPackAssignment,
    assign_pack,
    create_override,
)

router = v1_router(tags=["policy"])

StaffReader = Annotated[Actor, Depends(require_access())]
AdminWriter = Annotated[Actor, Depends(require_access(roles=(Role.ADMIN,), write=True))]


@router.get("/policy/packs")
def list_policy_packs(
    _reader: StaffReader, session: SessionDep, page: PageDep
) -> Page[PackVersionResponse]:
    rows, next_cursor = paginate(
        session,
        select(PolicyPackVersion),
        ts_col=PolicyPackVersion.loaded_at,
        id_col=PolicyPackVersion.id,
        key=lambda row: (row.loaded_at, row.id),
        page=page,
    )
    packs = {pack.id: pack for pack in session.scalars(select(PolicyPack))}
    return Page(
        items=[
            PackVersionResponse(
                id=row.id,
                code=packs[row.pack_id].code,
                version=row.version,
                title=packs[row.pack_id].title,
                effective_from=row.effective_from,
                effective_to=row.effective_to,
                content_hash=row.content_hash.hex(),
                loaded_at=row.loaded_at,
            )
            for row in rows
        ],
        next_cursor=next_cursor,
    )


@router.get("/policy/effective")
def get_effective_policy(
    _reader: StaffReader,
    session: SessionDep,
    on: date | None = None,
    product: str | None = None,
) -> EffectivePolicyResponse:
    return policy_service.effective_with_conversions(
        session, on=on or clock.now().date(), product=product
    )


@router.post("/policy/assignments", status_code=201)
def create_pack_assignment(
    body: AssignmentCreateRequest, actor: AdminWriter, session: SessionDep
) -> AssignmentResponse:
    assignment_id = assign_pack(
        session,
        pack_version_id=body.pack_version_id,
        product=body.product,
        effective_from=body.effective_from,
        approved_by=UUID(actor.context.actor_id),
    )
    row = session.get_one(
        TenantPackAssignment,
        (actor.context.require_tenant_id(), assignment_id),
    )
    return policy_service.assignment_responses(session, [row])[0]


@router.get("/policy/assignments")
def list_pack_assignments(
    _reader: StaffReader, session: SessionDep, page: PageDep
) -> Page[AssignmentResponse]:
    rows, next_cursor = paginate(
        session,
        select(TenantPackAssignment),
        ts_col=TenantPackAssignment.created_at,
        id_col=TenantPackAssignment.id,
        key=lambda row: (row.created_at, row.id),
        page=page,
    )
    return Page(
        items=policy_service.assignment_responses(session, rows),
        next_cursor=next_cursor,
    )


@router.post("/policy/overrides", status_code=201)
def create_policy_override(
    body: TenantOverride, actor: AdminWriter, session: SessionDep
) -> OverrideResponse:
    version = create_override(session, body, approved_by=UUID(actor.context.actor_id))
    row = session.scalars(
        select(TenantOverrideVersion).where(TenantOverrideVersion.version == version)
    ).one()
    return OverrideResponse.model_validate(row)


@router.get("/policy/overrides")
def list_policy_overrides(
    _reader: StaffReader, session: SessionDep, page: PageDep
) -> Page[OverrideResponse]:
    rows, next_cursor = paginate(
        session,
        select(TenantOverrideVersion),
        ts_col=TenantOverrideVersion.created_at,
        id_col=TenantOverrideVersion.id,
        key=lambda row: (row.created_at, row.id),
        page=page,
    )
    return Page(
        items=[OverrideResponse.model_validate(row) for row in rows],
        next_cursor=next_cursor,
    )


@router.get("/tenants/me/regulatory-profile")
def get_regulatory_profile(
    _reader: StaffReader, session: SessionDep
) -> RegulatoryProfileResponse:
    return policy_service.get_regulatory_profile(session)


@router.put("/tenants/me/regulatory-profile")
def set_regulatory_profile(
    body: RegulatoryProfileRequest, _admin: AdminWriter, session: SessionDep
) -> RegulatoryProfileResponse:
    return policy_service.set_regulatory_profile(session, body)


@router.post("/tenants/me/branches", status_code=201)
def create_branch(
    body: BranchCreateRequest, _admin: AdminWriter, session: SessionDep
) -> BranchResponse:
    return policy_service.create_branch(session, body)


@router.get("/tenants/me/branches")
def list_branches(
    _reader: StaffReader, session: SessionDep, page: PageDep
) -> Page[BranchResponse]:
    rows, next_cursor = paginate(
        session,
        select(TenantBranch),
        ts_col=TenantBranch.created_at,
        id_col=TenantBranch.id,
        key=lambda row: (row.created_at, row.id),
        page=page,
    )
    return Page(
        items=[policy_service.branch_response(row) for row in rows],
        next_cursor=next_cursor,
    )
