"""UN Security Council consolidated list, XML (``UN_CONSOLIDATED``, FR-SC-002).

Source: https://scsanctions.un.org/resources/xml/en/consolidated.xml, schema
https://www.un.org/sc/resources/sc-sanctions.xsd. The file holds ``INDIVIDUALS``
and ``ENTITIES``; ``DATAID`` is the stable key.

Known quirks handled here: names split over ``FIRST_NAME`` to ``FOURTH_NAME`` with
no reliable given/surname split (so neither is set); empty alias elements; alias
quality ``Good``/``Low`` for people and ``a.k.a.``/``f.k.a.`` for entities; birth
dates as an exact date, a year, a ``BETWEEN`` year range or only a note such as
"Nov. 1973"; ``LISTED_ON`` sometimes carrying a UTC offset; country names in UN
style ("Iran (Islamic Republic of)").
"""

import re
from collections.abc import Iterator
from datetime import date, datetime
from typing import BinaryIO

from xolotl.modules.screening.lists.countries import country_code
from xolotl.modules.screening.lists.errors import ListFormatError
from xolotl.modules.screening.lists.text import normalize_identifier, script_of
from xolotl.modules.screening.lists.types import (
    EntityType,
    EntryAddress,
    EntryIdentifier,
    EntryName,
    IdType,
    ListCode,
    ListEntry,
    NameQuality,
    ParsedList,
    PartialDate,
)
from xolotl.modules.screening.lists.xmlstream import Node, RecordStream, text, texts

PARSER_VERSION = "un-xml/1"
_WHERE = f"{ListCode.UN_CONSOLIDATED} ({PARSER_VERSION})"
_ROOT = "CONSOLIDATED_LIST"
_RECORDS = frozenset({"INDIVIDUAL", "ENTITY"})
_NAME_PARTS = ("FIRST_NAME", "SECOND_NAME", "THIRD_NAME", "FOURTH_NAME")
# A BETWEEN range wider than this keeps only its two ends.
_MAX_RANGE_YEARS = 20
_ISO_DATE_LEN = 10

_ALIAS_QUALITY = {"low": NameQuality.WEAK}
_DOCUMENT_TYPES = {
    "passport": IdType.PASSPORT,
    "national identification number": IdType.NATIONAL_ID,
}
_GENDERS = {"male": "MALE", "female": "FEMALE"}
_MONTHS = {
    name: number
    for number, names in enumerate(
        (
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ),
        start=1,
    )
    for name in names
}
_NOTE_DATE = re.compile(r"^(?:(?P<month>[A-Za-z]+)\.?\s+)?(?P<year>\d{4})$")


def parse_un(data: BinaryIO) -> ParsedList:
    """Parse the UN consolidated list XML.

    Returns:
        Every individual and entity, in file order.

    Raises:
        ListFormatError: the file is not the UN list, is malformed or truncated,
            or a record lacks its key or name.

    """
    stream = RecordStream(data, _RECORDS, _WHERE)
    entries = tuple(_entries(stream))
    if stream.root_tag != _ROOT:
        msg = f"{_WHERE}: root element is {stream.root_tag!r}, expected {_ROOT!r}"
        raise ListFormatError(msg)
    return ParsedList(
        list_code=ListCode.UN_CONSOLIDATED,
        parser_version=PARSER_VERSION,
        published_at=_generated(stream.root_attrib.get("dateGenerated")),
        source_version=None,
        entries=entries,
    )


def _entries(stream: RecordStream) -> Iterator[ListEntry]:
    for position, record in enumerate(stream):
        if stream.root_tag != _ROOT:
            return
        yield _entry(record, position)


def _generated(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        msg = f"{_WHERE}: dateGenerated {value!r} is not a timestamp"
        raise ListFormatError(msg) from exc


def _entry(record: Node, position: int) -> ListEntry:
    key = text(record, "DATAID")
    if key is None:
        msg = f"{_WHERE}: {record.tag} at record {position} has no DATAID"
        raise ListFormatError(msg)
    where = f"{_WHERE}: DATAID {key}"
    person = record.tag == "INDIVIDUAL"
    full = " ".join(p for part in _NAME_PARTS if (p := text(record, part)))
    if not full:
        msg = f"{where} has no name"
        raise ListFormatError(msg)
    try:
        return ListEntry(
            list_code=ListCode.UN_CONSOLIDATED,
            entry_key=key,
            entity_type=EntityType.PERSON if person else EntityType.ORGANIZATION,
            primary_name=EntryName(full, NameQuality.PRIMARY, script=script_of(full)),
            aliases=_aliases(record, person=person),
            birth_dates=_birth_dates(record),
            birth_places=_birth_places(record),
            nationalities=_unique(
                c for v in texts(record, "NATIONALITY/VALUE") if (c := country_code(v))
            ),
            gender=_GENDERS.get((text(record, "GENDER") or "").casefold()),
            identifiers=_identifiers(record),
            addresses=_addresses(
                record, "INDIVIDUAL_ADDRESS" if person else "ENTITY_ADDRESS"
            ),
            programs=_unique(
                p
                for p in (
                    text(record, "UN_LIST_TYPE"),
                    text(record, "REFERENCE_NUMBER"),
                )
                if p
            ),
            listed_on=_listed_on(text(record, "LISTED_ON")),
            source_ref=f"DATAID {key}",
        )
    except ValueError as exc:
        msg = f"{where}: {exc}"
        raise ListFormatError(msg) from exc


def _unique[T](values: Iterator[T] | list[T] | tuple[T, ...]) -> tuple[T, ...]:
    return tuple(dict.fromkeys(values))


def _aliases(record: Node, *, person: bool) -> tuple[EntryName, ...]:
    names: list[EntryName] = []
    original = text(record, "NAME_ORIGINAL_SCRIPT")
    if original:
        names.append(
            EntryName(original, NameQuality.STRONG, script=script_of(original))
        )
    tag = "INDIVIDUAL_ALIAS" if person else "ENTITY_ALIAS"
    for alias in record.iterfind(tag):
        name = text(alias, "ALIAS_NAME")
        if name is None:
            continue
        quality = _ALIAS_QUALITY.get(
            (text(alias, "QUALITY") or "").casefold(), NameQuality.STRONG
        )
        names.append(EntryName(name, quality, script=script_of(name)))
    return _unique(names)


def _birth_dates(record: Node) -> tuple[PartialDate, ...]:
    dates: list[PartialDate] = []
    for dob in record.iterfind("INDIVIDUAL_DATE_OF_BIRTH"):
        dates.extend(_dob(dob))
    for alias in record.iterfind("INDIVIDUAL_ALIAS"):
        value = text(alias, "DATE_OF_BIRTH")
        if value:
            dates.append(_iso_partial(value))
    return _unique(dates)


def _dob(dob: Node) -> list[PartialDate]:
    exact, year = text(dob, "DATE"), text(dob, "YEAR")
    if exact:
        return [_iso_partial(exact)]
    if year:
        return [PartialDate(int(year))]
    return _year_range(text(dob, "FROM_YEAR"), text(dob, "TO_YEAR")) or _note_date(
        text(dob, "NOTE")
    )


def _year_range(start: str | None, end: str | None) -> list[PartialDate]:
    if not (start and end):
        return []
    first, last = sorted((int(start), int(end)))
    if last - first > _MAX_RANGE_YEARS:
        return [PartialDate(first), PartialDate(last)]
    return [PartialDate(y) for y in range(first, last + 1)]


def _note_date(note: str | None) -> list[PartialDate]:
    # Some records give the date only as a note: "Nov. 1973", "August 1961", "1968".
    match = _NOTE_DATE.match(note or "")
    if match is None:
        return []
    month = match["month"]
    number = None if month is None else _MONTHS.get(month.casefold())
    return [PartialDate(int(match["year"]), number)]


def _iso_partial(value: str) -> PartialDate:
    parts = [int(p) for p in value[:_ISO_DATE_LEN].split("-")]
    return PartialDate(*parts)


def _birth_places(record: Node) -> tuple[str, ...]:
    places: list[str] = []
    for place in record.iterfind("INDIVIDUAL_PLACE_OF_BIRTH"):
        parts = [
            text(place, tag) for tag in ("STREET", "CITY", "STATE_PROVINCE", "COUNTRY")
        ]
        joined = ", ".join(p for p in parts if p)
        if joined:
            places.append(joined)
    for alias in record.iterfind("INDIVIDUAL_ALIAS"):
        parts = [text(alias, "CITY_OF_BIRTH"), text(alias, "COUNTRY_OF_BIRTH")]
        joined = ", ".join(p for p in parts if p)
        if joined:
            places.append(joined)
    return _unique(places)


def _identifiers(record: Node) -> tuple[EntryIdentifier, ...]:
    found: list[EntryIdentifier] = []
    for document in record.iterfind("INDIVIDUAL_DOCUMENT"):
        number = text(document, "NUMBER")
        normalized = normalize_identifier(number or "")
        if number is None or not normalized:
            continue
        kind = text(document, "TYPE_OF_DOCUMENT")
        label = text(document, "TYPE_OF_DOCUMENT2") or kind
        country = text(document, "ISSUING_COUNTRY") or text(
            document, "COUNTRY_OF_ISSUE"
        )
        found.append(
            EntryIdentifier(
                id_type=_DOCUMENT_TYPES.get((kind or "").casefold(), IdType.OTHER),
                value=number,
                normalized=normalized,
                country=country_code(country),
                label=label,
            )
        )
    return _unique(found)


def _addresses(record: Node, tag: str) -> tuple[EntryAddress, ...]:
    found: list[EntryAddress] = []
    for address in record.iterfind(tag):
        parts = [
            text(address, tag)
            for tag in ("STREET", "CITY", "STATE_PROVINCE", "ZIP_CODE")
        ]
        joined = ", ".join(p for p in parts if p) or None
        country = country_code(text(address, "COUNTRY"))
        if joined or country:
            found.append(EntryAddress(country=country, text=joined))
    return _unique(found)


def _listed_on(value: str | None) -> date | None:
    return None if value is None else date.fromisoformat(value[:_ISO_DATE_LEN])
