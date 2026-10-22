"""The transactions API for API clients and staff (SP-04 spec §15, plan 4a A10).

An API client sees a transaction's identity and status only (tipping-off, D20);
staff see the enriched row with the counterparty masked, and a reveal decrypts the
counterparty and the payload after auditing each.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from tests.helpers import audit_events, new_tenant, staff_session, system_ctx
from tests.modules.monitoring.support import add_customer, ingest_items, make_item
from tests.modules.party.route_support import (
    analyst_headers,
    machine_headers,
    post,
    staff_headers,
)
from xolotl.modules.monitoring.enums import TransactionStatus
from xolotl.modules.monitoring.kinds import INGEST_FILE_KIND, PROCESS_KIND
from xolotl.modules.monitoring.process.job import process_party
from xolotl.modules.monitoring.txn import files
from xolotl.modules.monitoring.txn.files import IngestFileJob
from xolotl.modules.monitoring.txn.models import Transaction
from xolotl.modules.party.models import CustomerRelationship
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform import clock
from xolotl.platform.audit import VIEW_PII
from xolotl.platform.config import get_settings
from xolotl.platform.db import get_sessionmaker, tenant_session
from xolotl.platform.enums import Role
from xolotl.platform.ids import uuid7
from xolotl.platform.jobs.models import JobRow
from xolotl.platform.jobs.queue import Job, PostgresJobQueue
from xolotl.platform.storage.local import LocalFsObjectStore

TRANSACTIONS = "/v1/transactions"
FILES = f"{TRANSACTIONS}/files"
CLIENT_KEYS = {"id", "external_id", "occurred_at", "status", "late"}
# Every unit value of the ITF pack exists for 2026-01-20; 2026-01-22 has no UDI.
VALUED = datetime(2026, 1, 20, 12, tzinfo=UTC)
UNVALUED = datetime(2026, 1, 22, 12, tzinfo=UTC)
# Within a day of VALUED: nothing processed from it is late.
RECEIVED = datetime(2026, 1, 21, 8, tzinfo=UTC)
RECEIVED_LATER = datetime(2026, 1, 22, 20, tzinfo=UTC)
COUNTERPARTY = {"kind": "PERSON", "name": "Juan Pérez", "country": "MX"}
CSV_HEADER = (
    "external_id,occurred_at,customer_ref,direction,rail,instrument,amount,currency"
)
CSV = (
    f"{CSV_HEADER}\n"
    "f-1,2026-01-20T12:00:00Z,cust-1,IN,CASH,CASH,100.00,MXN\n"
    "f-2,2026-01-20T13:00:00Z,cust-404,IN,CASH,CASH,200.00,MXN\n"
).encode()


@pytest.fixture
def object_store(app: FastAPI, tmp_path: Path) -> LocalFsObjectStore:
    store = LocalFsObjectStore(tmp_path / "objects")
    app.state.xolotl.object_store = store
    return store


@pytest.fixture
def writer(tenant: CreatedTenant) -> dict[str, str]:
    return machine_headers(tenant, "transactions:write")


@pytest.fixture
def reader(tenant: CreatedTenant) -> dict[str, str]:
    return machine_headers(tenant, "transactions:read")


def _ingest(
    tenant: CreatedTenant,
    monkeypatch: pytest.MonkeyPatch,
    items: list[dict[str, object]],
    *,
    now: datetime = RECEIVED,
) -> list[UUID]:
    with monkeypatch.context() as patched:
        patched.setattr(clock, "now", lambda: now)
        with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
            report = ingest_items(s, items, queue=PostgresJobQueue(get_sessionmaker()))
    return list(report.transaction_ids)


def _process(tenant: CreatedTenant, customer: CustomerRelationship) -> None:
    factory = get_sessionmaker()
    _ = process_party(
        factory,
        tenant.tenant_id,
        customer.party_id,
        queue=PostgresJobQueue(factory),
        job=None,
        settings=get_settings(),
        actor="cli:test",
    )


def _processed(
    tenant: CreatedTenant,
    customer: CustomerRelationship,
    monkeypatch: pytest.MonkeyPatch,
) -> UUID:
    (txn_id,) = _ingest(
        tenant,
        monkeypatch,
        [
            make_item(
                external_id="tx-1",
                occurred_at=VALUED.isoformat(),
                counterparty=COUNTERPARTY,
                raw={"note": "cliente frecuente"},
            )
        ],
    )
    _process(tenant, customer)
    return txn_id


def _view_pii(tenant: CreatedTenant) -> list[tuple[str | None, str | None, object]]:
    return [
        (row.entity_type, row.entity_id, row.detail)
        for row in audit_events(tenant.tenant_id)
        if row.action == VIEW_PII
    ]


def test_an_api_key_submits_a_batch_and_gets_ids_back(
    client: TestClient,
    tenant: CreatedTenant,
    customer: CustomerRelationship,
    writer: dict[str, str],
):
    body = {"transactions": [make_item(), make_item(external_id="tx-2")]}
    first = post(client, writer, TRANSACTIONS, body, "batch-1")
    assert first.status_code == 202, first.text
    accepted = first.json()
    assert accepted["accepted"] == 2
    assert accepted["duplicates"] == 0
    assert len(accepted["transaction_ids"]) == 2
    replay = post(client, writer, TRANSACTIONS, body, "batch-1")
    assert replay.status_code == 202
    assert replay.json() == accepted
    again = post(client, writer, TRANSACTIONS, body, "batch-2")
    assert again.json()["accepted"] == 0
    assert again.json()["duplicates"] == 2
    assert again.json()["transaction_ids"] == accepted["transaction_ids"]
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        jobs = s.scalars(select(JobRow.key).where(JobRow.kind == PROCESS_KIND)).all()
    assert jobs == [f"process:{customer.party_id}"]


def test_a_read_only_key_cannot_submit(
    client: TestClient,
    tenant: CreatedTenant,
    customer: CustomerRelationship,
    reader: dict[str, str],
):
    body = {"transactions": [make_item()]}
    assert post(client, reader, TRANSACTIONS, body, "k").status_code == 403


def test_a_batch_over_the_cap_is_413_and_an_invalid_one_422_with_field_errors(
    client: TestClient,
    tenant: CreatedTenant,
    customer: CustomerRelationship,
    writer: dict[str, str],
):
    cap = get_settings().monitoring_batch_max_items
    too_many = {
        "transactions": [make_item(external_id=f"tx-{n}") for n in range(cap + 1)]
    }
    big = post(client, writer, TRANSACTIONS, too_many, "big")
    assert big.status_code == 413
    assert big.json()["code"] == "TRANSACTION_BATCH_TOO_LARGE"
    invalid = {
        "transactions": [
            make_item(),
            make_item(external_id="tx-2", customer_ref="nobody"),
        ]
    }
    refused = post(client, writer, TRANSACTIONS, invalid, "invalid")
    assert refused.status_code == 422
    problem = refused.json()
    assert problem["code"] == "TRANSACTION_BATCH_INVALID"
    assert [(e["loc"], e["type"]) for e in problem["errors"]] == [
        (["transactions", 1, "customer_ref"], "CUSTOMER_REF_UNKNOWN")
    ]
    assert "nobody" not in refused.text
    malformed = post(
        client,
        writer,
        TRANSACTIONS,
        {"transactions": [make_item(amount="-5")]},
        "malformed",
    )
    assert malformed.status_code == 422
    assert [(e["loc"], e["type"]) for e in malformed.json()["errors"]] == [
        (["transactions", 0, "amount"], "OUT_OF_RANGE")
    ]
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        assert not s.scalars(select(JobRow).where(JobRow.kind == PROCESS_KIND)).all()


def test_a_conflicting_resend_is_409_naming_the_index_only(
    client: TestClient,
    tenant: CreatedTenant,
    customer: CustomerRelationship,
    writer: dict[str, str],
):
    first = {"transactions": [make_item(amount="1500.00")]}
    assert post(client, writer, TRANSACTIONS, first, "a").status_code == 202
    changed = {
        "transactions": [
            make_item(external_id="tx-0"),
            make_item(amount="9876.54"),
        ]
    }
    conflict = post(client, writer, TRANSACTIONS, changed, "b")
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "EXTERNAL_ID_CONFLICT"
    assert conflict.json()["detail"] == "transactions[1]"
    assert "9876" not in conflict.text
    assert "tx-1" not in conflict.text


def test_a_file_upload_is_202_and_the_same_bytes_again_200(
    client: TestClient,
    tenant: CreatedTenant,
    customer: CustomerRelationship,
    writer: dict[str, str],
    object_store: LocalFsObjectStore,
):
    headers = {**writer, "Content-Type": "text/csv; charset=utf-8"}
    first = client.post(
        FILES, content=CSV, headers={**headers, "Idempotency-Key": "f1"}
    )
    assert first.status_code == 202, first.text
    uploaded = first.json()
    assert uploaded["status"] == "UPLOADED"
    assert uploaded["rows_total"] == 2
    again = client.post(
        FILES, content=CSV, headers={**headers, "Idempotency-Key": "f2"}
    )
    assert again.status_code == 200
    assert again.json()["file_id"] == uploaded["file_id"]
    stored = list(object_store.list(tenant.tenant_id, "transactions/files/"))
    assert [ref.key for ref in stored] == [f"transactions/files/{uploaded['file_id']}"]
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        keys = s.scalars(select(JobRow.key).where(JobRow.kind == INGEST_FILE_KIND))
        assert list(keys) == [f"ingest_file:{uploaded['file_id']}"]


def test_a_file_that_loses_the_upload_race_answers_with_the_winner(
    client: TestClient,
    tenant: CreatedTenant,
    customer: CustomerRelationship,
    writer: dict[str, str],
    object_store: LocalFsObjectStore,
    monkeypatch: pytest.MonkeyPatch,
):
    # The concurrent upload commits between this upload's lookup and its insert.
    winner: list[UUID] = []

    def racing_uuid7() -> UUID:
        if not winner:
            winner.append(uuid7())
            with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
                row, created = files.upload_file(
                    s,
                    object_store,
                    data=CSV,
                    content_type="text/csv",
                    uploaded_by="staff:other",
                    queue=PostgresJobQueue(get_sessionmaker()),
                )
                assert created
                winner[0] = row.id
        return uuid7()

    monkeypatch.setattr("xolotl.modules.monitoring.txn.files.uuid7", racing_uuid7)
    lost = client.post(
        FILES,
        content=CSV,
        headers={**writer, "Content-Type": "text/csv", "Idempotency-Key": "race"},
    )
    assert lost.status_code == 200, lost.text
    assert lost.json()["file_id"] == str(winner[0])


def test_an_unaccepted_type_and_an_oversized_file_are_refused(
    client: TestClient,
    tenant: CreatedTenant,
    writer: dict[str, str],
    object_store: LocalFsObjectStore,
    monkeypatch: pytest.MonkeyPatch,
):
    pdf = client.post(
        FILES,
        content=b"%PDF-1.7",
        headers={**writer, "Content-Type": "application/pdf", "Idempotency-Key": "p"},
    )
    assert pdf.status_code == 422
    assert pdf.json()["code"] == "TRANSACTION_FILE_INVALID"
    monkeypatch.setattr(
        "xolotl.modules.monitoring.routes_transactions.get_settings",
        lambda: get_settings().model_copy(update={"monitoring_file_max_mb": 1}),
    )
    big = client.post(
        FILES,
        content=CSV + b"x" * (1024 * 1024),
        headers={**writer, "Content-Type": "text/csv", "Idempotency-Key": "b"},
    )
    assert big.status_code == 413
    assert big.json()["code"] == "TRANSACTION_FILE_TOO_LARGE"


def _ingest_file(
    tenant: CreatedTenant, file_id: str, store: LocalFsObjectStore
) -> None:
    factory = get_sessionmaker()
    job = Job(
        id=uuid7(),
        tenant_id=tenant.tenant_id,
        kind=INGEST_FILE_KIND,
        key=f"ingest_file:{file_id}",
        payload={"file_id": file_id},
        attempts=1,
        max_attempts=1,
        lock_token=uuid7(),
    )
    ingest = IngestFileJob(
        session_factory=factory,
        store=store,
        queue=PostgresJobQueue(factory),
        settings=get_settings(),
    )
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        ingest.run(s, job, leased=False)


def test_file_status_and_the_error_report_are_readable_by_both_audiences(
    client: TestClient,
    tenant: CreatedTenant,
    customer: CustomerRelationship,
    writer: dict[str, str],
    reader: dict[str, str],
    admin_headers: dict[str, str],
    object_store: LocalFsObjectStore,
):
    uploaded = client.post(
        FILES,
        content=CSV,
        headers={**writer, "Content-Type": "text/csv", "Idempotency-Key": "f"},
    ).json()
    file_id = uploaded["file_id"]
    report = f"{FILES}/{file_id}/errors"
    assert client.get(report, headers=reader).status_code == 404
    _ingest_file(tenant, file_id, object_store)
    for headers in (reader, admin_headers):
        status = client.get(f"{FILES}/{file_id}", headers=headers)
        assert status.status_code == 200
        assert status.json()["status"] == "COMPLETED"
        assert status.json()["rows_accepted"] == 1
        assert status.json()["rows_rejected"] == 1
        errors = client.get(report, headers=headers)
        assert errors.status_code == 200
        assert errors.headers["content-type"].startswith("text/csv")
        assert (
            errors.content == b"row,column,code\n2,customer_ref,CUSTOMER_REF_UNKNOWN\n"
        )
    assert client.get(f"{FILES}/{file_id}", headers=writer).status_code == 403
    assert client.get(f"{FILES}/{uuid7()}", headers=reader).status_code == 404


def test_a_batch_is_readable_with_its_counts_and_request_id(
    client: TestClient,
    tenant: CreatedTenant,
    customer: CustomerRelationship,
    writer: dict[str, str],
    reader: dict[str, str],
):
    body = {"transactions": [make_item()]}
    sent = client.post(
        TRANSACTIONS,
        json=body,
        headers={**writer, "Idempotency-Key": "k", "X-Request-Id": "req-batch-1"},
    )
    batch_id = sent.json()["batch_id"]
    batch = client.get(f"{TRANSACTIONS}/batches/{batch_id}", headers=reader)
    assert batch.status_code == 200
    shown = batch.json()
    assert (shown["source"], shown["item_count"], shown["accepted"]) == ("API", 1, 1)
    assert shown["request_id"] == "req-batch-1"
    assert shown["file_id"] is None
    missing = client.get(f"{TRANSACTIONS}/batches/{uuid7()}", headers=reader)
    assert missing.status_code == 404


def test_the_client_view_hides_everything_but_identity_and_status(
    client: TestClient,
    tenant: CreatedTenant,
    itf_tenant: UUID,
    customer: CustomerRelationship,
    reader: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
):
    txn_id = _processed(tenant, customer, monkeypatch)
    one = client.get(f"{TRANSACTIONS}/{txn_id}", headers=reader)
    assert one.status_code == 200
    shown = one.json()
    assert set(shown) == CLIENT_KEYS
    assert shown["status"] == "PROCESSED"
    assert shown["late"] is False
    listed = client.get(TRANSACTIONS, headers=reader)
    assert listed.status_code == 200
    (item,) = listed.json()["items"]
    assert set(item) == CLIENT_KEYS
    for leaked in ("UNSCREENED", "unit_amounts", "UDI", "Juan", "counterparty"):
        assert leaked not in one.text
        assert leaked not in listed.text


def test_the_staff_view_shows_units_periods_the_masked_counterparty_and_snapshot(
    client: TestClient,
    tenant: CreatedTenant,
    itf_tenant: UUID,
    customer: CustomerRelationship,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
):
    txn_id = _processed(tenant, customer, monkeypatch)
    response = client.get(f"{TRANSACTIONS}/{txn_id}", headers=admin_headers)
    assert response.status_code == 200
    shown = response.json()
    assert shown["status"] == "PROCESSED"
    assert shown["account_ref"] == f"default:{customer.id}"
    assert shown["amount_minor"] == 150000
    keys = {amount["key"] for amount in shown["unit_amounts"]}
    assert {"UDI/SAME_DAY", "MXN/SAME_DAY"} <= keys
    udi = next(a for a in shown["unit_amounts"] if a["key"] == "UDI/SAME_DAY")
    assert (udi["unit"], udi["rule"]) == ("UDI", "SAME_DAY")
    assert udi["unit_value_id"] is not None
    assert udi["resolved_date"] == "2026-01-20"
    assert shown["period_keys"] == {
        "DAY": "2026-01-20",
        "MONTH": "2026-01",
        "QUARTER": "2026-Q1",
        "BUSINESS_DAY": True,
    }
    assert shown["policy_snapshot_id"] is not None
    assert shown["counterparty_screening_status_at_processing"] == "UNSCREENED"
    counterparty = shown["counterparty"]
    assert counterparty["has_name"] is True
    assert counterparty["has_account"] is False
    assert counterparty["country"] == "MX"
    assert counterparty["screening_status"] == "UNSCREENED"
    assert shown["parked"] is None
    assert "raw" not in shown
    assert "Juan" not in response.text
    assert "frecuente" not in response.text
    assert not _view_pii(tenant)


def test_reveal_is_staff_only_and_audited(
    client: TestClient,
    tenant: CreatedTenant,
    itf_tenant: UUID,
    customer: CustomerRelationship,
    reader: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
):
    txn_id = _processed(tenant, customer, monkeypatch)
    machine = client.get(f"{TRANSACTIONS}/{txn_id}?reveal=true", headers=reader)
    assert machine.status_code == 403
    assert not _view_pii(tenant)
    analyst = analyst_headers(client, tenant)
    revealed = client.get(f"{TRANSACTIONS}/{txn_id}?reveal=true", headers=analyst)
    assert revealed.status_code == 200
    shown = revealed.json()
    assert shown["raw"]["raw"] == {"note": "cliente frecuente"}
    assert shown["raw"]["counterparty"]["name"] == "Juan Pérez"
    assert shown["counterparty_revealed"]["name"] == "Juan Pérez"
    assert shown["counterparty_revealed"]["account"] is None
    counterparty_id = shown["counterparty"]["id"]
    assert _view_pii(tenant) == [
        ("transaction", str(txn_id), {"field": "raw"}),
        ("transaction", str(txn_id), {"field": "counterparty"}),
        ("counterparty", counterparty_id, {"field": "name"}),
    ]


def test_list_filters_and_cursors(
    client: TestClient,
    tenant: CreatedTenant,
    itf_tenant: UUID,
    customer: CustomerRelationship,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
):
    other = add_customer(tenant, "cust-2")
    ids = _ingest(
        tenant,
        monkeypatch,
        [
            make_item(external_id="a", occurred_at=VALUED.isoformat()),
            make_item(
                external_id="b",
                occurred_at=(VALUED + timedelta(hours=1)).isoformat(),
                rail="SPEI",
                instrument="TRANSFER",
                direction="OUT",
                channel="APP",
            ),
            make_item(
                external_id="c",
                occurred_at=(VALUED + timedelta(hours=19)).isoformat(),
                customer_ref="cust-2",
            ),
        ],
    )
    _process(tenant, customer)

    def listed(**params: str) -> list[str]:
        response = client.get(TRANSACTIONS, params=params, headers=admin_headers)
        assert response.status_code == 200, response.text
        return [item["external_id"] for item in response.json()["items"]]

    assert listed() == ["c", "b", "a"]
    assert listed(party_id=str(customer.party_id)) == ["b", "a"]
    assert listed(relationship_id=str(other.id)) == ["c"]
    assert listed(external_id="b") == ["b"]
    assert listed(**{"from": "2026-01-21"}) == ["c"]
    assert listed(to="2026-01-20") == ["b", "a"]
    assert listed(**{"from": "2026-01-20", "to": "2026-01-20"}) == ["b", "a"]
    assert listed(status="PROCESSED") == ["b", "a"]
    assert listed(status="RECEIVED") == ["c"]
    assert listed(instrument="TRANSFER") == ["b"]
    assert listed(rail="CASH", late="false") == ["c", "a"]
    assert listed(late="true") == []
    first = client.get(TRANSACTIONS, params={"limit": 2}, headers=admin_headers)
    page = first.json()
    assert [item["id"] for item in page["items"]] == [str(ids[2]), str(ids[1])]
    assert page["next_cursor"] is not None
    rest = client.get(
        TRANSACTIONS,
        params={"limit": 2, "cursor": page["next_cursor"]},
        headers=admin_headers,
    ).json()
    assert [item["id"] for item in rest["items"]] == [str(ids[0])]
    assert rest["next_cursor"] is None


def test_parked_and_backfill_lists_show_reasons(
    client: TestClient,
    tenant: CreatedTenant,
    itf_tenant: UUID,
    customer: CustomerRelationship,
    admin_headers: dict[str, str],
    reader: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
):
    parked, waiting, held = _ingest(
        tenant,
        monkeypatch,
        [
            make_item(external_id="p", occurred_at=UNVALUED.isoformat()),
            make_item(
                external_id="w", occurred_at=(UNVALUED + timedelta(hours=1)).isoformat()
            ),
            make_item(external_id="h", occurred_at="2026-01-10T12:00:00Z"),
        ],
        now=RECEIVED_LATER,
    )
    _process(tenant, customer)
    parked_list = client.get(f"{TRANSACTIONS}/parked", headers=admin_headers)
    assert parked_list.status_code == 200
    (item,) = parked_list.json()["items"]
    assert item["id"] == str(parked)
    assert item["parked"]["reason"] == "UNIT_VALUE_MISSING"
    assert item["parked"]["unit_code"] == "UDI"
    assert item["parked"]["needed_on"] == "2026-01-22"
    assert item["parked"]["attempts"] >= 1
    backfill = client.get(f"{TRANSACTIONS}/backfill", headers=admin_headers)
    assert [i["id"] for i in backfill.json()["items"]] == [str(held)]
    assert backfill.json()["items"][0]["parked"] is None
    waiting_view = client.get(f"{TRANSACTIONS}/{waiting}", headers=admin_headers)
    assert waiting_view.json()["status"] == "RECEIVED"
    assert waiting_view.json()["parked"] is None
    for path in ("parked", "backfill"):
        assert client.get(f"{TRANSACTIONS}/{path}", headers=reader).status_code == 403


def test_an_unparked_row_shows_no_parked_reason(
    client: TestClient,
    tenant: CreatedTenant,
    itf_tenant: UUID,
    customer: CustomerRelationship,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
):
    (parked,) = _ingest(
        tenant,
        monkeypatch,
        [make_item(external_id="p", occurred_at=UNVALUED.isoformat())],
        now=RECEIVED_LATER,
    )
    _process(tenant, customer)
    # The sweep sets a row RECEIVED again and keeps its transaction_parked entry.
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        row = s.scalar(select(Transaction).where(Transaction.id == parked))
        assert row is not None
        row.status = TransactionStatus.RECEIVED
    assert client.get(f"{TRANSACTIONS}/parked", headers=admin_headers).json() == {
        "items": [],
        "next_cursor": None,
    }
    shown = client.get(f"{TRANSACTIONS}/{parked}", headers=admin_headers).json()
    assert shown["status"] == "RECEIVED"
    assert shown["parked"] is None


def test_tenant_b_sees_none_of_tenant_a(
    client: TestClient,
    tenant: CreatedTenant,
    itf_tenant: UUID,
    customer: CustomerRelationship,
    writer: dict[str, str],
    object_store: LocalFsObjectStore,
    monkeypatch: pytest.MonkeyPatch,
):
    txn_id = _processed(tenant, customer, monkeypatch)
    batch_id = post(
        client,
        writer,
        TRANSACTIONS,
        {"transactions": [make_item(external_id="tx-2")]},
        "k",
    ).json()["batch_id"]
    file_id = client.post(
        FILES,
        content=CSV,
        headers={**writer, "Content-Type": "text/csv", "Idempotency-Key": "f"},
    ).json()["file_id"]
    other = new_tenant("globex", "admin@globex.mx")
    their_admin = staff_headers(
        client, other, email="ops@globex.mx", roles=(Role.ADMIN,)
    )
    their_key = machine_headers(other, "transactions:read")
    for headers in (their_admin, their_key):
        assert client.get(TRANSACTIONS, headers=headers).json()["items"] == []
        for path in (
            f"{TRANSACTIONS}/{txn_id}",
            f"{TRANSACTIONS}/batches/{batch_id}",
            f"{FILES}/{file_id}",
            f"{FILES}/{file_id}/errors",
        ):
            assert client.get(path, headers=headers).status_code == 404, path
    assert (
        client.get(
            f"{TRANSACTIONS}/{txn_id}?reveal=true", headers=their_admin
        ).status_code
        == 404
    )
    for path in ("parked", "backfill"):
        listed = client.get(f"{TRANSACTIONS}/{path}", headers=their_admin)
        assert listed.json()["items"] == []
    assert not _view_pii(tenant)


def test_literal_routes_win_over_the_id_route(
    client: TestClient, tenant: CreatedTenant, admin_headers: dict[str, str]
):
    for path in ("parked", "backfill"):
        response = client.get(f"{TRANSACTIONS}/{path}", headers=admin_headers)
        assert response.status_code == 200, response.text
        assert response.json() == {"items": [], "next_cursor": None}
    unknown = client.get(f"{TRANSACTIONS}/{uuid7()}", headers=admin_headers)
    assert unknown.status_code == 404
