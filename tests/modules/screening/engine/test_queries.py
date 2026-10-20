"""The queries a party or an ad hoc subject is screened with."""

from datetime import date

import pytest
from sqlalchemy import Engine

from tests.helpers import staff_session
from xolotl.modules.party import service
from xolotl.modules.party.enums import IdentifierType, PartyType
from xolotl.modules.party.errors import IdentifierInvalidError
from xolotl.modules.party.retention import purge_party
from xolotl.modules.party.schemas import (
    IdentifierInput,
    OrganizationInput,
    PartyCreateRequest,
    PersonInput,
)
from xolotl.modules.screening.engine.queries import (
    AdHocIdentifier,
    AdHocSubjectInput,
    query_for_ad_hoc,
    query_for_party,
)
from xolotl.modules.screening.lists import EntityType, IdType
from xolotl.modules.screening.matching import NameQuery, QueryIdentifier
from xolotl.modules.tenants.service import CreatedTenant

ANA = PartyCreateRequest(
    party_type=PartyType.PERSON,
    country_code="MX",
    person=PersonInput(
        given_names="Ana María",
        first_surname="Pérez",
        second_surname="Gómez",
        birth_date=date(1976, 5, 10),
        birth_country="ES",
        nationalities=["MX", "ES"],
        gender="FEMALE",
    ),
    identifiers=[
        IdentifierInput(id_type=IdentifierType.RFC, value="pega-760510-xy0"),
        IdentifierInput(id_type=IdentifierType.CURP, value="PEGA760510MDFRMN03"),
        IdentifierInput(id_type=IdentifierType.CLABE, value="002010077777777771"),
        IdentifierInput(
            id_type=IdentifierType.PASSPORT, value="G1234567", country_code="ES"
        ),
    ],
)
ACME = PartyCreateRequest(
    party_type=PartyType.ORGANIZATION,
    organization=OrganizationInput(
        legal_name="Acme Servicios SA de CV",
        trade_name="Acme",
        country_of_incorporation="US",
    ),
    identifiers=[IdentifierInput(id_type=IdentifierType.RFC, value="ACM0101017A0")],
)


def test_a_person_query_carries_names_birth_date_and_screened_identifiers(
    engine: Engine, tenant: CreatedTenant
):
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        party = service.create_party(s, ANA)
        query = query_for_party(s, party.id)
    assert query == NameQuery(
        entity_type=EntityType.PERSON,
        names=("Ana María Pérez Gómez",),
        given_names="Ana María",
        first_surname="Pérez",
        second_surname="Gómez",
        birth_date=date(1976, 5, 10),
        gender="FEMALE",
        nationalities=("MX", "ES"),
        countries=("MX", "ES"),
        identifiers=(
            QueryIdentifier(IdType.RFC, "PEGA760510XY0", "MX"),
            QueryIdentifier(IdType.CURP, "PEGA760510MDFRMN03", "MX"),
            QueryIdentifier(IdType.PASSPORT, "G1234567", "ES"),
        ),
    )


def test_a_gender_other_than_male_or_female_is_not_passed(
    engine: Engine, tenant: CreatedTenant
):
    assert ANA.person is not None
    body = ANA.model_copy(
        update={
            "person": ANA.person.model_copy(update={"gender": "X"}),
            "identifiers": [],
        }
    )
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        party = service.create_party(s, body)
        query = query_for_party(s, party.id)
    assert query.gender is None


def test_an_organization_query_carries_legal_and_trade_names(
    engine: Engine, tenant: CreatedTenant
):
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        party = service.create_party(s, ACME)
        query = query_for_party(s, party.id)
    assert query == NameQuery(
        entity_type=EntityType.ORGANIZATION,
        names=("Acme Servicios SA de CV", "Acme"),
        countries=("US", "MX"),
        identifiers=(QueryIdentifier(IdType.RFC, "ACM0101017A0", "MX"),),
    )


def test_a_purged_organization_query_has_no_names_even_with_a_trade_name(
    engine: Engine, tenant: CreatedTenant
):
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        party = service.create_party(s, ACME)
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        _ = purge_party(s, service.get_party(s, party.id))
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        assert service.screening_profile(s, party.id).trade_name == "Acme"
        query = query_for_party(s, party.id)
    assert query == NameQuery(entity_type=EntityType.ORGANIZATION, names=())


def test_an_ad_hoc_identifier_is_validated_like_a_party_identifier():
    body = AdHocSubjectInput(
        type="PERSON",
        names=["Ana María Pérez Gómez"],
        identifiers=[
            AdHocIdentifier(id_type=IdentifierType.RFC, value="PEGA760510XY1")
        ],
    )
    with pytest.raises(IdentifierInvalidError) as refused:
        _ = query_for_ad_hoc(body)
    assert refused.value.detail == "RFC: CHECK_DIGIT"
    assert "PEGA760510XY1" not in str(refused.value)

    good = body.model_copy(
        update={
            "identifiers": [
                AdHocIdentifier(id_type=IdentifierType.RFC, value="pega 760510 xy0"),
                AdHocIdentifier(
                    id_type=IdentifierType.CLABE, value="002010077777777771"
                ),
            ],
            "countries": ["MX"],
            "gender": "FEMALE",
        }
    )
    query = query_for_ad_hoc(good)
    assert query.entity_type is EntityType.PERSON
    assert query.names == ("Ana María Pérez Gómez",)
    assert (query.countries, query.gender) == (("MX",), "FEMALE")
    assert query.identifiers == (QueryIdentifier(IdType.RFC, "PEGA760510XY0", None),)


def test_an_ad_hoc_subject_is_strict():
    with pytest.raises(ValueError, match="names"):
        _ = AdHocSubjectInput.model_validate({"type": "PERSON", "names": []})
    with pytest.raises(ValueError, match="extra"):
        _ = AdHocSubjectInput.model_validate({
            "type": "ORGANIZATION",
            "names": ["Acme"],
            "score": 1,
        })
    with pytest.raises(ValueError, match="names"):
        _ = AdHocSubjectInput.model_validate({"type": "PERSON", "names": ["x" * 301]})


def test_the_query_fingerprint_is_stable_across_calls(
    engine: Engine, tenant: CreatedTenant
):
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        party = service.create_party(s, ANA)
        first = query_for_party(s, party.id).fingerprint()
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        second = query_for_party(s, party.id).fingerprint()
    assert first == second
    assert len(first) == 32
    ad_hoc = AdHocSubjectInput(type="ORGANIZATION", names=["Acme Servicios SA de CV"])
    assert (
        query_for_ad_hoc(ad_hoc).fingerprint()
        == query_for_ad_hoc(
            AdHocSubjectInput.model_validate(ad_hoc.model_dump(mode="json"))
        ).fingerprint()
    )
