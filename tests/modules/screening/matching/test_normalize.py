"""Name normalization v2 (SP-03 spec §4.2)."""

import pytest

from xolotl.modules.screening.lists import EntityType
from xolotl.modules.screening.matching import (
    MATCHER_VERSION,
    TokenRole,
    normalize_name,
)

G, S, P, L, X, U = (
    TokenRole.GIVEN,
    TokenRole.SURNAME,
    TokenRole.PARTICLE,
    TokenRole.LEGAL_FORM,
    TokenRole.GENERIC,
    TokenRole.UNKNOWN,
)


def _person(text, **fields):
    name = normalize_name(text, entity_type=EntityType.PERSON, **fields)
    return name.folded, [t.role for t in name.tokens]


def _org(text, entity_type=EntityType.ORGANIZATION):
    name = normalize_name(text, entity_type=entity_type)
    return name.folded, [t.role for t in name.tokens], name.legal_form


def test_matcher_version():
    assert MATCHER_VERSION == "2.1.0"


@pytest.mark.parametrize(
    ("text", "folded", "roles"),
    [
        ("Juan de la Cruz Pérez", "juan de la cruz perez", [G, P, P, S, S]),
        ("JOSÉ  MARÍA   NÚÑEZ-LÓPEZ", "jose maria nunez lopez", [G, G, S, S]),
        ("Guadalupe Ramírez", "guadalupe ramirez", [G, S]),
        ("Madonna", "madonna", [U]),
        ("GUZMAN LOERA, Joaquín", "joaquin guzman loera", [G, S, S]),
        ("Dr. Ana Ruiz", "dr ana ruiz", [X, G, S]),
        ("Seán O'Brien", "sean obrien", [G, S]),
        ("De La", "de la", [U, U]),
        (
            "\uff2a\uff55\uff41\uff4e \uff30\u00e9rez",
            "juan perez",
            [G, S],
        ),  # full width
        ("Умаров Доку", "umarov doku", [G, S]),
        ("صدام حسين التكريتي", "صدام حسين التكريتي", [U, U, U]),
        ("Pérez,", "perez", [U]),
    ],
)
def test_person_names(text, folded, roles):
    assert _person(text) == (folded, roles)


def test_structured_fields_decide_person_roles():
    folded, roles = _person(
        "José Ángel Hernández de la Rosa",
        given="José Ángel",
        surname="Hernández de la Rosa",
    )
    assert folded == "jose angel hernandez de la rosa"
    assert roles == [G, G, S, P, P, S]
    _, roles = _person("Ana Pérez Extra", given="Ana")
    assert roles == [G, U, U]


@pytest.mark.parametrize(
    ("text", "folded", "roles", "form"),
    [
        (
            "Comercializadora El Sol, S. A. de C. V.",
            "comercializadora el sol sa de cv",
            [X, P, U, L, L, L],
            "SA_DE_CV",
        ),
        (
            "Grupo Bimbo S.A.B. de C.V.",
            "grupo bimbo sab de cv",
            [X, U, L, L, L],
            "SAB_DE_CV",
        ),
        (
            "Fintech Uno SAPI de CV",
            "fintech uno sapi de cv",
            [U, U, L, L, L],
            "SAPI_DE_CV",
        ),
        (
            "Taller López S. de R.L. de C.V.",
            "taller lopez s de rl de cv",
            [U, U, L, L, L, L, L],
            "S_DE_RL_DE_CV",
        ),
        ("Acme, LLC", "acme llc", [U, L], "LLC"),
        (
            "Kingly Won International Co., Ltd.",
            "kingly won international co ltd",
            [U, U, X, L, L],
            "LTD",
        ),
        ("Siemens AG", "siemens ag", [U, L], "AG"),
        ("Müller GmbH", "muller gmbh", [U, L], "GMBH"),
        # A legal form alone is not stripped: the name would be empty.
        ("S.A.", "sa", [U], None),
        ("Al-Qaida", "al qaida", [P, U], None),
        ("I.B.M. Corp", "ibm corp", [U, L], "CORP"),
        # Some registries put the legal form first.
        ("LLC Synesis", "llc synesis", [L, U], "LLC"),
    ],
)
def test_organization_names(text, folded, roles, form):
    assert _org(text) == (folded, roles, form)


def test_vessels_follow_the_organization_rules():
    folded, roles, form = _org("M/V Ocean Star", EntityType.VESSEL)
    assert folded == "mv ocean star"
    assert roles == [U, U, U]
    assert form is None


def test_tokens_keep_text_and_phonetic_key():
    name = normalize_name("Xavier González", entity_type=EntityType.PERSON)
    first, second = name.tokens
    assert (first.text, first.folded, first.phonetic) == ("Xavier", "xavier", "jbr")
    assert (second.text, second.phonetic) == ("González", "gnsls")


def test_empty_names_have_no_tokens():
    assert normalize_name("  ", entity_type=EntityType.PERSON).tokens == ()
    assert not normalize_name("", entity_type=EntityType.ORGANIZATION).folded
