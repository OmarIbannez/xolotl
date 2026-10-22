"""Pre-mapped report fields and the unit values of an event (plan 4b B10)."""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
import yaml

from tests.modules.monitoring.captures.samples import FIX_ROW, enriched
from tests.modules.monitoring.captures.test_definitions import pack_policy
from xolotl.modules.monitoring.captures.definitions import capture_definitions
from xolotl.modules.monitoring.captures.fields import (
    FIELD_MAPS_DIR,
    event_fields,
    read_field_map,
    unit_values,
)
from xolotl.modules.monitoring.captures.record import unit_amount_of
from xolotl.modules.monitoring.enrich.units import ParkTransactionError
from xolotl.modules.monitoring.enums import ParkReason
from xolotl.modules.monitoring.txn.schema import Direction, Instrument, Rail
from xolotl.platform.units import Conversion, ConversionRule

REPO = Path(__file__).resolve().parents[4]
CODETABLES = REPO / "report-defs/codetables/MX"
REPORT_CODES = (
    "MX_F41_ROR",
    "MX_ITF_FX_CASH",
    "MX_ITF_INTL_TRANSFER",
    "MX_ITF_VA_OPS",
    "MX_AVISO_AVI",
)


def test_every_report_of_both_packs_has_a_field_map():
    codes = {
        d.report_code
        for pack in ("MX/ITF-IFPE", "MX/LFPIORPI-AVI")
        for d in capture_definitions(pack_policy(pack))
    }
    assert codes == set(REPORT_CODES)
    assert {path.stem for path in FIELD_MAPS_DIR.glob("*.yaml")} == codes


@pytest.mark.parametrize("report_code", REPORT_CODES)
def test_every_code_in_a_field_map_exists_in_its_code_table(report_code: str):
    fmap = read_field_map(report_code)
    assert fmap.report_code == report_code
    for field, table in fmap.codetables.items():
        data = yaml.safe_load((CODETABLES / f"{table}.yaml").read_text("utf-8"))
        entries = set(data["entries"])
        values = (
            fmap.operation_type if field == "operation_type" else fmap.instrument
        ).values()
        assert set(values) <= entries, (report_code, field)


def test_the_ror_map_writes_the_f41_codes():
    fmap = read_field_map("MX_F41_ROR")
    assert fmap.codetables == {
        "operation_type": "f41_tipo_operacion",
        "instrument": "f41_instrumento_monetario",
    }
    assert fmap.operation_type["IN/CASH"] == "01"
    assert fmap.operation_type["OUT/CASH"] == "02"
    assert fmap.instrument["CASH"] == "01"


def test_an_unknown_report_has_no_field_map():
    with pytest.raises(ValueError, match="MX_NOPE"):
        _ = read_field_map("MX_NOPE")


def test_event_fields_carry_codes_amounts_and_ids_only():
    txn = enriched(
        counterparty_id=UUID(int=9),
        counterparty_key="ab" * 32,
        counterparty_country="US",
        fee_minor=2_500,
    )

    fields = event_fields(txn, "acct-77", read_field_map("MX_F41_ROR"))

    assert fields == {
        "operation_date": "2026-12-13",
        "operation_type": "01",
        "instrument": "01",
        "amount": {"minor": 600_000, "currency": "USD"},
        "fee": {"minor": 2_500, "currency": "USD"},
        "account_ref": "acct-77",
        "branch": "0000",
        "locality": None,
        "counterparty": {"country": "US", "institution": None, "account_hash": None},
        "crypto": None,
        "unmapped": [],
        "merged_captures": [],
        "period_keys": {"MONTH": "2026-12", "QUARTER": "2026-Q4"},
    }
    text = json.dumps(fields)
    assert "ab" * 32 not in text


def test_a_crypto_event_keeps_the_network_and_never_the_address():
    txn = enriched(
        direction=Direction.OUT,
        rail=Rail.CRYPTO_ONCHAIN,
        instrument=Instrument.CRYPTO,
        product="CRYPTO",
        asset_code="BTC",
        asset_qty=Decimal("0.125"),
        crypto={"network": "bitcoin", "asset_code": "BTC", "self_custody": True},
    )

    fields = event_fields(
        txn, "acct-1", read_field_map("MX_AVISO_AVI"), merged=["AVI_XVI_B"]
    )

    assert fields["operation_type"] == "VENTA"
    assert fields["instrument"] == "ACTIVO_VIRTUAL"
    assert fields["counterparty"] is None
    assert fields["crypto"] == {
        "network": "bitcoin",
        "asset_code": "BTC",
        "asset_qty": "0.125",
    }
    assert fields["merged_captures"] == ["AVI_XVI_B"]


def test_unit_values_record_both_sides_of_the_conversion():
    (ror,) = [
        d
        for d in capture_definitions(pack_policy("MX/ITF-IFPE"))
        if d.code == "ITF_ROR_CASH"
    ]
    converted = Conversion(
        amount=Decimal("90000.00"),
        unit_value_id=FIX_ROW,
        rule=ConversionRule.PRIOR_BANKING_DAY,
        on=date(2026, 12, 13),
        resolved_date=date(2026, 12, 11),
        unit="USD",
        currency="MXN",
        rate=Decimal(18),
    )

    values = unit_values(ror, enriched(), converted)

    assert values == {
        "threshold": {"amount": "5000", "unit": "USD"},
        "converted": {
            "amount": "90000.00",
            "currency": "MXN",
            "unit_value_id": FIX_ROW,
            "rule": "PRIOR_BANKING_DAY",
            "resolved_date": "2026-12-11",
        },
        "transaction": {
            "amount": "6000.00",
            "unit": "USD",
            "unit_value_id": None,
            "resolved_date": "2026-12-13",
        },
        "fee": None,
    }


def test_a_code_the_map_lacks_is_named_as_unmapped():
    txn = enriched(direction=Direction.INTERNAL)

    fields = event_fields(txn, "acct-1", read_field_map("MX_F41_ROR"))

    assert fields["operation_type"] is None
    assert fields["instrument"] == "01"
    assert fields["unmapped"] == ["operation_type"]


def test_a_missing_unit_amount_parks_the_row_rather_than_skip_the_capture():
    (va_ops,) = [
        d
        for d in capture_definitions(pack_policy("MX/ITF-IFPE"))
        if d.code == "ITF_VA_OPS"
    ]

    with pytest.raises(ParkTransactionError) as parked:
        _ = unit_amount_of(va_ops, enriched())

    assert parked.value.reason is ParkReason.UNIT_VALUE_MISSING
    assert parked.value.unit_code == "UDI"
    assert parked.value.needed_on == date(2026, 12, 13)
