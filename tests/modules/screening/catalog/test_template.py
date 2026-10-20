"""The private-list template: reading CSV and XLSX, row rules, errors without values."""

import csv
import io
import re
import tracemalloc
import zipfile
from collections.abc import Callable
from datetime import date
from pathlib import Path

import pytest
from openpyxl import Workbook

from xolotl.modules.screening.catalog.template import (
    CSV_TYPE,
    MAX_REPORTED_ERRORS,
    TEMPLATE_COLUMNS,
    XLSX_TYPE,
    TemplateError,
    TemplateRow,
    read_template,
    rows_to_entries,
)
from xolotl.modules.screening.errors import PrivateListInvalidError
from xolotl.modules.screening.lists import (
    EntityType,
    EntryIdentifier,
    EntryName,
    IdType,
    NameQuality,
    PartialDate,
)

PRIVATE = Path(__file__).parent / "fixtures" / "private"
LPB = "MX_LPB"
MAX_ROWS = 1000


def key_tag(text: str) -> str:
    return "t:" + text


def _entries(name: str, content_type: str = CSV_TYPE):
    rows = read_template((PRIVATE / name).read_bytes(), content_type, max_rows=MAX_ROWS)
    return rows_to_entries(rows, list_code=LPB, key_tag=key_tag)


def _csv(*rows: dict[str, str]) -> bytes:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=TEMPLATE_COLUMNS)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return out.getvalue().encode()


def _invalid(rows: list[TemplateRow]) -> PrivateListInvalidError:
    with pytest.raises(PrivateListInvalidError) as caught:
        _ = rows_to_entries(rows, list_code=LPB, key_tag=key_tag)
    return caught.value


def _file_error(data: bytes, content_type: str = CSV_TYPE, max_rows: int = 5):
    with pytest.raises(PrivateListInvalidError) as caught:
        _ = read_template(data, content_type, max_rows=max_rows)
    return caught.value


def test_a_csv_and_the_same_xlsx_give_the_same_entries():
    from_csv = _entries("lpb_v1.csv")
    assert from_csv == _entries("lpb_v1.xlsx", XLSX_TYPE)
    first, company, keyed = from_csv
    assert first.entry_key == "LPB-001"
    assert first.entity_type is EntityType.PERSON
    assert first.primary_name == EntryName(
        full="Ximena Quetzalcoyotl Tlalocana",
        quality=NameQuality.PRIMARY,
        given="Ximena",
        surname="Quetzalcoyotl Tlalocana",
    )
    assert first.aliases == (EntryName("Xime Quetzal", NameQuality.STRONG),)
    assert first.birth_dates == (PartialDate(1980, 1, 1),)
    assert [i.id_type for i in first.identifiers] == [IdType.RFC, IdType.CURP]
    assert first.listed_on == date(2024, 5, 1)
    assert first.list_code == LPB
    assert company.entity_type is EntityType.ORGANIZATION
    assert company.primary_name.full == "Fantasmagorica Comercial SA de CV"
    assert keyed.birth_dates == (PartialDate(1975),)
    assert keyed.nationalities == ("MX", "GT")
    assert keyed.identifiers == (
        EntryIdentifier(IdType.OTHER, "PAS-12345", "PAS12345"),
    )


def test_semicolon_csv_and_a_bom_are_read():
    assert _entries("lpb_semicolon.csv") == _entries("lpb_v1.csv")


PF = {"clave": "K1", "tipo": "PF", "nombre_completo": "Nombre Sintetico Uno"}
PM = {"clave": "K1", "tipo": "PM", "razon_social": "Empresa Sintetica SA"}


@pytest.mark.parametrize(
    ("cells", "column", "code"),
    [
        ({"clave": "K1", "tipo": ""}, "tipo", TemplateError.REQUIRED),
        ({**PF, "tipo": "XX"}, "tipo", TemplateError.INVALID_TYPE),
        ({"clave": "K1", "tipo": "PF"}, "nombre_completo", TemplateError.REQUIRED),
        (
            {"clave": "K1", "tipo": "PF", "nombres": "Uno"},
            "primer_apellido",
            TemplateError.REQUIRED,
        ),
        ({**PF, "nombres": "Uno"}, "nombre_completo", TemplateError.NAME_AMBIGUOUS),
        ({**PF, "razon_social": "Otra"}, "razon_social", TemplateError.NAME_AMBIGUOUS),
        ({**PM, "nombres": "Uno"}, "nombres", TemplateError.NAME_AMBIGUOUS),
        (
            {**PM, "segundo_apellido": "Dos"},
            "segundo_apellido",
            TemplateError.NAME_AMBIGUOUS,
        ),
        ({**PM, "razon_social": ""}, "razon_social", TemplateError.REQUIRED),
        (
            {**PF, "fecha_nacimiento": "1980-02-30"},
            "fecha_nacimiento",
            TemplateError.INVALID_DATE,
        ),
        (
            {**PF, "fecha_nacimiento": "80"},
            "fecha_nacimiento",
            TemplateError.INVALID_DATE,
        ),
        ({**PF, "fecha_alta": "1980"}, "fecha_alta", TemplateError.INVALID_DATE),
        ({**PF, "rfc": "GOMA800101H75"}, "rfc", TemplateError.INVALID_RFC),
        ({**PF, "curp": "GOMA800101HDFRRN09"}, "curp", TemplateError.INVALID_CURP),
        ({**PF, "rfc": "FCO010203XY7"}, "rfc", TemplateError.RFC_TYPE_MISMATCH),
        ({**PM, "rfc": "GOMA800101H74"}, "rfc", TemplateError.RFC_TYPE_MISMATCH),
        (
            {**PF, "nacionalidad": "MX|MEX"},
            "nacionalidad",
            TemplateError.INVALID_COUNTRY,
        ),
    ],
)
def test_each_row_rule_reports_its_code_and_cell(cells, column, code):
    error = _invalid([TemplateRow(number=2, cells=cells)])
    assert [(e.loc, e.type) for e in error.errors] == [(("row", 2, column), code.value)]
    assert error.errors[0].msg


def test_the_duplicate_rule_points_at_the_later_row():
    error = _invalid([TemplateRow(2, PF), TemplateRow(3, PF)])
    assert [(e.loc, e.type) for e in error.errors] == [
        (("row", 3, "clave"), TemplateError.DUPLICATE_KEY.value)
    ]


def test_every_bad_row_is_reported_up_to_two_hundred():
    rows = [
        TemplateRow(n, {**PF, "clave": f"K{n}", "tipo": "??"}) for n in range(2, 302)
    ]
    error = _invalid(rows)
    assert len(error.errors) == MAX_REPORTED_ERRORS
    assert error.errors[0].loc == ("row", 2, "tipo")
    assert "300" in error.detail


def test_bad_rows_fixture_reports_one_code_per_row():
    rows = read_template(
        (PRIVATE / "bad_rows.csv").read_bytes(), CSV_TYPE, max_rows=MAX_ROWS
    )
    error = _invalid(rows)
    assert [(e.loc[1], e.loc[2], e.type) for e in error.errors] == [
        (2, "tipo", "INVALID_TYPE"),
        (3, "nombre_completo", "NAME_AMBIGUOUS"),
        (4, "fecha_nacimiento", "INVALID_DATE"),
        (5, "rfc", "INVALID_RFC"),
        (6, "curp", "INVALID_CURP"),
        (7, "rfc", "RFC_TYPE_MISMATCH"),
        (8, "nacionalidad", "INVALID_COUNTRY"),
        (9, "razon_social", "REQUIRED"),
        (10, "fecha_alta", "INVALID_DATE"),
        (11, "clave", "DUPLICATE_KEY"),
    ]


def test_row_errors_never_echo_cell_values():
    data = (PRIVATE / "bad_rows.csv").read_bytes()
    rows = read_template(data, CSV_TYPE, max_rows=MAX_ROWS)
    error = _invalid(rows)
    said = "\n".join([
        str(error),
        error.detail,
        repr(error.errors),
        *(e.msg for e in error.errors),
    ])
    # Every cell but the key and the type, which the template itself defines.
    cells = {
        value
        for row in rows
        for column, value in row.cells.items()
        if value and column not in {"clave", "tipo"}
    }
    assert cells
    assert [cell for cell in cells if cell in said] == []


def test_a_blank_clave_gets_a_keyed_tag_and_duplicates_are_refused():
    keyed = {
        "tipo": "PF",
        "nombre_completo": "Zacarías  Ocelotl",
        "fecha_nacimiento": "1975",
    }
    (entry,) = rows_to_entries([TemplateRow(2, keyed)], list_code=LPB, key_tag=key_tag)
    assert entry.entry_key == "t:zacarias ocelotl|1975"
    assert not entry.source_ref
    same = {**keyed, "nombre_completo": "ZACARIAS OCELOTL"}
    error = _invalid([TemplateRow(2, keyed), TemplateRow(4, same)])
    assert [e.loc for e in error.errors] == [("row", 4, "clave")]


def test_too_many_rows_and_missing_columns_are_file_errors():
    too_many = _file_error(_csv(PF, {**PF, "clave": "K2"}), max_rows=1)
    no_tipo = _file_error(b"clave,nombre_completo\nK1,Uno\n")
    unknown = _file_error(b"clave,tipo,color\nK1,PF,rojo\n")
    empty = _file_error(b"clave,tipo\n\n")
    nothing = _file_error(b"")
    unreadable = _file_error(b"\xff\xfe\x00bad")
    not_xlsx = _file_error(b"plain text", XLSX_TYPE)
    for error, code in [
        (too_many, TemplateError.TOO_MANY_ROWS),
        (no_tipo, TemplateError.MISSING_COLUMN),
        (unknown, TemplateError.UNKNOWN_COLUMN),
        (empty, TemplateError.EMPTY_FILE),
        (nothing, TemplateError.EMPTY_FILE),
        (unreadable, TemplateError.UNREADABLE_FILE),
        (not_xlsx, TemplateError.UNREADABLE_FILE),
    ]:
        assert [(e.loc, e.type) for e in error.errors] == [(("file",), code.value)]


def test_header_names_are_trimmed_and_lowercased_and_blank_columns_skipped():
    rows = read_template(
        b" Clave , TIPO ,,Nombre_Completo\nK1,PF,,Uno Dos\n", CSV_TYPE, max_rows=5
    )
    assert rows == [
        TemplateRow(2, {"clave": "K1", "tipo": "PF", "nombre_completo": "Uno Dos"})
    ]


def test_identifiers_are_normalized_with_the_party_rules():
    cells = {**PF, "rfc": " goma-800101 h74 ", "curp": "goma800101hdfrrn05"}
    (entry,) = rows_to_entries([TemplateRow(2, cells)], list_code=LPB, key_tag=key_tag)
    assert entry.identifiers == (
        EntryIdentifier(IdType.RFC, "GOMA800101H74", "GOMA800101H74", country="MX"),
        EntryIdentifier(
            IdType.CURP, "GOMA800101HDFRRN05", "GOMA800101HDFRRN05", country="MX"
        ),
    )


def test_observaciones_and_row_numbers_never_enter_the_entry():
    entries = _entries("lpb_v1.csv")
    stored = b"".join(e.canonical_json() for e in entries)
    assert b"confidencial" not in stored
    assert b"secreta" not in stored
    assert [e.source_ref for e in entries] == ["clave LPB-001", "clave LPB-002", ""]
    moved = rows_to_entries(
        [
            TemplateRow(n + 7, r.cells)
            for n, r in enumerate(
                read_template(
                    (PRIVATE / "lpb_v1.csv").read_bytes(), CSV_TYPE, max_rows=9
                )
            )
        ],
        list_code=LPB,
        key_tag=key_tag,
    )
    assert [e.content_hash() for e in moved] == [e.content_hash() for e in entries]


def test_an_xlsx_with_dates_and_numbers_reads_as_text():
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.append(["clave", "tipo", "nombre_completo", "fecha_nacimiento", "fecha_alta"])
    sheet.append([1001, "PF", "Nombre Sintetico", 1980.0, date(2024, 5, 1)])
    out = io.BytesIO()
    book.save(out)
    rows = read_template(out.getvalue(), XLSX_TYPE, max_rows=5)
    assert rows[0].cells == {
        "clave": "1001",
        "tipo": "PF",
        "nombre_completo": "Nombre Sintetico",
        "fecha_nacimiento": "1980",
        "fecha_alta": "2024-05-01",
    }


def _xlsx(*rows: list[str]) -> bytes:
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    for row in rows:
        sheet.append(row)
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def _with_sheet(data: bytes, change: Callable[[bytes], bytes]) -> bytes:
    # The workbook with its first sheet's XML replaced by change(xml).
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(out, "w") as z:
        for name in source.namelist():
            content = source.read(name)
            if name.endswith("sheet1.xml"):
                content = change(content)
            z.writestr(name, content)
    return out.getvalue()


def test_a_truncated_xlsx_sheet_is_unreadable_not_a_crash():
    data = _with_sheet(
        (PRIVATE / "lpb_v1.xlsx").read_bytes(), lambda xml: xml[: len(xml) // 2]
    )
    error = _file_error(data, XLSX_TYPE, max_rows=MAX_ROWS)
    assert [(e.loc, e.type) for e in error.errors] == [
        (("file",), TemplateError.UNREADABLE_FILE.value)
    ]


def test_a_forged_xlsx_width_is_capped_and_extra_rows_stop_the_read():
    # The sheet claims 16,384 columns; padding every row to that width would take
    # tens of MB for these 200 rows.
    header = ["clave", "tipo", "nombre_completo"]
    body = [[f"K{n}", "PF", f"Nombre {n}"] for n in range(200)]
    data = _with_sheet(
        _xlsx(header, *body),
        lambda xml: re.sub(
            rb'<dimension ref="[^"]*"', b'<dimension ref="A1:XFD1048576"', xml
        ),
    )
    error = _file_error(data, XLSX_TYPE, max_rows=3)
    assert [(e.loc, e.type) for e in error.errors] == [
        (("file",), TemplateError.TOO_MANY_ROWS.value)
    ]
    tracemalloc.start()
    try:
        rows = read_template(data, XLSX_TYPE, max_rows=MAX_ROWS)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 5 * 1024 * 1024
    assert len(rows) == 200
    assert rows[0].cells == {"clave": "K0", "tipo": "PF", "nombre_completo": "Nombre 0"}


def test_reading_stops_at_the_first_row_past_the_limit():
    # A row after the limit is never read: here it would be unreadable.
    data = b"clave,tipo\nK1,PF\nK2,PF\n" + b'"' + b"x" * 200_000 + b'",PF\n'
    error = _file_error(data, max_rows=1)
    assert [e.type for e in error.errors] == [TemplateError.TOO_MANY_ROWS.value]


def test_a_repeated_header_is_an_unknown_column():
    error = _file_error(b"clave,tipo,clave\nK1,PF,K2\n")
    assert [(e.type, e.msg) for e in error.errors] == [
        (
            TemplateError.UNKNOWN_COLUMN.value,
            "a column is outside the template or repeated",
        )
    ]


def test_a_value_outside_the_header_is_a_row_error_without_the_value():
    data = (
        b"clave,tipo,,nombre_completo\n"
        b"K1,PF,,Uno Dos\n"
        b"K2,PF,,Tres Cuatro,valor-perdido\n"
        b"K3,PF,valor-oculto,Cinco Seis\n"
    )
    rows = read_template(data, CSV_TYPE, max_rows=5)
    assert [r.stray_column for r in rows] == [None, 5, 3]
    error = _invalid(rows)
    assert [(e.loc, e.type) for e in error.errors] == [
        (("row", 3, 5), TemplateError.UNKNOWN_COLUMN.value),
        (("row", 4, 3), TemplateError.UNKNOWN_COLUMN.value),
    ]
    assert "valor" not in repr(error.errors) + error.detail
