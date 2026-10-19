"""Informe en ceros, aviso modificatorio and the 2027 schema slot (no database)."""

from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
from lxml import etree

from tests.modules.reporting.serializers.aviso_support import (
    AVISO_FULL,
    FILER,
    MARCH_2027,
)
from tests.modules.reporting.serializers.support import (
    DEFS_DIR,
    HEADER_ROR,
    ROR_OPERATIONS,
    assert_golden,
)
from xolotl.modules.reporting.serializers.aviso import (
    AvisoInforme,
    Modificatorio,
    SchemaInvalidError,
    SchemaUnavailableError,
    serialize_aviso,
)
from xolotl.modules.reporting.serializers.definitions import (
    CorrectionRule,
    DefinitionError,
    DelimitedDefinition,
    XmlDefinition,
    load_xml,
    select_definition,
)
from xolotl.modules.reporting.serializers.errors import (
    CorrectionUnsupportedError,
    ReportFieldError,
)
from xolotl.modules.reporting.serializers.formato41 import serialize_f41
from xolotl.platform.enums import Confidence

AVI = load_xml(DEFS_DIR, "MX_AVISO_AVI", 1)
NS = {"a": "http://www.uif.shcp.gob.mx/recepcion/avi"}
APRIL_2027 = AvisoInforme(month=date(2027, 4, 1), subject=FILER)
CORRECTION = Modificatorio(
    folio="2027-845213",
    description=(
        "Se corrige el monto de la compra del 4 de marzo; fecha y hash sin cambio."
    ),
)


def test_an_empty_month_is_the_informe_en_ceros():
    report = serialize_aviso(DEFS_DIR, AVI, APRIL_2027)
    assert report.record_count == 0
    assert len(report.parts) == 1
    assert b"<aviso>" not in report.parts[0]
    assert_golden("MX_AVISO_AVI_v1_202704_ceros.xml", report.parts[0])


def test_an_exempt_informe_en_ceros_carries_the_exento_flag():
    report = serialize_aviso(DEFS_DIR, AVI, replace(APRIL_2027, exempt=True))
    assert_golden("MX_AVISO_AVI_v1_202704_ceros_exento.xml", report.parts[0])
    assert b"<exento>1</exento>" in report.parts[0]


def test_only_an_informe_en_ceros_can_be_exempt():
    with pytest.raises(ReportFieldError, match="only an informe en ceros"):
        _ = serialize_aviso(DEFS_DIR, AVI, replace(MARCH_2027, exempt=True))


def test_a_modificatorio_carries_the_folio_and_the_full_aviso():
    corrected = replace(AVISO_FULL, modificatorio=CORRECTION)
    report = serialize_aviso(DEFS_DIR, AVI, replace(MARCH_2027, avisos=(corrected,)))
    data = report.parts[0]
    assert_golden("MX_AVISO_AVI_v1_202703_modificatorio.xml", data)
    root = etree.fromstring(data, etree.XMLParser(resolve_entities=False))
    aviso = root.find("a:informe/a:aviso", NS)
    assert aviso is not None
    tags = [etree.QName(child).localname for child in aviso]
    assert tags[:4] == ["referencia_aviso", "prioridad", "modificatorio", "alerta"]
    assert [node.text for node in aviso.iterfind("a:modificatorio/*", NS)] == [
        "2027-845213",
        "SE CORRIGE EL MONTO DE LA COMPRA DEL 4 DE MARZO FECHA Y HASH SIN CAMBIO.",
    ]
    # The full aviso follows, not only the changed field.
    assert aviso.find("a:operaciones_persona/a:detalle_operaciones", NS) is not None


@pytest.mark.parametrize("folio", ["845213", "1999-1", "2027-0", "2027-1234567890"])
def test_a_malformed_folio_is_refused_by_the_schema(folio):
    corrected = replace(AVISO_FULL, modificatorio=replace(CORRECTION, folio=folio))
    with pytest.raises(SchemaInvalidError, match="folio_modificacion"):
        _ = serialize_aviso(DEFS_DIR, AVI, replace(MARCH_2027, avisos=(corrected,)))


def test_a_definition_without_corrections_refuses_a_modificatorio():
    no_corrections = AVI.model_copy(
        update={"correction_rule": CorrectionRule.UNSUPPORTED}
    )
    corrected = replace(AVISO_FULL, modificatorio=CORRECTION)
    with pytest.raises(CorrectionUnsupportedError) as err:
        _ = serialize_aviso(
            DEFS_DIR, no_corrections, replace(MARCH_2027, avisos=(corrected,))
        )
    assert err.value.code == "CORRECTION_UNSUPPORTED"


def test_formato41_definitions_have_no_correction_mechanism():
    for code in ("MX_F41_ROR", "MX_F41_ROI", "MX_F41_ROI24", "MX_F41_ROIP"):
        definition = select_definition(DEFS_DIR, code, date(2027, 3, 1))
        assert definition.correction_rule is CorrectionRule.UNSUPPORTED


def test_the_2027_schema_is_a_gated_slot():
    definition = select_definition(DEFS_DIR, "MX_AVISO_AVI", date(2027, 6, 1))
    assert isinstance(definition, XmlDefinition)
    assert definition.version == 2
    assert definition.xsd is None
    assert definition.encoding_confidence is Confidence.UNVERIFIED
    with pytest.raises(SchemaUnavailableError) as err:
        _ = serialize_aviso(
            DEFS_DIR, definition, replace(APRIL_2027, month=date(2027, 6, 1))
        )
    assert err.value.code == "REPORT_SCHEMA_UNAVAILABLE"


@pytest.mark.parametrize(
    ("on", "version"),
    [(date(2026, 11, 30), 1), (date(2027, 5, 31), 1), (date(2027, 6, 1), 2)],
)
def test_the_selector_picks_the_version_in_force(on, version):
    assert select_definition(DEFS_DIR, "MX_AVISO_AVI", on).version == version


def test_overlapping_versions_resolve_to_the_highest(tmp_path: Path):
    target = tmp_path / "MX_F41_ROR"
    target.mkdir()
    source = (DEFS_DIR / "MX_F41_ROR" / "v1.yaml").read_text(encoding="utf-8")
    _ = (target / "v1.yaml").write_text(source, encoding="utf-8")
    _ = (target / "v2.yaml").write_text(
        source.replace("version: 1", "version: 2"), encoding="utf-8"
    )
    _ = (target / "notes.yaml").write_text("ignored: true\n", encoding="utf-8")
    assert select_definition(tmp_path, "MX_F41_ROR", date(2027, 1, 1)).version == 2


def test_no_version_in_force_is_a_definition_error():
    with pytest.raises(DefinitionError, match="no version in force"):
        _ = select_definition(DEFS_DIR, "MX_AVISO_AVI", date(2019, 1, 1))


def test_the_selected_ror_definition_still_serializes():
    definition = select_definition(DEFS_DIR, "MX_F41_ROR", date(2027, 3, 1))
    assert isinstance(definition, DelimitedDefinition)
    report = serialize_f41(DEFS_DIR, definition, HEADER_ROR, ROR_OPERATIONS)
    assert report.record_count == 3
