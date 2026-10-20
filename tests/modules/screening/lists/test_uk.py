"""UK Sanctions List parser (``uk-sl-xml/1``) on a trimmed real sample."""

import io
import logging
from datetime import UTC, date, datetime

import pytest

from tests.modules.screening.support import FIXTURES, assert_golden
from xolotl.modules.screening.lists import (
    EntityType,
    IdType,
    ListEntry,
    ListFormatError,
    NameQuality,
    ParsedList,
    PartialDate,
    parse,
)

FIXTURE = FIXTURES / "uk" / "UK-Sanctions-List.xml"


def _parse_fixture() -> ParsedList:
    with FIXTURE.open("rb") as data:
        return parse("UK_FCDO", data)


@pytest.fixture(scope="module")
def parsed() -> ParsedList:
    return _parse_fixture()


def _by_key(parsed: ParsedList, key: str) -> ListEntry:
    return next(e for e in parsed.entries if e.entry_key == key)


def test_matches_the_golden(parsed):
    assert_golden("uk", parsed)


def test_header_and_counts(parsed):
    assert parsed.list_code == "UK_FCDO"
    assert parsed.parser_version == "uk-sl-xml/1"
    assert parsed.published_at == datetime(2026, 10, 16, tzinfo=UTC)
    kinds = {e.entity_type for e in parsed.entries}
    assert kinds == {EntityType.PERSON, EntityType.ORGANIZATION, EntityType.VESSEL}
    assert len(parsed.entries) == 30


def test_every_fixture_country_maps(caplog):
    with caplog.at_level(logging.WARNING):
        _ = _parse_fixture()
    assert not caplog.records


def test_individual_names_split_given_and_surname(parsed):
    baranov = _by_key(parsed, "RUS1683")
    assert baranov.primary_name.full == "Robert Petrovich BARANOV"
    assert baranov.primary_name.quality is NameQuality.PRIMARY
    assert (baranov.primary_name.given, baranov.primary_name.surname) == (
        "Robert Petrovich",
        "BARANOV",
    )
    assert baranov.aliases[0].script == "Cyrl"
    assert baranov.source_ref == "UniqueID RUS1683"


def test_an_empty_first_primary_name_is_skipped(parsed):
    kazakov = _by_key(parsed, "BEL0174")
    assert kazakov.primary_name.full == "Pavel Ivanovich KAZAKOV"


def test_a_second_primary_name_becomes_an_alias(parsed):
    hanif = _by_key(parsed, "AFG0038")
    assert hanif.primary_name.full == "DIN MOHAMMAD HANIF"
    assert "IADENA MOHAMMAD" in {a.full for a in hanif.aliases}


def test_alias_strength(parsed):
    anwari = _by_key(parsed, "AFG0009")
    assert NameQuality.WEAK in {a.quality for a in anwari.aliases}
    assert _by_key(parsed, "AFG0020").aliases[0].script == "Arab"
    assert _by_key(parsed, "CYB0044").aliases[0].script == "Hani"


def test_entities_and_ships_have_no_given_or_surname(parsed):
    synesis = _by_key(parsed, "BEL0065")
    assert synesis.entity_type is EntityType.ORGANIZATION
    assert (synesis.primary_name.given, synesis.primary_name.surname) == (None, None)
    ship = _by_key(parsed, "RUS2538")
    assert ship.entity_type is EntityType.VESSEL
    assert [(i.id_type, i.normalized) for i in ship.identifiers] == [
        (IdType.IMO, "9235725")
    ]
    # A ship's flag is its nationality.
    assert ship.nationalities == ("GM",)


def test_registration_numbers_are_extracted_from_free_text(parsed):
    synesis = _by_key(parsed, "BEL0065")
    assert [i.normalized for i in synesis.identifiers] == [
        "190950894",
        "7704734000",
        "770301001",
    ]
    assert {i.id_type for i in synesis.identifiers} == {IdType.REGISTRATION}


def test_people_dates_documents_and_gender(parsed):
    mahmood = _by_key(parsed, "AQD0145")
    assert mahmood.birth_dates == (PartialDate(1994, 5, 11),)
    assert mahmood.gender == "FEMALE"
    assert mahmood.nationalities == ("GB",)
    assert mahmood.identifiers[0].id_type is IdType.PASSPORT
    assert mahmood.listed_on == date(2015, 9, 28)
    assert _by_key(parsed, "AFG0012").identifiers[0].id_type is IdType.NATIONAL_ID
    assert _by_key(parsed, "GAC0032").nationalities == ("XK",)
    assert _by_key(parsed, "DPR0205").nationalities == ("KP",)


def test_programmes_are_regime_and_un_reference(parsed):
    programs = _by_key(parsed, "AFG0001").programs
    assert programs == (
        "The Afghanistan (Sanctions) (EU Exit) Regulations 2020",
        "TAe.010",
    )


def _document(*designations: str, generated: str = "16/10/2026") -> io.BytesIO:
    body = "".join(designations)
    xml = (
        f"<Designations><DateGenerated>{generated}</DateGenerated>{body}</Designations>"
    )
    return io.BytesIO(xml.encode())


def _designation(
    key: str = "X1",
    kind: str = "Individual",
    names: str = (
        "<Name><Name1>A</Name1><Name6>B</Name6><NameType>Primary Name</NameType></Name>"
    ),
    extra: str = "",
) -> str:
    return (
        f"<Designation><UniqueID>{key}</UniqueID><Names>{names}</Names>"
        f"<IndividualEntityShip>{kind}</IndividualEntityShip>"
        f"<DateDesignated>01/02/2020</DateDesignated>{extra}</Designation>"
    )


def _dobs(*values: str) -> str:
    dobs = "".join(f"<DOB>{v}</DOB>" for v in values)
    individual = f"<Individual><DOBs>{dobs}</DOBs></Individual>"
    return f"<IndividualDetails>{individual}</IndividualDetails>"


def test_partial_birth_dates():
    extra = _dobs("dd/mm/1970", "dd/03/1971", "00/00/1975", "1980", "05/06/1990")
    (entry,) = parse("UK_FCDO", _document(_designation(extra=extra))).entries
    assert entry.birth_dates == (
        PartialDate(1970),
        PartialDate(1971, 3),
        PartialDate(1975),
        PartialDate(1980),
        PartialDate(1990, 6, 5),
    )


def test_unusable_birth_dates_are_dropped_with_a_warning(caplog):
    extra = _dobs("15/08/19yy", "31/02/1970", "1/1/70")
    with caplog.at_level(logging.WARNING):
        (entry,) = parse("UK_FCDO", _document(_designation(extra=extra))).entries
    assert entry.birth_dates == ()
    assert caplog.text.count("dropped (not a date)") == 3


def test_conflicting_genders_say_nothing():
    genders = "<Genders><Gender>Male</Gender><Gender>female</Gender></Genders>"
    extra = f"<IndividualDetails><Individual>{genders}</Individual></IndividualDetails>"
    (entry,) = parse("UK_FCDO", _document(_designation(extra=extra))).entries
    assert entry.gender is None


def test_low_quality_only_applies_to_aliases():
    names = (
        "<Name><Name6>CORP</Name6><NameType>Primary Name</NameType>"
        "<AliasStrength>Low quality a.k.a</AliasStrength></Name>"
        "<Name><Name6>ALT</Name6><NameType>Alias</NameType>"
        "<AliasStrength>Low quality a.k.a</AliasStrength></Name>"
        "<Name><NameType>Alias</NameType></Name>"
    )
    original = "<NameNonLatinScript>КОРП</NameNonLatinScript>"
    extra = f"<NonLatinNames><NonLatinName>{original}</NonLatinName></NonLatinNames>"
    (entry,) = parse(
        "UK_FCDO", _document(_designation(kind="Entity", names=names, extra=extra))
    ).entries
    assert entry.primary_name.quality is NameQuality.PRIMARY
    assert [(a.full, a.quality, a.script) for a in entry.aliases] == [
        ("ALT", NameQuality.WEAK, "Latn"),
        ("КОРП", NameQuality.STRONG, "Cyrl"),
    ]


def test_a_truncated_file_is_refused():
    data = FIXTURE.read_bytes()
    with pytest.raises(ListFormatError, match=r"uk-sl-xml/1\): malformed XML"):
        _ = parse("UK_FCDO", io.BytesIO(data[: len(data) // 2]))


@pytest.mark.parametrize(
    ("designations", "generated", "message"),
    [
        ((_designation(key=""),), "16/10/2026", "record 0 has no UniqueID"),
        ((_designation(kind="Plane"),), "16/10/2026", "is not an individual, entity"),
        (
            (_designation(names="<Name><NameType>Alias</NameType></Name>"),),
            "16/10/2026",
            "UniqueID X1 has no name",
        ),
        ((_designation(), _designation()), "16/10/2026", "duplicate entry key 'X1'"),
        ((_designation(),), "2026-10-16", "DateGenerated: '2026-10-16' is not"),
        ((_designation(),), "31/02/2026", "DateGenerated: '31/02/2026' is not"),
    ],
)
def test_malformed_designations_are_refused(designations, generated, message):
    with pytest.raises(ListFormatError, match=message):
        _ = parse("UK_FCDO", _document(*designations, generated=generated))


def test_an_alias_only_designation_has_no_name():
    names = "<Name><Name6>ALT</Name6><NameType>Alias</NameType></Name>"
    with pytest.raises(ListFormatError, match="has no name"):
        _ = parse("UK_FCDO", _document(_designation(names=names)))


def test_another_document_is_refused():
    data = io.BytesIO(b"<Other>" + _designation().encode() + b"</Other>")
    with pytest.raises(ListFormatError, match="root element is 'Other'"):
        _ = parse("UK_FCDO", data)


def test_a_file_without_generation_date_has_no_published_at():
    data = io.BytesIO(b"<Designations>" + _designation().encode() + b"</Designations>")
    assert parse("UK_FCDO", data).published_at is None


def test_empty_optional_records_are_ignored():
    extra = (
        "<NonLatinNames><NonLatinName><NonLatinScriptType>Arabic</NonLatinScriptType>"
        "</NonLatinName></NonLatinNames>"
        "<IndividualDetails><Individual><BirthDetails><Location/></BirthDetails>"
        "</Individual></IndividualDetails>"
        "<Addresses><Address/></Addresses>"
    )
    (entry,) = parse("UK_FCDO", _document(_designation(extra=extra))).entries
    assert (entry.aliases, entry.birth_places, entry.addresses) == ((), (), ())
