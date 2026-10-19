"""UN consolidated list parser (``un-xml/1``) on a trimmed real sample."""

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

FIXTURE = FIXTURES / "un" / "consolidated.xml"


def _parse_fixture() -> ParsedList:
    with FIXTURE.open("rb") as data:
        return parse("UN_CONSOLIDATED", data)


@pytest.fixture(scope="module")
def parsed() -> ParsedList:
    return _parse_fixture()


def _by_key(parsed: ParsedList, key: str) -> ListEntry:
    return next(e for e in parsed.entries if e.entry_key == key)


def test_matches_the_golden(parsed):
    assert_golden("un", parsed)


def test_header_and_counts(parsed):
    assert parsed.list_code == "UN_CONSOLIDATED"
    assert parsed.parser_version == "un-xml/1"
    assert parsed.published_at == datetime(2026, 10, 19, 23, 0, 3, 500000, tzinfo=UTC)
    kinds = [e.entity_type for e in parsed.entries]
    assert kinds.count(EntityType.PERSON) == 22
    assert kinds.count(EntityType.ORGANIZATION) == 8


def test_every_fixture_country_maps(caplog):
    with caplog.at_level(logging.WARNING):
        _ = _parse_fixture()
    assert not caplog.records


def test_person_names_scripts_and_alias_quality(parsed):
    saddam = _by_key(parsed, "6908048")
    assert saddam.primary_name.full == "SADDAM HUSSEIN AL-TIKRITI"
    assert saddam.primary_name.quality is NameQuality.PRIMARY
    assert saddam.primary_name.given is None
    original, low = saddam.aliases
    assert (original.script, original.quality) == ("Arab", NameQuality.STRONG)
    assert (low.full, low.quality) == ("Abu Ali", NameQuality.WEAK)
    assert saddam.source_ref == "DATAID 6908048"
    assert saddam.programs == ("Iraq", "IQi.001")
    assert _by_key(parsed, "2960872").aliases[0].script == "Cyrl"


def test_birth_date_shapes(parsed):
    # BETWEEN 1959 and 1965 expands to each year.
    assert _by_key(parsed, "6909615").birth_dates == tuple(
        PartialDate(y) for y in range(1959, 1966)
    )
    # A note-only date ("Nov. 1973") keeps its month.
    assert _by_key(parsed, "6907996").birth_dates == (PartialDate(1973, 11),)
    assert _by_key(parsed, "6908048").birth_dates == (PartialDate(1937, 4, 28),)


def test_listed_on_with_an_offset_and_gender(parsed):
    assert _by_key(parsed, "6908457").listed_on == date(2015, 7, 1)
    assert _by_key(parsed, "6908473").gender == "FEMALE"
    assert _by_key(parsed, "6908021").gender == "MALE"


def test_documents_become_identifiers(parsed):
    passport = _by_key(parsed, "6908473").identifiers[0]
    assert passport.id_type is IdType.PASSPORT
    assert passport.country == "GB"
    assert passport.normalized.isalnum()
    assert passport.normalized == passport.normalized.upper()
    national = _by_key(parsed, "6908010").identifiers[0]
    assert national.id_type is IdType.NATIONAL_ID


def test_entities_and_addresses(parsed):
    group = _by_key(parsed, "110401")
    assert group.entity_type is EntityType.ORGANIZATION
    assert group.birth_dates == ()
    assert len(group.addresses) == 3
    assert {a.country for a in group.addresses} == {"IR"}


def _xml(body: str, root: str = "CONSOLIDATED_LIST") -> io.BytesIO:
    document = f"<{root}><INDIVIDUALS>{body}</INDIVIDUALS></{root}>"
    return io.BytesIO(b'<?xml version="1.0" encoding="UTF-8"?>' + document.encode())


def test_a_truncated_file_is_refused():
    data = FIXTURE.read_bytes()
    with pytest.raises(ListFormatError, match=r"un-xml/1\): malformed XML at line"):
        _ = parse("UN_CONSOLIDATED", io.BytesIO(data[: len(data) // 2]))


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (
            "<INDIVIDUAL><FIRST_NAME>A</FIRST_NAME></INDIVIDUAL>",
            "record 0 has no DATAID",
        ),
        ("<INDIVIDUAL><DATAID>7</DATAID></INDIVIDUAL>", "DATAID 7 has no name"),
        (
            (
                "<INDIVIDUAL><DATAID>7</DATAID><FIRST_NAME>A</FIRST_NAME></INDIVIDUAL>"
                "<INDIVIDUAL><DATAID>7</DATAID><FIRST_NAME>B</FIRST_NAME></INDIVIDUAL>"
            ),
            "duplicate entry key '7'",
        ),
        (
            (
                "<INDIVIDUAL><DATAID>7</DATAID><FIRST_NAME>A</FIRST_NAME>"
                "<LISTED_ON>2020-02-31</LISTED_ON></INDIVIDUAL>"
            ),
            "DATAID 7: day is out of range",
        ),
        (
            (
                "<INDIVIDUAL><DATAID>7</DATAID><FIRST_NAME>A</FIRST_NAME>"
                "<INDIVIDUAL_DATE_OF_BIRTH><YEAR>19x5</YEAR></INDIVIDUAL_DATE_OF_BIRTH>"
                "</INDIVIDUAL>"
            ),
            "DATAID 7: invalid literal",
        ),
    ],
)
def test_malformed_records_are_refused(body, message):
    with pytest.raises(ListFormatError, match=message):
        _ = parse("UN_CONSOLIDATED", _xml(body))


def test_another_document_is_refused():
    body = "<INDIVIDUAL><DATAID>7</DATAID><FIRST_NAME>A</FIRST_NAME></INDIVIDUAL>"
    with pytest.raises(ListFormatError, match="root element is 'OTHER'"):
        _ = parse("UN_CONSOLIDATED", _xml(body, root="OTHER"))


def test_entity_expansion_is_refused():
    bomb = (
        b'<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaa">]>'
        b"<CONSOLIDATED_LIST>&a;</CONSOLIDATED_LIST>"
    )
    with pytest.raises(ListFormatError, match="forbidden XML construct"):
        _ = parse("UN_CONSOLIDATED", io.BytesIO(bomb))


def test_a_bad_generation_timestamp_is_refused():
    data = io.BytesIO(b'<CONSOLIDATED_LIST dateGenerated="yesterday"/>')
    with pytest.raises(ListFormatError, match="dateGenerated 'yesterday'"):
        _ = parse("UN_CONSOLIDATED", data)


def test_unknown_list_codes_are_refused():
    with pytest.raises(ListFormatError) as caught:
        _ = parse("XX_NOPE", io.BytesIO(b""))
    assert caught.value.code == "LIST_CODE_UNSUPPORTED"


def test_synthetic_date_and_place_shapes():
    body = (
        "<INDIVIDUAL><DATAID>8</DATAID><FIRST_NAME>A</FIRST_NAME>"
        "<INDIVIDUAL_ALIAS><QUALITY>Good</QUALITY><ALIAS_NAME>B</ALIAS_NAME>"
        "<DATE_OF_BIRTH>1971</DATE_OF_BIRTH><CITY_OF_BIRTH>Algiers</CITY_OF_BIRTH>"
        "<COUNTRY_OF_BIRTH>Algeria</COUNTRY_OF_BIRTH></INDIVIDUAL_ALIAS>"
        "<INDIVIDUAL_DATE_OF_BIRTH><TYPE_OF_DATE>BETWEEN</TYPE_OF_DATE>"
        "<FROM_YEAR>1990</FROM_YEAR><TO_YEAR>1950</TO_YEAR></INDIVIDUAL_DATE_OF_BIRTH>"
        "<INDIVIDUAL_DATE_OF_BIRTH><TYPE_OF_DATE>EXACT</TYPE_OF_DATE>"
        "<NOTE>1968</NOTE></INDIVIDUAL_DATE_OF_BIRTH>"
        "<INDIVIDUAL_DATE_OF_BIRTH><TYPE_OF_DATE>EXACT</TYPE_OF_DATE>"
        "<NOTE>Approximately</NOTE></INDIVIDUAL_DATE_OF_BIRTH>"
        "</INDIVIDUAL>"
    )
    (entry,) = parse("UN_CONSOLIDATED", _xml(body)).entries
    # A range wider than 20 years keeps its two ends, sorted.
    assert entry.birth_dates == (
        PartialDate(1950),
        PartialDate(1990),
        PartialDate(1968),
        PartialDate(1971),
    )
    assert entry.birth_places == ("Algiers, Algeria",)
