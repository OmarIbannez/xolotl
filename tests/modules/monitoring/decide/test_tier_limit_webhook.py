"""The one limit webhook per relationship, limit and month (SP-04 spec §12.2).

Whichever path sees the N1 overrun first, the decision or the processed
transaction, records the month's ``tier_limit_notice`` and publishes
``monitoring.tier_limit_reached.v1``; the other path finds the notice and sends
nothing. The payload carries ids and codes, never an amount (PRV-502).
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import select

from tests.helpers import system_ctx
from tests.modules.monitoring.decide.support import (
    NOW,
    UDI_100,
    UDI_350,
    DecideStage,
    ask,
    candidate,
    dispatch_all,
    month_to_date,
)
from tests.modules.monitoring.rules.hook_support import (
    TIER_N1,
    events_of,
    hits,
    ingest,
)
from tests.modules.monitoring.support import run_processing
from tests.platform.webhooks_support import HOOK
from xolotl.modules.monitoring.aggregates.windows import MEXICO_CITY_ZONE
from xolotl.modules.monitoring.events import TIER_LIMIT_REACHED
from xolotl.modules.monitoring.rules.models import TierLimitNotice
from xolotl.platform.db import tenant_session
from xolotl.platform.events.models import WebhookDelivery

PAYLOAD_KEYS = {
    "relationship_id",
    "account_id",
    "tier",
    "limit_code",
    "period_key",
    "outcome",
}
NOVEMBER = datetime(2026, 11, 16, 12, tzinfo=MEXICO_CITY_ZONE).astimezone(UTC)


def _notices(tenant_id: UUID) -> list[TierLimitNotice]:
    with tenant_session(system_ctx(tenant_id)) as s:
        return list(
            s.scalars(select(TierLimitNotice).order_by(TierLimitNotice.period_key))
        )


def _wildcard(client: TestClient, admin_headers: dict[str, str]) -> None:
    created = client.post(
        "/v1/webhooks/endpoints",
        json={"url": HOOK, "event_types": ["*"]},
        headers={**admin_headers, "Idempotency-Key": "hook-1"},
    )
    assert created.status_code == 201, created.text


def _deliveries(tenant_id: UUID, event_id: UUID) -> int:
    with tenant_session(system_ctx(tenant_id)) as s:
        return len(
            s.scalars(
                select(WebhookDelivery.id).where(WebhookDelivery.event_id == event_id)
            ).all()
        )


def test_the_limit_webhook_is_sent_once_across_both_paths(
    client: TestClient, decide_stage: DecideStage, admin_headers: dict[str, str]
):
    _wildcard(client, admin_headers)
    _ = month_to_date(decide_stage, UDI_350, UDI_350)
    item = candidate("pay-1", UDI_100)
    decided = ask(client, decide_stage.writer, item)
    assert decided.json()["decision"] == "BLOCK", decided.text

    # Executed anyway: the processing path fires the limit too.
    _ = ingest(decide_stage.stage, [item], received=NOW + timedelta(hours=1))
    _ = run_processing(decide_stage.stage.services, decide_stage.tenant_id)
    _ = dispatch_all(decide_stage)

    [hit] = hits(decide_stage.stage, TIER_N1)
    assert hit.outcome == "BLOCK"
    [notice] = _notices(decide_stage.tenant_id)
    assert (notice.source, notice.period_key) == ("DECIDE", "2026-12")
    [event] = events_of(decide_stage.tenant_id, TIER_LIMIT_REACHED)
    assert _deliveries(decide_stage.tenant_id, event.id) == 1


def test_the_webhook_payload_has_ids_and_codes_only(
    client: TestClient, decide_stage: DecideStage
):
    _ = month_to_date(decide_stage, UDI_350, UDI_350)

    decided = ask(client, decide_stage.writer, candidate("pay-1", UDI_100))

    assert decided.status_code == 200, decided.text
    [event] = events_of(decide_stage.tenant_id, TIER_LIMIT_REACHED)
    assert set(event.data) == PAYLOAD_KEYS
    assert event.data == {
        "relationship_id": str(decide_stage.n1),
        # The DEFAULT account was created by the month's deposits.
        "account_id": event.data["account_id"],
        "tier": "N1",
        "limit_code": "monthly_ops",
        "period_key": "2026-12",
        "outcome": "BLOCK",
    }
    assert event.data["account_id"] is not None
    assert UDI_100 not in str(event.data)


def test_a_new_month_sends_again(client: TestClient, decide_stage: DecideStage):
    # About 820 UDI in one deposit: past the N1 limit in either month.
    november = ask(
        client,
        decide_stage.writer,
        candidate("nov-1", "7000.00", at=NOVEMBER),
        at=NOVEMBER,
    )
    december = ask(client, decide_stage.writer, candidate("dec-1", "7000.00"))

    assert (november.json()["decision"], december.json()["decision"]) == (
        "BLOCK",
        "BLOCK",
    )
    notices = _notices(decide_stage.tenant_id)
    assert [notice.period_key for notice in notices] == ["2026-11", "2026-12"]
    assert {notice.source for notice in notices} == {"DECIDE"}
    events = events_of(decide_stage.tenant_id, TIER_LIMIT_REACHED)
    assert [event.data["period_key"] for event in events] == ["2026-11", "2026-12"]
