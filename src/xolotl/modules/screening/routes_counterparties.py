"""The staff counterparties API (SP-04 plan 4c, C3; spec §15, §17).

``GET /v1/counterparties`` lists the tenant's counterparties masked, newest last
sighting first; the detail adds the findings and runs of the counterparty's subject;
the reveal is A10's ``reveal_counterparty``, one ``VIEW_PII`` per value. Every route
is staff only (``require_access()`` without a scope refuses every API client): a
counterparty's screening status never reaches a client (tipping-off, spec §15). Any
staff role reads and reveals, as for parties and the match reveal. Another tenant's
counterparty is ``NOT_FOUND`` (row-level security). Routes hold no business rule.
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import Depends

from xolotl.modules.screening.counterparty import api
from xolotl.modules.screening.counterparty.models import Counterparty
from xolotl.modules.screening.counterparty.schema import (
    CounterpartyKind,
    CounterpartyScreeningStatus,
)
from xolotl.modules.screening.counterparty.schemas import (
    CounterpartyDetail,
    CounterpartyResponse,
    RevealedCounterparty,
)
from xolotl.modules.screening.counterparty.views import reveal_counterparty
from xolotl.platform.api.pagination import Page, PageDep, paginate
from xolotl.platform.api.router import v1_router
from xolotl.platform.auth.actor import Actor
from xolotl.platform.auth.principal import SessionDep, require_access

router = v1_router(prefix="/counterparties", tags=["screening"])

# Staff only: a route without a scope refuses every API client.
CounterpartyStaff = Annotated[Actor, Depends(require_access())]


@router.get("")
def list_counterparties(
    _staff: CounterpartyStaff,
    session: SessionDep,
    page: PageDep,
    status: CounterpartyScreeningStatus | None = None,
    kind: CounterpartyKind | None = None,
    seen_after: datetime | None = None,
) -> Page[CounterpartyResponse]:
    rows, next_cursor = paginate(
        session,
        api.counterparties_query(status=status, kind=kind, seen_after=seen_after),
        ts_col=Counterparty.last_seen_at,
        id_col=Counterparty.id,
        key=lambda row: (row.last_seen_at, row.id),
        page=page,
    )
    return Page(
        items=[api.counterparty_row(session, row) for row in rows],
        next_cursor=next_cursor,
    )


@router.get("/{counterparty_id}")
def get_counterparty(
    counterparty_id: UUID, _staff: CounterpartyStaff, session: SessionDep
) -> CounterpartyDetail:
    return api.counterparty_detail(session, counterparty_id)


@router.get("/{counterparty_id}/reveal")
def reveal_counterparty_endpoint(
    counterparty_id: UUID, _staff: CounterpartyStaff, session: SessionDep
) -> RevealedCounterparty:
    return reveal_counterparty(session, counterparty_id)
