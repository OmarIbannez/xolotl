"""``NameQuery`` fingerprint and helpers."""

from datetime import date

from xolotl.modules.screening.lists import EntityType, IdType
from xolotl.modules.screening.matching import NameQuery, QueryIdentifier
from xolotl.modules.screening.matching.query import compatible, query_names


def test_fingerprint_is_stable_and_sensitive():
    query = NameQuery(
        EntityType.PERSON,
        ("Ana Ruiz",),
        birth_date=date(1980, 1, 2),
        identifiers=(QueryIdentifier(IdType.RFC, "RUAA800102AB1"),),
    )
    again = NameQuery(
        EntityType.PERSON,
        ("Ana Ruiz",),
        birth_date=date(1980, 1, 2),
        identifiers=(QueryIdentifier(IdType.RFC, "RUAA800102AB1"),),
    )
    assert query.fingerprint() == again.fingerprint()
    assert len(query.fingerprint()) == 32
    assert (
        NameQuery(EntityType.PERSON, ("Ana Ruiz",)).fingerprint() != query.fingerprint()
    )


def test_structured_fields_apply_to_the_legal_name_only():
    query = NameQuery(
        EntityType.PERSON,
        ("Ana Sofía Ruiz", "Sofi Ruiz", "  "),
        given_names="Ana Sofía",
        first_surname="Ruiz",
    )
    legal, other = query_names(query)
    assert [t.role.value for t in legal.tokens] == ["GIVEN", "GIVEN", "SURNAME"]
    assert [t.role.value for t in other.tokens] == ["GIVEN", "SURNAME"]
    assert query.surname() == "Ruiz"
    assert NameQuery(EntityType.PERSON, ("x",)).surname() is None


def test_entity_type_compatibility():
    assert compatible(EntityType.PERSON, EntityType.PERSON)
    assert compatible(EntityType.ORGANIZATION, EntityType.VESSEL)
    assert compatible(EntityType.PERSON, EntityType.OTHER)
    assert not compatible(EntityType.PERSON, EntityType.ORGANIZATION)
    assert not compatible(EntityType.ORGANIZATION, EntityType.PERSON)
