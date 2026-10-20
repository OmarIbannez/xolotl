"""Formato 41 inusuales, 24-hour reports and preocupantes (no database)."""

from dataclasses import replace

import pytest

from tests.modules.reporting.serializers.support import (
    ACME,
    DEFS_DIR,
    HEADER_ROI,
    ROI_OPERATION,
    ROIP_OPERATION,
    assert_golden,
)
from xolotl.modules.reporting.serializers.definitions import load_delimited
from xolotl.modules.reporting.serializers.errors import (
    ReportFieldError,
    ZeroReportNotAllowedError,
)
from xolotl.modules.reporting.serializers.formato41 import (
    F41Related,
    Legend,
    serialize_f41,
)

ROI = load_delimited(DEFS_DIR, "MX_F41_ROI", 1)
ROI24 = load_delimited(DEFS_DIR, "MX_F41_ROI24", 1)
ROIP = load_delimited(DEFS_DIR, "MX_F41_ROIP", 1)


def _records(data: bytes) -> list[list[str]]:
    return [
        line.split(";")
        for line in data.decode("ascii").removesuffix("\r\n").split("\r\n")
    ]


def test_roi_matches_its_golden_file():
    report = serialize_f41(DEFS_DIR, ROI, HEADER_ROI, [ROI_OPERATION])
    assert report.record_count == 3
    assert_golden("MX_F41_ROI_v1_20270409.txt", report.parts[0])


def test_roi_related_rows_repeat_the_operation_and_vary_34_to_39():
    report = serialize_f41(DEFS_DIR, ROI, HEADER_ROI, [ROI_OPERATION])
    main, account, person = _records(report.parts[0])
    assert all(len(row) == 41 for row in (main, account, person))
    assert main[:3] == ["2", "20270409", "000001"]
    assert main[13] == "20270329"
    assert [row[33] for row in (main, account, person)] == ["00", "01", "02"]
    for row in (account, person):
        assert row[:33] == main[:33]
        assert row[39:] == main[39:]
    assert main[34:39] == [""] * 5
    assert account[34:39] == ["2000000009", "040012", "LUIS", "PEREZ", "NANEZ"]
    assert person[34:39] == ["", "", "RAUL", "ORTIZ", ""]
    assert main[39].startswith("ALERTA GENERADA EL 29/03/2027.")
    assert ";" not in main[40]


def test_two_operations_get_consecutive_folios_shared_by_their_related_rows():
    second = replace(ROI_OPERATION, related=())
    report = serialize_f41(DEFS_DIR, ROI, HEADER_ROI, [ROI_OPERATION, second])
    assert [row[2] for row in _records(report.parts[0])] == [
        "000001",
        "000001",
        "000001",
        "000002",
    ]


@pytest.mark.parametrize(
    ("legend", "golden"),
    [
        (Legend.LISTA_PERSONAS_BLOQUEADAS, "MX_F41_ROI24_v1_20270409_lpb.txt"),
        (Legend.REPORTE_24_HORAS, "MX_F41_ROI24_v1_20270409_24h.txt"),
    ],
)
def test_roi24_heads_the_description_with_its_legend(legend, golden):
    operation = replace(ROI_OPERATION, related=())
    report = serialize_f41(DEFS_DIR, ROI24, HEADER_ROI, [operation], legend=legend)
    assert_golden(golden, report.parts[0])
    (row,) = _records(report.parts[0])
    assert row[0] == "2"
    assert row[39].startswith(f"{legend.value}. ALERTA GENERADA")


def test_the_legend_does_not_stand_in_for_a_missing_description():
    operation = replace(ROI_OPERATION, related=(), description=None)
    with pytest.raises(ReportFieldError, match="DESCRIPCION DE LA OPERACION"):
        _ = serialize_f41(
            DEFS_DIR, ROI24, HEADER_ROI, [operation], legend=Legend.REPORTE_24_HORAS
        )


def test_a_related_person_without_a_paternal_surname_gets_xxxx():
    related = F41Related(first_names="Rosa", maternal_surname="Luna")
    operation = replace(ROI_OPERATION, related=(related,))
    report = serialize_f41(DEFS_DIR, ROI, HEADER_ROI, [operation])
    _, row = _records(report.parts[0])
    assert row[36:39] == ["ROSA", "XXXX", "LUNA"]


def test_the_legend_is_required_for_roi24_and_refused_elsewhere():
    with pytest.raises(ReportFieldError, match="needs a legend"):
        _ = serialize_f41(DEFS_DIR, ROI24, HEADER_ROI, [ROI_OPERATION])
    with pytest.raises(ReportFieldError, match="takes no legend"):
        _ = serialize_f41(
            DEFS_DIR, ROI, HEADER_ROI, [ROI_OPERATION], legend=Legend.REPORTE_24_HORAS
        )


def test_roip_reports_the_employee_with_a_zero_amount():
    report = serialize_f41(DEFS_DIR, ROIP, HEADER_ROI, [ROIP_OPERATION])
    assert_golden("MX_F41_ROIP_v1_20270409.txt", report.parts[0])
    (row,) = _records(report.parts[0])
    assert row[0] == "3"
    assert row[10] == "0.00"
    assert row[17:22] == [
        "PATRICIA",
        "ROBLES",
        "PENA",
        "ROPA780702K78",
        "ROPA780702MJCDRN01",
    ]


@pytest.mark.parametrize("definition", [ROI, ROI24, ROIP])
def test_event_reports_have_no_empty_report(definition):
    legend = Legend.REPORTE_24_HORAS if definition.legend_required else None
    with pytest.raises(ZeroReportNotAllowedError) as err:
        _ = serialize_f41(DEFS_DIR, definition, HEADER_ROI, [], legend=legend)
    assert err.value.code == "ZERO_REPORT_NOT_ALLOWED"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"detection_date": None}, "FECHA DE DETECCION"),
        ({"description": None}, "DESCRIPCION DE LA OPERACION"),
        ({"reasons": " ; "}, "RAZONES"),
    ],
)
def test_event_report_columns_are_required(change, message):
    operation = replace(ROI_OPERATION, **change)
    with pytest.raises(ReportFieldError, match=message):
        _ = serialize_f41(DEFS_DIR, ROI, HEADER_ROI, [operation])


def test_unknown_nationality_is_for_relevantes_only():
    subject = replace(ROI_OPERATION.subject, nationality=None)
    with pytest.raises(ReportFieldError, match="unknown only for relevantes"):
        _ = serialize_f41(
            DEFS_DIR, ROI, HEADER_ROI, [replace(ROI_OPERATION, subject=subject)]
        )


def test_a_birth_date_is_required_for_event_reports():
    subject = replace(ROI_OPERATION.subject, birth_or_incorporation=None)
    with pytest.raises(ReportFieldError, match="FECHA DE NACIMIENTO"):
        _ = serialize_f41(
            DEFS_DIR, ROI, HEADER_ROI, [replace(ROI_OPERATION, subject=subject)]
        )


def test_at_most_99_related_rows():
    operation = replace(ROI_OPERATION, related=ROI_OPERATION.related[:1] * 100)
    with pytest.raises(ReportFieldError, match="99 related rows"):
        _ = serialize_f41(DEFS_DIR, ROI, HEADER_ROI, [operation])


@pytest.mark.parametrize(
    ("related", "message"),
    [
        (F41Related(), "an account or a person"),
        (F41Related(maternal_surname="Ruiz"), "column 37"),
    ],
)
def test_related_rows_need_an_account_or_a_named_person(related, message):
    operation = replace(ROI_OPERATION, related=(related,))
    with pytest.raises(ReportFieldError, match=message):
        _ = serialize_f41(DEFS_DIR, ROI, HEADER_ROI, [operation])


def test_a_related_sat_key_passes_through_column_36():
    related = F41Related(account="77", entity_key="ABC123")
    operation = replace(ROI_OPERATION, related=(related,))
    report = serialize_f41(DEFS_DIR, ROI, HEADER_ROI, [operation])
    _, row = _records(report.parts[0])
    assert row[34:36] == ["77", "ABC123"]


def test_a_preocupante_reports_a_natural_person():
    operation = replace(ROIP_OPERATION, subject=ACME)
    with pytest.raises(ReportFieldError, match="natural person"):
        _ = serialize_f41(DEFS_DIR, ROIP, HEADER_ROI, [operation])


def test_the_legend_is_not_repeated_when_the_description_already_has_it():
    operation = replace(
        ROI_OPERATION, related=(), description="Reporte de 24 horas. Cliente X."
    )
    report = serialize_f41(
        DEFS_DIR, ROI24, HEADER_ROI, [operation], legend=Legend.REPORTE_24_HORAS
    )
    (row,) = _records(report.parts[0])
    assert row[39] == "REPORTE DE 24 HORAS. CLIENTE X."
