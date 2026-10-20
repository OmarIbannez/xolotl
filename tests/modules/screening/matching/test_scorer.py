"""The explaining scorer and bands (SP-03 spec §4.2; FR-SC-004, 005, 006)."""

import json
import os
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from tests.modules.screening.matching.support import THRESHOLDS, entry
from xolotl.modules.screening.lists import EntityType, IdType, PartialDate
from xolotl.modules.screening.matching import (
    Band,
    IdfTable,
    MatchKind,
    NameQuery,
    QualifierOutcome,
    QueryIdentifier,
    ScoringConfig,
    band_for,
    score,
)

PERSON, ORG = EntityType.PERSON, EntityType.ORGANIZATION
GUZMAN = entry(
    "1",
    "Joaquín Archivaldo Guzmán Loera",
    "El Chapo Guzmán",
    birth_dates=(PartialDate(1957, 4, 4),),
    nationalities=("MX",),
    gender="MALE",
    identifiers=((IdType.CURP, "GULJ570404HSLZRQ02"),),
)
ACME = entry("2", "Comercializadora Acme Sol, S.A. de C.V.", entity_type=ORG)
SAT = entry(
    "AAA010101AAA",
    "Acme Sol",
    entity_type=ORG,
    list_code="MX_SAT_69B",
    identifiers=((IdType.RFC, "AAA010101AAA"),),
)
IDF = IdfTable.build([GUZMAN, ACME, SAT])
CONFIG = ScoringConfig()

CASES = {
    "exact": (NameQuery(PERSON, ("Joaquín Archivaldo Guzmán Loera",)), GUZMAN),
    "dropped_maternal_and_middle": (NameQuery(PERSON, ("Joaquin Guzman",)), GUZMAN),
    "order_swapped": (NameQuery(PERSON, ("GUZMAN LOERA, Joaquin Archivaldo",)), GUZMAN),
    "nickname_and_accents": (
        NameQuery(PERSON, ("Joaquin Archibaldo Guzman Loera",)),
        GUZMAN,
    ),
    "initial": (NameQuery(PERSON, ("J. Guzmán Loera",)), GUZMAN),
    "typo": (NameQuery(PERSON, ("Joaquin Guzmam Loera",)), GUZMAN),
    "birth_date_conflict": (
        NameQuery(PERSON, ("Joaquin Guzman Loera",), birth_date=date(1990, 1, 1)),
        GUZMAN,
    ),
    "qualifiers_agree": (
        NameQuery(
            PERSON,
            ("Joaquin Guzman Loera",),
            birth_date=date(1957, 4, 4),
            gender="MALE",
            nationalities=("MX",),
            countries=("MX",),
        ),
        GUZMAN,
    ),
    "identifier_floor": (
        NameQuery(
            PERSON,
            ("J. Loera",),
            identifiers=(QueryIdentifier(IdType.CURP, "GULJ570404HSLZRQ02"),),
        ),
        GUZMAN,
    ),
    "identifier_conflict": (
        NameQuery(
            PERSON,
            ("Joaquin Archivaldo Guzman Loera",),
            identifiers=(QueryIdentifier(IdType.CURP, "AAAA000000HDFXXX00"),),
        ),
        GUZMAN,
    ),
    "unrelated": (NameQuery(PERSON, ("María Fernanda López",)), GUZMAN),
    "entity_mismatch": (NameQuery(ORG, ("Guzman Loera SA de CV",)), GUZMAN),
    "organization_legal_form": (NameQuery(ORG, ("ACME SOL SAPI DE CV",)), ACME),
    "sat_rfc_equal": (
        NameQuery(
            ORG,
            ("Otra Razón",),
            identifiers=(QueryIdentifier(IdType.RFC, "AAA010101AAA"),),
        ),
        SAT,
    ),
    "sat_rfc_differs": (
        NameQuery(
            ORG,
            ("Acme Sol",),
            identifiers=(QueryIdentifier(IdType.RFC, "BBB010101BBB"),),
        ),
        SAT,
    ),
    "sat_without_rfc": (NameQuery(ORG, ("Acme Sol",)), SAT),
}
GOLDEN = Path(__file__).parent / "goldens" / "explanations.json"


def _results():
    out = {}
    for name, (query, item) in CASES.items():
        result = score(query, item, idf=IDF, config=CONFIG)
        out[name] = {
            "score": str(result.score),
            "band": band_for(result, THRESHOLDS).value,
            "explanation": result.explanation.to_json(),
        }
    return out


def test_explanations_match_the_golden():
    results = _results()
    if os.environ.get("XOLOTL_UPDATE_GOLDENS") == "1":
        GOLDEN.parent.mkdir(exist_ok=True)
        _ = GOLDEN.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n")
    expected = json.loads(GOLDEN.read_text())
    for name in CASES:
        assert results[name] == expected[name], name


@pytest.mark.parametrize(
    ("case", "band"),
    [
        ("exact", Band.ALERT),
        ("dropped_maternal_and_middle", Band.ALERT),
        ("order_swapped", Band.ALERT),
        ("nickname_and_accents", Band.ALERT),
        ("initial", Band.ALERT),
        ("typo", Band.ALERT),
        ("qualifiers_agree", Band.ALERT),
        ("identifier_floor", Band.ALERT),
        ("identifier_conflict", Band.AUTO_DISCARDED),
        ("unrelated", Band.BELOW_CUTOFF),
        ("entity_mismatch", Band.BELOW_CUTOFF),
        ("organization_legal_form", Band.ALERT),
        ("sat_rfc_equal", Band.ALERT),
        ("sat_rfc_differs", Band.AUTO_DISCARDED),
        ("sat_without_rfc", Band.LOW_CONFIDENCE),
    ],
)
def test_bands(case, band):
    query, item = CASES[case]
    assert band_for(score(query, item, idf=IDF, config=CONFIG), THRESHOLDS) is band


def _score(case):
    query, item = CASES[case]
    return score(query, item, idf=IDF, config=CONFIG)


def test_exact_scores_one_and_aligns_every_token():
    result = _score("exact")
    assert result.score == Decimal("1.0000")
    assert {a.kind for a in result.explanation.alignment} == {MatchKind.EXACT}
    assert result.explanation.unmatched_query == ()


def test_alignment_kinds():
    kinds = {
        a.query: a.kind for a in _score("nickname_and_accents").explanation.alignment
    }
    assert kinds["archibaldo"] is MatchKind.PHONETIC
    kinds = {a.query: a.kind for a in _score("initial").explanation.alignment}
    assert kinds["j"] is MatchKind.INITIAL
    kinds = {a.query: a.kind for a in _score("typo").explanation.alignment}
    assert kinds["guzmam"] is MatchKind.FUZZY


def test_a_birth_date_conflict_lowers_the_score():
    assert _score("birth_date_conflict").score < _score("qualifiers_agree").score
    outcomes = {
        q.attribute: q.outcome
        for q in _score("qualifiers_agree").explanation.qualifiers
    }
    assert outcomes == {
        "BIRTH_DATE": QualifierOutcome.MATCH,
        "NATIONALITY": QualifierOutcome.MATCH,
        "COUNTRY": QualifierOutcome.MATCH,
        "GENDER": QualifierOutcome.MATCH,
    }


def test_identifiers_are_masked_in_explanations():
    text = json.dumps(_score("identifier_floor").explanation.to_json())
    assert "GULJ570404HSLZRQ02" not in text
    assert "****RQ02" in text
    assert _score("identifier_floor").score == Decimal("0.9500")
    assert _score("identifier_conflict").score == Decimal("0.3000")


def test_identifier_only_lists():
    assert _score("sat_rfc_equal").score == Decimal("1.0000")
    rules = [r.code for r in _score("sat_without_rfc").explanation.rules]
    assert rules == ["IDENTIFIER_ONLY_LIST"]


def test_entity_mismatch_scores_zero():
    result = _score("entity_mismatch")
    assert result.score == Decimal("0.0000")
    assert [r.code for r in result.explanation.rules] == ["ENTITY_TYPE_MISMATCH"]


@pytest.mark.parametrize(
    ("born", "listed", "outcome", "adjustment"),
    [
        (date(1957, 4, 4), PartialDate(1957), QualifierOutcome.MATCH, 0.05),
        (date(1957, 4, 4), PartialDate(1957, 4), QualifierOutcome.MATCH, 0.06),
        (date(1957, 4, 4), PartialDate(1957, 4, 4), QualifierOutcome.MATCH, 0.08),
        (date(1957, 4, 4), PartialDate(1957, 5, 1), QualifierOutcome.PARTIAL, 0.02),
        (date(1957, 4, 4), PartialDate(1959), QualifierOutcome.PARTIAL, 0.0),
        (date(1957, 4, 4), PartialDate(1970), QualifierOutcome.CONFLICT, -0.15),
    ],
)
def test_birth_date_precision(born, listed, outcome, adjustment):
    item = entry("9", "Ana Ruiz", birth_dates=(listed,))
    result = score(
        NameQuery(PERSON, ("Ana Ruiz",), birth_date=born), item, idf=IDF, config=CONFIG
    )
    (qualifier,) = result.explanation.qualifiers
    assert (qualifier.outcome, qualifier.adjustment) == (outcome, adjustment)


def test_absent_and_conflicting_qualifiers():
    item = entry("9", "Ana Ruiz", nationalities=("ES",), gender="FEMALE")
    query = NameQuery(
        PERSON,
        ("Ana Ruiz",),
        birth_date=date(1980, 1, 1),
        nationalities=("MX",),
        gender="MALE",
        countries=("MX",),
    )
    outcomes = {
        q.attribute: q.outcome
        for q in score(query, item, idf=IDF, config=CONFIG).explanation.qualifiers
    }
    assert outcomes == {
        "BIRTH_DATE": QualifierOutcome.ABSENT,
        "NATIONALITY": QualifierOutcome.CONFLICT,
        "COUNTRY": QualifierOutcome.ABSENT,
        "GENDER": QualifierOutcome.CONFLICT,
    }
    bare = entry("8", "Ana Ruiz")
    outcomes = {
        q.attribute: q.outcome
        for q in score(query, bare, idf=IDF, config=CONFIG).explanation.qualifiers
    }
    assert outcomes == {
        "BIRTH_DATE": QualifierOutcome.ABSENT,
        "NATIONALITY": QualifierOutcome.ABSENT,
        "COUNTRY": QualifierOutcome.ABSENT,
    }


def test_weak_aliases_count_less():
    strong = entry("9", "Zed Kofi", "Abu Bakr")
    weak = entry("9", "Zed Kofi", weak=("Abu Bakr",))
    query = NameQuery(PERSON, ("Abu Bakr",))
    assert (
        score(query, weak, idf=IDF, config=CONFIG).score
        < score(query, strong, idf=IDF, config=CONFIG).score
    )


def test_a_query_without_tokens_scores_zero():
    result = score(NameQuery(PERSON, ("  ",)), GUZMAN, idf=IDF, config=CONFIG)
    assert result.score == Decimal("0.0000")
    assert result.explanation.alignment == ()


def test_config_hash_tracks_parameters():
    assert ScoringConfig().config_hash() == CONFIG.config_hash()
    assert (
        ScoringConfig(id_match_floor=Decimal("0.97")).config_hash()
        != CONFIG.config_hash()
    )


def test_nicknames_and_repeated_tokens_align_once():
    item = entry("9", "José Hernández Hernández")
    result = score(NameQuery(PERSON, ("Pepe Hernández",)), item, idf=IDF, config=CONFIG)
    kinds = [(a.query, a.entry, a.kind) for a in result.explanation.alignment]
    assert kinds == [
        ("pepe", "jose", MatchKind.NICKNAME),
        ("hernandez", "hernandez", MatchKind.EXACT),
    ]
    assert result.explanation.unmatched_entry == ("hernandez",)


def test_misspelt_weightless_words_are_absorbed_at_no_weight():
    item = entry("9", "Armament Industries Group", entity_type=ORG)
    query = NameQuery(ORG, ("Armament Industryes Grup",))
    result = score(query, item, idf=IDF, config=CONFIG)
    absorbed = [a for a in result.explanation.alignment if not a.weight]
    assert {(a.query, a.entry) for a in absorbed} == {
        ("industryes", "industries"),
        ("grup", "group"),
    }
    clean = NameQuery(ORG, ("Armament Industries Group",))
    assert result.score == score(clean, item, idf=IDF, config=CONFIG).score
    reverse = score(
        NameQuery(ORG, ("Armament Industries Group",)),
        entry("8", "Armament Industryes Grup", entity_type=ORG),
        idf=IDF,
        config=CONFIG,
    )
    assert reverse.score == result.score


@pytest.mark.parametrize(
    ("first", "second"), [("Su", "Zu"), ("Ho", "O"), ("The", "Te")]
)
def test_one_sound_tokens_match_when_lengths_are_close(first, second):
    item = entry("9", f"Jang Bom {first}")
    result = score(
        NameQuery(PERSON, (f"Jang Bom {second}",)), item, idf=IDF, config=CONFIG
    )
    kinds = {a.entry: a.kind for a in result.explanation.alignment}
    assert kinds[first.lower()] is MatchKind.PHONETIC
