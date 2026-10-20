"""SAT Article 69-B CFF list, CSV (``MX_SAT_69B``, FR-SC-002, TS-03 §6).

Source: http://omawww.sat.gob.mx/cifras_sat/Documents/Listado_Completo_69-B.csv,
taxpayers with presumed non-existent operations. Windows-1252 (UTF-8 is accepted
too), two preamble lines before the header row; one row per proceeding.

Shape decisions:

- ``entry_key`` is the RFC. A taxpayer with several proceedings appears on several
  rows; they become one entry whose ``status`` is the most severe one
  (``DEFINITIVO`` > ``PRESUNTO`` > ``SENTENCIA_FAVORABLE`` > ``DESVIRTUADO``), so
  an RFC stays screenable while any proceeding keeps it listed. Every proceeding's
  presumption notice number is kept in ``programs``.
- ``entity_type`` follows the RFC length: 12 characters for a company, 13 for a
  person. Rows whose RFC is ``XXXXXXXXXXXX`` were suppressed by court order and
  carry no identity; they are skipped. Any other RFC shape refuses the file.
- Text after ``//`` in a name is a court-ruling annotation, not a name, and is
  dropped, except ``ANTES <name>`` (a former name), which becomes an alias.
- ``listed_on`` is the earliest SAT publication of the presumption;
  ``delisted_on`` the SAT publication of the clearing ruling when the entry is not
  screenable. ``published_at`` comes from the preamble's "Información actualizada
  al <d> de <mes> de <aaaa>".
"""

import csv
import io
import logging
import re
import unicodedata
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import BinaryIO

from xolotl.modules.screening.lists.errors import ListFormatError
from xolotl.modules.screening.lists.types import (
    EntityType,
    EntryIdentifier,
    EntryName,
    IdType,
    ListCode,
    ListEntry,
    NameQuality,
    ParsedList,
)

logger = logging.getLogger(__name__)

PARSER_VERSION = "sat-69b-csv/1"
_WHERE = f"{ListCode.MX_SAT_69B} ({PARSER_VERSION})"
PROGRAM = "CFF_69B"

# Most severe first; the first two keep the taxpayer screenable.
_STATUSES = ("DEFINITIVO", "PRESUNTO", "SENTENCIA_FAVORABLE", "DESVIRTUADO")
_SCREENABLE = frozenset(_STATUSES[:2])

_COL_NUMBER = "No"
_COL_RFC = "RFC"
_COL_NAME = "Nombre del Contribuyente"
_COL_STATUS = "Situación del contribuyente"
_COL_NOTICE = "Número y fecha de oficio global de presunción SAT"
_COL_PRESUMED = "Publicación página SAT presuntos"
_COL_CLEARED = "Publicación página SAT desvirtuados"
_COL_FAVORABLE = "Publicación página SAT sentencia favorable"
_REQUIRED = (_COL_NUMBER, _COL_RFC, _COL_NAME, _COL_STATUS, _COL_NOTICE, _COL_PRESUMED)

_SUPPRESSED_RFC = "XXXXXXXXXXXX"
_RFC = re.compile(r"^[A-ZÑ&]{3,4}\d{6}[A-Z\d]{3}$")
_COMPANY_RFC_LEN = 12
_DATE = re.compile(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})")
_NOTICE = re.compile(r"^\s*(\S+)\s+de\s+fecha", re.IGNORECASE)
_FORMER_NAME = re.compile(r"^ANTES\s+(?P<name>.+)$", re.IGNORECASE)
_UPDATED = re.compile(
    r"actualizada al (?P<day>\d{1,2}) de (?P<month>[a-z]+) de (?P<year>\d{4})",
    re.IGNORECASE,
)
_MONTHS = {
    name: number
    for number, name in enumerate(
        (
            "enero",
            "febrero",
            "marzo",
            "abril",
            "mayo",
            "junio",
            "julio",
            "agosto",
            "septiembre",
            "octubre",
            "noviembre",
            "diciembre",
        ),
        start=1,
    )
}


@dataclass(frozen=True, slots=True)
class _Row:
    line: int
    number: str
    rfc: str
    name: str
    former: str | None
    status: str
    notice: str | None
    presumed_on: date | None
    cleared_on: date | None


def parse_sat_69b(data: BinaryIO) -> ParsedList:
    """Parse ``Listado_Completo_69-B.csv``.

    A damaged encoding, a missing header, an invalid RFC, an unknown status or a
    missing presumption date refuses the whole file with ``ListFormatError``.

    Returns:
        One entry per RFC, ordered by the first row the RFC appears on.

    """
    reader = csv.reader(io.StringIO(_decode(data.read()), newline=""))
    preamble: list[str] = []
    header = _header(reader, preamble)
    grouped: dict[str, list[_Row]] = {}
    for cells in reader:
        if not any(c.strip() for c in cells):
            continue
        row = _row(dict(zip(header, cells, strict=False)), reader.line_num)
        if row is not None:
            grouped.setdefault(row.rfc, []).append(row)
    return ParsedList(
        list_code=ListCode.MX_SAT_69B,
        parser_version=PARSER_VERSION,
        published_at=_published_at(preamble),
        source_version=None,
        entries=tuple(_entry(rows) for rows in grouped.values()),
    )


def _decode(raw: bytes) -> str:
    try:
        decoded = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            decoded = raw.decode("cp1252")
        except UnicodeDecodeError as exc:
            msg = (
                f"{_WHERE}: file is neither UTF-8 nor Windows-1252 at byte {exc.start}"
            )
            raise ListFormatError(msg) from exc
    if "�" in decoded:
        # The file was re-saved through a wrong encoding: names are already lost.
        line = decoded[: decoded.index("�")].count("\n") + 1
        msg = f"{_WHERE}: replacement character at line {line} (encoding damaged)"
        raise ListFormatError(msg)
    return decoded


def _header(reader: "Iterator[list[str]]", preamble: list[str]) -> list[str]:
    # Rows before the header are the preamble; quoted cells may span lines.
    for cells in reader:
        if cells[:2] == [_COL_NUMBER, _COL_RFC]:
            missing = [c for c in _REQUIRED if c not in cells]
            if missing:
                msg = f"{_WHERE}: header lacks {missing}"
                raise ListFormatError(msg)
            return cells
        preamble.append(" ".join(cells))
    msg = f"{_WHERE}: header row ('No,RFC,…') not found"
    raise ListFormatError(msg)


def _published_at(preamble: Sequence[str]) -> datetime | None:
    for line in preamble:
        match = _UPDATED.search(line)
        if match is None:
            continue
        month = _MONTHS.get(match["month"].casefold())
        if month is None:
            msg = f"{_WHERE}: unknown month {match['month']!r} in the preamble"
            raise ListFormatError(msg)
        return datetime(int(match["year"]), month, int(match["day"]), tzinfo=UTC)
    return None


def _fold(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return "_".join(stripped.upper().split())


def _cell(record: dict[str, str], column: str) -> str:
    return " ".join(record.get(column, "").split())


def _row(record: dict[str, str], line: int) -> _Row | None:
    where = f"{_WHERE}: line {line}"
    rfc = _cell(record, _COL_RFC).upper()
    if rfc == _SUPPRESSED_RFC:
        logger.info("%s: row suppressed by court order skipped", where)
        return None
    if not _RFC.match(rfc):
        msg = f"{where}: RFC is not a 12- or 13-character RFC"
        raise ListFormatError(msg)
    status = _fold(_cell(record, _COL_STATUS))
    if status not in _STATUSES:
        msg = f"{where}: unknown status {_cell(record, _COL_STATUS)!r}"
        raise ListFormatError(msg)
    raw_name, _, note = _cell(record, _COL_NAME).partition("//")
    name = raw_name.strip()
    if not name:
        msg = f"{where}: taxpayer name is empty"
        raise ListFormatError(msg)
    former = _FORMER_NAME.match(note.strip())
    notice = _NOTICE.match(_cell(record, _COL_NOTICE))
    presumed = _cell(record, _COL_PRESUMED)
    cleared = _cell(record, _COL_CLEARED) or _cell(record, _COL_FAVORABLE)
    return _Row(
        line=line,
        number=_cell(record, _COL_NUMBER),
        rfc=rfc,
        name=name,
        former=former["name"].strip() if former else None,
        status=status,
        notice=notice[1] if notice else None,
        presumed_on=_first_date(presumed, f"{where}: {_COL_PRESUMED}", required=True),
        cleared_on=_first_date(cleared, f"{where}: clearing publication"),
    )


def _first_date(value: str, where: str, *, required: bool = False) -> date | None:
    # Some cells hold a range ("01/02/2019 - 05/03/2019"); the first date counts.
    match = _DATE.search(value)
    if match is None:
        if required:
            msg = f"{where}: {value!r} has no dd/mm/yyyy date"
            raise ListFormatError(msg)
        return None
    try:
        return date(int(match[3]), int(match[2]), int(match[1]))
    except ValueError as exc:
        msg = f"{where}: {match[0]!r} is not a valid date"
        raise ListFormatError(msg) from exc


def _unique[T](values: Iterator[T]) -> tuple[T, ...]:
    return tuple(dict.fromkeys(values))


def _entry(rows: list[_Row]) -> ListEntry:
    lead = min(rows, key=lambda r: _STATUSES.index(r.status))
    rfc = lead.rfc
    # The RFC pattern admits only these two lengths.
    entity_type = (
        EntityType.ORGANIZATION if len(rfc) == _COMPANY_RFC_LEN else EntityType.PERSON
    )
    screenable = lead.status in _SCREENABLE
    presumed = [r.presumed_on for r in rows if r.presumed_on is not None]
    names = _unique(
        n for r in rows for n in (r.name, r.former) if n is not None and n != lead.name
    )
    return ListEntry(
        list_code=ListCode.MX_SAT_69B,
        entry_key=rfc,
        entity_type=entity_type,
        primary_name=EntryName(lead.name, NameQuality.PRIMARY, script="Latn"),
        aliases=tuple(EntryName(n, NameQuality.STRONG, script="Latn") for n in names),
        identifiers=(EntryIdentifier(IdType.RFC, rfc, rfc, country="MX", label="RFC"),),
        programs=(PROGRAM, *_unique(r.notice for r in rows if r.notice)),
        listed_on=min(presumed) if presumed else None,
        delisted_on=None if screenable else lead.cleared_on,
        status=lead.status,
        screenable=screenable,
        source_ref=f"RFC {rfc} (No {', '.join(r.number for r in rows)})",
    )
