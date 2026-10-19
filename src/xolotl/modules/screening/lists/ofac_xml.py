"""OFAC SDN list, Sanctions List Service enhanced XML (``OFAC_SDN``, FR-SC-002).

Source: https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN_ENHANCED.XML
(schema ``ENHANCED_XML.xsd`` beside it). The file opens with ``referenceValues``
(countries carry their ISO code there) and then streams ``entities``; the entity
``id`` attribute is OFAC's uid and the stable key. Values are also inlined as text,
so the reference section is only read for country codes, kept in a small dict.

Mapping: the primary name's primary translation is the primary name; every other
translation (other scripts) and alias becomes an alias, ``WEAK`` when OFAC flags
the name low quality. Names keep OFAC's ``SURNAME, Given`` text; people get
``given`` and ``surname`` from the formatted parts. Birth dates take their
precision from the ``valueDate`` bounds; ranges expand per year. Identity
documents map to ``IdType`` by their type text; vessel IMO numbers become ``IMO``;
digital-currency address features become ``DIGITAL_CURRENCY_ADDRESS``
identifiers labelled with the network (FR-SC-015), compared case-sensitively.
"""

import calendar
from collections.abc import Iterable, Iterator
from datetime import UTC, date, datetime
from typing import IO

from xolotl.modules.screening.lists.countries import alpha2, country_code
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
from xolotl.modules.screening.lists.xmlstream import Node, RecordStream, text, texts

PARSER_VERSION = "ofac-sls-xml/1"
_WHERE = f"{ListCode.OFAC_SDN} ({PARSER_VERSION})"
_ROOT = "sanctionsData"
_RECORDS = frozenset({"publicationInfo", "referenceValue", "entity"})
_SDN_LIST = "SDN List"
WALLET_PREFIX = "Digital Currency Address - "
_IMO_PREFIX = "IMO"
_VESSEL_REGISTRATION = "Vessel Registration Identification"

_ENTITY_TYPES = {
    "Individual": EntityType.PERSON,
    "Entity": EntityType.ORGANIZATION,
    "Vessel": EntityType.VESSEL,
    "Aircraft": EntityType.AIRCRAFT,
}
_SCRIPTS = {
    "Latin": "Latn",
    "Cyrillic": "Cyrl",
    "Arabic": "Arab",
    "Chinese Simplified": "Hans",
    "Chinese Traditional": "Hant",
    "Japanese": "Jpan",
    "Korean": "Kore",
}
_NATIONALITY_FEATURES = frozenset({
    "Nationality Country",
    "Citizenship Country",
    "Nationality of Registration",
})
_GENDERS = {"Male": "MALE", "Female": "FEMALE"}
_DOCUMENT_TYPES = {
    IdType.PASSPORT: (
        "Passport",
        "Diplomatic Passport",
        "British National Overseas Passport",
        "Stateless Person Passport",
    ),
    IdType.NATIONAL_ID: (
        "National ID No.",
        "Personal ID Card",
        "Cedula No.",
        "D.N.I.",
        "C.I.N.",
        "Numero de Identidad",
        "Tarjeta de Identidad",
        "Turkish Identification Number",
        "Citizen's Card Number",
        "Kenyan ID No.",
        "National Foreign ID Number",
        "Moroccan Personal ID No.",
        "Bosnian Personal ID No.",
        "CNP (Personal Numerical Code)",
        "Tazkira National ID Card",
        "Credencial electoral",
        "I.F.E.",
        "SSN",
    ),
    IdType.RFC: ("R.F.C.", "RFC"),
    IdType.CURP: ("C.U.R.P.",),
    IdType.TAX_ID: (
        "Tax ID No.",
        "NIT #",
        "RUC #",
        "RIF #",
        "V.A.T. Number",
        "C.U.I.T.",
        "C.U.I.",
        "C.I.F.",
        "N.I.F.",
        "US FEIN",
        "Italian Fiscal Code",
        "Fiscal Code",
        "Paraguayan tax identification number",
        "Romanian Tax Registration",
        "RTN",
    ),
    IdType.REGISTRATION: (
        "Registration Number",
        "Business Registration Number",
        "Business Registration Document #",
        "Company Number",
        "Commercial Registry Number",
        "Registration ID",
        "Unified Social Credit Code (USCC)",
        "United Social Credit Code Certificate (USCCC)",
        "Folio Mercantil No.",
        "Matricula Mercantil No",
        "Legal Entity Number",
        "Chamber of Commerce Number",
        "UK Company Number",
        "Public Registration Number",
        "Business Number",
        "Enterprise Number",
        "C.R. No.",
        "Trade License No.",
        "Economic Register Number (CBLS)",
        "Certificate of Incorporation Number",
        "Romanian C.R.",
    ),
}
_DOCUMENT_TYPE = {
    label: kind for kind, labels in _DOCUMENT_TYPES.items() for label in labels
}
# Remarks in the legacy CSV use the same labels; longest first so a regex
# alternation prefers "Diplomatic Passport" over "Passport".
DOCUMENT_LABELS = (
    *sorted([*_DOCUMENT_TYPE, _VESSEL_REGISTRATION], key=len, reverse=True),
)


def document_kind(label: str) -> IdType:
    """Map an OFAC identity document type to the identifier kind.

    Returns:
        The kind; ``OTHER`` for labels the scorer does not compare.

    """
    return _DOCUMENT_TYPE.get(label, IdType.OTHER)


def parse_ofac_xml(data: IO[bytes]) -> ParsedList:
    """Parse the OFAC SDN enhanced XML.

    Returns:
        Every SDN entity (people, organizations, vessels, aircraft) in file order.

    Raises:
        ListFormatError: the file is not the enhanced SDN XML, is malformed or
            truncated, or an entity lacks its id or primary name.

    """
    stream = RecordStream(data, _RECORDS, _WHERE)
    countries: dict[str, str] = {}
    published: list[datetime] = []
    entries: list[ListEntry] = []
    for position, record in enumerate(stream):
        if stream.root_tag != _ROOT:
            break
        if record.tag == "referenceValue":
            _remember_country(record, countries)
        elif record.tag == "publicationInfo":
            published.extend(_published(record))
        else:
            entries.append(_entry(record, position, countries))
    if stream.root_tag != _ROOT:
        msg = f"{_WHERE}: root element is {stream.root_tag!r}, expected {_ROOT!r}"
        raise ListFormatError(msg)
    return ParsedList(
        list_code=ListCode.OFAC_SDN,
        parser_version=PARSER_VERSION,
        published_at=published[0] if published else None,
        source_version=None,
        entries=tuple(entries),
    )


def _remember_country(record: Node, countries: dict[str, str]) -> None:
    ref = record.attrib.get("refId")
    code = text(record, "isoCode")
    if ref and code and text(record, "type") == "COUNTRY":
        countries[ref] = code


def _published(record: Node) -> list[datetime]:
    value = text(record, "dataAsOf")
    if value is None:
        return []
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError as exc:
        msg = f"{_WHERE}: dataAsOf {value!r} is not a timestamp"
        raise ListFormatError(msg) from exc
    # OFAC publishes the as-of time without an offset; it is a US date, read as UTC.
    return [stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)]


def _entry(record: Node, position: int, countries: dict[str, str]) -> ListEntry:
    key = record.attrib.get("id")
    if not key:
        msg = f"{_WHERE}: entity at record {position} has no id"
        raise ListFormatError(msg)
    where = f"{_WHERE}: uid {key}"
    kind = _ENTITY_TYPES.get(text(record, "generalInfo/entityType") or "")
    if kind is None:
        msg = f"{where} has an unknown entity type"
        raise ListFormatError(msg)
    names = _names(record, person=kind is EntityType.PERSON)
    primary = next((n for n in names if n.quality is NameQuality.PRIMARY), None)
    if primary is None:
        msg = f"{where} has no primary name"
        raise ListFormatError(msg)
    features = list(record.iterfind("features/feature"))
    try:
        return ListEntry(
            list_code=ListCode.OFAC_SDN,
            entry_key=key,
            entity_type=kind,
            primary_name=primary,
            aliases=tuple(n for n in names if n is not primary),
            birth_dates=_unique(
                d for f in _typed(features, "Birthdate") for d in _dob(f)
            ),
            birth_places=_unique(
                v for f in _typed(features, "Place of Birth") if (v := text(f, "value"))
            ),
            nationalities=_unique(
                c
                for f in features
                if text(f, "type") in _NATIONALITY_FEATURES
                and (c := _feature_country(f, countries))
            ),
            gender=next(
                (
                    _GENDERS[v]
                    for f in _typed(features, "Gender")
                    if (v := text(f, "value")) in _GENDERS
                ),
                None,
            ),
            identifiers=_unique([
                *_documents(record, countries),
                *_wallets(features),
            ]),
            addresses=_addresses(record, countries),
            programs=_unique(texts(record, "sanctionsPrograms/sanctionsProgram")),
            listed_on=_listed_on(record),
            source_ref=f"uid {key}",
        )
    except ValueError as exc:
        msg = f"{where}: {exc}"
        raise ListFormatError(msg) from exc


def _unique[T](values: Iterable[T]) -> tuple[T, ...]:
    return tuple(dict.fromkeys(values))


def _typed(features: list[Node], kind: str) -> Iterator[Node]:
    return (f for f in features if text(f, "type") == kind)


def _names(record: Node, *, person: bool) -> list[EntryName]:
    found: list[EntryName] = []
    for name in record.iterfind("names/name"):
        primary_name = text(name, "isPrimary") == "true"
        low = text(name, "isLowQuality") == "true"
        for translation in name.iterfind("translations/translation"):
            full = text(translation, "formattedFullName")
            if full is None:
                continue
            primary_translation = text(translation, "isPrimary") == "true"
            if primary_name and primary_translation:
                quality = NameQuality.PRIMARY
            elif low:
                quality = NameQuality.WEAK
            else:
                quality = NameQuality.STRONG
            script = text(translation, "script")
            found.append(
                EntryName(
                    full=full,
                    quality=quality,
                    # Entities, vessels and aircraft put the whole name in the
                    # last-name part; only people have a given/surname split.
                    given=text(translation, "formattedFirstName") if person else None,
                    surname=text(translation, "formattedLastName") if person else None,
                    script=_SCRIPTS.get(script or "") or script_of(full),
                )
            )
    return list(dict.fromkeys(found))


def _dob(feature: Node) -> list[PartialDate]:
    span = feature.find("valueDate")
    if span is None:
        return []
    begin, end = text(span, "fromDateBegin"), text(span, "fromDateEnd")
    if begin is None or end is None:
        return []
    start, stop = date.fromisoformat(begin), date.fromisoformat(end)
    if text(span, "isDateRange") == "true":
        last = text(span, "toDateEnd") or end
        return year_span(start.year, date.fromisoformat(last).year)
    return [_precision(start, stop)]


def _precision(start: date, stop: date) -> PartialDate:
    # The bounds of one date say how precise it is: one day, one month or one year.
    if start == stop:
        return PartialDate(start.year, start.month, start.day)
    last_day = calendar.monthrange(start.year, start.month)[1]
    if (start.year, start.month) == (stop.year, stop.month) and (
        start.day,
        stop.day,
    ) == (
        1,
        last_day,
    ):
        return PartialDate(start.year, start.month)
    return PartialDate(start.year)


def _feature_country(feature: Node, countries: dict[str, str]) -> str | None:
    ref = text(feature, "valueRefId")
    if ref and ref in countries:
        return alpha2(countries[ref])
    return country_code(text(feature, "value"))


def _ref_country(element: Node | None, countries: dict[str, str]) -> str | None:
    if element is None:
        return None
    ref = element.attrib.get("refId")
    if ref and ref in countries:
        return alpha2(countries[ref])
    return country_code(element.text)


def document_identifier(
    label: str, number: str, country: str | None
) -> EntryIdentifier | None:
    """Build the identifier for one OFAC identity document.

    Vessel registrations of the form ``IMO 1234567`` become ``IMO`` identifiers
    holding the digits.

    Returns:
        The identifier, or ``None`` when the number has no letters or digits.

    """
    kind = document_kind(label)
    normalized = normalize_identifier(number)
    if label == _VESSEL_REGISTRATION and normalized.startswith(_IMO_PREFIX):
        kind, normalized = IdType.IMO, normalized.removeprefix(_IMO_PREFIX)
    if not normalized:
        return None
    return EntryIdentifier(
        id_type=kind, value=number, normalized=normalized, country=country, label=label
    )


def wallet_identifier(network: str, address: str) -> EntryIdentifier:
    """Build the identifier for a digital-currency address on ``network``.

    Addresses are case-sensitive (base58, bech32 checksums), so only whitespace is
    removed.

    Returns:
        The identifier labelled with the network (``XBT``, ``ETH``, ...).

    """
    return EntryIdentifier(
        id_type=IdType.DIGITAL_CURRENCY_ADDRESS,
        value=address,
        normalized="".join(address.split()),
        label=network,
    )


def _documents(record: Node, countries: dict[str, str]) -> list[EntryIdentifier]:
    found: list[EntryIdentifier] = []
    for document in record.iterfind("identityDocuments/identityDocument"):
        number = text(document, "documentNumber")
        if number is None:
            continue
        country = _ref_country(document.find("issuingCountry"), countries)
        identifier = document_identifier(text(document, "type") or "", number, country)
        if identifier is not None:
            found.append(identifier)
    return found


def _wallets(features: list[Node]) -> list[EntryIdentifier]:
    found: list[EntryIdentifier] = []
    for feature in features:
        kind = text(feature, "type") or ""
        address = text(feature, "value")
        if kind.startswith(WALLET_PREFIX) and address is not None:
            found.append(wallet_identifier(kind.removeprefix(WALLET_PREFIX), address))
    return found


def _addresses(record: Node, countries: dict[str, str]) -> tuple[EntryAddress, ...]:
    found: list[EntryAddress] = []
    for address in record.iterfind("addresses/address"):
        parts: list[str] = []
        for translation in address.iterfind("translations/translation"):
            if text(translation, "isPrimary") == "true":
                parts = texts(translation, "addressParts/addressPart/value")
        country = _ref_country(address.find("country"), countries)
        joined = ", ".join(parts) or None
        if joined or country:
            found.append(EntryAddress(country=country, text=joined))
    return _unique(found)


def _listed_on(record: Node) -> date | None:
    for listing in record.iterfind("sanctionsLists/sanctionsList"):
        published = listing.attrib.get("datePublished")
        if listing.text == _SDN_LIST and published:
            return date.fromisoformat(published)
    return None
