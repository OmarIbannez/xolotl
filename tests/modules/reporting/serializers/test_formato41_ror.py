"""Formato 41 relevantes (MX_F41_ROR) and the reporte vacío (no database)."""

from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from tests.modules.reporting.serializers.support import (
    ACME,
    DEFS_DIR,
    HEADER_ROR,
    MARIA,
    RELATED_ACCOUNT,
    ROR_OPERATIONS,
    SYNTHETIC_CURPS,
    SYNTHETIC_RFCS,
    assert_golden,
)
from xolotl.modules.party.enums import IdentifierType
from xolotl.modules.party.identifiers import NormalizedIdentifier, validate
from xolotl.modules.reporting.serializers.definitions import (
    DefinitionError,
    load_code_table,
    load_definition,
    load_layout,
)
from xolotl.modules.reporting.serializers.errors import ReportFieldError
from xolotl.modules.reporting.serializers.formato41 import (
    F41Subject,
    PersonType,
    serialize_f41,
)
from xolotl.modules.reporting.serializers.normalizers import f41_text
from xolotl.platform.enums import Confidence

ROR = load_definition(DEFS_DIR, "MX_F41_ROR", 1)
LAYOUT = load_layout(DEFS_DIR, ROR.layout)


def _records(data: bytes) -> list[list[str]]:
    text = data.decode("ascii")
    assert text.endswith("\r\n")
    return [line.split(";") for line in text.removesuffix("\r\n").split("\r\n")]


def test_fixture_identifiers_pass_the_party_validators():
    for value in SYNTHETIC_RFCS:
        assert isinstance(validate(IdentifierType.RFC, value), NormalizedIdentifier)
    for value in SYNTHETIC_CURPS:
        assert isinstance(validate(IdentifierType.CURP, value), NormalizedIdentifier)


def test_the_ror_definition_and_layout_load():
    assert ROR.report_type == "1"
    assert ROR.encoding_confidence is Confidence.UNVERIFIED
    assert len(LAYOUT.columns) == 41
    assert [c.width for c in LAYOUT.columns[16:20]] == [60, 60, 30, 30]


def test_ror_matches_its_golden_file():
    report = serialize_f41(DEFS_DIR, ROR, HEADER_ROR, ROR_OPERATIONS)
    assert len(report.parts) == 1
    assert report.record_count == 3
    assert report.confidence is Confidence.UNVERIFIED
    assert_golden("MX_F41_ROR_v1_202703.txt", report.parts[0])


def test_ror_records_follow_the_layout():
    report = serialize_f41(DEFS_DIR, ROR, HEADER_ROR, ROR_OPERATIONS)
    maria, acme, walk_in = _records(report.parts[0])
    assert all(len(row) == 41 for row in (maria, acme, walk_in))
    assert maria[:5] == ["1", "202703", "000001", "040002", "090123"]
    assert acme[2] == "000002"
    assert maria[10] == "125000.50"
    # Half up, not half even: 98765.445 is 98765.45.
    assert acme[10] == "98765.45"
    # Ñ and accents folded, the separator inside the address replaced.
    assert maria[17:20] == ["MARIA JOSE", "GOMEZ", "LOPEZ"]
    assert maria[23] == "AV. INSURGENTES SUR 1234, INT. 5 CP 03100"
    assert acme[16] == "ABARROTES EL CONDOR, S.A. DE C.V."
    # An unknown nationality is 0, a missing maternal surname is XXXX and a
    # missing neighbourhood is 0 (occasional user).
    assert walk_in[14] == "0"
    assert walk_in[19] == "XXXX"
    assert walk_in[24] == "0"
    # Columns 14 and 34 to 41 never apply to relevantes.
    assert all(not row[13] for row in (maria, acme, walk_in))
    assert all(row[33:] == [""] * 8 for row in (maria, acme, walk_in))


def test_the_empty_ror_is_the_reporte_vacio():
    report = serialize_f41(DEFS_DIR, ROR, HEADER_ROR, [])
    assert report.record_count == 1
    assert_golden("MX_F41_ROR_v1_202703_vacio.txt", report.parts[0])
    (row,) = _records(report.parts[0])
    assert row[:5] == ["1", "202703", "", "040002", "090123"]
    assert row[5:] == [""] * 36


def test_ror_is_stable_for_the_same_input():
    first = serialize_f41(DEFS_DIR, ROR, HEADER_ROR, ROR_OPERATIONS)
    second = serialize_f41(DEFS_DIR, ROR, HEADER_ROR, ROR_OPERATIONS)
    assert first == second


def test_long_free_text_is_truncated_with_a_warning():
    long_name = replace(MARIA, first_names="Anastasia " * 10)
    operation = replace(ROR_OPERATIONS[0], subject=long_name)
    report = serialize_f41(DEFS_DIR, ROR, HEADER_ROR, [operation])
    (row,) = _records(report.parts[0])
    # Cut at 60 and the space left at the cut dropped.
    assert row[17] == ("ANASTASIA " * 6).strip()
    assert [(w.code, w.line, w.column) for w in report.warnings] == [
        ("FIELD_TRUNCATED", 1, 18)
    ]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"operation_type": "99"}, "not in MX/f41_tipo_operacion"),
        ({"instrument": "1"}, "not in MX/f41_instrumento_monetario"),
        ({"account": "12345678901234567"}, "longer than 16"),
        ({"locality": ""}, "LOCALIDAD: is required"),
        ({"locality": "0901 4001"}, "must not contain spaces"),
        ({"amount": Decimal("1E14")}, "more than 14 integer digits"),
    ],
)
def test_bad_operation_values_are_refused(change, message):
    operation = replace(ROR_OPERATIONS[0], **change)
    with pytest.raises(ReportFieldError, match=message):
        _ = serialize_f41(DEFS_DIR, ROR, HEADER_ROR, [operation])


@pytest.mark.parametrize(
    ("subject", "message"),
    [
        (replace(ACME, company_name=None), "column 17"),
        (replace(MARIA, first_names=" "), "column 18"),
        (
            replace(MARIA, rfc=None, curp=None, birth_or_incorporation=None),
            "FECHA DE NACIMIENTO O CONSTITUCION: is required",
        ),
        (
            replace(MARIA, paternal_surname=None, maternal_surname=" "),
            "at least one surname",
        ),
        (replace(MARIA, neighbourhood=None), "COLONIA: is required"),
        (replace(MARIA, rfc="NOT-AN-RFC"), "rfc: not an RFC"),
        (replace(MARIA, city_code=""), "CIUDAD O POBLACION: is required"),
    ],
)
def test_bad_subjects_are_refused(subject, message):
    operation = replace(ROR_OPERATIONS[0], subject=subject)
    with pytest.raises(ReportFieldError, match=message):
        _ = serialize_f41(DEFS_DIR, ROR, HEADER_ROR, [operation])


def test_a_bad_entity_key_is_refused():
    header = replace(HEADER_ROR, entity_key="ABC")
    with pytest.raises(ReportFieldError, match="entidad"):
        _ = serialize_f41(DEFS_DIR, ROR, header, ROR_OPERATIONS)


_TEXT = st.text(min_size=1, max_size=120).filter(lambda s: s.strip(";\r\n\t "))


@settings(max_examples=60, deadline=None)
@given(
    first=_TEXT,
    surname=_TEXT,
    address=_TEXT,
    amount=st.decimals(min_value=0, max_value=Decimal("1e13"), places=4),
)
def test_any_person_yields_one_ascii_record_within_the_widths(
    first, surname, address, amount
):
    subject = F41Subject(
        person_type=PersonType.FISICA,
        nationality="MX",
        first_names=first,
        paternal_surname=surname,
        maternal_surname=None,
        birth_or_incorporation=date(1990, 1, 1),
        address=address,
        city_code="09014001",
        occasional_user=True,
    )
    operation = replace(ROR_OPERATIONS[0], subject=subject, amount=amount)
    # Text that folds to nothing would leave a required column empty.
    _ = assume(f41_text(first) and f41_text(surname) and f41_text(address))
    report = serialize_f41(DEFS_DIR, ROR, HEADER_ROR, [operation])
    data = report.parts[0]
    assert all(0x20 <= byte <= 0x7E for byte in data.removesuffix(b"\r\n"))
    (row,) = _records(data)
    assert len(row) == 41
    for column, cell in zip(LAYOUT.columns, row, strict=True):
        assert column.width is None or len(cell) <= column.width


def test_definitions_that_disagree_with_their_path_are_refused(tmp_path: Path):
    (tmp_path / "MX_F41_ROR").mkdir()
    source = (DEFS_DIR / "MX_F41_ROR" / "v1.yaml").read_text(encoding="utf-8")
    _ = (tmp_path / "MX_F41_ROR" / "v2.yaml").write_text(source, encoding="utf-8")
    with pytest.raises(DefinitionError, match="declares MX_F41_ROR v1"):
        _ = load_definition(tmp_path, "MX_F41_ROR", 2)


@pytest.mark.parametrize(
    "content", ["- just\n- a list\n", "code: [unclosed\n", "code: MX_X\n"]
)
def test_malformed_definitions_are_refused(tmp_path: Path, content: str):
    (tmp_path / "MX_X").mkdir()
    _ = (tmp_path / "MX_X" / "v1.yaml").write_text(content, encoding="utf-8")
    with pytest.raises(DefinitionError):
        _ = load_definition(tmp_path, "MX_X", 1)


def test_layouts_must_number_their_columns_in_order(tmp_path: Path):
    (tmp_path / "_layouts").mkdir()
    source = (DEFS_DIR / "_layouts" / "MX_F41.v1.yaml").read_text(encoding="utf-8")
    broken = source.replace("{number: 2,", "{number: 22,", 1)
    _ = (tmp_path / "_layouts" / "MX_F41.v1.yaml").write_text(broken, encoding="utf-8")
    with pytest.raises(DefinitionError, match="numbered 1 to n"):
        _ = load_layout(tmp_path, "MX_F41.v1")


def test_a_missing_code_table_is_a_definition_error():
    with pytest.raises(DefinitionError, match="unreadable"):
        _ = load_code_table(DEFS_DIR, "MX/does_not_exist")


ALL = "123"
EVENT = "23"
# (width, kinds the column is filled for, kinds that require it) for columns 1-41,
# copied from Anexo 1 of the resolution; None width is the variable-length text.
EXPECTED_LAYOUT = (
    (1, ALL, ALL), (8, ALL, ALL), (6, ALL, ALL), (6, ALL, ALL), (8, ALL, ALL),
    (8, ALL, ALL), (8, ALL, ALL), (2, ALL, ALL), (2, ALL, ALL), (16, ALL, ALL),
    (17, ALL, ALL), (3, ALL, ALL), (8, ALL, ALL), (8, EVENT, EVENT), (2, ALL, ALL),
    (1, ALL, ALL), (60, ALL, ""), (60, ALL, ""), (30, ALL, ""), (30, ALL, ""),
    (13, ALL, ""), (18, ALL, ""), (8, ALL, ALL), (60, ALL, ALL), (30, ALL, ALL),
    (8, ALL, ALL), (40, ALL, ""), (7, ALL, ""), (60, ALL, ""), (60, ALL, ""),
    (30, ALL, ""), (13, ALL, ""), (18, ALL, ""), (2, EVENT, EVENT), (16, EVENT, ""),
    (6, EVENT, ""), (60, EVENT, ""), (60, EVENT, ""), (30, EVENT, ""),
    (None, EVENT, EVENT), (None, EVENT, EVENT),
)  # fmt: skip


def test_the_layout_matches_the_official_column_table():
    actual = tuple(
        (
            column.width,
            "".join(sorted(column.report_types)),
            "".join(sorted(column.required_for)),
        )
        for column in LAYOUT.columns
    )
    assert actual == EXPECTED_LAYOUT


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"related": (RELATED_ACCOUNT,)}, "related rows are for inusuales"),
        ({"detection_date": date(2027, 3, 3)}, "detection date is for inusuales"),
    ],
)
def test_ror_refuses_input_that_does_not_apply(change, message):
    operation = replace(ROR_OPERATIONS[0], **change)
    with pytest.raises(ReportFieldError, match=message):
        _ = serialize_f41(DEFS_DIR, ROR, HEADER_ROR, [operation])
