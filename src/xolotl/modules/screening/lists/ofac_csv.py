"""OFAC SDN list, legacy CSV files (``OFAC_SDN`` fallback, FR-SC-002).

Source: ``SDN.CSV`` and ``ALT.CSV`` from
https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/, given
together as one zip (``sdn.csv`` required, ``alt.csv`` optional). Ported from
sentinel's ``screening/lists.py`` (D22): the remarks regexes, the ``-0-`` nulls and
the Windows-1252 fallback. The enhanced XML is the primary format; this parser
keeps fixtures and a fallback when only the CSV is reachable.

The CSV flattens what the XML structures: birth dates, places, nationalities,
gender and documents are read back out of the free-text remarks, which OFAC
truncates for long entries; there is no low-quality flag, no listing date and no
publication timestamp.
"""

import csv
import io
import re
from collections.abc import Iterable

from xolotl.modules.screening.lists.countries import country_code
from xolotl.modules.screening.lists.dates import year_span
from xolotl.modules.screening.lists.errors import ListFormatError
from xolotl.modules.screening.lists.ofac_xml import (
    DOCUMENT_LABELS,
    WALLET_PREFIX,
    document_identifier,
    wallet_identifier,
)
from xolotl.modules.screening.lists.text import script_of
from xolotl.modules.screening.lists.types import (
    EntityType,
    EntryIdentifier,
    EntryName,
    ListCode,
    ListEntry,
    NameQuality,
    ParsedList,
    PartialDate,
)

PARSER_VERSION = "ofac-csv/1"
_WHERE = f"{ListCode.OFAC_SDN} ({PARSER_VERSION})"
_NULL = "-0-"
_SDN_COLUMNS = 12
_ALT_COLUMNS = 5
# Old OFAC files end with a DOS end-of-file byte.
_EOF = "\x1a"

_ENTITY_TYPES = {
    "individual": EntityType.PERSON,
    "vessel": EntityType.VESSEL,
    "aircraft": EntityType.AIRCRAFT,
    "": EntityType.ORGANIZATION,
}
_MONTHS = {
    m: i
    for i, m in enumerate(
        ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
         "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
        start=1,
    )
}  # fmt: skip
_DATE = r"(?:\d{1,2} )?(?:[A-Z][a-z]{2} )?\d{4}"
_DOB = re.compile(rf"\bDOB (?:circa )?(?P<value>{_DATE})(?: to (?P<to>{_DATE}))?")
_POB = re.compile(r"\bPOB (?P<place>[^;]+?)\s*(?:;|$)")
_NATIONALITY = re.compile(r"\b(?:nationality|citizen) (?P<country>[^;]+?)\s*(?:;|$)")
_GENDER = re.compile(r"\bGender (?P<gender>Male|Female)\b")
_DOCUMENT = re.compile(
    r"(?:^|; )(?:alt\. )?(?P<label>"
    + "|".join(re.escape(label) for label in DOCUMENT_LABELS)
    + r") (?P<number>[^;(]+?)(?= \(| issued| expires|[;,]|$)"
    + r"(?: \((?P<country>[^)]*)\))?"
)
_WALLET = re.compile(
    r"(?:^|; )(?:alt\. )?"
    + re.escape(WALLET_PREFIX)
    + r"(?P<network>\S+) (?P<address>[^;\s]+)"
)


def parse_ofac_csv(sdn: str, alt: str | None) -> ParsedList:
    """Parse the legacy SDN CSV with its optional aliases file.

    A row with the wrong number of columns, no uid or no name, or an impossible
    date in its remarks raises ``ListFormatError``.

    Returns:
        Every SDN row in file order.

    """
    aliases = _aliases(alt or "")
    entries = [
        _entry(row, line, aliases.get(row[0].strip(), []))
        for line, row in _rows(sdn, _SDN_COLUMNS, "sdn.csv")
    ]
    return ParsedList(
        list_code=ListCode.OFAC_SDN,
        parser_version=PARSER_VERSION,
        published_at=None,
        source_version=None,
        entries=tuple(entries),
    )


def decode(raw: bytes) -> str:
    """Decode a CSV file: UTF-8 when it is, else Windows-1252 as OFAC used to.

    Returns:
        The text without a byte-order mark.

    """
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def _rows(text: str, width: int, name: str) -> Iterable[tuple[int, list[str]]]:
    for line, row in enumerate(csv.reader(io.StringIO(text)), start=1):
        if not row or row == [_EOF]:
            continue
        if len(row) != width:
            msg = f"{_WHERE}: {name} line {line} has {len(row)} columns, not {width}"
            raise ListFormatError(msg)
        yield line, row


def _clean(value: str) -> str:
    value = value.strip()
    return "" if value == _NULL else value


def _aliases(alt: str) -> dict[str, list[str]]:
    # Every alias type (aka, fka, nka) is a strong name; the CSV has no quality flag.
    found: dict[str, list[str]] = {}
    for line, row in _rows(alt, _ALT_COLUMNS, "alt.csv"):
        name = _clean(row[3])
        if not name:
            msg = f"{_WHERE}: alt.csv line {line} has no name"
            raise ListFormatError(msg)
        found.setdefault(row[0].strip(), []).append(name)
    return found


def _split_name(full: str, *, person: bool) -> EntryName:
    surname, comma, given = full.partition(",")
    if not (person and comma):
        return EntryName(full, NameQuality.STRONG, script=script_of(full))
    return EntryName(
        full,
        NameQuality.STRONG,
        given=given.strip() or None,
        surname=surname.strip() or None,
        script=script_of(full),
    )


def _entry(row: list[str], line: int, aliases: list[str]) -> ListEntry:
    key, full, kind_text = row[0].strip(), _clean(row[1]), _clean(row[2])
    if not key or not full:
        msg = f"{_WHERE}: sdn.csv line {line} has no uid or no name"
        raise ListFormatError(msg)
    kind = _ENTITY_TYPES.get(kind_text)
    if kind is None:
        msg = f"{_WHERE}: uid {key} has an unknown type"
        raise ListFormatError(msg)
    person = kind is EntityType.PERSON
    remarks = _clean(row[11])
    primary = _split_name(full, person=person)
    try:
        return ListEntry(
            list_code=ListCode.OFAC_SDN,
            entry_key=key,
            entity_type=kind,
            primary_name=EntryName(
                primary.full,
                NameQuality.PRIMARY,
                given=primary.given,
                surname=primary.surname,
                script=primary.script,
            ),
            aliases=_unique(_split_name(name, person=person) for name in aliases),
            birth_dates=_unique(d for m in _DOB.finditer(remarks) for d in _dob(m)),
            birth_places=_unique(m["place"] for m in _POB.finditer(remarks)),
            nationalities=_unique(
                c
                for m in _NATIONALITY.finditer(remarks)
                if (c := country_code(m["country"]))
            ),
            gender=(g["gender"].upper() if (g := _GENDER.search(remarks)) else None),
            identifiers=_unique(_identifiers(remarks)),
            programs=_programs(_clean(row[3])),
            source_ref=f"uid {key}",
        )
    except ValueError as exc:
        msg = f"{_WHERE}: uid {key}: {exc}"
        raise ListFormatError(msg) from exc


def _unique[T](values: Iterable[T]) -> tuple[T, ...]:
    return tuple(dict.fromkeys(values))


def _partial(value: str) -> PartialDate:
    # "24 Oct 1952", "Feb 1965" or "1948".
    match value.split():
        case [day, month, year]:
            return PartialDate(int(year), _month(month), int(day))
        case [month, year]:
            return PartialDate(int(year), _month(month))
        case _:
            return PartialDate(int(value))


def _month(name: str) -> int:
    if name not in _MONTHS:
        msg = f"unknown month {name!r}"
        raise ValueError(msg)
    return _MONTHS[name]


def _dob(match: re.Match[str]) -> list[PartialDate]:
    start = _partial(match["value"])
    if match["to"] is None:
        return [start]
    return year_span(start.year, _partial(match["to"]).year)


def _identifiers(remarks: str) -> list[EntryIdentifier]:
    found: list[EntryIdentifier] = []
    for match in _DOCUMENT.finditer(remarks):
        country = country_code(match["country"], quiet=True)
        identifier = document_identifier(match["label"], match["number"], country)
        if identifier is not None:
            found.append(identifier)
    found.extend(
        wallet_identifier(m["network"], m["address"]) for m in _WALLET.finditer(remarks)
    )
    return found


def _programs(value: str) -> tuple[str, ...]:
    # "SDGT] [IRGC" or "[SDGT] [IFSR]": bracketed codes separated by spaces.
    return _unique(p for p in re.split(r"\]\s*\[", value.strip("[] ")) if p)
