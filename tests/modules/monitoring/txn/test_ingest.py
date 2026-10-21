"""Batch ingest: rows, refs, idempotent re-sends, refusals, jobs and the event.

Whenever a test expects an exception from inside a session, ``pytest.raises`` is
the outermost context manager, so the error crosses the session and rolls it back.
"""

import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, wait
from datetime import timedelta
from uuid import UUID

import pytest
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session

from tests.helpers import everything_stored, staff_session, system_ctx
from tests.modules.monitoring.support import add_customer, ingest_items, make_item
from xolotl.modules.monitoring.enums import BatchSource, TransactionStatus
from xolotl.modules.monitoring.errors import BatchInvalidError, ExternalIdConflictError
from xolotl.modules.monitoring.events import TRANSACTION_BATCH_ACCEPTED
from xolotl.modules.monitoring.kinds import PROCESS_KIND
from xolotl.modules.monitoring.txn import accounts, ingest
from xolotl.modules.monitoring.txn.ingest import BatchReport, ingest_batch
from xolotl.modules.monitoring.txn.models import (
    Account,
    Transaction,
    TransactionBatch,
    TransactionRef,
)
from xolotl.modules.monitoring.txn.schema import validate_batch
from xolotl.modules.party.enums import CustomerRelationshipStatus
from xolotl.modules.party.models import CustomerRelationship
from xolotl.modules.party.service import RelationshipRef
from xolotl.modules.screening.counterparty.models import Counterparty
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform import clock
from xolotl.platform.config import get_settings
from xolotl.platform.db import tenant_session
from xolotl.platform.events.event import event_from_row
from xolotl.platform.events.models import OutboxRow
from xolotl.platform.jobs.models import JobRow
from xolotl.platform.jobs.queue import PostgresJobQueue

COUNTERPARTY = {"kind": "PERSON", "name": "Juan Pérez", "country": "MX"}


def _ingest(
    tenant: CreatedTenant, items: list[dict[str, object]], queue: PostgresJobQueue
) -> BatchReport:
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        return ingest_items(s, items, queue=queue)


def _rows(tenant: CreatedTenant) -> list[Transaction]:
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        return list(
            s.scalars(select(Transaction).order_by(Transaction.external_id)).all()
        )


def _count(tenant: CreatedTenant, model: type[object]) -> int | None:
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        return s.scalar(select(func.count()).select_from(model))


def _jobs(tenant: CreatedTenant) -> list[tuple[str, str | None, dict[str, object]]]:
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        rows = s.scalars(
            select(JobRow).where(JobRow.kind == PROCESS_KIND).order_by(JobRow.key)
        ).all()
        return [(row.kind, row.key, dict(row.payload)) for row in rows]


def test_a_batch_writes_rows_refs_a_batch_row_and_one_job_per_party(
    tenant: CreatedTenant, customer: CustomerRelationship, queue: PostgresJobQueue
):
    other = add_customer(tenant, "cust-2")
    items = [
        make_item(external_id="tx-1", counterparty=COUNTERPARTY),
        make_item(external_id="tx-2", account_ref="acc-9"),
        make_item(external_id="tx-3", customer_ref="cust-2"),
    ]
    report = _ingest(tenant, items, queue)
    assert report.accepted == 3
    assert report.duplicates == 0
    assert report.backfill == 0
    assert report.party_ids == {customer.party_id, other.party_id}
    rows = _rows(tenant)
    assert [row.id for row in rows] == list(report.transaction_ids)
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        first = s.scalars(
            select(Transaction).where(Transaction.external_id == "tx-1")
        ).one()
        raw = first.raw.reveal(session=s)
        refs = s.scalars(select(TransactionRef)).all()
        batch = s.get_one(TransactionBatch, (tenant.tenant_id, report.batch_id))
        counterparty = s.get_one(
            Counterparty, (tenant.tenant_id, first.counterparty_id)
        )
        assert counterparty.txn_count == 1
    assert raw["external_id"] == "tx-1"
    assert raw["counterparty"] == {
        "kind": "PERSON",
        "name": "Juan Pérez",
        "account": None,
        "institution": None,
        "country": "MX",
        "wallet_address": None,
        "wallet_network": None,
        "identifier": None,
    }
    assert first.status is TransactionStatus.RECEIVED
    assert first.received_at is not None
    assert first.amount_minor == 150000
    assert first.product == "EMONEY"
    assert first.relationship_id == customer.id
    assert first.counterparty_country == "MX"
    # The DEFAULT account and acc-9 are two rows of one relationship.
    assert rows[0].account_id != rows[1].account_id
    assert rows[0].relationship_id == rows[1].relationship_id == customer.id
    assert rows[1].counterparty_id is None
    assert {(ref.external_id, ref.transaction_id) for ref in refs} == {
        (row.external_id, row.id) for row in rows
    }
    assert (batch.item_count, batch.accepted, batch.duplicates) == (3, 3, 0)
    assert batch.submitted_by == "api_key:test"
    assert _jobs(tenant) == sorted(
        (PROCESS_KIND, f"process:{party_id}", {"party_id": str(party_id)})
        for party_id in (customer.party_id, other.party_id)
    )


def test_a_resend_is_a_duplicate_and_writes_nothing_new(
    tenant: CreatedTenant, customer: CustomerRelationship, queue: PostgresJobQueue
):
    item = make_item(counterparty=COUNTERPARTY)
    first = _ingest(tenant, [item], queue)
    second = _ingest(tenant, [item], queue)
    assert (second.accepted, second.duplicates) == (0, 1)
    assert second.transaction_ids == first.transaction_ids
    assert second.party_ids == frozenset()
    assert second.batch_id != first.batch_id
    assert _count(tenant, Transaction) == 1
    assert _count(tenant, TransactionRef) == 1
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        # A duplicate resolves no counterparty, so it counts no transaction.
        assert s.scalar(select(Counterparty.txn_count)) == 1


def test_a_resend_with_different_content_is_a_conflict_and_the_batch_rolls_back(
    tenant: CreatedTenant, customer: CustomerRelationship, queue: PostgresJobQueue
):
    _ = _ingest(tenant, [make_item()], queue)
    with pytest.raises(ExternalIdConflictError) as caught:
        _ = _ingest(
            tenant,
            [make_item(external_id="tx-new"), make_item(amount="99.00")],
            queue,
        )
    assert caught.value.detail == "transactions[1]"
    assert caught.value.code == "EXTERNAL_ID_CONFLICT"
    assert _count(tenant, TransactionBatch) == 1
    assert [row.external_id for row in _rows(tenant)] == ["tx-1"]


def test_an_unknown_or_closed_customer_ref_refuses_the_whole_batch(
    tenant: CreatedTenant, customer: CustomerRelationship, queue: PostgresJobQueue
):
    _ = add_customer(tenant, "cust-closed", status=CustomerRelationshipStatus.EXITED)
    items = [
        make_item(external_id="tx-1", account_ref="acc-new"),
        make_item(external_id="tx-2", customer_ref="cust-unknown"),
        make_item(external_id="tx-3", customer_ref="cust-closed"),
    ]
    with pytest.raises(BatchInvalidError) as caught:
        _ = _ingest(tenant, items, queue)
    assert caught.value.code == "TRANSACTION_BATCH_INVALID"
    assert [(*error.loc, error.type) for error in caught.value.errors] == [
        ("transactions", 1, "customer_ref", "CUSTOMER_REF_UNKNOWN"),
        ("transactions", 2, "customer_ref", "CUSTOMER_REF_UNKNOWN"),
    ]
    assert all("cust-" not in error.msg for error in caught.value.errors)
    assert _count(tenant, Transaction) == 0
    assert _count(tenant, TransactionBatch) == 0
    assert _count(tenant, Account) == 0
    assert _jobs(tenant) == []


def test_a_refused_batch_writes_no_account_even_before_the_rollback(
    tenant: CreatedTenant, customer: CustomerRelationship, queue: PostgresJobQueue
):
    items = [
        make_item(external_id="tx-1", account_ref="acc-new"),
        make_item(external_id="tx-2", customer_ref="cust-unknown"),
    ]
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        with pytest.raises(BatchInvalidError):
            _ = ingest_items(s, items, queue=queue)
        # Still inside the refused batch's transaction: nothing was written.
        assert s.scalar(select(func.count()).select_from(Account)) == 0


def test_an_account_ref_of_another_customer_refuses_the_batch(
    tenant: CreatedTenant, customer: CustomerRelationship, queue: PostgresJobQueue
):
    _ = add_customer(tenant, "cust-2")
    _ = _ingest(tenant, [make_item(account_ref="acc-9")], queue)
    with pytest.raises(BatchInvalidError) as caught:
        _ = _ingest(
            tenant,
            [make_item(external_id="tx-2", customer_ref="cust-2", account_ref="acc-9")],
            queue,
        )
    assert [(*error.loc, error.type) for error in caught.value.errors] == [
        ("transactions", 0, "account_ref", "ACCOUNT_REF_CONFLICT")
    ]


def test_an_account_ref_shaped_like_a_default_key_is_refused(
    tenant: CreatedTenant, customer: CustomerRelationship, queue: PostgresJobQueue
):
    other = add_customer(tenant, "cust-2")
    with pytest.raises(BatchInvalidError) as caught:
        _ = _ingest(tenant, [make_item(account_ref=f"default:{other.id}")], queue)
    assert [(*error.loc, error.type) for error in caught.value.errors] == [
        ("transactions", 0, "account_ref", "INVALID")
    ]


def test_an_old_transaction_is_backfill_and_not_enqueued(
    tenant: CreatedTenant, customer: CustomerRelationship, queue: PostgresJobQueue
):
    old = (clock.now() - timedelta(days=8)).isoformat()
    report = _ingest(tenant, [make_item(occurred_at=old)], queue)
    assert (report.accepted, report.duplicates, report.backfill) == (1, 0, 1)
    assert report.party_ids == frozenset()
    assert [row.status for row in _rows(tenant)] == [TransactionStatus.BACKFILL]
    assert _jobs(tenant) == []


def test_the_batch_event_carries_ids_and_counts_only(
    tenant: CreatedTenant, customer: CustomerRelationship, queue: PostgresJobQueue
):
    item = make_item(counterparty=COUNTERPARTY)
    _ = _ingest(tenant, [item], queue)
    report = _ingest(tenant, [item, make_item(external_id="tx-2")], queue)
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        rows = s.scalars(
            select(OutboxRow)
            .where(OutboxRow.type == TRANSACTION_BATCH_ACCEPTED)
            .order_by(OutboxRow.seq)
        ).all()
        events = [event_from_row(row) for row in rows]
    assert len(events) == 2
    event = events[1]
    assert event.data == {
        "batch_id": str(report.batch_id),
        "accepted": 1,
        "duplicates": 1,
    }
    assert event.source == "xolotl.monitoring"
    assert event.subject == f"transaction_batch/{report.batch_id}"
    assert event.key == str(report.batch_id)


def _backend_pid(session: Session) -> int:
    return session.execute(text("SELECT pg_backend_pid()")).scalar_one()


_LOCK_WAIT = text(
    "SELECT wait_event_type = 'Lock' FROM pg_stat_activity WHERE pid = :pid"
)


def _waits_on_a_lock(admin_engine: Engine, pid: int) -> bool:
    with admin_engine.connect() as conn:
        return bool(conn.execute(_LOCK_WAIT, {"pid": pid}).scalar())


def _until(condition: Callable[[], bool], what: str, timeout: float = 10) -> None:
    """Poll ``condition`` until it holds.

    Raises:
        AssertionError: When it does not hold within ``timeout`` seconds.

    """
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            msg = f"timed out waiting until {what}"
            raise AssertionError(msg)
        time.sleep(0.01)


def _race(
    tenant: CreatedTenant,
    queue: PostgresJobQueue,
    admin_engine: Engine,
    items: tuple[dict[str, object], dict[str, object]],
) -> tuple[Future[BatchReport], Future[BatchReport]]:
    """Ingest two one-item batches, the first committing once the second waits.

    The first batch ingests and keeps its transaction open until the second
    batch's backend is blocked on a row lock the first holds, then commits. Where
    the second blocks depends on the items: on the account upsert when both name
    one account, else on the ``transaction_ref`` insert.

    Returns:
        The two batches' futures, both done.

    """
    first_ingested = threading.Event()
    second_pid: list[int] = []

    def first() -> BatchReport:
        with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
            report = ingest_items(s, [items[0]], queue=queue)
            first_ingested.set()
            _until(
                lambda: (
                    bool(second_pid) and _waits_on_a_lock(admin_engine, second_pid[0])
                ),
                "the second batch waits on a lock",
            )
            return report

    def second() -> BatchReport:
        _until(first_ingested.is_set, "the first batch has ingested")
        with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
            second_pid.append(_backend_pid(s))
            return ingest_items(s, [items[1]], queue=queue)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = (pool.submit(first), pool.submit(second))
        _ = wait(futures, timeout=30)
    return futures


def test_two_concurrent_batches_with_one_external_id_keep_one_row(
    tenant: CreatedTenant,
    customer: CustomerRelationship,
    queue: PostgresJobQueue,
    admin_engine: Engine,
):
    # Review Focus 1. One customer and one account: the second batch blocks on
    # the account upsert, then its pre-read sees the committed ref.
    item = make_item(counterparty=COUNTERPARTY)
    first, second = _race(tenant, queue, admin_engine, (item, item))
    assert (first.result().accepted, first.result().duplicates) == (1, 0)
    assert (second.result().accepted, second.result().duplicates) == (0, 1)
    assert second.result().transaction_ids == first.result().transaction_ids
    assert _count(tenant, Transaction) == 1
    assert _count(tenant, TransactionRef) == 1
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        assert s.scalar(select(Counterparty.txn_count)) == 1


def test_a_concurrent_batch_that_loses_the_ref_with_other_content_conflicts(
    tenant: CreatedTenant,
    customer: CustomerRelationship,
    queue: PostgresJobQueue,
    admin_engine: Engine,
):
    # Another customer's account, so the two batches share no account row lock
    # and the second blocks on the ref insert until the first commits.
    _ = add_customer(tenant, "cust-2")
    first, second = _race(
        tenant, queue, admin_engine, (make_item(), make_item(customer_ref="cust-2"))
    )
    assert first.result().accepted == 1
    with pytest.raises(ExternalIdConflictError):
        _ = second.result()
    rows = _rows(tenant)
    assert [row.id for row in rows] == list(first.result().transaction_ids)
    assert _count(tenant, TransactionRef) == 1
    assert _count(tenant, TransactionBatch) == 1


def test_two_batches_naming_two_customers_in_opposite_orders_do_not_deadlock(
    tenant: CreatedTenant,
    customer: CustomerRelationship,
    queue: PostgresJobQueue,
    admin_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
):
    _ = add_customer(tenant, "cust-2")
    pids: dict[int, int] = {}
    paused: set[int] = set()
    guard = threading.Lock()
    account_for = accounts.account_for

    def other_waits(me: int) -> bool:
        return any(
            _waits_on_a_lock(admin_engine, pid)
            for ident, pid in pids.items()
            if ident != me
        )

    def account_then_pause(
        session: Session, relationship: RelationshipRef, account_ref: str | None
    ) -> Account:
        account = account_for(session, relationship, account_ref)
        me = threading.get_ident()
        with guard:
            first_call = me not in paused
            paused.add(me)
        if first_call:
            # Hold the first account until the other batch holds one too or waits
            # on this one: opposite lock orders would deadlock from here.
            _until(
                lambda: len(paused) == 2 or other_waits(me),
                "the other batch holds or waits on an account",
            )
        return account

    monkeypatch.setattr(ingest, "account_for", account_then_pause)
    batches = (
        [
            make_item(external_id="a-1"),
            make_item(external_id="a-2", customer_ref="cust-2"),
        ],
        [
            make_item(external_id="b-1", customer_ref="cust-2"),
            make_item(external_id="b-2"),
        ],
    )

    def run(items: list[dict[str, object]]) -> BatchReport:
        with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
            pids[threading.get_ident()] = _backend_pid(s)
            return ingest_items(s, items, queue=queue)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run, items) for items in batches]
        _ = wait(futures, timeout=30)
    assert [future.result().accepted for future in futures] == [2, 2]
    assert _count(tenant, Transaction) == 4


def test_the_ip_lives_only_in_raw(
    tenant: CreatedTenant,
    customer: CustomerRelationship,
    queue: PostgresJobQueue,
    admin_engine: Engine,
):
    item = make_item(
        device={
            "device_id": "dev-42",
            "ip": "203.0.113.9",
            "geo_country": "MX",
            "geo_admin_area": "CMX",
        }
    )
    report = _ingest(tenant, [item], queue)
    assert "203.0.113.9" not in everything_stored(admin_engine)
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        row = s.scalars(select(Transaction)).one()
        assert row.raw.reveal(session=s)["device"] == item["device"]
    assert row.id == report.transaction_ids[0]
    assert row.device_hash is not None
    assert (row.geo_country, row.geo_admin_area) == ("MX", "CMX")


def test_a_crypto_address_is_the_counterparty_wallet_and_never_a_column(
    tenant: CreatedTenant,
    customer: CustomerRelationship,
    queue: PostgresJobQueue,
    admin_engine: Engine,
):
    crypto = {
        "network": "bitcoin",
        "address": "bc1qWalletProbe",
        "asset_code": "BTC",
        "asset_qty": "0.25",
    }
    item = make_item(instrument="CRYPTO", rail="CRYPTO_ONCHAIN", crypto=crypto)
    _ = _ingest(tenant, [item], queue)
    assert "WalletProbe" not in everything_stored(admin_engine)
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        row = s.scalars(select(Transaction)).one()
        counterparty = s.get_one(Counterparty, (tenant.tenant_id, row.counterparty_id))
        assert counterparty.wallet is not None
        assert counterparty.wallet.reveal(session=s) == "bc1qWalletProbe"
    assert counterparty.wallet_network == "bitcoin"
    assert row.crypto is not None
    assert "address" not in row.crypto
    assert row.asset_code == "BTC"
    assert isinstance(row.counterparty_id, UUID)


def test_counterparties_resolve_once_per_batch_and_an_unindexable_one_is_none(
    tenant: CreatedTenant, customer: CustomerRelationship, queue: PostgresJobQueue
):
    crypto = {"network": "ethereum", "address": "0xFeed", "asset_code": "ETH"}
    items = [
        make_item(external_id="tx-1", counterparty=COUNTERPARTY),
        make_item(external_id="tx-2", counterparty=COUNTERPARTY),
        make_item(external_id="tx-3", counterparty={"name": "!!!"}),
        make_item(
            external_id="tx-4",
            instrument="CRYPTO",
            counterparty={"name": "Exchange Uno"},
            crypto={**crypto, "asset_qty": "1"},
        ),
        make_item(
            external_id="tx-5",
            instrument="CRYPTO",
            counterparty={"name": "Exchange Uno", "wallet_address": "0xFeed"},
            crypto={**crypto, "asset_qty": "2"},
        ),
    ]
    _ = _ingest(tenant, items, queue)
    rows = {row.external_id: row for row in _rows(tenant)}
    assert rows["tx-1"].counterparty_id == rows["tx-2"].counterparty_id
    assert rows["tx-3"].counterparty_id is None
    # The crypto address stands in for the wallet the counterparty did not name.
    assert rows["tx-4"].counterparty_id == rows["tx-5"].counterparty_id
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        counts = sorted(s.scalars(select(Counterparty.txn_count)).all())
    assert counts == [2, 2]


def test_wallets_that_differ_only_by_whitespace_resolve_to_one_counterparty(
    tenant: CreatedTenant, customer: CustomerRelationship, queue: PostgresJobQueue
):
    crypto = {"network": "bitcoin", "asset_code": "BTC", "asset_qty": "1"}
    items = [
        make_item(
            external_id="tx-1",
            counterparty={"wallet_address": "bc1qAbcDef9"},
        ),
        make_item(
            external_id="tx-2",
            counterparty={"wallet_address": " bc1q Abc\tDef9 "},
        ),
        make_item(
            external_id="tx-3",
            instrument="CRYPTO",
            crypto={**crypto, "address": "bc1qAbc Def9"},
        ),
    ]
    _ = _ingest(tenant, items, queue)
    rows = {row.external_id: row for row in _rows(tenant)}
    assert rows["tx-1"].counterparty_id is not None
    assert rows["tx-1"].counterparty_id == rows["tx-2"].counterparty_id
    assert rows["tx-1"].counterparty_id == rows["tx-3"].counterparty_id
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        counterparty = s.scalars(select(Counterparty)).one()
        assert counterparty.wallet is not None
        assert counterparty.wallet.reveal(session=s) == "bc1qAbcDef9"
        assert counterparty.txn_count == 3


def test_two_items_with_one_counterparty_count_two_transactions(
    tenant: CreatedTenant, customer: CustomerRelationship, queue: PostgresJobQueue
):
    items = [
        make_item(external_id="tx-1", counterparty=COUNTERPARTY),
        make_item(external_id="tx-2", counterparty=COUNTERPARTY),
    ]
    _ = _ingest(tenant, items, queue)
    _ = _ingest(tenant, [*items, make_item(external_id="tx-3")], queue)
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        assert s.scalars(select(Counterparty.txn_count)).all() == [2]


def test_an_item_that_loses_its_ref_takes_back_its_counterparty_count(
    tenant: CreatedTenant, customer: CustomerRelationship, queue: PostgresJobQueue
):
    # validate_batch refuses a repeated external_id, so only a direct call reaches
    # the lost-ref path with equal content: the second item loses to the first.
    item = make_item(counterparty=COUNTERPARTY)
    accepted, _errors = validate_batch(
        [item],
        now=clock.now(),
        max_future_minutes=get_settings().monitoring_max_future_minutes,
    )
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        report = ingest_batch(
            s,
            [accepted[0], accepted[0]],
            source=BatchSource.API,
            submitted_by="api_key:test",
            queue=queue,
        )
    assert (report.accepted, report.duplicates) == (1, 1)
    assert report.transaction_ids[0] == report.transaction_ids[1]
    assert _count(tenant, Transaction) == 1
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        assert s.scalars(select(Counterparty.txn_count)).all() == [1]


def test_counterparty_country_is_kept_only_with_a_resolved_counterparty(
    tenant: CreatedTenant, customer: CustomerRelationship, queue: PostgresJobQueue
):
    _ = _ingest(
        tenant,
        [make_item(counterparty={"name": "!!!", "country": "US"})],
        queue,
    )
    row = _rows(tenant)[0]
    assert (row.counterparty_id, row.counterparty_country) == (None, None)
