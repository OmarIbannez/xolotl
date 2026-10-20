"""The party name index: normalized tokens per party name (program spec D7)."""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import Engine, select

from tests.helpers import new_tenant, staff_session
from xolotl.modules.party import service
from xolotl.modules.party.enums import PartyType
from xolotl.modules.party.retention import purge_party
from xolotl.modules.party.schemas import (
    OrganizationInput,
    OrganizationUpdate,
    PartyCreateRequest,
    PartyUpdateRequest,
    PersonInput,
)
from xolotl.modules.screening.engine.enums import NameKind
from xolotl.modules.screening.engine.models import PartyNameIndex
from xolotl.modules.screening.engine.names import index_party_names
from xolotl.modules.screening.lists import EntityType
from xolotl.modules.screening.matching import (
    MATCHER_VERSION,
    NormalizedName,
    TokenRole,
    normalize_name,
)
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform import clock

JUAN = PartyCreateRequest(
    party_type=PartyType.PERSON,
    person=PersonInput(
        given_names="Juan",
        first_surname="de la Cruz",
        second_surname="Pérez",
        birth_date=date(1970, 3, 1),
    ),
)
ACME = PartyCreateRequest(
    party_type=PartyType.ORGANIZATION,
    organization=OrganizationInput(
        legal_name="Acme Servicios, S.A. de C.V.", trade_name="Acme Café"
    ),
)


def _expected(name: NormalizedName) -> tuple[str, list[str], list[str], str]:
    return (
        name.folded,
        [t.folded for t in name.tokens],
        sorted({
            t.phonetic
            for t in name.tokens
            if t.role not in {TokenRole.PARTICLE, TokenRole.LEGAL_FORM}
        }),
        MATCHER_VERSION,
    )


def _shape(row: PartyNameIndex) -> tuple[str, list[str], list[str], str]:
    return (row.norm_name, row.tokens, row.phonetic_keys, row.matcher_version)


def test_a_person_is_indexed_with_the_librarys_normalization(
    engine: Engine, tenant: CreatedTenant
):
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        party = service.create_party(s, JUAN)
        rows = index_party_names(s, party.id)
    (row,) = rows
    expected = normalize_name(
        "Juan de la Cruz Pérez",
        entity_type=EntityType.PERSON,
        given="Juan",
        surname="de la Cruz Pérez",
    )
    assert row.name_kind is NameKind.LEGAL
    assert _shape(row) == _expected(expected)
    assert row.norm_name == "juan de la cruz perez"
    # The particles are kept in the tokens but give no phonetic key.
    assert "de" in row.tokens
    assert len(row.phonetic_keys) == len({"juan", "cruz", "perez"})


def test_an_organization_gets_a_legal_and_a_trade_row(
    engine: Engine, tenant: CreatedTenant
):
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        party = service.create_party(s, ACME)
        legal, trade = index_party_names(s, party.id)
    assert (legal.name_kind, trade.name_kind) == (NameKind.LEGAL, NameKind.TRADE)
    organization = EntityType.ORGANIZATION
    assert _shape(legal) == _expected(
        normalize_name("Acme Servicios, S.A. de C.V.", entity_type=organization)
    )
    assert _shape(trade) == _expected(
        normalize_name("Acme Café", entity_type=organization)
    )
    assert trade.norm_name == "acme cafe"


def test_removing_the_trade_name_removes_its_row(engine: Engine, tenant: CreatedTenant):
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        party = service.create_party(s, ACME)
        assert len(index_party_names(s, party.id)) == 2
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        _ = service.update_party(
            s,
            party.id,
            PartyUpdateRequest(organization=OrganizationUpdate(trade_name=None)),
        )
        (legal,) = index_party_names(s, party.id)
        assert legal.name_kind is NameKind.LEGAL
        kinds = s.scalars(
            select(PartyNameIndex.name_kind).where(PartyNameIndex.party_id == party.id)
        ).all()
    assert kinds == [NameKind.LEGAL]


def test_reindexing_is_an_upsert(
    engine: Engine, tenant: CreatedTenant, monkeypatch: pytest.MonkeyPatch
):
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        party = service.create_party(s, ACME)
        first = {
            r.name_kind: (r.id, r.updated_at) for r in index_party_names(s, party.id)
        }
    later = datetime.now(UTC) + timedelta(hours=1)
    monkeypatch.setattr(clock, "now", lambda: later)
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        again = index_party_names(s, party.id)
        count = len(
            s.scalars(
                select(PartyNameIndex).where(PartyNameIndex.party_id == party.id)
            ).all()
        )
    assert count == 2
    for row in again:
        row_id, updated_at = first[row.name_kind]
        assert row.id == row_id
        assert row.updated_at > updated_at
        assert row.updated_at == later


def test_a_purged_party_has_no_name_index_rows(engine: Engine, tenant: CreatedTenant):
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        party = service.create_party(s, ACME)
        assert len(index_party_names(s, party.id)) == 2
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        _ = purge_party(s, service.get_party(s, party.id))
        assert index_party_names(s, party.id) == []
        left = s.scalars(
            select(PartyNameIndex).where(PartyNameIndex.party_id == party.id)
        ).all()
    assert left == []


def test_the_name_index_is_invisible_to_another_tenant(
    engine: Engine, tenant: CreatedTenant
):
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        party = service.create_party(s, JUAN)
        _ = index_party_names(s, party.id)
    other = new_tenant("beta", "admin@beta.mx")
    with staff_session(other.tenant_id, other.admin_user_id) as s:
        assert s.scalars(select(PartyNameIndex)).all() == []
