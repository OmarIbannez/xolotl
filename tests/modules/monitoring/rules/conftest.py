"""Builders of the evaluator tests: a fake reader, a transaction, a profile, policy.

The transaction is an MXN cash deposit of 1,000 on 2026-12-10 at 10:00 Mexico
City, with unit amounts at fixed rates (USD 18.0, UDI 8.6, UMA 117.31, MXN 1).
"""

from collections.abc import Mapping
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from tests.modules.monitoring.rules.samples import document
from tests.platform.policy_support import REPO, both_packs
from xolotl.modules.monitoring.aggregates.store import AggregateValue
from xolotl.modules.monitoring.aggregates.windows import MEXICO_CITY_ZONE
from xolotl.modules.monitoring.rules.definitions import RuleDefinition, RuleKind
from xolotl.modules.monitoring.rules.kinds import (
    INCOMPLETE,
    EvalContext,
    EvalProfile,
    Incomplete,
)
from xolotl.modules.monitoring.txn.schema import (
    Channel,
    Direction,
    EnrichedTransaction,
    Instrument,
    PeriodKeys,
    Rail,
    UnitAmount,
)
from xolotl.platform.policy import EffectivePolicy, load_jurisdictions
from xolotl.platform.policy.jurisdictions import JURISDICTIONS_FILE, JurisdictionLists

OCCURRED = datetime(2026, 12, 10, 10, tzinfo=MEXICO_CITY_ZONE).astimezone(UTC)
USD = "USD/PRIOR_BANKING_DAY"
UDI_MONTH = "UDI/LAST_DAY_PREV_MONTH"
# Pesos per unit.
RATES: Mapping[str, Decimal] = {
    USD: Decimal("18.0"),
    "UDI/SAME_DAY": Decimal("8.6"),
    UDI_MONTH: Decimal("8.6"),
    "UMA/SAME_DAY": Decimal("117.31"),
    "MXN/SAME_DAY": Decimal(1),
}


def unit_amounts(pesos: Decimal) -> dict[str, UnitAmount]:
    return {
        key: UnitAmount(
            amount=pesos / rate, unit_value_id=None, resolved_date=date(2026, 12, 10)
        )
        for key, rate in RATES.items()
    }


def usd_amounts(usd: str) -> dict[str, UnitAmount]:
    """Value a deposit worth exactly ``usd`` dollars in every unit.

    Returns:
        The amounts, with the USD one exact.

    """
    amounts = unit_amounts(Decimal(usd) * RATES[USD])
    amounts[USD] = UnitAmount(
        amount=Decimal(usd), unit_value_id=None, resolved_date=date(2026, 12, 9)
    )
    return amounts


def enriched(**overrides: object) -> EnrichedTransaction:
    data: dict[str, object] = {
        "id": UUID(int=1),
        "external_id": "dep-0001",
        "occurred_at": OCCURRED,
        "received_at": OCCURRED,
        "party_id": UUID(int=2),
        "relationship_id": UUID(int=3),
        "account_id": UUID(int=4),
        "product": "CUENTA_N1",
        "direction": Direction.IN,
        "rail": Rail.CASH,
        "instrument": Instrument.CASH,
        "channel": Channel.BRANCH,
        "amount_minor": 100_000,
        "currency": "MXN",
        "fee_minor": None,
        "asset_qty": None,
        "asset_code": None,
        "unit_amounts": unit_amounts(Decimal(1000)),
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
        "period_keys": PeriodKeys(
            DAY="2026-12-10", MONTH="2026-12", QUARTER="2026-Q4", BUSINESS_DAY=True
        ),
        "tier": "N1",
        "risk_rating": "LOW",
        "monitoring_profile": "STD",
        "policy_snapshot_id": UUID(int=5),
        "policy_country": "MX",
        "late": False,
    }
    data.update(overrides)
    return EnrichedTransaction.model_validate(data)


def profile(**overrides: Any) -> EvalProfile:
    data: dict[str, Any] = {
        "tier": "N1",
        "risk_rating": "LOW",
        "monitoring_profile": "STD",
        "pep_status": "NONE",
        "party_screening_status": "CLEAR",
        "relationship_started_at": datetime(2025, 1, 1, tzinfo=UTC),
        "declared_profile": None,
        "history_months": 12,
    }
    data.update(overrides)
    return EvalProfile(**data)


def itf_policy() -> EffectivePolicy:
    pack = both_packs()["MX/ITF-IFPE"]
    return EffectivePolicy(
        units=pack.units,
        kyc=pack.kyc,
        ubo=pack.ubo,
        screening=pack.screening,
        captures=pack.captures,
        reports=pack.reports,
        retention=pack.retention,
        governance=pack.governance,
        on=date(2026, 12, 10),
        product=None,
        country=pack.country,
        pack_codes=[pack.pack],
        pack_version_ids=[],
        override_version=None,
    )


def lists() -> JurisdictionLists:
    return load_jurisdictions(REPO / JURISDICTIONS_FILE)


def value(
    total: str = "0",
    *,
    count: int = 0,
    distinct: int | None = None,
    last_at: datetime | None = None,
    complete: bool = True,
) -> AggregateValue:
    return AggregateValue(
        value=Decimal(total),
        count=count,
        max_value=None,
        min_value=None,
        first_at=None,
        last_at=last_at,
        distinct=distinct,
        complete=complete,
    )


class FakeReader:
    """Answers reads by declaration name and records each ``at`` asked for."""

    def __init__(self, values: Mapping[str, AggregateValue | Incomplete]) -> None:
        self.values = dict(values)
        self.calls: list[tuple[str, datetime | None]] = []

    def read(
        self, name: str, *, at: datetime | None = None
    ) -> AggregateValue | Incomplete:
        self.calls.append((name, at))
        return self.values.get(name, INCOMPLETE)


def context(
    values: Mapping[str, AggregateValue | Incomplete] | None = None,
    *,
    txn: EnrichedTransaction | None = None,
    who: EvalProfile | None = None,
    policy: EffectivePolicy | None = None,
) -> EvalContext:
    return EvalContext(
        txn=txn or enriched(),
        aggregates=FakeReader(values or {}),
        profile=who or profile(),
        policy=policy or itf_policy(),
        lists=lists(),
        now=OCCURRED,
    )


def declaration(
    name: str,
    function: str,
    window: str,
    *,
    measure: str = "UNIT_AMOUNT",
    window_kind: str = "DURATION",
    **extra: Any,
) -> dict[str, Any]:
    return {
        "name": name,
        "function": function,
        "measure": measure,
        "unit_key": USD if measure == "UNIT_AMOUNT" else None,
        "key_kind": "RELATIONSHIP",
        "window_kind": window_kind,
        "window": window,
        **extra,
    }


def rule(kind: RuleKind, **changes: Any) -> RuleDefinition:
    """Build the kind's hand-built definition, with ``changes`` applied.

    Returns:
        The validated definition.

    """
    data = document(kind)
    data.update(_EXTRA.get(kind, {}))
    data.update(changes)
    return RuleDefinition.model_validate(data)


_BAND = {
    "direction": ["IN"],
    "instrument": ["CASH"],
    "amount_unit_key": USD,
    "amount_gte": "3500",
    "amount_lt": "5000",
}
_EXTRA: dict[RuleKind, dict[str, Any]] = {
    RuleKind.STRUCTURING: {
        "aggregates": [
            declaration("near_count", "COUNT", "P7D", measure="ONE", where=_BAND),
            declaration("near_sum", "SUM", "P7D", where=_BAND),
        ],
        "evidence": {
            "transactions_window": "P7D",
            "aggregates": ["near_count", "near_sum"],
        },
    },
    RuleKind.VELOCITY: {
        "aggregates": [
            declaration("window_count", "COUNT", "P1D", measure="ONE"),
            declaration("window_sum", "SUM", "P1D"),
        ],
    },
    RuleKind.PROFILE_DEVIATION: {
        "aggregates": [
            declaration(
                name,
                "SUM",
                window,
                window_kind=kind,
                unit_key="MXN/SAME_DAY",
                where={"direction": [direction]},
            )
            for name, window, kind, direction in (
                ("mtd_in", "MONTH", "MONTH", "IN"),
                ("mtd_out", "MONTH", "MONTH", "OUT"),
                ("trailing_in", "P6M", "DURATION", "IN"),
                ("trailing_out", "P6M", "DURATION", "OUT"),
            )
        ],
    },
}
