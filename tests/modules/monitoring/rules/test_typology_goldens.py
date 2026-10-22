"""Each library template fires its golden scenario once (spec §9.3, plan 4b B8).

``tests/golden/monitoring/typologies/<CODE>/scenario.yaml`` holds ``rates`` (pesos
per unit key), the customer's ``profile`` (fields of ``EvalProfile`` over the
defaults) and ``transactions``: fields of ``EnrichedTransaction`` over an MXN cash
deposit, with the amount given once as ``pesos``, ``usd`` or ``udi`` and valued in
every unit at the scenario's rates, and an optional ``profile`` of its own (the
customer at that moment). Each scenario also holds transactions that must not fire.
``expected.yaml`` lists the one hit ``replay_party`` must find with the template's
default params, the ITF policy and the shipped lists; a mismatch prints a diff.
"""

import difflib
from collections.abc import Mapping
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
import yaml

from tests.modules.monitoring.rules.conftest import itf_policy, lists, profile
from tests.modules.monitoring.simulation.support import library_rule
from xolotl.modules.monitoring.aggregates.windows import MEXICO_CITY_ZONE
from xolotl.modules.monitoring.rules.kinds import DeclaredProfile, EvalProfile
from xolotl.modules.monitoring.rules.library import read_library
from xolotl.modules.monitoring.simulation.engine import SimulatedHit, replay_party
from xolotl.modules.monitoring.txn.schema import (
    EnrichedTransaction,
    PeriodKeys,
    UnitAmount,
)

GOLDEN = Path(__file__).resolve().parents[3] / "golden/monitoring/typologies"
CODES = sorted(template.code for template in read_library())
_AMOUNT_UNITS = {
    "pesos": "MXN/SAME_DAY",
    "usd": "USD/PRIOR_BANKING_DAY",
    "udi": "UDI/LAST_DAY_PREV_MONTH",
}
_BASE: dict[str, Any] = {
    "party_id": UUID(int=2),
    "relationship_id": UUID(int=3),
    "account_id": UUID(int=4),
    "product": "EMONEY",
    "direction": "IN",
    "rail": "CASH",
    "instrument": "CASH",
    "channel": "BRANCH",
    "currency": "MXN",
    "fee_minor": None,
    "asset_qty": None,
    "asset_code": None,
    "counterparty_id": None,
    "counterparty_key": None,
    "counterparty_country": None,
    "counterparty_screening_status": None,
    "device_key": None,
    "geo_country": "MX",
    "geo_admin_area": None,
    "crypto": None,
    "fx": None,
    "purpose_code": None,
    "tier": "N3",
    "risk_rating": "LOW",
    "monitoring_profile": "STD",
    "policy_snapshot_id": UUID(int=5),
    "policy_country": "MX",
    "late": False,
}


def _pesos(item: Mapping[str, Any], rates: Mapping[str, Decimal]) -> Decimal:
    [(field, unit)] = [
        (name, unit) for name, unit in _AMOUNT_UNITS.items() if name in item
    ]
    rate = Decimal(1) if field == "pesos" else rates[unit]
    return Decimal(str(item[field])) * rate


def _transaction(
    number: int, item: Mapping[str, Any], rates: Mapping[str, Decimal]
) -> EnrichedTransaction:
    at = datetime.fromisoformat(str(item["occurred_at"])).astimezone(UTC)
    local = at.astimezone(MEXICO_CITY_ZONE)
    pesos = _pesos(item, rates)
    data = {
        **_BASE,
        "id": UUID(int=1000 + number),
        "received_at": at,
        "amount_minor": int(pesos * 100),
        "unit_amounts": {
            key: UnitAmount(
                amount=pesos / rate, unit_value_id=None, resolved_date=local.date()
            )
            for key, rate in rates.items()
        },
        "period_keys": PeriodKeys(
            DAY=local.date().isoformat(),
            MONTH=f"{local.year}-{local.month:02d}",
            QUARTER=f"{local.year}-Q{(local.month - 1) // 3 + 1}",
            BUSINESS_DAY=True,
        ),
        **{
            name: value
            for name, value in item.items()
            if name not in _AMOUNT_UNITS and name != "profile"
        },
        "occurred_at": at,
    }
    return EnrichedTransaction.model_validate(data)


def _profile(fields: Mapping[str, Any]) -> EvalProfile:
    data = dict(fields)
    declared = data.pop("declared_profile", None)
    if isinstance(declared, dict):
        data["declared_profile"] = DeclaredProfile(**declared)
    started = data.get("relationship_started_at")
    if isinstance(started, str):
        data["relationship_started_at"] = datetime.fromisoformat(started)
    elif isinstance(started, date):
        data["relationship_started_at"] = datetime.combine(
            started, datetime.min.time(), UTC
        )
    return profile(**data)


def _shown(hit: SimulatedHit, external_ids: Mapping[UUID, str]) -> dict[str, Any]:
    return {
        "trigger": external_ids[hit.trigger_transaction_id],
        "dedup_key": hit.dedup_key,
        "window_start": hit.window_start.isoformat(),
        "window_end": hit.window_end.isoformat(),
        "severity": hit.severity.value,
        "outcome": hit.outcome.value,
        "aggregates": {
            name: {
                "value": f"{snapshot.value.normalize():f}",
                "unit": snapshot.unit,
                "window": snapshot.window,
                "count": snapshot.count,
            }
            for name, snapshot in sorted(hit.aggregates.items())
        },
    }


def test_every_template_has_a_golden():
    assert sorted(path.name for path in GOLDEN.iterdir() if path.is_dir()) == CODES


@pytest.mark.parametrize("code", CODES)
def test_typology_goldens(code: str):
    scenario = yaml.safe_load((GOLDEN / code / "scenario.yaml").read_text("utf-8"))
    rates = {key: Decimal(str(rate)) for key, rate in scenario["rates"].items()}
    base_profile = scenario.get("profile") or {}
    items: list[dict[str, Any]] = scenario["transactions"]
    stream = [_transaction(n, item, rates) for n, item in enumerate(items)]
    profiles = {
        txn.id: _profile({**base_profile, **(item.get("profile") or {})})
        for txn, item in zip(stream, items, strict=True)
    }
    policy = itf_policy()

    found = replay_party(
        [library_rule(code)],
        stream,
        policy_for=lambda _snapshot_id: policy,
        profile_for=lambda txn: profiles[txn.id],
        lists=lists(),
        now=stream[-1].occurred_at,
    )

    external_ids = {txn.id: txn.external_id for txn in stream}
    actual = yaml.safe_dump(
        {"hits": [_shown(hit, external_ids) for hit in found]},
        sort_keys=False,
        allow_unicode=True,
    )
    expected = (GOLDEN / code / "expected.yaml").read_text("utf-8")
    if actual != expected:
        diff = "".join(
            difflib.unified_diff(
                expected.splitlines(keepends=True),
                actual.splitlines(keepends=True),
                fromfile=f"golden/{code}/expected.yaml",
                tofile="actual",
            )
        )
        pytest.fail(f"{code} differs from its golden:\n{diff}")
