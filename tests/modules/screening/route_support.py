"""Clients, lists and findings for the screening route tests (E7).

A person on OFAC's list is checked through the real application, so every finding
these tests decide on comes from ``screen()`` with a stored hit and explanation.
"""

from datetime import date
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import Engine, inspect, literal_column, select, table

from tests.modules.party.route_support import machine_headers, post, staff_headers
from tests.modules.screening.catalog.support import load_shared, seed_sources
from xolotl.models import metadata
from xolotl.modules.party.enums import IdentifierType, PartyType
from xolotl.modules.party.schemas import (
    IdentifierInput,
    PartyCreateRequest,
    PersonInput,
)
from xolotl.modules.screening.lists import (
    EntityType,
    EntryIdentifier,
    EntryName,
    IdType,
    ListEntry,
    NameQuality,
    PartialDate,
)
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform.enums import Role
from xolotl.platform.storage import ObjectStore

SCREENING = "/v1/screening"
CHECKS = f"{SCREENING}/checks"
MATCHES = f"{SCREENING}/matches"
OFAC = "OFAC_SDN"
LPB = "MX_LPB"
BIRTH = date(1970, 5, 10)
RFC = "PEGJ700510XY2"
JUAN = ListEntry(
    list_code=OFAC,
    entry_key="7001",
    entity_type=EntityType.PERSON,
    primary_name=EntryName("JUAN CARLOS PEREZ GOMEZ", NameQuality.PRIMARY),
    birth_dates=(PartialDate(1970, 5, 10),),
    identifiers=(EntryIdentifier(IdType.PASSPORT, "X0000001", "X0000001"),),
    source_ref="sdn 7001",
)
BLOCKED = ListEntry(
    list_code=LPB,
    entry_key="LPB-1",
    entity_type=EntityType.PERSON,
    primary_name=EntryName("JUAN CARLOS PEREZ GOMEZ", NameQuality.PRIMARY),
    birth_dates=(PartialDate(1970, 5, 10),),
    source_ref="clave LPB-1",
)
PERSON = PartyCreateRequest(
    party_type=PartyType.PERSON,
    person=PersonInput(
        given_names="Juan Carlos",
        first_surname="Pérez",
        second_surname="Gómez",
        birth_date=BIRTH,
    ),
    identifiers=[IdentifierInput(id_type=IdentifierType.RFC, value=RFC)],
)
NOBODY = PartyCreateRequest(
    party_type=PartyType.PERSON,
    person=PersonInput(given_names="Citlali", first_surname="Xochitl"),
)
AD_HOC = {
    "type": "PERSON",
    "names": ["Juan Carlos Pérez Gómez"],
    "given_names": "Juan Carlos",
    "first_surname": "Pérez",
    "second_surname": "Gómez",
    "birth_date": BIRTH.isoformat(),
}


def seed_lists(admin_engine: Engine, store: ObjectStore) -> None:
    """Seed the list catalog and load OFAC with Juan Carlos on it."""
    seed_sources(admin_engine)
    _ = load_shared(OFAC, (JUAN,), store=store)


def staff_client(
    client: TestClient, tenant: CreatedTenant, role: Role
) -> dict[str, str]:
    """Sign in a new staff member of the tenant with one role.

    Returns:
        A mapping holding the member's ``Authorization`` header.

    """
    email = f"{role.value.lower()}.{uuid4().hex[:8]}@acme.mx"
    return staff_headers(client, tenant, email=email, roles=(role,))


def api_client(tenant: CreatedTenant, *scopes: str) -> dict[str, str]:
    """Issue an API key of the tenant with ``scopes``.

    Returns:
        A mapping holding the key's ``Authorization`` header.

    """
    return machine_headers(tenant, *scopes)


def create_party(
    client: TestClient,
    headers: dict[str, str],
    body: PartyCreateRequest = PERSON,
) -> str:
    """Create a party through the API.

    Returns:
        The party id.

    """
    created = post(
        client, headers, "/v1/parties", body.model_dump(mode="json"), uuid4().hex
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


def check_party(client: TestClient, headers: dict[str, str], party_id: str) -> dict:
    """Check a party through the API.

    Returns:
        The response body.

    """
    checked = post(client, headers, CHECKS, {"party_id": party_id}, uuid4().hex)
    assert checked.status_code == 201, checked.text
    return checked.json()


def seed_match(client: TestClient, headers: dict[str, str]) -> tuple[str, str]:
    """Create Juan Carlos as a party and check him, raising one OFAC finding.

    Returns:
        ``(party id, match id)``.

    """
    party_id = create_party(client, headers)
    body = check_party(client, headers, party_id)
    [match_id] = {h["match_id"] for h in body["hits"] if h["match_id"]}
    return party_id, match_id


def dispose(
    client: TestClient,
    headers: dict[str, str],
    match_id: str,
    decision: str,
    rationale: str = "Fecha de nacimiento distinta",
):
    """Post a disposition on a finding.

    Returns:
        The response.

    """
    return post(
        client,
        headers,
        f"{MATCHES}/{match_id}/dispositions",
        {"decision": decision, "rationale": rationale},
        uuid4().hex,
    )


# Every row of every table as text, like ``everything_stored``, but without what D7
# allows in plaintext: ``party_name_index`` and ``screening_hit.features``.
def stored_beyond_name_index(admin_engine: Engine) -> str:
    with admin_engine.connect() as conn:
        present = set(inspect(conn).get_table_names())
        dumps: list[str] = []
        for name, tbl in metadata().tables.items():
            if name not in present or name == "party_name_index":
                continue
            if name == "screening_hit":
                columns = [c for c in tbl.columns if c.name != "features"]
                dumps.append(repr(conn.execute(select(*columns)).all()))
                continue
            dumps.append(
                repr(
                    conn.execute(
                        select(literal_column("*")).select_from(table(name))
                    ).all()
                )
            )
    return "\n".join(dumps)
