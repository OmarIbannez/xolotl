"""An enriched transaction for the pure capture tests (plan 4b B10).

A USD 6,000 cash deposit on 2026-12-13 at 10:00 Mexico City, a Sunday, valued at
the FIX of Friday 11 December (18.0 pesos per dollar).
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from xolotl.modules.monitoring.aggregates.windows import MEXICO_CITY_ZONE
from xolotl.modules.monitoring.txn.schema import (
    Channel,
    Direction,
    EnrichedTransaction,
    Instrument,
    PeriodKeys,
    Rail,
    UnitAmount,
)

OCCURRED = datetime(2026, 12, 13, 10, tzinfo=MEXICO_CITY_ZONE).astimezone(UTC)
FIX_ROW = 812


def enriched(**overrides: object) -> EnrichedTransaction:
    data: dict[str, object] = {
        "id": UUID(int=1),
        "external_id": "dep-0001",
        "occurred_at": OCCURRED,
        "received_at": OCCURRED,
        "party_id": UUID(int=2),
        "relationship_id": UUID(int=3),
        "account_id": UUID(int=4),
        "product": "EMONEY",
        "direction": Direction.IN,
        "rail": Rail.CASH,
        "instrument": Instrument.CASH,
        "channel": Channel.BRANCH,
        "amount_minor": 600_000,
        "currency": "USD",
        "fee_minor": None,
        "asset_qty": None,
        "asset_code": None,
        "unit_amounts": {
            "USD/PRIOR_BANKING_DAY": UnitAmount(
                amount=Decimal("6000.00"),
                unit_value_id=None,
                resolved_date=date(2026, 12, 13),
            ),
            "MXN/SAME_DAY": UnitAmount(
                amount=Decimal("108000.00"),
                unit_value_id=FIX_ROW,
                resolved_date=date(2026, 12, 11),
            ),
        },
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
            DAY="2026-12-13", MONTH="2026-12", QUARTER="2026-Q4", BUSINESS_DAY=False
        ),
        "tier": "N2",
        "risk_rating": "LOW",
        "monitoring_profile": "STD",
        "policy_snapshot_id": UUID(int=5),
        "policy_country": "MX",
        "late": False,
    }
    data.update(overrides)
    return EnrichedTransaction.model_validate(data)
