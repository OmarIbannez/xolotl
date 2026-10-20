"""OFAC SDN parsers: enhanced XML (``ofac-sls-xml/1``) and legacy CSV (``ofac-csv/1``).

Both run on the same 30 trimmed real entries, so they are also compared with each
other.
"""

import io
import logging
import struct
import zipfile
from datetime import UTC, date, datetime
from typing import override

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

OFAC = FIXTURES / "ofac"
XML = OFAC / "sdn_enhanced.xml"
NS = "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/ENHANCED_XML"


def _csv_zip(sdn: bytes, alt: bytes | None = None) -> io.BytesIO:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("sdn.csv", sdn)
        if alt is not None:
            archive.writestr("alt.csv", alt)
    _ = buffer.seek(0)
    return buffer


def _parse_xml() -> ParsedList:
    with XML.open("rb") as data:
        return parse("OFAC_SDN", data)


def _parse_csv() -> ParsedList:
    data = _csv_zip((OFAC / "sdn.csv").read_bytes(), (OFAC / "alt.csv").read_bytes())
    return parse("OFAC_SDN", data)


@pytest.fixture(scope="module")
def xml() -> dict[str, ListEntry]:
    return {e.entry_key: e for e in _parse_xml().entries}


@pytest.fixture(scope="module")
def csv_entries() -> dict[str, ListEntry]:
    return {e.entry_key: e for e in _parse_csv().entries}


def test_xml_matches_the_golden():
    assert_golden("ofac_xml", _parse_xml())


def test_csv_matches_the_golden():
    assert_golden("ofac_csv", _parse_csv())


def test_headers():
    xml, csv = _parse_xml(), _parse_csv()
    assert (xml.parser_version, csv.parser_version) == ("ofac-sls-xml/1", "ofac-csv/1")
    assert xml.list_code == csv.list_code == "OFAC_SDN"
    assert xml.published_at == datetime(2026, 10, 15, tzinfo=UTC)
    assert csv.published_at is None
    assert len(xml.entries) == len(csv.entries) == 30


def test_every_fixture_country_maps(caplog):
    with caplog.at_level(logging.WARNING):
        _ = _parse_xml()
        _ = _parse_csv()
    assert not caplog.records


def test_csv_and_xml_agree_on_keys_names_and_birth_years(xml, csv_entries):
    assert set(xml) == set(csv_entries)
    for key, entry in xml.items():
        other = csv_entries[key]
        assert other.entity_type is entry.entity_type, key
        assert other.primary_name.full == entry.primary_name.full, key
        assert {d.year for d in other.birth_dates} == {
            d.year for d in entry.birth_dates
        }
        assert {i.normalized for i in other.identifiers} <= {
            i.normalized for i in entry.identifiers
        }, key


def test_entity_types(xml):
    assert xml["7779"].entity_type is EntityType.PERSON
    assert xml["1700"].entity_type is EntityType.ORGANIZATION
    assert xml["23422"].entity_type is EntityType.VESSEL
    assert xml["28332"].entity_type is EntityType.AIRCRAFT


def test_names_scripts_and_quality(xml):
    person = xml["7779"]
    assert person.primary_name.full == "REXHEPI, Daut"
    assert (person.primary_name.given, person.primary_name.surname) == (
        "Daut",
        "REXHEPI",
    )
    assert xml["1700"].primary_name.surname is None
    (cyrillic,) = xml["36014"].aliases
    assert (cyrillic.script, cyrillic.quality) == ("Cyrl", NameQuality.STRONG)
    assert xml["29299"].aliases[0].script == "Hans"
    assert {a.full: a.quality for a in xml["8803"].aliases}[
        "MACUMBA"
    ] is NameQuality.WEAK
    assert xml["26723"].gender == "FEMALE"


def test_dates_and_listing(xml):
    assert xml["16606"].birth_dates == (PartialDate(1955), PartialDate(1956))
    assert xml["57118"].birth_dates == (PartialDate(1996, 6, 21),)
    assert xml["1700"].listed_on == date(1989, 10, 10)
    assert xml["18527"].nationalities == ("PS",)


def test_identifiers(xml):
    curp, wallet = xml["57118"].identifiers
    assert (curp.id_type, curp.country) == (IdType.CURP, "MX")
    assert wallet.id_type is IdType.DIGITAL_CURRENCY_ADDRESS
    assert wallet.label == "ETH"
    # Wallet addresses keep their case; checksums depend on it.
    assert wallet.normalized == "0xaC4cC4B68ea24BbFAAC8fD127B67Ed445ACcCE22"
    xbt = next(i for i in xml["45314"].identifiers if i.label == "XBT")
    assert xbt.normalized.startswith("bc1q")
    (imo,) = xml["23422"].identifiers
    assert (imo.id_type, imo.normalized) == (IdType.IMO, "6421660")
    rfc = xml["11006"].identifiers[0]
    assert (rfc.id_type, rfc.country) == (IdType.RFC, "MX")


def test_addresses_and_programs(xml):
    assert xml["45314"].addresses[0].country == "CA"
    assert "Port Coquitlam" in (xml["45314"].addresses[0].text or "")
    assert xml["36014"].programs == ("BALKANS-EO14033",)
    assert xml["7779"].source_ref == "uid 7779"


# ---- format detection --------------------------------------------------------------


def test_xml_after_a_byte_order_mark_is_detected():
    data = b"\xef\xbb\xbf" + XML.read_bytes()
    assert len(parse("OFAC_SDN", io.BytesIO(data)).entries) == 30


def test_xml_after_whitespace_is_detected():
    data = b"\n  " + _xml(_entity()).read()
    assert len(parse("OFAC_SDN", io.BytesIO(data)).entries) == 1


class _OneWay(io.BytesIO):
    @override
    def seekable(self) -> bool:
        return False


def test_a_non_seekable_stream_is_buffered():
    parsed = parse("OFAC_SDN", _OneWay(XML.read_bytes()))
    assert len(parsed.entries) == 30


def test_the_enhanced_zip_is_read_as_xml():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(XML, "SDN_ENHANCED.XML")
    _ = buffer.seek(0)
    assert parse("OFAC_SDN", buffer).parser_version == "ofac-sls-xml/1"


def test_csv_without_aliases_file():
    parsed = parse("OFAC_SDN", _csv_zip((OFAC / "sdn.csv").read_bytes()))
    assert all(not e.aliases for e in parsed.entries)


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"ent_num,name\n", "not an SDN XML file or zip"),
        (b"PK\x03\x04garbage", "corrupt zip"),
    ],
)
def test_unknown_formats_are_refused(data, message):
    with pytest.raises(ListFormatError, match=message):
        _ = parse("OFAC_SDN", io.BytesIO(data))


def test_a_zip_without_the_csv_is_refused():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("readme.txt", "hello")
    _ = buffer.seek(0)
    with pytest.raises(ListFormatError, match=r"neither an XML member nor sdn\.csv"):
        _ = parse("OFAC_SDN", buffer)


def test_a_zip_member_claiming_a_huge_size_is_refused():
    raw = bytearray(_csv_zip(b"1,A,-0- ,X,-0-,-0-,-0-,-0-,-0-,-0-,-0-,-0-\r\n").read())
    # Rewrite the central directory's uncompressed size, as a zip bomb would.
    central = raw.index(b"PK\x01\x02")
    raw[central + 24 : central + 28] = struct.pack("<I", 0xFFFF0000)
    with pytest.raises(ListFormatError, match="is too large"):
        _ = parse("OFAC_SDN", io.BytesIO(bytes(raw)))


# ---- malformed CSV -----------------------------------------------------------------

_ROW = (
    '{key},"{name}",{kind},"SDGT] [IRGC",-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,"{remarks}"'
)


def _row(
    key="1", name="DOE, John", kind='"individual"', remarks="-0-", encoding="utf-8"
) -> bytes:
    row = _ROW.format(key=key, name=name, kind=kind, remarks=remarks)
    return row.encode(encoding) + b"\r\n"


def test_csv_remarks_are_parsed():
    remarks = (
        "DOB 24 Oct 1952; alt. DOB circa 1950 to 1951; POB Culiacan, Sinaloa, Mexico;"
        " nationality Mexico; Gender Male; C.U.R.P. AAPR960621HSLLLD02 (Mexico);"
        " Passport A123 (Jalisco) issued 01 Jan 2000; Digital Currency Address - XBT"
        " 1AbC"
    )
    (entry,) = parse("OFAC_SDN", _csv_zip(_row(remarks=remarks))).entries
    assert entry.birth_dates == (
        PartialDate(1952, 10, 24),
        PartialDate(1950),
        PartialDate(1951),
    )
    assert entry.birth_places == ("Culiacan, Sinaloa, Mexico",)
    assert (entry.nationalities, entry.gender) == (("MX",), "MALE")
    assert entry.programs == ("SDGT", "IRGC")
    curp, passport, wallet = entry.identifiers
    assert (curp.id_type, curp.country) == (IdType.CURP, "MX")
    # A city in the parentheses is not a country, and is not logged as one.
    assert (passport.normalized, passport.country) == ("A123", None)
    assert (wallet.normalized, wallet.label) == ("1AbC", "XBT")


def test_csv_documents_without_letters_or_digits_are_skipped():
    (entry,) = parse("OFAC_SDN", _csv_zip(_row(remarks="Passport --; SSN 12"))).entries
    assert [i.normalized for i in entry.identifiers] == ["12"]


def test_windows_1252_csv_is_decoded():
    data = _csv_zip(_row(name="NUÑEZ, José", encoding="cp1252"))
    (entry,) = parse("OFAC_SDN", data).entries
    assert entry.primary_name.full == "NUÑEZ, José"


@pytest.mark.parametrize(
    ("sdn", "alt", "message"),
    [
        (b"1,A,B\r\n", None, "sdn.csv line 1 has 3 columns, not 12"),
        (_row(name="-0-"), None, "line 1 has no uid or no name"),
        (_row(kind='"submarine"'), None, "uid 1 has an unknown type"),
        (_row(remarks="DOB 10 Foo 1948"), None, "uid 1: unknown month 'Foo'"),
        (_row(), b'1,2,"aka",-0- ,-0- \r\n', "alt.csv line 1 has no name"),
        (_row() + _row(name="DOE, Jane"), None, "duplicate entry key '1'"),
    ],
)
def test_malformed_csv_is_refused(sdn, alt, message):
    with pytest.raises(ListFormatError, match=message):
        _ = parse("OFAC_SDN", _csv_zip(sdn, alt))


# ---- malformed or unusual XML ------------------------------------------------------


def _xml(
    entities: str, info: str = "<dataAsOf>2026-10-15T00:00:00</dataAsOf>"
) -> io.BytesIO:
    document = (
        f'<sanctionsData xmlns="{NS}"><publicationInfo>{info}</publicationInfo>'
        f"<entities>{entities}</entities></sanctionsData>"
    )
    return io.BytesIO(document.encode())


def _entity(
    body: str = "", kind: str = "Individual", key: str = 'id="9"', *, name: bool = True
) -> str:
    names = (
        (
            "<names><name><isPrimary>true</isPrimary><isLowQuality>false</isLowQuality>"
            "<translations><translation><isPrimary>true</isPrimary>"
            "<formattedFullName>DOE, John</formattedFullName></translation>"
            "</translations></name></names>"
        )
        if name
        else ""
    )
    return (
        f"<entity {key}><generalInfo><entityType>{kind}</entityType></generalInfo>"
        f"{names}{body}</entity>"
    )


def _birthdate(span: str) -> str:
    return (
        "<features><feature><type>Birthdate</type>"
        f"<valueDate>{span}</valueDate></feature></features>"
    )


@pytest.mark.parametrize(
    ("span", "dates"),
    [
        (
            "<fromDateBegin>1970-03-01</fromDateBegin><fromDateEnd>1970-03-31</fromDateEnd>",
            (PartialDate(1970, 3),),
        ),
        (
            "<fromDateBegin>1970-01-01</fromDateBegin><fromDateEnd>1970-12-31</fromDateEnd>",
            (PartialDate(1970),),
        ),
        (
            "<fromDateBegin>1970-03-05</fromDateBegin><fromDateEnd>1970-03-05</fromDateEnd>",
            (PartialDate(1970, 3, 5),),
        ),
        ("<fromDateBegin>1970-03-05</fromDateBegin>", ()),
    ],
)
def test_birth_date_precision_comes_from_the_bounds(span, dates):
    (entry,) = parse("OFAC_SDN", _xml(_entity(_birthdate(span)))).entries
    assert entry.birth_dates == dates


def test_a_feature_without_a_date_and_documents_without_numbers_are_skipped():
    body = (
        "<features><feature><type>Birthdate</type><value>?</value></feature>"
        "<feature><type>Digital Currency Address - XBT</type></feature></features>"
        "<identityDocuments><identityDocument><type>Passport</type></identityDocument>"
        "<identityDocument><type>Passport</type><documentNumber>--</documentNumber>"
        "</identityDocument></identityDocuments>"
        "<addresses><address><translations/></address></addresses>"
    )
    (entry,) = parse("OFAC_SDN", _xml(_entity(body))).entries
    assert (entry.birth_dates, entry.identifiers, entry.addresses) == ((), (), ())
    assert entry.listed_on is None


def test_unusual_names_countries_addresses_and_listings():
    body = (
        "<names><name><isPrimary>false</isPrimary><isLowQuality>false</isLowQuality>"
        "<translations><translation><isPrimary>true</isPrimary></translation>"
        "</translations></name></names>"
        "<features><feature><type>Nationality Country</type><value>Mexico</value>"
        "</feature></features>"
        "<addresses><address><country>Mexico</country><translations><translation>"
        "<isPrimary>false</isPrimary><addressParts><addressPart><value>Calle 1</value>"
        "</addressPart></addressParts></translation></translations></address></addresses>"
        '<sanctionsLists><sanctionsList datePublished="2020-01-02">Consolidated List'
        "</sanctionsList></sanctionsLists>"
    )
    (entry,) = parse("OFAC_SDN", _xml(_entity(body))).entries
    # A translation without a full name adds nothing; names without a reference
    # value map by their text; only the primary address translation is read.
    assert entry.aliases == ()
    assert entry.nationalities == ("MX",)
    assert [(a.country, a.text) for a in entry.addresses] == [("MX", None)]
    assert entry.listed_on is None


def test_without_publication_info_there_is_no_timestamp():
    assert parse("OFAC_SDN", _xml(_entity(), info="")).published_at is None


IMPOSSIBLE = (
    "<fromDateBegin>1970-02-30</fromDateBegin><fromDateEnd>1970-02-30</fromDateEnd>"
)


@pytest.mark.parametrize(
    ("document", "message"),
    [
        (_xml(_entity(key="")), "entity at record 1 has no id"),
        (_xml(_entity(kind="Submarine")), "uid 9 has an unknown entity type"),
        (_xml(_entity(name=False)), "uid 9 has no primary name"),
        (_xml(_entity(), info="<dataAsOf>soon</dataAsOf>"), "dataAsOf 'soon'"),
        (_xml(_entity(_birthdate(IMPOSSIBLE))), "uid 9: day is out of range"),
        (io.BytesIO(b"<other><entity id='1'/></other>"), "root element is 'other'"),
        (io.BytesIO(XML.read_bytes()[:50_000]), "malformed XML at line"),
    ],
)
def test_malformed_xml_is_refused(document, message):
    with pytest.raises(ListFormatError, match=message):
        _ = parse("OFAC_SDN", document)
