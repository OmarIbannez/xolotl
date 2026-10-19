"""EU consolidated financial sanctions list, FSF XML 1.1 (``EU_FSF``, FR-SC-002).

Source: https://webgate.ec.europa.eu/fsd/fsf/public/files/xmlFullSanctionsList_1_1/content
(the public download token is a query parameter of that URL). Root ``export``
with ``generationDate`` and ``globalFileId``; one ``sanctionEntity`` per entry,
its ``logicalId`` the stable key. Everything is in attributes, and countries come
as ISO codes (``00`` meaning unknown).

Mapping: the EU has no primary-name flag, so the primary name is the first
``nameAlias`` in Latin script (some entries open with the Arabic or Cyrillic
spelling), else the first one; the rest are aliases, ``WEAK`` only when ``strong`` is
false. Birth dates use the full ``birthdate``, else ``year``/``monthOfYear``/
``dayOfMonth``, else the ``yearRangeFrom``/``yearRangeTo`` span; non-Gregorian
dates are skipped. ``designationDate`` is the listing date, falling back to the
earliest regulation publication date.
"""

from collections.abc import Iterable
from datetime import date, datetime
from typing import BinaryIO

from xolotl.modules.screening.lists.countries import alpha2
from xolotl.modules.screening.lists.dates import year_span
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
from xolotl.modules.screening.lists.xmlstream import Node, RecordStream

PARSER_VERSION = "eu-fsf-xml/1"
_WHERE = f"{ListCode.EU_FSF} ({PARSER_VERSION})"
_ROOT = "export"
_RECORDS = frozenset({"sanctionEntity"})
_GREGORIAN = "GREGORIAN"
_UNKNOWN_COUNTRY = "UNKNOWN"
_LATIN = "Latn"

_SUBJECT_TYPES = {"person": EntityType.PERSON, "enterprise": EntityType.ORGANIZATION}
_GENDERS = {"M": "MALE", "F": "FEMALE"}
_ID_TYPES = {
    "passport": IdType.PASSPORT,
    "id": IdType.NATIONAL_ID,
    "taxid": IdType.TAX_ID,
    "fiscalcode": IdType.TAX_ID,
    "euvat": IdType.TAX_ID,
    "regnumber": IdType.REGISTRATION,
    "imo": IdType.IMO,
}


def parse_eu(data: BinaryIO) -> ParsedList:
    """Parse the EU FSF consolidated XML (schema 1.1).

    Returns:
        Every sanction entity in file order.

    Raises:
        ListFormatError: the file is not the FSF export, is malformed or
            truncated, or an entity lacks its key, type or name.

    """
    stream = RecordStream(data, _RECORDS, _WHERE)
    entries: list[ListEntry] = []
    for position, record in enumerate(stream):
        if stream.root_tag != _ROOT:
            break
        entries.append(_entry(record, position))
    if stream.root_tag != _ROOT:
        msg = f"{_WHERE}: root element is {stream.root_tag!r}, expected {_ROOT!r}"
        raise ListFormatError(msg)
    return ParsedList(
        list_code=ListCode.EU_FSF,
        parser_version=PARSER_VERSION,
        published_at=_generated(stream.root_attrib.get("generationDate")),
        source_version=stream.root_attrib.get("globalFileId") or None,
        entries=tuple(entries),
    )


def _generated(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        msg = f"{_WHERE}: generationDate {value!r} is not a timestamp"
        raise ListFormatError(msg) from exc


def _attr(element: Node, name: str) -> str | None:
    value = " ".join(element.attrib.get(name, "").split())
    return value or None


def _unique[T](values: Iterable[T]) -> tuple[T, ...]:
    return tuple(dict.fromkeys(values))


def _entry(record: Node, position: int) -> ListEntry:
    key = _attr(record, "logicalId")
    if key is None:
        msg = f"{_WHERE}: sanctionEntity at record {position} has no logicalId"
        raise ListFormatError(msg)
    where = f"{_WHERE}: logicalId {key}"
    subject = record.find("subjectType")
    kind = (
        _SUBJECT_TYPES.get(_attr(subject, "code") or "")
        if subject is not None
        else None
    )
    if kind is None:
        msg = f"{where} has an unknown subject type"
        raise ListFormatError(msg)
    aliases = list(record.iterfind("nameAlias"))
    names = [n for a in aliases if (n := _name(a)) is not None]
    if not names:
        msg = f"{where} has no name"
        raise ListFormatError(msg)
    first = next((n for n in names if n.script == _LATIN), names[0])
    reference = _attr(record, "euReferenceNumber")
    try:
        return ListEntry(
            list_code=ListCode.EU_FSF,
            entry_key=key,
            entity_type=kind,
            primary_name=EntryName(
                first.full,
                NameQuality.PRIMARY,
                given=first.given,
                surname=first.surname,
                script=first.script,
            ),
            aliases=_unique(n for n in names if n != first),
            birth_dates=_unique(
                d for b in record.iterfind("birthdate") for d in _dob(b)
            ),
            birth_places=_unique(
                p for b in record.iterfind("birthdate") if (p := _birth_place(b))
            ),
            nationalities=_unique(
                c
                for c in (
                    alpha2(_attr(e, "countryIso2Code"))
                    for e in record.iterfind("citizenship")
                )
                if c
            ),
            gender=next(
                (_GENDERS[g] for a in aliases if (g := _attr(a, "gender")) in _GENDERS),
                None,
            ),
            identifiers=_unique(_identifiers(record)),
            addresses=_unique(_addresses(record)),
            programs=_unique(
                p for r in record.iterfind("regulation") if (p := _attr(r, "programme"))
            ),
            listed_on=_listed_on(record),
            source_ref=f"{reference} (logicalId {key})"
            if reference
            else f"logicalId {key}",
        )
    except ValueError as exc:
        msg = f"{where}: {exc}"
        raise ListFormatError(msg) from exc


def _name(alias: Node) -> EntryName | None:
    whole = _attr(alias, "wholeName")
    if whole is None:
        return None
    given = " ".join(
        p for p in (_attr(alias, "firstName"), _attr(alias, "middleName")) if p
    )
    weak = alias.attrib.get("strong") == "false"
    return EntryName(
        whole,
        NameQuality.WEAK if weak else NameQuality.STRONG,
        given=given or None,
        surname=_attr(alias, "lastName"),
        script=script_of(whole),
    )


def _dob(birth: Node) -> list[PartialDate]:
    calendar_type = _attr(birth, "calendarType")
    if calendar_type is not None and calendar_type != _GREGORIAN:
        return []
    full = _attr(birth, "birthdate")
    if full is not None:
        exact = date.fromisoformat(full)
        return [PartialDate(exact.year, exact.month, exact.day)]
    year = _attr(birth, "year")
    if year is not None:
        month, day = _attr(birth, "monthOfYear"), _attr(birth, "dayOfMonth")
        return [
            PartialDate(
                int(year),
                int(month) if month else None,
                int(day) if month and day else None,
            )
        ]
    start, end = _attr(birth, "yearRangeFrom"), _attr(birth, "yearRangeTo")
    if start and end:
        return year_span(int(start), int(end))
    return []


def _birth_place(birth: Node) -> str | None:
    country = _attr(birth, "countryDescription")
    parts = [
        _attr(birth, "place"),
        _attr(birth, "city"),
        _attr(birth, "region"),
        country if country != _UNKNOWN_COUNTRY else None,
    ]
    return ", ".join(p for p in parts if p) or None


def _identifiers(record: Node) -> list[EntryIdentifier]:
    found: list[EntryIdentifier] = []
    for document in record.iterfind("identification"):
        number = _attr(document, "number")
        normalized = normalize_identifier(number or "")
        if number is None or not normalized:
            continue
        kind = _ID_TYPES.get(
            _attr(document, "identificationTypeCode") or "", IdType.OTHER
        )
        if kind is IdType.IMO:
            normalized = normalized.removeprefix("IMO")
        found.append(
            EntryIdentifier(
                id_type=kind,
                value=number,
                normalized=normalized,
                country=alpha2(_attr(document, "countryIso2Code")),
                label=_attr(document, "identificationTypeDescription"),
            )
        )
    return found


def _addresses(record: Node) -> list[EntryAddress]:
    found: list[EntryAddress] = []
    for address in record.iterfind("address"):
        parts = [
            _attr(address, tag)
            for tag in ("street", "poBox", "city", "zipCode", "region", "place")
        ]
        text = ", ".join(p for p in parts if p) or None
        country = alpha2(_attr(address, "countryIso2Code"))
        if text or country:
            found.append(EntryAddress(country=country, text=text))
    return found


def _listed_on(record: Node) -> date | None:
    designated = _attr(record, "designationDate")
    if designated is not None:
        return date.fromisoformat(designated)
    published = [
        date.fromisoformat(p)
        for r in record.iterfind("regulation")
        if (p := _attr(r, "publicationDate"))
    ]
    return min(published, default=None)
