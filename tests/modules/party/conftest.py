"""Sample parties and outbox readers shared by the party tests.

The readers and ``RolledBackError`` repeat those of
``tests/modules/tenants/test_kernel_events.py``: a test module is never imported from
another one.
"""

import json
from datetime import date
from uuid import UUID

from sqlalchemy import select

from tests.helpers import system_ctx
from xolotl.modules.party.enums import IdentifierType, PartyType
from xolotl.modules.party.schemas import (
    IdentifierInput,
    OrganizationInput,
    PartyCreateRequest,
    PersonInput,
)
from xolotl.platform.db import tenant_session
from xolotl.platform.events.event import Event, event_from_row
from xolotl.platform.events.models import OutboxRow

ANA = PartyCreateRequest(
    party_type=PartyType.PERSON,
    person=PersonInput(
        given_names="Ana María",
        first_surname="Pérez",
        second_surname="Gómez",
        birth_date=date(1976, 5, 10),
    ),
    identifiers=[
        IdentifierInput(id_type=IdentifierType.RFC, value="pega-760510-xy0"),
        IdentifierInput(id_type=IdentifierType.CURP, value="PEGA760510MDFRMN03"),
    ],
)
CARLOS = PartyCreateRequest(
    party_type=PartyType.PERSON,
    person=PersonInput(
        given_names="Carlos",
        first_surname="Sánchez",
        second_surname="Gómez",
        birth_date=date(1980, 1, 1),
    ),
    identifiers=[
        IdentifierInput(id_type=IdentifierType.RFC, value="SAGC800101AB8"),
        IdentifierInput(id_type=IdentifierType.CURP, value="SAGC800101HJCNMR02"),
    ],
)
ACME = PartyCreateRequest(
    party_type=PartyType.ORGANIZATION,
    organization=OrganizationInput(
        legal_name="Acme Servicios SA de CV", trade_name="Acme"
    ),
    identifiers=[IdentifierInput(id_type=IdentifierType.RFC, value="ACM0101017A0")],
)


class RolledBackError(Exception):
    """Raised inside the caller's transaction after the service call succeeded."""


def events_of(tenant_id: UUID) -> list[Event]:
    with tenant_session(system_ctx(tenant_id)) as s:
        rows = s.scalars(select(OutboxRow).order_by(OutboxRow.seq)).all()
        return [event_from_row(row) for row in rows]


def of_type(tenant_id: UUID, event_type: str) -> list[Event]:
    return [event for event in events_of(tenant_id) if event.type == event_type]


def payloads_of(tenant_id: UUID) -> str:
    """Every stored outbox row of the tenant, as the JSON text a leak would show in.

    Returns:
        The type, key and payload of each row, as one JSON document.

    """
    with tenant_session(system_ctx(tenant_id)) as s:
        rows = s.scalars(select(OutboxRow).order_by(OutboxRow.seq)).all()
        return json.dumps(
            [[row.type, row.key, row.payload] for row in rows], ensure_ascii=False
        )
