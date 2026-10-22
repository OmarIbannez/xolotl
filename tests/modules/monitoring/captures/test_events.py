"""Reportable events over real processing: thresholds, fees, merges, tipping-off."""

from datetime import date
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from tests.helpers import system_ctx
from tests.modules.monitoring.captures.support import (
    CaptureStage,
    add_relationship,
    crypto_item,
    events,
    feed,
    local,
    outbox,
    periods,
)
from tests.modules.monitoring.captures.test_groups import MONTHLY
from tests.modules.monitoring.support import make_item
from tests.modules.party.route_support import machine_headers
from tests.platform.webhooks_support import deliveries, make_endpoint
from xolotl.modules.monitoring.captures.enums import (
    CapturePeriodStatus,
    ReportableEventStatus,
)
from xolotl.modules.monitoring.events import (
    REPORTABLE_EVENT_CAPTURED,
    RULES_STAFF_ONLY_EVENT_TYPES,
    TRANSACTION_BATCH_ACCEPTED,
)
from xolotl.modules.monitoring.rules.models import RuleHit
from xolotl.platform.db import tenant_session
from xolotl.platform.events.models import OutboxRow

CLIENT_KEYS = {"id", "external_id", "occurred_at", "status", "late"}


def usd_deposit(external_id: str, amount: str, *, day: int = 13) -> dict[str, object]:
    return make_item(
        external_id=external_id,
        occurred_at=local(12, day).isoformat(),
        customer_ref="cust-1",
        account_ref="acct-1",
        amount=amount,
        currency="USD",
    )


def test_a_threshold_capture_records_both_sides_of_the_conversion(
    stage: CaptureStage,
):
    _ = add_relationship(stage, "cust-1")
    captured, _below = feed(
        stage, [usd_deposit("dep-1", "5000.00"), usd_deposit("dep-2", "4999.99")]
    )

    (event,) = events(stage, "ITF_ROR_CASH")
    fix = stage.unit_ids["USD_MXN_FIX", date(2026, 12, 11)]
    assert event.transaction_id == captured
    assert event.report_code == "MX_F41_ROR"
    assert event.period_key == "2026-Q4"
    assert event.status is ReportableEventStatus.CAPTURED
    assert not event.late
    assert event.unit_values["converted"] == {
        "amount": "91012.50",  # 5,000 x 18.2025
        "currency": "MXN",
        "unit_value_id": fix,
        "rule": "PRIOR_BANKING_DAY",
        "resolved_date": "2026-12-11",
    }
    assert event.unit_values["threshold"] == {"amount": "5000", "unit": "USD"}
    assert event.fields["operation_type"] == "01"
    assert event.fields["account_ref"] == "acct-1"
    (period,) = [p for p in periods(stage) if p.capture_code == "ITF_ROR_CASH"]
    assert (period.period_key, period.status) == ("2026-Q4", CapturePeriodStatus.OPEN)
    assert (period.period_start, period.period_end) == (
        date(2026, 10, 1),
        date(2026, 12, 31),
    )
    (notice,) = outbox(stage, REPORTABLE_EVENT_CAPTURED)
    assert notice.data == {
        "reportable_event_id": str(event.id),
        "capture_code": "ITF_ROR_CASH",
        "report_code": "MX_F41_ROR",
        "period_key": "2026-Q4",
        "transaction_id": str(captured),
    }
    assert notice.key == str(event.party_id)


def test_a_fee_below_four_uma_is_not_captured(stage: CaptureStage):
    _ = add_relationship(stage, "crypto-1", product="CRYPTO")
    # Four UMA is 469.24 pesos at 117.31.
    _below, at_four = feed(
        stage,
        [
            crypto_item("fee-low", local(12, 9), "1000.00", fee="469.23"),
            crypto_item("fee-four", local(12, 10), "1000.00", fee="469.24"),
        ],
    )

    (event,) = events(stage, "AVI_XVI_B")
    assert event.transaction_id == at_four
    fee = event.unit_values["fee"]
    assert isinstance(fee, dict)
    assert fee["unit"] == "UMA"
    assert event.fields["fee"] == {"minor": 46_924, "currency": "MXN"}
    assert events(stage, "AVI_XVI_A") == []


def test_a_group_crossing_voids_an_earlier_fee_event(stage: CaptureStage):
    _ = add_relationship(stage, "crypto-1", product="CRYPTO")
    items = [
        crypto_item(
            f"avi-{month:02d}",
            local(month, mday),
            "4000.00",
            fee="500.00" if month == 11 else None,
        )
        for month, mday in MONTHLY
    ]
    items.append(crypto_item("avi-12", local(12, 9), "5000.00"))
    ids = feed(stage, items)

    (fee_event,) = events(stage, "AVI_XVI_B")
    assert fee_event.transaction_id == ids[4]
    assert fee_event.status is ReportableEventStatus.VOIDED
    assert fee_event.void_reason == "MERGED"
    november = next(e for e in events(stage, "AVI_XVI_A") if e.transaction_id == ids[4])
    assert november.fields["merged_captures"] == ["AVI_XVI_B"]
    assert len(events(stage, "AVI_XVI_A")) == 6


def test_a_capture_never_opens_a_hit_or_reaches_a_client(
    stage: CaptureStage, client: TestClient
):
    _ = add_relationship(stage, "cust-1")
    (row,) = feed(stage, [usd_deposit("dep-1", "6000.00")])
    assert events(stage, "ITF_ROR_CASH")

    with tenant_session(system_ctx(stage.tenant_id)) as s:
        assert s.scalar(select(func.count()).select_from(RuleHit)) == 0
    reader = machine_headers(stage.tenant, "transactions:read")
    shown = client.get(f"/v1/transactions/{row}", headers=reader)
    assert shown.status_code == 200
    assert set(shown.json()) == CLIENT_KEYS
    assert shown.json()["status"] == "PROCESSED"
    for leaked in ("ITF_ROR_CASH", "capture", "MX_F41_ROR", "reportable"):
        assert leaked not in shown.text


def _delivered_types(tenant_id: UUID) -> set[str]:
    delivered = {row.event_id for row in deliveries(tenant_id)}
    with tenant_session(system_ctx(tenant_id)) as s:
        rows = s.scalars(select(OutboxRow).where(OutboxRow.id.in_(delivered)))
        return {row.type for row in rows}


def test_capture_events_never_reach_a_webhook(stage: CaptureStage):
    _ = make_endpoint(stage.tenant_id, event_types=["*"])
    _ = add_relationship(stage, "cust-1")
    _ = feed(stage, [usd_deposit("dep-1", "6000.00")])
    assert outbox(stage, REPORTABLE_EVENT_CAPTURED)

    with tenant_session(system_ctx(stage.tenant_id)) as s:
        _ = s.execute(
            text("UPDATE outbox SET created_at = created_at - interval '1 minute'")
        )
    while stage.services.bus.dispatch_pending():
        pass

    types = _delivered_types(stage.tenant_id)
    assert TRANSACTION_BATCH_ACCEPTED in types
    assert not types & set(RULES_STAFF_ONLY_EVENT_TYPES)
    assert not [
        t
        for t in types
        if t.startswith(("monitoring.reportable_event", "monitoring.capture_period"))
    ]
