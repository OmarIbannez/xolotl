"""``POST /v1/transactions/decide`` and its fail modes (SP-04 spec §12.1, plan 4c C5).

The decide path answers ``ALLOW``, ``REVIEW`` or ``BLOCK`` from the sync rules over
point reads with the candidate counted in its own aggregates, stores every answer
in ``txn_decision`` and tells the client codes only (``TIER_LIMIT`` with the tier
and limit, or ``RISK_REVIEW``). Over budget, or when a read fails, the rail's fail
mode answers 200 and never a 503. It never waits on processing's locks.
"""

import hashlib
import logging
import time
from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import Engine, select, text, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from tests.helpers import staff_session, system_ctx
from tests.modules.monitoring.decide.support import (
    N2,
    UDI_50,
    UDI_100,
    UDI_350,
    DecideStage,
    ask,
    candidate,
    decision_count,
    decision_row,
    dispatch_all,
    mark_sync,
    month_to_date,
)
from tests.modules.monitoring.rules.hook_support import (
    TIER_N1,
    events_of,
    ingest,
    local,
    version,
)
from tests.modules.party.route_support import machine_headers
from tests.platform.webhooks_support import HOOK
from xolotl.modules.monitoring.aggregates.postgres import PostgresAggregateStore
from xolotl.modules.monitoring.aggregates.windows import MEXICO_CITY_ZONE
from xolotl.modules.monitoring.decide import service
from xolotl.modules.monitoring.decide.enums import FailMode
from xolotl.modules.monitoring.decide.rails import set_fail_mode
from xolotl.modules.monitoring.events import DECISION_ISSUED
from xolotl.modules.monitoring.process.job import lock_party
from xolotl.modules.monitoring.rules.models import TierLimitNotice
from xolotl.modules.monitoring.txn.schema import Rail
from xolotl.modules.party.service import relationship_ref
from xolotl.modules.screening.counterparty.models import Counterparty
from xolotl.platform.config import get_settings
from xolotl.platform.db import tenant_session
from xolotl.platform.events.models import WebhookDelivery

_GONE = OperationalError("SELECT agg_bucket", {}, Exception("gone"))
_FULL = OperationalError("INSERT INTO txn_decision", {}, Exception("full"))
COUNTERPARTY = {"kind": "PERSON", "name": "Juan Pérez", "account": "012180001234567890"}


def _ok(response: Response) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    return response.json()


def test_allow_when_no_sync_rule_fires(client: TestClient, decide_stage: DecideStage):
    body = _ok(ask(client, decide_stage.writer, candidate("d-1", UDI_100)))

    assert body["decision"] == "ALLOW"
    assert body["reasons"] == []
    assert isinstance(body["latency_ms"], int)
    row = decision_row(decide_stage.tenant_id, str(body["decision_id"]))
    assert row is not None
    assert (row.decision, row.mode, row.reasons) == ("ALLOW", "EVALUATED", [])
    assert row.external_id == "d-1"
    assert row.relationship_id == decide_stage.n1
    assert row.actor.startswith("api_client:")
    assert row.transaction_id is None


def test_block_on_the_n1_monthly_limit_with_the_tier_and_limit_code(
    client: TestClient, decide_stage: DecideStage
):
    _ = month_to_date(decide_stage, UDI_350, UDI_350)

    body = _ok(ask(client, decide_stage.writer, candidate("d-1", UDI_100)))

    assert body["decision"] == "BLOCK"
    assert body["reasons"] == [
        {"code": "TIER_LIMIT", "tier": "N1", "limit_code": "monthly_ops"}
    ]
    row = decision_row(decide_stage.tenant_id, str(body["decision_id"]))
    assert row is not None
    assert row.mode == "EVALUATED"
    stage = decide_stage.stage
    hashes = sorted(
        version(stage, code).definition_hash
        for code in (TIER_N1, "TIER_N2_MONTHLY_OPS")
    )
    assert row.ruleset_hash == hashlib.sha256(b"".join(hashes)).digest()
    [reason] = row.reasons
    assert reason["code"] == "TIER_LIMIT"
    assert reason["rule_code"] == TIER_N1
    assert reason["rule_version_id"] == str(stage.versions[TIER_N1])
    assert reason["severity"] == "HIGH"
    assert reason["outcome"] == "BLOCK"
    detail = reason["detail"]
    assert isinstance(detail, dict)
    month_in = detail["month_in"]
    assert isinstance(month_in, dict)
    assert Decimal(str(month_in["value"])) == 800
    assert (month_in["unit"], month_in["window"], month_in["count"]) == (
        "UDI/LAST_DAY_PREV_MONTH",
        "MONTH",
        3,
    )
    assert row.policy_snapshot_id is not None
    assert row.latency_ms >= row.db_ms >= 0


def test_review_on_the_n2_limit(client: TestClient, decide_stage: DecideStage):
    # 3,022 UDI: past the N2 limit of 3,000 UDI in one deposit.
    item = candidate("d-1", "26000.00", customer_ref=N2)

    body = _ok(ask(client, decide_stage.writer, item))

    assert body["decision"] == "REVIEW"
    assert body["reasons"] == [
        {"code": "TIER_LIMIT", "tier": "N2", "limit_code": "monthly_ops"}
    ]


@pytest.mark.parametrize(
    ("amount", "decision"), [(UDI_50, "ALLOW"), ("430.11", "BLOCK")]
)
def test_the_candidate_counts_in_its_own_aggregates(
    client: TestClient, decide_stage: DecideStage, amount: str, decision: str
):
    # 700 UDI month to date: 50 more reach the limit, a centavo more passes it.
    _ = month_to_date(decide_stage, UDI_350, UDI_350)

    body = _ok(ask(client, decide_stage.writer, candidate("d-1", amount)))

    assert body["decision"] == decision


def _true_match_counterparty(stage: DecideStage, admin_engine: Engine) -> None:
    deposit = candidate("seen-1", "100.00", customer_ref=N2, counterparty=COUNTERPARTY)
    _ = ingest(stage.stage, [deposit])
    with admin_engine.begin() as conn:
        _ = conn.execute(
            update(Counterparty)
            .where(Counterparty.tenant_id == stage.tenant_id)
            .values(screening_status="TRUE_MATCH")
        )


def test_block_on_a_critical_sync_rule_and_the_client_sees_risk_review_only(
    client: TestClient, decide_stage: DecideStage, admin_engine: Engine
):
    mark_sync(admin_engine, decide_stage)
    _true_match_counterparty(decide_stage, admin_engine)
    item = candidate("d-1", "100.00", customer_ref=N2, counterparty=COUNTERPARTY)

    response = ask(client, decide_stage.writer, item)

    body = _ok(response)
    assert body["decision"] == "BLOCK"
    assert body["reasons"] == [{"code": "RISK_REVIEW"}]
    assert "COUNTERPARTY" not in response.text
    assert "TRUE_MATCH" not in response.text
    row = decision_row(decide_stage.tenant_id, str(body["decision_id"]))
    assert row is not None
    [reason] = row.reasons
    assert (reason["code"], reason["rule_code"], reason["severity"]) == (
        "RISK_REVIEW",
        "COUNTERPARTY_TRUE_MATCH",
        "CRITICAL",
    )


def test_an_unscreened_counterparty_is_not_treated_as_clear(
    client: TestClient, decide_stage: DecideStage, admin_engine: Engine
):
    # Never seen: the decide path reads UNSCREENED, writes no counterparty and
    # fires nothing.
    mark_sync(admin_engine, decide_stage)
    item = candidate("d-1", "100.00", customer_ref=N2, counterparty=COUNTERPARTY)

    body = _ok(ask(client, decide_stage.writer, item))

    assert body["decision"] == "ALLOW"
    with tenant_session(system_ctx(decide_stage.tenant_id)) as s:
        assert s.scalars(select(Counterparty)).all() == []


def test_fail_open_answers_review_on_a_budget_overrun(
    client: TestClient, decide_stage: DecideStage, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(get_settings(), "monitoring_decide_budget_ms", 0)

    body = _ok(ask(client, decide_stage.writer, candidate("d-1", UDI_100)))

    assert body["decision"] == "REVIEW"
    assert body["reasons"] == [{"code": "DECIDE_UNAVAILABLE"}]
    row = decision_row(decide_stage.tenant_id, str(body["decision_id"]))
    assert row is not None
    assert (row.decision, row.mode) == ("REVIEW", "FAIL_OPEN")
    assert row.reasons == [{"code": "DECIDE_UNAVAILABLE"}]


def _fail_closed(stage: DecideStage, rail: Rail) -> None:
    with staff_session(stage.tenant_id, stage.tenant.admin_user_id) as s:
        _ = set_fail_mode(s, rail, FailMode.FAIL_CLOSED)


def test_fail_closed_answers_block_on_a_database_error(
    client: TestClient, decide_stage: DecideStage, monkeypatch: pytest.MonkeyPatch
):
    _fail_closed(decide_stage, Rail.SPEI)

    def broken(*_args: object, **_kwargs: object) -> list[object]:
        raise _GONE

    monkeypatch.setattr(PostgresAggregateStore, "buckets", broken)
    item = candidate("d-1", UDI_100, rail="SPEI", instrument="TRANSFER")

    body = _ok(ask(client, decide_stage.writer, item))

    assert body["decision"] == "BLOCK"
    assert body["reasons"] == [{"code": "DECIDE_UNAVAILABLE"}]
    row = decision_row(decide_stage.tenant_id, str(body["decision_id"]))
    assert row is not None
    assert (row.decision, row.mode, row.rail) == ("BLOCK", "FAIL_CLOSED", "SPEI")


def test_a_missing_unit_value_is_a_budget_failure_not_a_stale_value(
    client: TestClient, decide_stage: DecideStage
):
    # No UDI is loaded for January 2027: enrichment raises ParkTransactionError.
    at = local(14, 12).replace(year=2027, month=1)

    item = candidate("d-1", UDI_100, at=at)

    body = _ok(ask(client, decide_stage.writer, item, at=at))

    assert body["decision"] == "REVIEW"
    assert body["reasons"] == [{"code": "DECIDE_UNAVAILABLE"}]
    row = decision_row(decide_stage.tenant_id, str(body["decision_id"]))
    assert row is not None
    assert row.mode == "FAIL_OPEN"


def test_a_decision_row_that_cannot_be_written_does_not_lose_the_response(
    client: TestClient,
    decide_stage: DecideStage,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    def broken(*_args: object, **_kwargs: object) -> None:
        raise _FULL

    monkeypatch.setattr(service, "record_decision", broken)

    with caplog.at_level(logging.WARNING):
        response = ask(client, decide_stage.writer, candidate("d-1", UDI_100))

    body = _ok(response)
    assert body["decision"] == "ALLOW"
    assert decision_count(decide_stage.tenant_id) == 0
    [record] = [r for r in caplog.records if r.message == service.DECISION_UNRECORDED]
    assert getattr(record, "decision", None) == "ALLOW"
    assert getattr(record, "mode", None) == "EVALUATED"


def test_the_decide_route_needs_the_write_scope_and_an_idempotency_key(
    client: TestClient, decide_stage: DecideStage
):
    item = candidate("d-1", UDI_100)
    reader = machine_headers(decide_stage.tenant, "transactions:read")
    refused = ask(client, reader, item)
    assert (refused.status_code, refused.json()["code"]) == (403, "SCOPE_MISSING")

    keyless = client.post(
        "/v1/transactions/decide", json=item, headers=decide_stage.writer
    )
    assert (keyless.status_code, keyless.json()["code"]) == (
        400,
        "IDEMPOTENCY_KEY_REQUIRED",
    )

    first = _ok(ask(client, decide_stage.writer, item, key="k-1"))
    again = _ok(ask(client, decide_stage.writer, item, key="k-1"))
    assert again["decision_id"] == first["decision_id"]
    assert decision_count(decide_stage.tenant_id) == 1


def test_a_naive_occurred_at_is_a_422_field_error(
    client: TestClient, decide_stage: DecideStage
):
    item = candidate("d-1", UDI_100, occurred_at="2026-12-14T11:55:00")

    response = ask(client, decide_stage.writer, item)

    assert response.status_code == 422, response.text
    [error] = response.json()["errors"]
    assert (error["loc"], error["type"]) == (["body", "occurred_at"], "NAIVE_DATETIME")
    assert decision_count(decide_stage.tenant_id) == 0


def test_an_unknown_customer_is_a_422_on_customer_ref(
    client: TestClient, decide_stage: DecideStage
):
    response = ask(
        client, decide_stage.writer, candidate("d-1", UDI_100, customer_ref="nobody")
    )

    assert response.status_code == 422, response.text
    [error] = response.json()["errors"]
    assert (error["loc"], error["type"]) == (
        ["body", "customer_ref"],
        "CUSTOMER_REF_UNKNOWN",
    )


def test_an_account_of_another_customer_is_a_422_on_account_ref(
    client: TestClient, decide_stage: DecideStage
):
    _ = ingest(
        decide_stage.stage,
        [candidate("seen-1", "100.00", customer_ref=N2, account_ref="acct-9")],
    )

    response = ask(
        client, decide_stage.writer, candidate("d-1", UDI_100, account_ref="acct-9")
    )

    assert response.status_code == 422, response.text
    [error] = response.json()["errors"]
    assert (error["loc"], error["type"]) == (
        ["body", "account_ref"],
        "ACCOUNT_REF_CONFLICT",
    )


def test_decision_issued_never_reaches_a_webhook(
    client: TestClient, decide_stage: DecideStage, admin_headers: dict[str, str]
):
    created = client.post(
        "/v1/webhooks/endpoints",
        json={"url": HOOK, "event_types": ["*"]},
        headers={**admin_headers, "Idempotency-Key": "hook-1"},
    )
    assert created.status_code == 201, created.text
    refused = client.post(
        "/v1/webhooks/endpoints",
        json={"url": HOOK, "event_types": [DECISION_ISSUED]},
        headers={**admin_headers, "Idempotency-Key": "hook-2"},
    )
    assert (refused.status_code, refused.json()["code"]) == (
        422,
        "EVENT_TYPE_NOT_DELIVERABLE",
    )

    body = _ok(ask(client, decide_stage.writer, candidate("d-1", UDI_100)))
    _ = dispatch_all(decide_stage)

    [event] = events_of(decide_stage.tenant_id, DECISION_ISSUED)
    assert event.data == {
        "decision_id": body["decision_id"],
        "external_id": "d-1",
        "decision": "ALLOW",
        "mode": "EVALUATED",
    }
    assert event.key == str(decide_stage.n1)
    with tenant_session(system_ctx(decide_stage.tenant_id)) as s:
        delivered = s.scalars(
            select(WebhookDelivery.event_id).where(WebhookDelivery.event_id == event.id)
        ).all()
    assert delivered == []


def test_the_decide_path_never_waits_on_processing_locks(
    client: TestClient, decide_stage: DecideStage
):
    # A drain of the customer's party holds the party lock, the tenant's epoch lock
    # exclusively (as a backfill does) and the customer's bucket rows.
    _ = month_to_date(decide_stage, UDI_350, UDI_350)
    tenant_id = decide_stage.tenant_id
    with tenant_session(system_ctx(tenant_id)) as holder:
        party_id = relationship_ref(holder, decide_stage.n1).party_id
        assert lock_party(holder, tenant_id, party_id) is False
        _ = holder.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"monitoring.agg_epoch:{tenant_id}"},
        )
        _ = holder.execute(
            text("SELECT 1 FROM agg_bucket WHERE key = :key FOR UPDATE"),
            {"key": str(decide_stage.n1)},
        ).all()

        body = _ok(ask(client, decide_stage.writer, candidate("d-1", UDI_100)))

    assert (body["decision"], body["reasons"][0]["code"]) == ("BLOCK", "TIER_LIMIT")
    row = decision_row(tenant_id, str(body["decision_id"]))
    assert row is not None
    assert row.mode == "EVALUATED"


def test_a_notice_held_by_another_transaction_does_not_hold_the_decision(
    client: TestClient,
    decide_stage: DecideStage,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    # Processing is inserting the month's notice and has not committed: the decide
    # path gives up on the notice after the budget and still answers.
    monkeypatch.setattr(get_settings(), "monitoring_decide_budget_ms", 1000)
    _ = month_to_date(decide_stage, UDI_350, UDI_350)
    tenant_id = decide_stage.tenant_id
    with tenant_session(system_ctx(tenant_id)) as holder:
        holder.add(
            TierLimitNotice(
                tenant_id=tenant_id,
                relationship_id=decide_stage.n1,
                limit_code="monthly_ops",
                period_key="2026-12",
                source="PROCESS",
            )
        )
        holder.flush()
        started = time.monotonic()

        with caplog.at_level(logging.WARNING):
            body = _ok(ask(client, decide_stage.writer, candidate("d-1", UDI_100)))

        waited = time.monotonic() - started
    assert body["decision"] == "BLOCK"
    # It waited for the notice no longer than the lock timeout (the budget).
    assert 1 <= waited < 10
    # The answer's latency and the row's include that wait.
    assert body["latency_ms"] >= 1000
    assert [r for r in caplog.records if r.message == service.NOTICE_DEFERRED]
    row = decision_row(tenant_id, str(body["decision_id"]))
    assert row is not None
    assert row.mode == "EVALUATED"
    assert row.latency_ms == body["latency_ms"]
    with tenant_session(system_ctx(tenant_id)) as s:
        [notice] = s.scalars(select(TierLimitNotice)).all()
    assert notice.source == "PROCESS"


def test_the_decision_is_stamped_with_the_clock(
    client: TestClient, decide_stage: DecideStage
):
    at = local(14, 12)

    body = _ok(ask(client, decide_stage.writer, candidate("d-1", UDI_100), at=at))

    row = decision_row(decide_stage.tenant_id, UUID(str(body["decision_id"])))
    assert row is not None
    assert row.decided_at.astimezone(MEXICO_CITY_ZONE).date().isoformat() == (
        "2026-12-14"
    )


def test_a_slow_query_is_cancelled_at_the_budget(
    client: TestClient, decide_stage: DecideStage, monkeypatch: pytest.MonkeyPatch
):
    # A read that stalls without waiting on a lock is cut off by statement_timeout.
    monkeypatch.setattr(get_settings(), "monitoring_decide_budget_ms", 200)

    def stalled(s: Session) -> frozenset[str]:
        _ = s.execute(text("SELECT pg_sleep(2)"))
        return frozenset()

    monkeypatch.setattr(service, "backfilled_ids", stalled)
    started = time.monotonic()

    body = _ok(ask(client, decide_stage.writer, candidate("d-1", UDI_100)))

    assert time.monotonic() - started < 1.5
    assert (body["decision"], body["reasons"]) == (
        "REVIEW",
        [{"code": "DECIDE_UNAVAILABLE"}],
    )
    row = decision_row(decide_stage.tenant_id, str(body["decision_id"]))
    assert row is not None
    assert row.mode == "FAIL_OPEN"


def test_a_second_decision_at_the_same_instant_is_still_recorded(
    client: TestClient, decide_stage: DecideStage
):
    # Same external_id, same frozen clock, two keys: the second row moves a microsecond.
    first = _ok(ask(client, decide_stage.writer, candidate("d-1", UDI_100), key="a"))
    second = _ok(ask(client, decide_stage.writer, candidate("d-1", UDI_50), key="b"))

    rows = [
        decision_row(decide_stage.tenant_id, str(body["decision_id"]))
        for body in (first, second)
    ]
    assert all(row is not None for row in rows)
    assert decision_count(decide_stage.tenant_id) == 2
    decided = [row.decided_at for row in rows if row is not None]
    assert decided[1] - decided[0] == timedelta(microseconds=1)
