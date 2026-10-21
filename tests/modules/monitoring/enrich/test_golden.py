"""The enrichment goldens (tests/golden/monitoring/enrichment/cases.yaml, A6)."""

import difflib
import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from tests.helpers import staff_session
from tests.modules.monitoring.support import both_packs_tenant
from xolotl.modules.monitoring.enrich.periods import period_keys_for
from xolotl.modules.monitoring.enrich.policy import snapshot_for
from xolotl.modules.monitoring.enrich.service import ParkTransaction
from xolotl.modules.monitoring.enrich.units import unit_amounts_for, unit_pairs
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform.calendar import Holidays, load_holidays
from xolotl.platform.enums import HolidayScope
from xolotl.platform.units import ConversionRule, UnitService, UnitValueRow

GOLDEN = Path(__file__).resolve().parents[3] / "golden/monitoring/enrichment/cases.yaml"
# PyYAML reads the bare key ``on`` as the boolean true (YAML 1.1); name it back.
CASES: list[dict[str, Any]] = yaml.safe_load(GOLDEN.read_text(encoding="utf-8"))


def _named(operation: dict[Any, Any]) -> dict[str, Any]:
    return {
        ("on" if key is True else str(key)): value for key, value in operation.items()
    }


def _row_key(s: Session, row_id: int | None) -> list[str] | None:
    if row_id is None:
        return None
    row = s.get_one(UnitValueRow, row_id)
    return [row.unit_code, row.valid_from.isoformat()]


def _pairs(
    s: Session, operation: dict[str, Any], on: date
) -> frozenset[tuple[str, ConversionRule]]:
    if "pairs" in operation:
        return frozenset(
            (unit, ConversionRule(rule))
            for unit, rule in (str(pair).split("/") for pair in operation["pairs"])
        )
    snapshot = snapshot_for(s, on=on, product=operation["product"], cache={})
    return unit_pairs(snapshot.policy)


def _units(s: Session, operation: dict[str, Any], holidays: Holidays) -> Any:
    on: date = operation["on"]
    try:
        found = unit_amounts_for(
            UnitService(s),
            amount=Decimal(operation["amount"]),
            currency=operation["currency"],
            on=on,
            pairs=_pairs(s, operation, on),
            holidays=holidays,
        )
    except ParkTransaction as parked:
        return {
            "park": {
                "reason": parked.reason.value,
                "unit_code": parked.unit_code,
                "needed_on": None
                if parked.needed_on is None
                else parked.needed_on.isoformat(),
            }
        }
    return {
        key: {
            "amount": str(value.amount),
            "row": _row_key(s, value.unit_value_id),
            "resolved_date": value.resolved_date.isoformat(),
        }
        for key, value in found.items()
    }


def _expected(operation: dict[str, Any]) -> Any:
    if "park" in operation:
        return {"park": operation["park"]}
    if operation["kind"] == "periods":
        return operation["expected"]
    return {
        key: {
            **value,
            "row": None
            if value["row"] is None
            else [str(value["row"][0]), value["row"][1].isoformat()],
        }
        for key, value in operation["expected"].items()
    }


def _dump(value: Any) -> list[str]:
    return json.dumps(value, indent=2, sort_keys=True).splitlines(keepends=True)


@pytest.fixture
def golden_tenant(tenant: CreatedTenant, admin_engine: Engine) -> CreatedTenant:
    _ = both_packs_tenant(tenant, admin_engine)
    return tenant


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_enrichment_goldens(case: dict[str, Any], golden_tenant: CreatedTenant):
    expected: list[Any] = []
    actual: list[Any] = []
    with staff_session(golden_tenant.tenant_id, golden_tenant.admin_user_id) as s:
        holidays = load_holidays(s, country="MX", scope=HolidayScope.BANXICO)
        for raw in case["operations"]:
            operation = _named(raw)
            expected.append(_expected(operation))
            if operation["kind"] == "periods":
                at = datetime.fromisoformat(operation["occurred_at"])
                actual.append(
                    period_keys_for(at, holidays=holidays).model_dump(mode="json")
                )
            else:
                actual.append(_units(s, operation, holidays))
    if actual != expected:
        diff = "".join(
            difflib.unified_diff(_dump(expected), _dump(actual), "expected", "actual")
        )
        pytest.fail(f"{case['id']} differs from its golden:\n{diff}")
