"""Shared paths, synthetic people and the golden-file helper.

Every RFC and CURP here is synthetic and passes the party validators
(``test_fixture_identifiers_pass_the_party_validators``). Set
``XOLOTL_UPDATE_GOLDENS=1`` to rewrite a golden file after a reviewed change.
"""

import os
from datetime import date
from decimal import Decimal
from pathlib import Path

from xolotl.modules.reporting.serializers.formato41 import (
    F41Header,
    F41Operation,
    F41Related,
    F41Subject,
    PersonType,
)

ROOT = Path(__file__).resolve().parents[4]
DEFS_DIR = ROOT / "report-defs"
GOLDEN_DIR = Path(__file__).resolve().parent / "golden"

SYNTHETIC_RFCS = (
    "GOLM850315AB0",
    "ROPA780702K78",
    "AEC180516MX3",
    "SIN150301QZ4",
)
SYNTHETIC_CURPS = ("GOLM850315HDFMPR01", "ROPA780702MJCDRN01")


def assert_golden(name: str, data: bytes) -> None:
    path = GOLDEN_DIR / name
    if os.environ.get("XOLOTL_UPDATE_GOLDENS") == "1":
        _ = path.write_bytes(data)
    assert data == path.read_bytes(), f"{name} differs from its golden file"


HEADER_ROR = F41Header(
    period=date(2027, 3, 1), supervisor_key="40-002", entity_key="90-123"
)

MARIA = F41Subject(
    person_type=PersonType.FISICA,
    nationality="MX",
    first_names="María José",
    paternal_surname="Gómez",
    maternal_surname="López",
    rfc="GOLM850315AB0",
    curp="GOLM850315HDFMPR01",
    birth_or_incorporation=date(1985, 3, 15),
    address="Av. Insurgentes Sur 1234, int. 5; CP 03100",
    neighbourhood="Del Valle",
    city_code="09014001",
    phone="5512345678",
    economic_activity="8121001",
)

ACME = F41Subject(
    person_type=PersonType.MORAL,
    nationality="MX",
    company_name="Abarrotes El Cóndor, S.A. de C.V.",
    rfc="AEC180516MX3",
    birth_or_incorporation=date(2018, 5, 16),
    address="Calle Hidalgo 45, CP 44100",
    neighbourhood="Centro",
    city_code="14039001",
    economic_activity="4611001",
)

WALK_IN = F41Subject(
    person_type=PersonType.FISICA,
    nationality=None,
    first_names="Ana",
    paternal_surname="Rodríguez",
    maternal_surname=None,
    birth_or_incorporation=date(1978, 7, 2),
    address="Domicilio no proporcionado, CP 06000",
    city_code="09015001",
)

ROR_OPERATIONS = (
    F41Operation(
        locality="09014001",
        branch="0",
        operation_type="01",
        instrument="01",
        account="1000000001",
        amount=Decimal("125000.5"),
        currency="MXN",
        operation_date=date(2027, 3, 2),
        subject=MARIA,
    ),
    F41Operation(
        locality="14039001",
        branch="GDL01",
        operation_type="02",
        instrument="01",
        account="1000000002",
        amount=Decimal("98765.435"),
        currency="USD",
        operation_date=date(2027, 3, 15),
        subject=ACME,
    ),
    F41Operation(
        locality="09015001",
        operation_type="03",
        instrument="07",
        account="0",
        amount=Decimal(6000),
        currency="USD",
        operation_date=date(2027, 3, 30),
        subject=WALK_IN,
    ),
)

RELATED_ACCOUNT = F41Related(
    account="2000000009",
    entity_key="40-012",
    first_names="Luis",
    paternal_surname="Pérez",
    maternal_surname="Ñañez",
)

HEADER_ROI = F41Header(
    period=date(2027, 4, 9), supervisor_key="40-002", entity_key="90-123"
)

ROI_OPERATION = F41Operation(
    locality="09014001",
    operation_type="01",
    instrument="03",
    account="1000000001",
    amount=Decimal(1480000),
    currency="MXN",
    operation_date=date(2027, 3, 28),
    detection_date=date(2027, 3, 29),
    subject=MARIA,
    description=(
        "Alerta generada el 29/03/2027. Del 01/03/2027 al 28/03/2027 la clienta "
        "recibió 14 transferencias de terceros; perfil declarado: empleada."
    ),
    reasons=(
        "Monto acumulado inconsistente con el perfil transaccional; "
        "dispersión inmediata a cuentas de terceros."
    ),
    related=(
        RELATED_ACCOUNT,
        F41Related(first_names="Raúl", paternal_surname="Ortiz", maternal_surname=""),
    ),
)

EMPLOYEE = F41Subject(
    person_type=PersonType.FISICA,
    nationality="MX",
    first_names="Patricia",
    paternal_surname="Robles",
    maternal_surname="Peña",
    rfc="ROPA780702K78",
    curp="ROPA780702MJCDRN01",
    birth_or_incorporation=date(1978, 7, 2),
    address="Calle Morelos 8, CP 45050",
    neighbourhood="Zapopan Centro",
    city_code="14120001",
    economic_activity="9999999",
)

ROIP_OPERATION = F41Operation(
    locality="14120001",
    branch="GDL02",
    operation_type="01",
    instrument="01",
    account="0",
    amount=Decimal(0),
    currency="MXN",
    operation_date=date(2027, 4, 1),
    detection_date=date(2027, 4, 2),
    subject=EMPLOYEE,
    description="Conducta: la empleada omitió registrar depósitos en efectivo.",
    reasons="Posible participación en ocultamiento de operaciones (art. 400 Bis).",
)
