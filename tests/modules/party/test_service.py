"""The party service: creation, identifiers, contacts, addresses, views and reveal.

Whenever a test expects an exception from inside a session, ``pytest.raises`` is the
outermost context manager, so the error crosses the session and rolls it back: nested
the other way round, the session would commit whatever the service flushed before it
raised.
"""

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import Engine, func, select

from tests.helpers import (
    audit_events,
    everything_stored,
    leaked,
    new_tenant,
    staff_session,
    system_ctx,
)
from tests.modules.party.conftest import (
    ACME,
    ANA,
    RolledBackError,
    events_of,
    of_type,
    payloads_of,
)
from xolotl.modules.party import service
from xolotl.modules.party.enums import (
    AddressType,
    ContactKind,
    IdentifierType,
    PartyType,
    PepStatus,
    RelationshipType,
)
from xolotl.modules.party.errors import IdentifierExistsError, IdentifierInvalidError
from xolotl.modules.party.models import Party, PartyIdentifier, Person
from xolotl.modules.party.schemas import (
    AddressInput,
    AddressLines,
    ContactInput,
    IdentifierInput,
    OrganizationUpdate,
    PartyRelationshipInput,
    PartyUpdateRequest,
    PersonUpdate,
)
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform.audit import VIEW_PII, verify_chain
from xolotl.platform.crypto import Encrypted
from xolotl.platform.db import tenant_session
from xolotl.platform.enums import ActorType
from xolotl.platform.errors import DomainError, NotFoundError, ValidationFailedError
from xolotl.platform.ids import uuid7
from xolotl.platform.tenancy.context import TenantContext


def _party_count(tenant: CreatedTenant) -> int | None:
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        return s.scalar(select(func.count()).select_from(Party))


def test_a_person_is_created_with_encrypted_names_and_validated_identifiers(
    engine: Engine, tenant: CreatedTenant, admin_engine: Engine
):
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        party = service.create_party(s, ANA)
        person = s.get_one(Person, (tenant.tenant_id, party.id))
        assert isinstance(person.given_names, Encrypted)
        ids = s.scalars(
            select(PartyIdentifier)
            .where(PartyIdentifier.party_id == party.id)
            .order_by(PartyIdentifier.created_at, PartyIdentifier.id)
        ).all()
        assert sorted(i.id_type for i in ids) == [
            IdentifierType.CURP,
            IdentifierType.RFC,
        ]
        assert all(len(i.value_hash) == 32 for i in ids)
    assert audit_events(tenant.tenant_id)[-1].action == "party.created"
    (made,) = of_type(tenant.tenant_id, "party.created.v1")
    assert made.data == {
        "party_id": str(party.id),
        "party_type": "PERSON",
        "identifier_ids": [str(i.id) for i in ids],
    }
    assert made.key == str(party.id)
    assert made.subject == f"party/{party.id}"
    assert made.source == "xolotl.party"
    stored = everything_stored(admin_engine)
    payloads = payloads_of(tenant.tenant_id)
    for secret in (
        "Ana María",
        "Pérez",
        "PEGA760510XY0",
        "PEGA760510MDFRMN03",
        "1976-05-10",
    ):
        assert leaked(stored, secret.encode()) == []
        assert secret not in payloads


def test_identifier_spellings_collapse_to_one_blind_index_and_a_duplicate_is_refused(
    engine: Engine, tenant: CreatedTenant
):
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        first = service.create_party(s, ANA)
    again = ANA.model_copy(
        update={
            "identifiers": [
                IdentifierInput(id_type=IdentifierType.RFC, value=" PEGA 760510 xy0 ")
            ]
        }
    )
    with (
        pytest.raises(IdentifierExistsError) as refused,
        staff_session(tenant.tenant_id, tenant.admin_user_id) as s,
    ):
        _ = service.create_party(s, again)
    assert (refused.value.code, refused.value.detail) == (
        "IDENTIFIER_EXISTS",
        str(first.id),
    )
    machine = TenantContext(
        tenant_id=tenant.tenant_id, actor_type=ActorType.API_CLIENT, actor_id="key:1"
    )
    with pytest.raises(IdentifierExistsError) as refused, tenant_session(machine) as s:
        _ = service.create_party(s, again)
    assert refused.value.detail == "a party with this identifier already exists"
    assert _party_count(tenant) == 1


def test_an_invalid_identifier_is_refused_with_its_reason_and_without_its_value(
    engine: Engine, tenant: CreatedTenant
):
    bad = ANA.model_copy(
        update={
            "identifiers": [
                IdentifierInput(id_type=IdentifierType.RFC, value="EKU9003173C1")
            ]
        }
    )
    with (
        pytest.raises(IdentifierInvalidError) as refused,
        staff_session(tenant.tenant_id, tenant.admin_user_id) as s,
    ):
        _ = service.create_party(s, bad)
    assert (refused.value.code, refused.value.detail) == (
        "IDENTIFIER_INVALID",
        "RFC: CHECK_DIGIT",
    )
    assert "EKU9003173C1" not in str(refused.value)
    assert _party_count(tenant) == 0


def test_a_curp_must_agree_with_the_declared_birth_date(
    engine: Engine, tenant: CreatedTenant
):
    # ANA lists the RFC first, whose own date check would fail before the CURP is
    # reached, so the request carries the CURP alone.
    assert ANA.person is not None
    request = ANA.model_copy(
        update={
            "person": ANA.person.model_copy(update={"birth_date": date(1976, 5, 11)}),
            "identifiers": [
                IdentifierInput(id_type=IdentifierType.CURP, value="PEGA760510MDFRMN03")
            ],
        }
    )
    with (
        pytest.raises(IdentifierInvalidError) as refused,
        staff_session(tenant.tenant_id, tenant.admin_user_id) as s,
    ):
        _ = service.create_party(s, request)
    assert refused.value.detail == "CURP: BIRTH_DATE_MISMATCH"
    assert _party_count(tenant) == 0


def test_search_by_identifier_uses_the_blind_index_and_never_decrypts(
    engine: Engine, tenant: CreatedTenant
):
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        party = service.create_party(s, ANA)
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        found = s.scalars(
            service.parties_query(
                s, id_type=IdentifierType.RFC, value="pega 760510 xy0"
            )
        ).all()
        assert [p.id for p in found] == [party.id]
        other = s.scalars(
            service.parties_query(s, id_type=IdentifierType.RFC, value="EKU9003173C9")
        ).all()
        assert other == []
    with (
        pytest.raises(IdentifierInvalidError),
        staff_session(tenant.tenant_id, tenant.admin_user_id) as s,
    ):
        _ = service.parties_query(s, id_type=IdentifierType.RFC, value="EKU9003173C1")
    with (
        pytest.raises(ValidationFailedError) as refused,
        staff_session(tenant.tenant_id, tenant.admin_user_id) as s,
    ):
        _ = service.parties_query(s, id_type=IdentifierType.RFC)
    assert refused.value.detail == "id_type and value go together"
    assert [e for e in audit_events(tenant.tenant_id) if e.action == VIEW_PII] == []


def test_parties_query_filters_by_status_and_external_ref(
    engine: Engine, tenant: CreatedTenant
):
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        party = service.create_party(s, ACME)
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        active = s.scalars(service.parties_query(s, status=party.status)).all()
        assert [p.id for p in active] == [party.id]
        by_ref = s.scalars(service.parties_query(s, external_ref="CTA-1")).all()
        assert by_ref == []


def test_an_organisation_is_summarised_with_initials_and_masked_identifiers(
    engine: Engine, tenant: CreatedTenant
):
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        party = service.create_party(s, ACME)
        view = service.summary(s, party)
    assert view.party_type is PartyType.ORGANIZATION
    assert view.display_name == "A.S.S.D.C."
    assert view.identifiers[0].masked_value == "****17A0"
    assert "Acme Servicios" not in view.model_dump_json()


def test_summaries_read_every_party_at_once(engine: Engine, tenant: CreatedTenant):
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        ana = service.create_party(s, ANA)
        acme = service.create_party(s, ACME)
        views = service.summaries(s, [ana, acme])
    assert [(v.id, v.display_name) for v in views] == [
        (ana.id, "A.M.P.G."),
        (acme.id, "A.S.S.D.C."),
    ]
    assert [i.masked_value for i in views[0].identifiers] == ["****0XY0", "****MN03"]


def test_contacts_normalise_and_deduplicate(engine: Engine, tenant: CreatedTenant):
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        party = service.create_party(s, ANA)
        email = service.add_contact(
            s,
            party.id,
            ContactInput(
                kind=ContactKind.EMAIL, value=" Ana@Acme.MX ", is_primary=True
            ),
        )
    with (
        pytest.raises(DomainError) as refused,
        staff_session(tenant.tenant_id, tenant.admin_user_id) as s,
    ):
        _ = service.add_contact(
            s, party.id, ContactInput(kind=ContactKind.EMAIL, value="ana@acme.mx")
        )
    assert refused.value.detail == "contact already exists"
    with (
        pytest.raises(ValidationFailedError) as refused,
        staff_session(tenant.tenant_id, tenant.admin_user_id) as s,
    ):
        _ = service.add_contact(
            s, party.id, ContactInput(kind=ContactKind.EMAIL, value="not-an-email")
        )
    assert refused.value.detail == "invalid email"
    with (
        pytest.raises(ValidationFailedError) as refused,
        staff_session(tenant.tenant_id, tenant.admin_user_id) as s,
    ):
        _ = service.add_contact(
            s, party.id, ContactInput(kind=ContactKind.PHONE, value="12-34")
        )
    assert refused.value.detail == "invalid phone"
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        _ = service.add_contact(
            s,
            party.id,
            ContactInput(kind=ContactKind.PHONE, value="+52 (55) 1234-5678"),
        )
        other = service.add_contact(
            s,
            party.id,
            ContactInput(kind=ContactKind.EMAIL, value="ana@xolo.mx", is_primary=True),
        )
        view = service.staff_view(s, party.id)
        pii = service.reveal_pii(s, party.id)
    assert [c.value for c in pii.contacts] == [
        "ana@acme.mx",
        "525512345678",
        "ana@xolo.mx",
    ]
    assert [(c.masked_value, c.is_primary) for c in view.contacts] == [
        ("****e.mx", False),
        ("****5678", False),
        ("****o.mx", True),
    ]
    assert [c.id for c in view.contacts][::2] == [email.id, other.id]
    added = [
        e for e in audit_events(tenant.tenant_id) if e.action == "party.contact_added"
    ]
    assert [e.detail for e in added] == [
        {"party_id": str(party.id), "kind": "EMAIL"},
        {"party_id": str(party.id), "kind": "PHONE"},
        {"party_id": str(party.id), "kind": "EMAIL"},
    ]


def test_addresses_keep_lines_encrypted_and_codes_in_clear(
    engine: Engine, tenant: CreatedTenant, admin_engine: Engine
):
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        party = service.create_party(s, ANA)
        row = service.add_address(
            s,
            party.id,
            AddressInput(
                address_type=AddressType.RESIDENTIAL,
                lines=AddressLines(street="Reforma 222", city="Ciudad de México"),
                postal_code="06600",
                admin_area_code="09",
                municipality_code="015",
            ),
        )
        assert row.valid_from is not None
        view = service.staff_view(s, party.id)
    assert view.addresses[0].postal_code == "06600"
    assert view.addresses[0].municipality_code == "015"
    assert leaked(everything_stored(admin_engine), b"Reforma 222") == []
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        pii = service.reveal_pii(s, party.id)
    assert pii.addresses[0].lines.street == "Reforma 222"
    assert pii.addresses[0].lines.ext_number is None
    (added,) = [
        e for e in audit_events(tenant.tenant_id) if e.action == "party.address_added"
    ]
    assert added.detail == {"party_id": str(party.id), "address_type": "RESIDENTIAL"}


def test_an_identifier_added_later_is_audited_and_published(
    engine: Engine, tenant: CreatedTenant
):
    no_ids = ANA.model_copy(update={"identifiers": []})
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        party = service.create_party(s, no_ids)
        row = service.add_identifier(
            s,
            party.id,
            IdentifierInput(id_type=IdentifierType.CURP, value="pega760510mdfrmn03"),
        )
    assert audit_events(tenant.tenant_id)[-1].detail == {
        "party_id": str(party.id),
        "id_type": "CURP",
    }
    (added,) = of_type(tenant.tenant_id, "party.identifier_added.v1")
    assert added.data == {
        "party_id": str(party.id),
        "identifier_id": str(row.id),
        "id_type": "CURP",
    }
    assert added.key == str(party.id)


def test_the_staff_view_is_masked_and_the_reveal_records_one_view_pii_per_group(
    engine: Engine, tenant: CreatedTenant
):
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        ana = service.create_party(s, ANA)
        view = service.staff_view(s, ana.id)
    assert view.display_name == "A.M.P.G."
    assert [i.masked_value for i in view.identifiers] == ["****0XY0", "****MN03"]
    assert view.person is not None
    assert view.person.pep_status is PepStatus.NONE
    assert view.organization is None
    assert (view.contacts, view.addresses, view.relationships) == ([], [], [])
    assert (view.documents, view.customer_relationships) == ([], [])
    assert "Ana" not in view.model_dump_json()
    assert [e for e in audit_events(tenant.tenant_id) if e.action == VIEW_PII] == []
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        pii = service.reveal_pii(s, ana.id)
    assert pii.given_names == "Ana María"
    assert pii.second_surname == "Gómez"
    assert pii.birth_date == date(1976, 5, 10)
    assert pii.legal_name is None
    assert {i.value for i in pii.identifiers} == {
        "PEGA760510XY0",
        "PEGA760510MDFRMN03",
    }
    views = [e for e in audit_events(tenant.tenant_id) if e.action == VIEW_PII]
    assert [(e.action, e.detail) for e in views] == [
        (
            "VIEW_PII",
            {
                "group": "profile",
                "fields": [
                    "given_names",
                    "first_surname",
                    "second_surname",
                    "birth_date",
                ],
            },
        ),
        ("VIEW_PII", {"group": "identifiers", "fields": ["value"], "count": 2}),
    ]
    assert {(e.entity_type, e.entity_id) for e in views} == {("party", str(ana.id))}
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        assert verify_chain(s).valid


def test_an_organisations_reveal_shows_its_legal_name(
    engine: Engine, tenant: CreatedTenant
):
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        acme = service.create_party(s, ACME)
        view = service.staff_view(s, acme.id)
        pii = service.reveal_pii(s, acme.id)
    assert view.organization is not None
    assert view.organization.trade_name == "Acme"
    assert view.person is None
    assert pii.legal_name == "Acme Servicios SA de CV"
    assert pii.given_names is None
    (profile, _) = [e for e in audit_events(tenant.tenant_id) if e.action == VIEW_PII]
    assert profile.detail == {"group": "profile", "fields": ["legal_name"]}


def test_a_party_of_another_tenant_is_not_found(engine: Engine, tenant: CreatedTenant):
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        ana = service.create_party(s, ANA)
    other = new_tenant("beta", "admin@beta.mx")
    with (
        pytest.raises(NotFoundError),
        staff_session(other.tenant_id, other.admin_user_id) as s,
    ):
        _ = service.get_party(s, ana.id)
    with staff_session(other.tenant_id, other.admin_user_id) as s:
        found = s.scalars(
            service.parties_query(s, id_type=IdentifierType.RFC, value="PEGA760510XY0")
        ).all()
    assert found == []


def test_a_failed_create_leaves_nothing_behind(engine: Engine, tenant: CreatedTenant):
    published = events_of(tenant.tenant_id)
    with (
        pytest.raises(RolledBackError),
        staff_session(tenant.tenant_id, tenant.admin_user_id) as s,
    ):
        _ = service.create_party(s, ANA)
        raise RolledBackError
    assert events_of(tenant.tenant_id) == published
    assert of_type(tenant.tenant_id, "party.created.v1") == []
    assert _party_count(tenant) == 0
    assert "party.created" not in [e.action for e in audit_events(tenant.tenant_id)]


def test_relationships_validate_the_pair_and_ownership(
    engine: Engine, tenant: CreatedTenant
):
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        ana = service.create_party(s, ANA)
        acme = service.create_party(s, ACME)
        edge = service.add_relationship(
            s,
            ana.id,
            PartyRelationshipInput(
                to_party_id=acme.id,
                rel_type=RelationshipType.SHAREHOLDER,
                ownership_pct=Decimal("51.5"),
            ),
        )
        view = service.staff_view(s, acme.id)
    assert edge.valid_from is not None
    assert [(r.id, r.ownership_pct) for r in view.relationships] == [
        (edge.id, Decimal("51.5"))
    ]
    for body, detail in (
        (
            PartyRelationshipInput(
                to_party_id=acme.id,
                rel_type=RelationshipType.DIRECTOR,
                ownership_pct=Decimal(10),
            ),
            "ownership_pct applies to SHAREHOLDER only",
        ),
        (
            PartyRelationshipInput(
                to_party_id=ana.id, rel_type=RelationshipType.DIRECTOR
            ),
            "a party cannot relate to itself",
        ),
        (
            PartyRelationshipInput(
                to_party_id=uuid7(), rel_type=RelationshipType.DIRECTOR
            ),
            "unknown to_party_id",
        ),
    ):
        with (
            pytest.raises(ValidationFailedError) as refused,
            staff_session(tenant.tenant_id, tenant.admin_user_id) as s,
        ):
            _ = service.add_relationship(s, ana.id, body)
        assert refused.value.detail == detail
    (added,) = [
        e
        for e in audit_events(tenant.tenant_id)
        if e.action == "party.relationship_added"
    ]
    assert (added.entity_type, added.entity_id) == ("party_relationship", str(edge.id))
    assert added.detail == {
        "from_party_id": str(ana.id),
        "to_party_id": str(acme.id),
        "rel_type": "SHAREHOLDER",
    }


def test_update_changes_only_attributes_and_audits_the_field_names(
    engine: Engine, tenant: CreatedTenant
):
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        ana = service.create_party(s, ANA)
    change = PartyUpdateRequest(
        person=PersonUpdate(pep_status=PepStatus.DOMESTIC, is_resident=True)
    )
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        _ = service.update_party(s, ana.id, change)
        person = s.get_one(Person, (tenant.tenant_id, ana.id))
        assert (person.pep_status, person.is_resident) == (PepStatus.DOMESTIC, True)
    (updated,) = [
        e for e in audit_events(tenant.tenant_id) if e.action == "party.updated"
    ]
    assert updated.detail == {"fields": ["is_resident", "pep_status"]}
    (event,) = of_type(tenant.tenant_id, "party.updated.v1")
    assert event.data == {
        "party_id": str(ana.id),
        "fields": ["is_resident", "pep_status"],
    }
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        _ = service.update_party(s, ana.id, change)
    assert [
        e.id for e in audit_events(tenant.tenant_id) if e.action == "party.updated"
    ] == [updated.id]
    assert len(of_type(tenant.tenant_id, "party.updated.v1")) == 1
    with (
        pytest.raises(ValidationFailedError) as refused,
        staff_session(tenant.tenant_id, tenant.admin_user_id) as s,
    ):
        _ = service.update_party(
            s,
            ana.id,
            PartyUpdateRequest(organization=OrganizationUpdate(trade_name="x")),
        )
    assert refused.value.detail == "attributes do not match the party type"


def test_an_organisation_update_applies_its_own_attributes(
    engine: Engine, tenant: CreatedTenant
):
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        acme = service.create_party(s, ACME)
        _ = service.update_party(
            s,
            acme.id,
            PartyUpdateRequest(organization=OrganizationUpdate(trade_name="Acme MX")),
        )
        view = service.staff_view(s, acme.id)
    assert view.organization is not None
    assert view.organization.trade_name == "Acme MX"
    assert audit_events(tenant.tenant_id)[-1].detail == {"fields": ["trade_name"]}
    with pytest.raises(PydanticValidationError, match="pep_status cannot be null"):
        _ = PersonUpdate(pep_status=None)
