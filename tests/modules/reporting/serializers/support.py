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
