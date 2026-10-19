"""LFPIORPI aviso XML for fr. XVI against the official avi.xsd (no database)."""

import shutil
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest
import yaml
from hypothesis import given, settings
from hypothesis import strategies as st
from lxml import etree

from tests.modules.reporting.serializers.aviso_support import (
    AVISO_FULL,
    COMPANY_CLIENT,
    FULL_OPERATIONS,
    MARCH_2027,
    PERSON_CLIENT,
    REPRESENTATIVE,
    small_aviso,
)
from tests.modules.reporting.serializers.support import DEFS_DIR, assert_golden
from xolotl.modules.reporting.serializers.aviso import (
    ForeignAddress,
    LegalPerson,
    NaturalPerson,
    Operations,
    ReportTooLargeError,
    SchemaInvalidError,
    SchemaUnavailableError,
    Trust,
    load_schema,
    serialize_aviso,
)
from xolotl.modules.reporting.serializers.definitions import (
    DefinitionError,
    load_delimited,
    load_xml,
)
from xolotl.modules.reporting.serializers.errors import ReportFieldError
from xolotl.platform.enums import Confidence

AVI = load_xml(DEFS_DIR, "MX_AVISO_AVI", 1)
NS = {"a": "http://www.uif.shcp.gob.mx/recepcion/avi"}


def _texts(data: bytes, path: str) -> list[str]:
    root = etree.fromstring(data, etree.XMLParser(resolve_entities=False))
    return [str(node.text) for node in root.iterfind(path, NS)]


def test_the_definition_points_at_the_vendored_schema():
    assert AVI.activity == "AVI"
    assert AVI.size_limit_bytes == 2_000_000
    assert AVI.encoding_confidence is Confidence.VERIFIED
    _ = load_schema(DEFS_DIR, AVI)


def test_the_official_example_validates_against_the_vendored_schema():
    schema = load_schema(DEFS_DIR, AVI)
    example = etree.parse(
        str(DEFS_DIR / "sources" / "ejemplo_avi.xml"),
        etree.XMLParser(resolve_entities=False, no_network=True),
    )
    assert schema.validate(example)


def test_a_full_month_matches_its_golden_file():
    report = serialize_aviso(DEFS_DIR, AVI, MARCH_2027)
    assert report.record_count == 1
    assert len(report.parts) == 1
    assert_golden("MX_AVISO_AVI_v1_202703.xml", report.parts[0])


def test_values_are_normalized_for_the_schema():
    data = serialize_aviso(DEFS_DIR, AVI, MARCH_2027).parts[0]
    assert data.startswith(b"<?xml version='1.0' encoding='UTF-8'?>\n<archivo")
    assert _texts(data, "a:informe/a:mes_reportado") == ["202703"]
    assert _texts(data, ".//a:persona_aviso//a:persona_moral/a:denominacion_razon") == [
        "ABARROTES EL CONDOR, S.A. DE C.V."
    ]
    # Ñ is kept, other accents go.
    assert _texts(data, ".//a:representante_apoderado/a:apellido_materno") == ["PEÑA"]
    assert _texts(data, ".//a:dueno_beneficiario//a:nombre") == ["MARIA JOSE"]
    # Instants are Mexico City wall-clock time: 18:28:31 UTC is 12:28:31.
    assert _texts(data, ".//a:venta/a:fecha_hora_operacion") == ["20270311122831"]
    assert _texts(data, ".//a:compra//a:tipo_cambio_mn") == ["1887523.60"]
    assert _texts(data, ".//a:compra//a:cantidad_activo_virtual") == ["0.0397346025"]
    assert _texts(data, ".//a:recepcion//a:cantidad_activo_virtual") == ["0.15"]
    assert _texts(data, ".//a:compra/a:hash_operacion")[0].isupper()


def test_the_owner_is_rendered_with_the_simple_person_type():
    data = serialize_aviso(DEFS_DIR, AVI, MARCH_2027).parts[0]
    owner = _texts(data, ".//a:dueno_beneficiario//a:persona_fisica/*")
    assert owner == [
        "MARIA JOSE",
        "GOMEZ",
        "LOPEZ",
        "19850315",
        "GOLM850315AB0",
        "GOLM850315HDFMPR01",
        "MX",
    ]


def test_only_the_operation_groups_present_are_rendered():
    ops = Operations(purchases=FULL_OPERATIONS.purchases)
    informe = replace(MARCH_2027, avisos=(replace(AVISO_FULL, operations=ops),))
    data = serialize_aviso(DEFS_DIR, AVI, informe).parts[0]
    root = etree.fromstring(data, etree.XMLParser(resolve_entities=False))
    detail = root.find(".//a:detalle_operaciones", NS)
    assert detail is not None
    assert [etree.QName(child).localname for child in detail] == ["operaciones_compra"]


def test_a_value_the_schema_refuses_fails_loudly_without_echoing_it():
    filer = replace(MARCH_2027.subject, platform_domain="cripto.example")
    with pytest.raises(SchemaInvalidError) as err:
        _ = serialize_aviso(DEFS_DIR, AVI, replace(MARCH_2027, subject=filer))
    assert err.value.code == "REPORT_SCHEMA_INVALID"
    assert "dominio_plataforma" in err.value.detail
    assert "CRIPTO" not in err.value.detail


def test_an_incomplete_client_fails_schema_validation():
    person = PERSON_CLIENT.person
    assert isinstance(person, NaturalPerson)
    client = replace(PERSON_CLIENT, person=replace(person, document=None))
    aviso = replace(small_aviso(1), client=client)
    with pytest.raises(SchemaInvalidError, match="persona_fisica"):
        _ = serialize_aviso(DEFS_DIR, AVI, replace(MARCH_2027, avisos=(aviso,)))


def test_an_aviso_without_operations_is_refused():
    aviso = replace(AVISO_FULL, operations=Operations())
    with pytest.raises(ReportFieldError, match="has no operation"):
        _ = serialize_aviso(DEFS_DIR, AVI, replace(MARCH_2027, avisos=(aviso,)))


def test_a_naive_operation_time_is_refused():
    trade = replace(
        FULL_OPERATIONS.purchases[0], at=datetime.fromisoformat("2027-03-04T09:00")
    )
    ops = Operations(purchases=(trade,))
    aviso = replace(AVISO_FULL, operations=ops)
    with pytest.raises(ReportFieldError, match="time zone"):
        _ = serialize_aviso(DEFS_DIR, AVI, replace(MARCH_2027, avisos=(aviso,)))


def _defs_with(tmp_path: Path, **changes: object) -> Path:
    target = tmp_path / "defs"
    _ = shutil.copytree(DEFS_DIR / "MX_AVISO_AVI", target / "MX_AVISO_AVI")
    path = target / "MX_AVISO_AVI" / "v1.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data.update(changes)
    _ = path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return target


def test_a_month_over_the_size_limit_is_split_into_valid_parts(tmp_path: Path):
    defs = _defs_with(tmp_path, size_limit_bytes=6000)
    definition = load_xml(defs, "MX_AVISO_AVI", 1)
    avisos = tuple(small_aviso(i) for i in range(1, 13))
    report = serialize_aviso(defs, definition, replace(MARCH_2027, avisos=avisos))
    assert len(report.parts) > 1
    assert report.record_count == 12
    references: list[str] = []
    for part in report.parts:
        assert len(part) <= 6000
        assert _texts(part, "a:informe/a:mes_reportado") == ["202703"]
        references += _texts(part, ".//a:referencia_aviso")
    assert references == [f"REF{i:06d}" for i in range(1, 13)]


def test_one_aviso_over_the_size_limit_is_refused(tmp_path: Path):
    defs = _defs_with(tmp_path, size_limit_bytes=1500)
    definition = load_xml(defs, "MX_AVISO_AVI", 1)
    with pytest.raises(ReportTooLargeError):
        _ = serialize_aviso(defs, definition, MARCH_2027)


def test_a_tampered_schema_is_refused(tmp_path: Path):
    defs = _defs_with(tmp_path)
    xsd = defs / "MX_AVISO_AVI" / "xsd" / "avi.xsd"
    _ = xsd.write_bytes(xsd.read_bytes().replace(b"AVI", b"AVX"))
    with pytest.raises(SchemaUnavailableError, match="hash mismatch"):
        _ = serialize_aviso(defs, load_xml(defs, "MX_AVISO_AVI", 1), MARCH_2027)


def test_a_missing_schema_file_is_refused(tmp_path: Path):
    defs = _defs_with(tmp_path)
    (defs / "MX_AVISO_AVI" / "xsd" / "avi.xsd").unlink()
    with pytest.raises(SchemaUnavailableError, match="file missing"):
        _ = serialize_aviso(defs, load_xml(defs, "MX_AVISO_AVI", 1), MARCH_2027)


def test_families_are_not_interchangeable():
    with pytest.raises(DefinitionError, match="not a DELIMITED"):
        _ = load_delimited(DEFS_DIR, "MX_AVISO_AVI", 1)
    with pytest.raises(DefinitionError, match="not an XML_XSD"):
        _ = load_xml(DEFS_DIR, "MX_F41_ROR", 1)


_NAME = st.text(
    alphabet=st.characters(categories=("L", "Zs"), max_codepoint=0x24F),
    min_size=1,
    max_size=40,
)


@settings(max_examples=40, deadline=None)
@given(first=_NAME, surname=_NAME)
def test_any_latin_name_is_valid_or_refused_by_the_schema(first, surname):
    person = PERSON_CLIENT.person
    assert isinstance(person, NaturalPerson)
    client = replace(
        PERSON_CLIENT,
        person=replace(person, first_names=first, paternal_surname=surname),
    )
    informe = replace(MARCH_2027, avisos=(replace(small_aviso(1), client=client),))
    try:
        report = serialize_aviso(DEFS_DIR, AVI, informe)
    except SchemaInvalidError:
        return
    assert report.parts[0] == serialize_aviso(DEFS_DIR, AVI, informe).parts[0]
    (name,) = _texts(report.parts[0], ".//a:persona_aviso//a:nombre")
    assert name == name.upper()
    assert not set(name) & set("ÁÉÍÓÚÜ")


def test_a_trust_with_a_foreign_address_and_mixed_owners_validates():
    trust_client = replace(
        COMPANY_CLIENT,
        person=Trust(
            name="Fideicomiso Ñandú 1234",
            rfc="SIN150301QZ4",
            trust_id="F-1234",
            delegate=REPRESENTATIVE,
        ),
        address=ForeignAddress(
            country="US",
            state="Texas",
            city="Houston",
            neighbourhood="Downtown",
            street="Main St",
            exterior_number="1200",
            postal_code="77002",
        ),
        phone=None,
    )
    owners = (
        LegalPerson(name="Holding Ñ, S.A.", nationality="ES"),
        Trust(name="Fideicomiso Familiar", trust_id="FF-1"),
    )
    aviso = replace(AVISO_FULL, client=trust_client, beneficial_owners=owners)
    data = serialize_aviso(DEFS_DIR, AVI, replace(MARCH_2027, avisos=(aviso,))).parts[0]
    assert _texts(data, ".//a:persona_aviso//a:fideicomiso/a:denominacion_razon") == [
        "FIDEICOMISO ÑANDU 1234"
    ]
    assert _texts(data, ".//a:extranjero/a:ciudad_poblacion") == ["HOUSTON"]
    owner_kinds = _texts(data, ".//a:dueno_beneficiario//a:denominacion_razon")
    assert owner_kinds == ["HOLDING Ñ, S.A.", "FIDEICOMISO FAMILIAR"]


def test_a_definition_without_a_schema_refuses_to_serialize(tmp_path: Path):
    defs = _defs_with(tmp_path, xsd=None, xsd_sha256=None)
    with pytest.raises(SchemaUnavailableError, match="no official schema yet"):
        _ = serialize_aviso(defs, load_xml(defs, "MX_AVISO_AVI", 1), MARCH_2027)
