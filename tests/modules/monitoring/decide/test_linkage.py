"""Decisions linked to the transactions that arrive later (SP-04 spec §6.1 step 3).

Ingest links each new row to the latest unlinked decision of its ``external_id``
from the last seven days; both rows then point at each other. The hit detail shows
the triggering transaction's decision ("decided BLOCK at 15:20:11", spec §12.1).
"""

from datetime import datetime, timedelta
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import select

from tests.helpers import system_ctx
from tests.modules.monitoring.decide.support import (
    DECISIONS,
    N2,
    NOW,
    UDI_100,
    UDI_350,
    DecideStage,
    ask,
    candidate,
    decision_row,
    month_to_date,
)
from tests.modules.monitoring.rules.hook_support import TIER_N1, hits, ingest
from tests.modules.monitoring.support import run_processing
from tests.modules.party.route_support import staff_headers
from xolotl.modules.monitoring.decide.linkage import link_decisions
from xolotl.modules.monitoring.decide.models import TxnDecision
from xolotl.modules.monitoring.txn.models import Transaction
from xolotl.platform.db import tenant_session
from xolotl.platform.enums import Role


def _admin(client: TestClient, stage: DecideStage) -> dict[str, str]:
    return staff_headers(client, stage.tenant, email="ops@acme.mx", roles=(Role.ADMIN,))


def _decide(client: TestClient, stage: DecideStage, item: dict[str, object]) -> str:
    response = ask(client, stage.writer, item, key=f"k-{item['external_id']}-{NOW}")
    assert response.status_code == 200, response.text
    return str(response.json()["decision_id"])


def _transaction(tenant_id: UUID, transaction_id: UUID) -> Transaction:
    with tenant_session(system_ctx(tenant_id)) as s:
        return s.scalars(
            select(Transaction).where(Transaction.id == transaction_id)
        ).one()


def test_a_later_transaction_links_to_its_decision_by_external_id(
    client: TestClient, decide_stage: DecideStage
):
    item = candidate("pay-1", UDI_100)
    decision_id = _decide(client, decide_stage, item)

    [txn_id] = ingest(decide_stage.stage, [item], received=NOW + timedelta(hours=1))

    decision = decision_row(decide_stage.tenant_id, decision_id)
    assert decision is not None
    txn = _transaction(decide_stage.tenant_id, txn_id)
    assert decision.transaction_id == txn_id
    assert decision.occurred_at == txn.occurred_at
    assert txn.decision_id == decision.id
    assert txn.decided_at == decision.decided_at
    _ = run_processing(decide_stage.stage.services, decide_stage.tenant_id)
    admin = _admin(client, decide_stage)
    staff_view = client.get(f"/v1/transactions/{txn_id}", headers=admin).json()
    assert (staff_view["status"], staff_view["decision_id"]) == (
        "PROCESSED",
        decision_id,
    )
    detail = client.get(f"{DECISIONS}/{decision_id}", headers=admin).json()
    assert detail["transaction"] == {
        "id": str(txn_id),
        "occurred_at": staff_view["occurred_at"],
        "status": "PROCESSED",
    }


def test_a_decision_older_than_seven_days_is_not_linked(
    client: TestClient, decide_stage: DecideStage
):
    item = candidate("pay-1", UDI_100)
    decision_id = _decide(client, decide_stage, item)

    [txn_id] = ingest(
        decide_stage.stage, [item], received=NOW + timedelta(days=7, minutes=1)
    )

    decision = decision_row(decide_stage.tenant_id, decision_id)
    assert decision is not None
    assert decision.transaction_id is None
    assert _transaction(decide_stage.tenant_id, txn_id).decision_id is None


def test_a_duplicate_resend_links_nothing_twice(
    client: TestClient, decide_stage: DecideStage
):
    item = candidate("pay-1", UDI_100)
    first = _decide(client, decide_stage, item)
    received = NOW + timedelta(hours=1)
    [txn_id] = ingest(decide_stage.stage, [item], received=received)
    second = ask(
        client, decide_stage.writer, item, key="k-again", at=NOW + timedelta(minutes=1)
    )
    assert second.status_code == 200, second.text

    [again] = ingest(decide_stage.stage, [item], received=received)

    assert again == txn_id
    linked = decision_row(decide_stage.tenant_id, first)
    unlinked = decision_row(decide_stage.tenant_id, str(second.json()["decision_id"]))
    assert linked is not None
    assert unlinked is not None
    assert linked.transaction_id == txn_id
    assert unlinked.transaction_id is None
    assert _transaction(decide_stage.tenant_id, txn_id).decision_id == linked.id


def test_the_latest_decision_of_an_external_id_is_the_one_linked(
    client: TestClient, decide_stage: DecideStage
):
    item = candidate("pay-1", UDI_100)
    _ = _decide(client, decide_stage, item)
    later = ask(
        client, decide_stage.writer, item, key="k-2", at=NOW + timedelta(days=1)
    )
    assert later.status_code == 200, later.text

    [txn_id] = ingest(decide_stage.stage, [item], received=NOW + timedelta(days=2))

    txn = _transaction(decide_stage.tenant_id, txn_id)
    assert str(txn.decision_id) == later.json()["decision_id"]


def test_linking_nothing_writes_nothing(decide_stage: DecideStage):
    with tenant_session(system_ctx(decide_stage.tenant_id)) as s:
        assert link_decisions(s, []) == 0
        assert link_decisions(s, [(UUID(int=1), NOW, "nobody", UUID(int=2))]) == 0
        assert s.scalars(select(TxnDecision)).all() == []


def test_the_hit_detail_shows_the_decision(
    client: TestClient, decide_stage: DecideStage
):
    _ = month_to_date(decide_stage, UDI_350, UDI_350)
    item = candidate("pay-1", UDI_100)
    decision_id = _decide(client, decide_stage, item)
    _ = ingest(decide_stage.stage, [item], received=NOW + timedelta(hours=1))
    _ = run_processing(decide_stage.stage.services, decide_stage.tenant_id)
    [hit] = hits(decide_stage.stage, TIER_N1)

    detail = client.get(
        f"/v1/monitoring/hits/{hit.id}", headers=_admin(client, decide_stage)
    )

    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["decision_id"] == decision_id
    decision = body["decision"]
    assert (decision["decision_id"], decision["decision"], decision["mode"]) == (
        decision_id,
        "BLOCK",
        "EVALUATED",
    )
    assert datetime.fromisoformat(decision["decided_at"]) == NOW


def test_a_decision_of_another_customer_is_not_linked(
    client: TestClient, decide_stage: DecideStage
):
    # The client reused an external_id across customers: no cross-customer link.
    decision_id = _decide(client, decide_stage, candidate("pay-1", UDI_100))

    [txn_id] = ingest(
        decide_stage.stage,
        [candidate("pay-1", UDI_100, customer_ref=N2)],
        received=NOW + timedelta(hours=1),
    )

    decision = decision_row(decide_stage.tenant_id, decision_id)
    assert decision is not None
    assert decision.transaction_id is None
    assert _transaction(decide_stage.tenant_id, txn_id).decision_id is None
