"""The party API (SP-02 spec §9, program spec D20).

Staff of any role and API keys with ``parties:read`` / ``parties:write`` share these
routes. Every view is masked; only ``/pii`` shows plaintext, to staff, through the
service's audited reveal. Routes hold no business rule.
"""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, Query

from xolotl.modules.party import service
from xolotl.modules.party.enums import IdentifierType, PartyStatus
from xolotl.modules.party.models import Party
from xolotl.modules.party.schemas import (
    AddressInput,
    AddressResponse,
    ContactInput,
    IdentifierInput,
    MaskedContact,
    MaskedIdentifier,
    PartyCreateRequest,
    PartyPiiResponse,
    PartyResponse,
    PartyStaffResponse,
)
from xolotl.platform.api.pagination import Page, PageDep, paginate
from xolotl.platform.api.router import v1_router
from xolotl.platform.auth.actor import Actor
from xolotl.platform.auth.principal import SessionDep, require_access

router = v1_router(prefix="/parties", tags=["parties"])

PartyReader = Annotated[Actor, Depends(require_access(scope="parties:read"))]
PartyWriter = Annotated[
    Actor, Depends(require_access(scope="parties:write", write=True))
]
# Staff only: a route without a scope refuses every API client.
PartyStaff = Annotated[Actor, Depends(require_access())]


@router.post("", status_code=201)
def create_party(
    body: PartyCreateRequest, _writer: PartyWriter, session: SessionDep
) -> PartyResponse:
    return service.summary(session, service.create_party(session, body))


@router.get("")
def list_parties(
    _reader: PartyReader,
    session: SessionDep,
    page: PageDep,
    status: PartyStatus | None = None,
    external_ref: Annotated[str | None, Query(max_length=100)] = None,
    id_type: IdentifierType | None = None,
    value: Annotated[str | None, Query(max_length=64)] = None,
) -> Page[PartyResponse]:
    query = service.parties_query(
        session, status=status, external_ref=external_ref, id_type=id_type, value=value
    )
    rows, next_cursor = paginate(
        session,
        query,
        ts_col=Party.created_at,
        id_col=Party.id,
        key=lambda row: (row.created_at, row.id),
        page=page,
    )
    return Page(items=service.summaries(session, rows), next_cursor=next_cursor)


@router.get("/{party_id}")
def get_party(
    party_id: UUID, actor: PartyReader, session: SessionDep
) -> PartyStaffResponse | PartyResponse:
    if actor.is_staff:
        return service.staff_view(session, party_id)
    return service.summary(session, service.get_party(session, party_id))


@router.get("/{party_id}/pii")
def reveal_party_pii(
    party_id: UUID, _staff: PartyStaff, session: SessionDep
) -> PartyPiiResponse:
    return service.reveal_pii(session, party_id)


@router.post("/{party_id}/identifiers", status_code=201)
def add_party_identifier(
    party_id: UUID, body: IdentifierInput, _writer: PartyWriter, session: SessionDep
) -> MaskedIdentifier:
    row = service.add_identifier(session, party_id, body)
    return service.masked_identifier(session, row)


@router.post("/{party_id}/contacts", status_code=201)
def add_party_contact(
    party_id: UUID, body: ContactInput, _writer: PartyWriter, session: SessionDep
) -> MaskedContact:
    row = service.add_contact(session, party_id, body)
    return service.masked_contact(session, row)


@router.post("/{party_id}/addresses", status_code=201)
def add_party_address(
    party_id: UUID, body: AddressInput, _writer: PartyWriter, session: SessionDep
) -> AddressResponse:
    return AddressResponse.model_validate(service.add_address(session, party_id, body))
