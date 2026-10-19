"""Customer relationships: open and close (SP-02 spec §9).

Closing starts the party's retention clock from the effective policy. A tenant
whose pack assignments do not cover the relationship answers 404
``POLICY_NOT_ASSIGNED`` on either route.
"""

from uuid import UUID

from xolotl.modules.party import customer
from xolotl.modules.party.routes import PartyWriter
from xolotl.modules.party.schemas import (
    CustomerRelationshipResponse,
    RelationshipCloseRequest,
    RelationshipOpenRequest,
)
from xolotl.platform.api.router import v1_router
from xolotl.platform.auth.principal import SessionDep

router = v1_router(prefix="/relationships", tags=["relationships"])


@router.post("", status_code=201)
def open_customer_relationship(
    body: RelationshipOpenRequest, _writer: PartyWriter, session: SessionDep
) -> CustomerRelationshipResponse:
    row = customer.open_relationship(session, body)
    return CustomerRelationshipResponse.model_validate(row)


@router.post("/{relationship_id}/close")
def close_customer_relationship(
    relationship_id: UUID,
    body: RelationshipCloseRequest,
    _writer: PartyWriter,
    session: SessionDep,
) -> CustomerRelationshipResponse:
    row = customer.close_relationship(session, relationship_id, ended_at=body.ended_at)
    return CustomerRelationshipResponse.model_validate(row)
