"""FCDO UK Sanctions List, XML (``UK_FCDO``, FR-SC-002).

Source: https://sanctionslist.fcdo.gov.uk/docs/UK-Sanctions-List.xml, fields as in
https://www.gov.uk/guidance/format-guide-for-the-uk-sanctions-list. Since 28 January
2026 this is the single UK list; OFSI's consolidated list is closed. The root is
``Designations`` with one ``Designation`` per individual, entity or ship, keyed by
``UniqueID``.

Known quirks handled here: ``Name6`` is the surname of an individual or the whole
name of an entity or ship, ``Name1`` to ``Name5`` the other names; name types vary
in case ("Primary Name", "Primary name"); a few designations carry two primary
names or an empty name record; dates are ``dd/mm/yyyy`` with ``dd``, ``mm`` or
``00`` standing for an unknown day or month, and a birth date that is still not a
date (``15/08/19yy``) is dropped with a warning rather than refusing the whole
list, since birth dates only qualify a match; IMO
numbers come with or without the ``IMO`` prefix; country names in UK style
("Congo (Democratic Republic)", "DPRK").
"""

import logging
import re
from collections.abc import Iterator
from datetime import UTC, date, datetime
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
from xolotl.modules.screening.lists.xmlstream import (
    Node,
    RecordStream,
    local,
    text,
    texts,
)

logger = logging.getLogger(__name__)

PARSER_VERSION = "uk-sl-xml/1"
_WHERE = f"{ListCode.UK_FCDO} ({PARSER_VERSION})"
_ROOT = "Designations"
_RECORD = "Designation"
_GENERATED = "DateGenerated"

_ENTITY_TYPES = {
    "individual": EntityType.PERSON,
    "entity": EntityType.ORGANIZATION,
    "ship": EntityType.VESSEL,
}
_GIVEN_PARTS = ("Name1", "Name2", "Name3", "Name4", "Name5")
_PRIMARY = "primary name"
_VARIATION = "primary name variation"
_LOW_QUALITY = "low quality a.k.a"
_GENDERS = {"male": "MALE", "female": "FEMALE"}
# Script names the list uses to ISO 15924; others fall back to the characters.
_SCRIPTS = {
    "cyrillic": "Cyrl",
    "arabic": "Arab",
    "chinese": "Hani",
    "hebrew": "Hebr",
    "georgian": "Geor",
    "korean": "Hang",
    "greek": "Grek",
    "armenian": "Armn",
}
_DATE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")
_PARTIAL_DATE = re.compile(r"^(\d{2}|dd)/(\d{2}|mm)/(\d{4})$")
_YEAR = re.compile(r"^\d{4}$")
_WORD = re.compile(r"[^\W\d_]{4,}")
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9.\-]*")
_MIN_DIGITS = 4
_ADDRESS_PARTS = (
    "AddressLine1",
    "AddressLine2",
    "AddressLine3",
    "AddressLine4",
    "AddressLine5",
    "AddressLine6",
    "AddressPostalCode",
)


def parse_uk(data: BinaryIO) -> ParsedList:
    """Parse the UK Sanctions List XML.

    Returns:
        Every designation, in file order.

    Raises:
        ListFormatError: the file is not the UK list, is malformed or truncated, or
            a designation lacks its key, a name, its type or a valid date.

    """
    stream = RecordStream(data, frozenset({_RECORD, _GENERATED}), _WHERE)
    generated: list[datetime] = []
    entries = tuple(_entries(stream, generated))
    if stream.root_tag != _ROOT:
        msg = f"{_WHERE}: root element is {stream.root_tag!r}, expected {_ROOT!r}"
        raise ListFormatError(msg)
    return ParsedList(
        list_code=ListCode.UK_FCDO,
        parser_version=PARSER_VERSION,
        published_at=generated[0] if generated else None,
        source_version=None,
        entries=entries,
    )


def _entries(stream: RecordStream, generated: list[datetime]) -> Iterator[ListEntry]:
    position = 0
    for element in stream:
        if stream.root_tag != _ROOT:
            return
        if local(element.tag) == _GENERATED:
            day = _date(element.text or "", f"{_WHERE}: {_GENERATED}")
            generated.append(datetime(day.year, day.month, day.day, tzinfo=UTC))
            continue
        yield _entry(element, position)
        position += 1


def _date(value: str, where: str) -> date:
    match = _DATE.match(value.strip())
    parsed = None
    if match is not None:
        try:
            parsed = date(int(match[3]), int(match[2]), int(match[1]))
        except ValueError:
            parsed = None
    if parsed is None:
        msg = f"{where}: {value.strip()!r} is not a dd/mm/yyyy date"
        raise ListFormatError(msg)
    return parsed


def _entry(record: Node, position: int) -> ListEntry:
    key = text(record, "UniqueID")
    if key is None:
        msg = f"{_WHERE}: designation at record {position} has no UniqueID"
        raise ListFormatError(msg)
    where = f"{_WHERE}: UniqueID {key}"
    kind = _ENTITY_TYPES.get((text(record, "IndividualEntityShip") or "").casefold())
    if kind is None:
        msg = f"{where} is not an individual, entity or ship"
        raise ListFormatError(msg)
    names = _names(record, person=kind is EntityType.PERSON)
    if not names:
        msg = f"{where} has no name"
        raise ListFormatError(msg)
    designated = text(record, "DateDesignated")
    return ListEntry(
        list_code=ListCode.UK_FCDO,
        entry_key=key,
        entity_type=kind,
        primary_name=names[0],
        aliases=_unique(names[1:]),
        birth_dates=_birth_dates(record, where),
        birth_places=_birth_places(record),
        nationalities=_nationalities(record, kind),
        gender=_gender(record),
        identifiers=_identifiers(record),
        addresses=_addresses(record),
        programs=_unique(
            p
            for p in (text(record, "RegimeName"), text(record, "UNReferenceNumber"))
            if p
        ),
        listed_on=_date(designated, f"{where}: DateDesignated") if designated else None,
        source_ref=f"UniqueID {key}",
    )


def _unique[T](values: Iterator[T] | list[T] | tuple[T, ...]) -> tuple[T, ...]:
    return tuple(dict.fromkeys(values))


def _names(record: Node, *, person: bool) -> list[EntryName]:
    # The first primary name leads; further primaries, variations and aliases
    # follow as aliases. Name records with no text are skipped.
    primaries: list[EntryName] = []
    others: list[EntryName] = []
    for name in record.iterfind("Names/Name"):
        entry_name = _name(name, person=person)
        if entry_name is None:
            continue
        name_type = (text(name, "NameType") or "").casefold()
        if name_type == _PRIMARY:
            primaries.append(entry_name)
        else:
            others.append(entry_name)
    for original in record.iterfind("NonLatinNames/NonLatinName"):
        full = text(original, "NameNonLatinScript")
        if full is None:
            continue
        script = _SCRIPTS.get((text(original, "NonLatinScriptType") or "").casefold())
        others.append(
            EntryName(full, NameQuality.STRONG, script=script or script_of(full))
        )
    if not primaries:
        return []
    first = primaries[0]
    lead = EntryName(
        first.full,
        NameQuality.PRIMARY,
        given=first.given,
        surname=first.surname,
        script=first.script,
    )
    return [lead, *primaries[1:], *others]


def _name(name: Node, *, person: bool) -> EntryName | None:
    given = " ".join(p for part in _GIVEN_PARTS if (p := text(name, part))) or None
    last = text(name, "Name6")
    full = " ".join(p for p in (given, last) if p)
    if not full:
        return None
    name_type = (text(name, "NameType") or "").casefold()
    strength = (text(name, "AliasStrength") or "").casefold()
    quality = NameQuality.STRONG
    if name_type not in {_PRIMARY, _VARIATION} and strength == _LOW_QUALITY:
        quality = NameQuality.WEAK
    return EntryName(
        full,
        quality,
        given=given if person else None,
        surname=last if person else None,
        script=script_of(full),
    )


def _birth_dates(record: Node, where: str) -> tuple[PartialDate, ...]:
    found: list[PartialDate] = []
    for value in texts(record, "IndividualDetails/Individual/DOBs/DOB"):
        partial = _partial_date(value)
        if partial is None:
            logger.warning("%s: birth date %r dropped (not a date)", where, value)
            continue
        found.append(partial)
    return _unique(found)


def _partial_date(value: str) -> PartialDate | None:
    # "dd", "mm" and "00" stand for an unknown day or month.
    if _YEAR.match(value):
        return PartialDate(int(value))
    match = _PARTIAL_DATE.match(value)
    if match is None:
        return None
    day, month, year = (int(p) if p.isdigit() else 0 for p in match.groups())
    try:
        if month == 0:
            return PartialDate(year)
        return PartialDate(year, month, day or None)
    except ValueError:
        return None


def _birth_places(record: Node) -> tuple[str, ...]:
    places: list[str] = []
    for location in record.iterfind(
        "IndividualDetails/Individual/BirthDetails/Location"
    ):
        parts = [text(location, "TownOfBirth"), text(location, "CountryOfBirth")]
        joined = ", ".join(p for p in parts if p)
        if joined:
            places.append(joined)
    return _unique(places)


def _nationalities(record: Node, kind: EntityType) -> tuple[str, ...]:
    # A ship's flag is its nationality (the state it is registered in).
    path = (
        "ShipDetails/Ship/CurrentBelievedFlagOfShips/CurrentBelievedFlagOfShip"
        if kind is EntityType.VESSEL
        else "IndividualDetails/Individual/Nationalities/Nationality"
    )
    return _unique(c for v in texts(record, path) if (c := country_code(v)))


def _gender(record: Node) -> str | None:
    genders = {
        _GENDERS.get(v.casefold())
        for v in texts(record, "IndividualDetails/Individual/Genders/Gender")
    }
    # Two different genders on one record say nothing usable.
    return genders.pop() if len(genders) == 1 else None


def _identifiers(record: Node) -> tuple[EntryIdentifier, ...]:
    found: list[EntryIdentifier] = []
    individual = "IndividualDetails/Individual"
    for passport in record.iterfind(f"{individual}/PassportDetails/Passport"):
        found.extend(
            _identifier(
                IdType.PASSPORT,
                text(passport, "PassportNumber"),
                text(passport, "PassportAdditionalInformation"),
            )
        )
    for national in record.iterfind(
        f"{individual}/NationalIdentifierDetails/NationalIdentifier"
    ):
        found.extend(
            _identifier(
                IdType.NATIONAL_ID,
                text(national, "NationalIdentifierNumber"),
                text(national, "NationalIdentifierAdditionalInformation"),
            )
        )
    for number in texts(
        record,
        "EntityDetails/Entity/BusinessRegistrationNumbers/BusinessRegistrationNumber",
    ):
        found.extend(_identifier(IdType.REGISTRATION, number, None))
    for number in texts(record, "ShipDetails/Ship/IMONumbers/IMONumber"):
        found.extend(_identifier(IdType.IMO, number, None))
    return _unique(found)


def _identifier(
    id_type: IdType, value: str | None, label: str | None
) -> list[EntryIdentifier]:
    if value is None:
        return []
    return [
        EntryIdentifier(id_type, value, normalized, label=label)
        for normalized in _numbers(id_type, value)
    ]


def _numbers(id_type: IdType, value: str) -> list[str]:
    # Identifier cells are free text ("TIN (Taxpayer Identification Number)
    # 9718168599 / SRN ... 1217700038710"). A cell without a word of four or more
    # letters is one identifier, spaces and all; otherwise every token with at least
    # four digits is one.
    if id_type is IdType.IMO:
        # The register number is the seven digits; "IMO" is a prefix some rows carry.
        number = normalize_identifier(value).removeprefix("IMO")
        return [number] if number else []
    if _WORD.search(value) is None:
        number = normalize_identifier(value)
        return [number] if number else []
    tokens = (normalize_identifier(m[0]) for m in _TOKEN.finditer(value))
    return list(
        dict.fromkeys(t for t in tokens if sum(c.isdigit() for c in t) >= _MIN_DIGITS)
    )


def _addresses(record: Node) -> tuple[EntryAddress, ...]:
    found: list[EntryAddress] = []
    for address in record.iterfind("Addresses/Address"):
        joined = ", ".join(p for part in _ADDRESS_PARTS if (p := text(address, part)))
        country = country_code(text(address, "AddressCountry"))
        if joined or country:
            found.append(EntryAddress(country=country, text=joined or None))
    return _unique(found)
