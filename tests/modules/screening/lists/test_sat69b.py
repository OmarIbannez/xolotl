"""SAT 69-B list parser (``sat-69b-csv/1``) on a trimmed real sample (TS-03 §6)."""

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
    parse,
)

FIXTURE = FIXTURES / "sat69b" / "Listado_Completo_69-B.csv"
HEADER = (
    "No,RFC,Nombre del Contribuyente,Situación del contribuyente,"
    "Número y fecha de oficio global de presunción SAT,"
    "Publicación página SAT presuntos,"
    "Publicación página SAT desvirtuados,"
    "Publicación página SAT sentencia favorable"
)


def _parse_fixture() -> ParsedList:
    with FIXTURE.open("rb") as data:
        return parse("MX_SAT_69B", data)


@pytest.fixture(scope="module")
def parsed() -> ParsedList:
    return _parse_fixture()


def _by_key(parsed: ParsedList, key: str) -> ListEntry:
    return next(e for e in parsed.entries if e.entry_key == key)


def test_matches_the_golden(parsed):
    assert_golden("sat69b", parsed)


def test_header_and_statuses(parsed):
    assert parsed.list_code == "MX_SAT_69B"
    assert parsed.parser_version == "sat-69b-csv/1"
    assert parsed.published_at == datetime(2025, 12, 31, tzinfo=UTC)
    statuses = {(e.status, e.screenable) for e in parsed.entries}
    assert statuses == {
        ("PRESUNTO", True),
        ("DEFINITIVO", True),
        ("DESVIRTUADO", False),
        ("SENTENCIA_FAVORABLE", False),
    }


def test_rfc_is_key_identifier_and_entity_type(parsed):
    company = _by_key(parsed, "AAA120730823")
    assert company.entity_type is EntityType.ORGANIZATION
    assert [(i.id_type, i.normalized, i.country) for i in company.identifiers] == [
        (IdType.RFC, "AAA120730823", "MX")
    ]
    person = _by_key(parsed, "AAAA620217U54")
    assert person.entity_type is EntityType.PERSON
    assert person.primary_name.full == "AMADOR AQUINO JOSÉ AVENAMAR"
    assert person.listed_on == date(2017, 6, 1)


def test_several_proceedings_merge_into_the_most_severe(parsed):
    abira = _by_key(parsed, "AAS110331G59")
    assert (abira.status, abira.screenable) == ("DEFINITIVO", True)
    assert abira.programs == ("CFF_69B", "500-05-2020-7866", "500-05-2017-16140")
    assert abira.listed_on == date(2017, 6, 1)
    assert abira.delisted_on is None
    assert abira.source_ref == "RFC AAS110331G59 (No 155, 156)"
    # The court-ruling annotation after "//" is not part of the name.
    assert abira.primary_name.full == "ABIRA & SAFFI CORPORATIVO, S. DE R.L. DE C.V."
    araxaxa = _by_key(parsed, "ACO101220B96")
    assert [a.full for a in araxaxa.aliases] == ["ARAXAXA COMUNICACIÓN, S.C."]
    assert araxaxa.aliases[0].quality is NameQuality.STRONG


def test_cleared_entries_carry_their_clearing_date(parsed):
    cleared = _by_key(parsed, "AAA091014835")
    assert (cleared.status, cleared.screenable) == ("DESVIRTUADO", False)
    assert cleared.delisted_on == date(2017, 10, 13)
    favorable = _by_key(parsed, "AAA080808HL8")
    assert favorable.delisted_on == date(2019, 3, 5)


def test_former_names_and_multiline_names(parsed):
    former = _by_key(parsed, "CDT991103QR3")
    assert [a.full for a in former.aliases] == [
        "CERAMICA DISEÑO Y TRADICION, S.A. DE C.V."
    ]
    assert _by_key(parsed, "CAR160229F29").primary_name.full == (
        "CARYRA, S. DE R.L. DE C.V."
    )


def test_court_suppressed_rows_are_skipped(parsed, caplog):
    assert all(e.entry_key != "XXXXXXXXXXXX" for e in parsed.entries)
    with caplog.at_level(logging.INFO):
        _ = _parse_fixture()
    assert "suppressed by court order" in caplog.text


def _csv(
    *rows: str, preamble: str = "Información actualizada al 5 de mayo de 2026"
) -> io.BytesIO:
    text = "\r\n".join([preamble, HEADER, *rows]) + "\r\n"
    return io.BytesIO(text.encode("cp1252"))


ROW = (
    "1,ABC010203AB1,EMPRESA X S.A. DE C.V.,Presunto,"
    "500-05-2026-1 de fecha 1 de mayo de 2026,02/05/2026,,"
)


def test_a_minimal_file_and_utf8_input():
    parsed = parse("MX_SAT_69B", _csv(ROW))
    assert parsed.published_at == datetime(2026, 5, 5, tzinfo=UTC)
    assert parsed.entries[0].programs == ("CFF_69B", "500-05-2026-1")
    utf8 = io.BytesIO(("﻿" + HEADER + "\r\n" + ROW + ",\r\n,,,\r\n").encode())
    entry = parse("MX_SAT_69B", utf8).entries[0]
    assert entry.listed_on == date(2026, 5, 2)
    assert parse("MX_SAT_69B", _csv(ROW, preamble="x")).published_at is None


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (_csv(ROW.replace("ABC010203AB1", "ABC0102")), "line 3: RFC is not"),
        (_csv(ROW.replace("Presunto", "Cancelado")), "unknown status 'Cancelado'"),
        (_csv(ROW.replace(",02/05/2026,", ",pendiente,")), "has no dd/mm/yyyy date"),
        (
            _csv(ROW.replace(",02/05/2026,", ",31/02/2026,")),
            "'31/02/2026' is not a valid",
        ),
        (_csv(ROW.replace("EMPRESA X S.A. DE C.V.", "// nota")), "name is empty"),
        (_csv(ROW, preamble="actualizada al 5 de brumario de 2026"), "unknown month"),
        (io.BytesIO(b"No;RFC\r\n1;X\r\n"), "header row"),
        (io.BytesIO(b"No,RFC,Nombre del Contribuyente\r\n"), "header lacks"),
        (io.BytesIO(b"No,RFC\r\n\x81\x8d\xff\xfe"), "neither UTF-8 nor Windows-1252"),
        (
            io.BytesIO((HEADER + "\r\n" + ROW.replace("EMPRESA", "EMPR�SA")).encode()),
            "replacement character at line 2",
        ),
    ],
)
def test_malformed_files_are_refused(data, message):
    with pytest.raises(ListFormatError, match=message):
        _ = parse("MX_SAT_69B", data)
