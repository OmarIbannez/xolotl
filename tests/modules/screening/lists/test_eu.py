"""EU FSF consolidated list parser (``eu-fsf-xml/1``) on a trimmed real sample."""

import io
import logging
from datetime import date, datetime, timedelta, timezone

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

FIXTURE = FIXTURES / "eu" / "full_sanctions_list_1_1.xml"
NS = "http://eu.europa.ec/fpi/fsd/export"


def _parse_fixture() -> ParsedList:
    with FIXTURE.open("rb") as data:
        return parse("EU_FSF", data)


@pytest.fixture(scope="module")
def entries() -> dict[str, ListEntry]:
    return {e.entry_key: e for e in _parse_fixture().entries}


def test_matches_the_golden():
    assert_golden("eu", _parse_fixture())


def test_header():
    parsed = _parse_fixture()
    assert parsed.parser_version == "eu-fsf-xml/1"
    assert parsed.published_at == datetime(
        2026, 10, 15, 12, 10, 34, 160000, tzinfo=timezone(timedelta(hours=2))
    )
    assert parsed.source_version == "185699"
    assert len(parsed.entries) == 25


def test_every_fixture_country_maps(caplog):
    with caplog.at_level(logging.WARNING):
        _ = _parse_fixture()
    assert not caplog.records


def test_names(entries):
    saddam = entries["13"]
    assert saddam.entity_type is EntityType.PERSON
    assert saddam.primary_name.full == "Saddam Hussein Al-Tikriti"
    assert (saddam.primary_name.given, saddam.primary_name.surname) == (
        "Saddam",
        "Hussein Al-Tikriti",
    )
    assert [a.full for a in saddam.aliases] == ["Abu Ali", "Abou Ali"]
    assert saddam.gender == "MALE"
    assert saddam.source_ref == "EU.27.28 (logicalId 13)"
    # The Arabic spelling is listed first; the Latin one becomes primary.
    sukkar = entries["6504"]
    assert sukkar.primary_name.full == "Husam SUKKAR"
    assert sukkar.primary_name.quality is NameQuality.PRIMARY
    assert [(a.full, a.script) for a in sukkar.aliases] == [("حسام سكر", "Arab")]
    assert entries["112198"].gender == "FEMALE"
    assert entries["382"].entity_type is EntityType.ORGANIZATION


def test_birth_dates_and_places(entries):
    assert entries["13"].birth_dates == (PartialDate(1937, 4, 28),)
    assert entries["13"].birth_places == ("al-Awja, near Tikrit, IRAQ",)
    assert entries["131717"].birth_dates == (PartialDate(1958, 1),)
    assert entries["159310"].birth_dates == tuple(
        PartialDate(y) for y in range(1960, 1976)
    )
    # The Islamic-calendar date is skipped; the Gregorian year stays.
    assert entries["127411"].birth_dates == (PartialDate(1961),)


def test_countries_identifiers_and_listing(entries):
    assert entries["177757"].nationalities == ("RU", "AU")
    (imo,) = entries["116172"].identifiers
    assert (imo.id_type, imo.normalized) == (IdType.IMO, "5963351")
    (fiscal,) = entries["150279"].identifiers
    assert (fiscal.id_type, fiscal.normalized, fiscal.country) == (
        IdType.TAX_ID,
        "M354838D0001",
        "CF",
    )
    assert entries["117498"].identifiers[0].id_type is IdType.OTHER
    assert entries["116172"].addresses[0].country == "KP"
    assert entries["6214"].listed_on == date(2011, 4, 12)
    # No designationDate: the earliest regulation publication date.
    assert entries["13"].listed_on == date(2003, 7, 8)
    assert entries["13"].programs == ("IRQ",)


def _xml(body: str, root: str = "export", generated: str = "2026-10-15") -> io.BytesIO:
    document = f'<{root} xmlns="{NS}" generationDate="{generated}">{body}</{root}>'
    return io.BytesIO(document.encode())


def _entity(inner: str, attrs: str = 'logicalId="5"', code: str = "person") -> str:
    return (
        f'<sanctionEntity {attrs}><subjectType code="{code}"/>{inner}</sanctionEntity>'
    )


NAME = '<nameAlias wholeName="Juan PEREZ" strong="true"/>'


def test_synthetic_shapes():
    inner = NAME + (
        '<nameAlias wholeName="Juanito" strong="false"/><nameAlias wholeName=""/>'
        '<birthdate yearRangeFrom="1990" yearRangeTo="1950"/>'
        '<birthdate yearRangeTo="1950"/>'
        '<birthdate year="1970" dayOfMonth="3"/>'
        '<identification number="--" identificationTypeCode="passport"/>'
        '<address countryIso2Code="00"/>'
    )
    (entry,) = parse("EU_FSF", _xml(_entity(inner))).entries
    assert entry.aliases[0].quality is NameQuality.WEAK
    assert entry.birth_dates == (
        PartialDate(1950),
        PartialDate(1990),
        PartialDate(1970),
    )
    assert (entry.identifiers, entry.addresses, entry.listed_on) == ((), (), None)
    assert entry.source_ref == "logicalId 5"
    assert parse("EU_FSF", _xml("", generated="")).published_at is None


@pytest.mark.parametrize(
    ("document", "message"),
    [
        (_xml(_entity(NAME, attrs="")), "sanctionEntity at record 0 has no logicalId"),
        (_xml(_entity(NAME, code="vessel")), "logicalId 5 has an unknown subject type"),
        (_xml(_entity("")), "logicalId 5 has no name"),
        (
            _xml(_entity(NAME + '<birthdate birthdate="1970-02-30"/>')),
            "logicalId 5: day is out of range",
        ),
        (_xml("", generated="yesterday"), "generationDate 'yesterday'"),
        (_xml(_entity(NAME), root="other"), "root element is 'other'"),
        (io.BytesIO(FIXTURE.read_bytes()[:20_000]), "malformed XML at line"),
    ],
)
def test_malformed_files_are_refused(document, message):
    with pytest.raises(ListFormatError, match=message):
        _ = parse("EU_FSF", document)
