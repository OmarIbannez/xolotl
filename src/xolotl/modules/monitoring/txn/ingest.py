"""Batch ingest: each ``external_id`` kept exactly once (SP-04 spec §6.1, D11).

``ingest_batch`` runs in the caller's transaction, in plan 4a's order (Deviation
4: spec step 3, decision linking, is plan 4c's):

1. Resolve every item's ``customer_ref`` (one query) and check every
   ``account_ref`` against the accounts already bound (one query) and the items
   before it. Any unknown or inactive customer, or an account bound to another
   relationship, refuses the whole batch with ``TRANSACTION_BATCH_INVALID``
   before anything is written. Then upsert each distinct account once.
2. Classify each re-sent ``external_id`` against ``transaction_ref``: equal content
   is a duplicate, other content refuses the batch with ``EXTERNAL_ID_CONFLICT``.
   Resolve the counterparties of the new items, each counting its items, write
   the ``transaction_batch`` row and, per new item, the ``transaction`` row and
   then its ``transaction_ref`` with ``ON CONFLICT DO NOTHING``. The ref's foreign
   key to the partitioned primary key is not deferrable, so the row goes first;
   when a concurrent batch won the ref, the row is deleted again, its
   counterparty count taken back, and the item is classified as above. (Plan
   4c's C5 links decisions here, over ``created`` rows.)
4. Enqueue one ``monitoring.process`` job per party with a ``RECEIVED`` row.
5. Publish ``monitoring.transaction_batch_accepted.v1``.

Concurrent batches take their row locks in one order, so they wait on each other
instead of deadlocking: accounts by ``external_ref``, counterparties by
``dedup_hash``, refs by ``external_id``.

A row whose ``occurred_at`` is more than ``MONITORING_LATE_DAYS`` before receipt is
``BACKFILL``: kept and counted as accepted, but never enqueued (spec §8.5).
No audit row is written: the batch row is the provenance record (spec §14).
"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from xolotl.modules.monitoring.enums import BatchSource, TransactionStatus
from xolotl.modules.monitoring.errors import (
    BatchInvalidError,
    ExternalIdConflictError,
    field_error_items,
)
from xolotl.modules.monitoring.events import (
    TRANSACTION_BATCH_ACCEPTED,
    publish_monitoring_event,
)
from xolotl.modules.monitoring.process.enqueue import enqueue_processing
from xolotl.modules.monitoring.txn.accounts import (
    AccountRefConflictError,
    account_for,
    account_key,
    bound_relationships,
)
from xolotl.modules.monitoring.txn.models import (
    Transaction,
    TransactionBatch,
    TransactionRef,
)
from xolotl.modules.monitoring.txn.schema import (
    FieldError,
    FieldErrorCode,
    TransactionInput,
    amount_minor,
    content_hash,
    counterparty_of,
)
from xolotl.modules.party.enums import CustomerRelationshipStatus
from xolotl.modules.party.service import RelationshipRef, relationship_by_external_ref
from xolotl.modules.screening.counterparty.resolve import (
    CounterpartyRef,
    CounterpartyUnindexableError,
    dedup_hash_of,
    resolve,
    uncount,
)
from xolotl.platform import clock
from xolotl.platform.config import get_settings
from xolotl.platform.crypto import encrypt_json
from xolotl.platform.crypto.blind_index import blind_index, normalize_identifier
from xolotl.platform.db import bound_context
from xolotl.platform.ids import uuid7
from xolotl.platform.jobs.queue import PostgresJobQueue

if TYPE_CHECKING:
    from xolotl.modules.screening.counterparty.schema import CounterpartyInput


@dataclass(frozen=True, slots=True)
class BatchReport:
    """What one batch did; ``transaction_ids`` follow the items' order."""

    batch_id: UUID
    accepted: int
    duplicates: int
    backfill: int
    transaction_ids: tuple[UUID, ...]
    party_ids: frozenset[UUID]


@dataclass(frozen=True, slots=True)
class _Resolved:
    relationship: RelationshipRef
    account_id: UUID


def _field_error(index: int, field: str, code: FieldErrorCode) -> FieldError:
    return FieldError(index=index, field=field, code=code)


def _relationships(
    session: Session, items: Sequence[TransactionInput]
) -> list[RelationshipRef]:
    # Validates without writing: customers, then accounts bound elsewhere, either
    # already stored or by an earlier item of this batch.
    by_ref = relationship_by_external_ref(
        session, {item.customer_ref for item in items}
    )
    errors: list[FieldError] = []
    found: list[RelationshipRef | None] = []
    for index, item in enumerate(items):
        relationship = by_ref.get(item.customer_ref)
        if (
            relationship is None
            or relationship.status is not CustomerRelationshipStatus.ACTIVE
        ):
            errors.append(
                _field_error(index, "customer_ref", FieldErrorCode.CUSTOMER_REF_UNKNOWN)
            )
            relationship = None
        found.append(relationship)
    owners = bound_relationships(
        session, {item.account_ref for item in items if item.account_ref is not None}
    )
    for index, (item, relationship) in enumerate(zip(items, found, strict=True)):
        if relationship is None or item.account_ref is None:
            continue
        if owners.setdefault(item.account_ref, relationship.id) != relationship.id:
            errors.append(
                _field_error(index, "account_ref", FieldErrorCode.ACCOUNT_REF_CONFLICT)
            )
    if errors:
        errors.sort(key=lambda error: error.index)
        raise BatchInvalidError(errors=field_error_items(errors))
    return [relationship for relationship in found if relationship is not None]


def _resolve_items(
    session: Session, items: Sequence[TransactionInput]
) -> list[_Resolved]:
    relationships = _relationships(session, items)
    keys = [
        account_key(relationship.id, item.account_ref)
        for item, relationship in zip(items, relationships, strict=True)
    ]
    first_index: dict[str, int] = {}
    for index, key in enumerate(keys):
        _ = first_index.setdefault(key, index)
    # One upsert per account, in key order, so concurrent batches lock alike.
    account_ids: dict[str, UUID] = {}
    errors: list[FieldError] = []
    for key in sorted(first_index):
        index = first_index[key]
        try:
            account = account_for(
                session, relationships[index], items[index].account_ref
            )
        except AccountRefConflictError:
            # A concurrent batch bound the ref after the check above.
            errors.extend(
                _field_error(other, "account_ref", FieldErrorCode.ACCOUNT_REF_CONFLICT)
                for other, other_key in enumerate(keys)
                if other_key == key
            )
            continue
        account_ids[key] = account.id
    if errors:
        errors.sort(key=lambda error: error.index)
        raise BatchInvalidError(errors=field_error_items(errors))
    return [
        _Resolved(relationship=relationship, account_id=account_ids[key])
        for relationship, key in zip(relationships, keys, strict=True)
    ]


def _counterparties(
    session: Session, items: Sequence[TransactionInput]
) -> list[CounterpartyRef | None]:
    # One resolve per distinct counterparty, counting its items, in dedup_hash
    # order so concurrent batches lock alike.
    hashes: list[bytes | None] = []
    inputs: dict[bytes, CounterpartyInput] = {}
    counts: Counter[bytes] = Counter()
    for item in items:
        counterparty = counterparty_of(item)
        dedup_hash = None
        if counterparty is not None:
            try:
                dedup_hash = dedup_hash_of(session, counterparty)
            except CounterpartyUnindexableError:
                # Nothing left to index (a name of punctuation only): the input
                # stays in raw and the row names no counterparty.
                dedup_hash = None
        hashes.append(dedup_hash)
        if counterparty is not None and dedup_hash is not None:
            _ = inputs.setdefault(dedup_hash, counterparty)
            counts[dedup_hash] += 1
    refs = {
        dedup_hash: resolve(session, inputs[dedup_hash], count=counts[dedup_hash])
        for dedup_hash in sorted(inputs)
    }
    return [None if dedup_hash is None else refs[dedup_hash] for dedup_hash in hashes]


def _stored_refs(
    session: Session, items: Sequence[TransactionInput]
) -> dict[str, TransactionRef]:
    rows = session.scalars(
        select(TransactionRef).where(
            TransactionRef.external_id.in_([item.external_id for item in items])
        )
    ).all()
    return {row.external_id: row for row in rows}


def _known(index: int, stored: TransactionRef, digest: bytes) -> UUID:
    if stored.content_hash != digest:
        msg = f"transactions[{index}]"
        raise ExternalIdConflictError(msg)
    return stored.transaction_id


def _device_hash(session: Session, item: TransactionInput) -> bytes | None:
    device_id = None if item.device is None else item.device.device_id
    if device_id is None or not normalize_identifier("device", device_id):
        return None
    return blind_index(
        "device",
        device_id,
        tenant_id=bound_context(session).require_tenant_id(),
        session=session,
    )


def _row(
    session: Session,
    item: TransactionInput,
    *,
    batch_id: UUID,
    resolved: _Resolved,
    counterparty: CounterpartyRef | None,
    digest: bytes,
    received_at: datetime,
    status: TransactionStatus,
) -> Transaction:
    tenant_id = bound_context(session).require_tenant_id()
    crypto = item.crypto
    device = item.device
    return Transaction(
        tenant_id=tenant_id,
        occurred_at=item.occurred_at,
        id=uuid7(),
        external_id=item.external_id,
        received_at=received_at,
        batch_id=batch_id,
        party_id=resolved.relationship.party_id,
        relationship_id=resolved.relationship.id,
        account_id=resolved.account_id,
        product=resolved.relationship.product,
        direction=item.direction,
        rail=item.rail,
        instrument=item.instrument,
        channel=item.channel,
        amount_minor=amount_minor(item.amount),
        currency=item.currency,
        fee_minor=None if item.fee_amount is None else amount_minor(item.fee_amount),
        asset_qty=None if crypto is None else crypto.asset_qty,
        asset_code=None if crypto is None else crypto.asset_code,
        counterparty_id=None if counterparty is None else counterparty.id,
        counterparty_country=(
            None
            if counterparty is None or item.counterparty is None
            else item.counterparty.country
        ),
        device_hash=_device_hash(session, item),
        geo_country=None if device is None else device.geo_country,
        geo_admin_area=None if device is None else device.geo_admin_area,
        # The wallet address is the counterparty's: encrypted there and in raw.
        crypto=(
            None
            if crypto is None
            else crypto.model_dump(mode="json", exclude={"address"})
        ),
        fx=None if item.fx is None else item.fx.model_dump(mode="json"),
        purpose_code=item.purpose_code,
        status=status,
        content_hash=digest,
        raw=encrypt_json(
            item.model_dump(mode="json"), tenant_id=tenant_id, session=session
        ),
    )


def _claim_ref(session: Session, row: Transaction) -> bool:
    claimed = session.execute(
        insert(TransactionRef)
        .values(
            tenant_id=row.tenant_id,
            external_id=row.external_id,
            transaction_id=row.id,
            occurred_at=row.occurred_at,
            content_hash=row.content_hash,
        )
        .on_conflict_do_nothing(
            index_elements=[TransactionRef.tenant_id, TransactionRef.external_id]
        )
        .returning(TransactionRef.external_id)
    ).scalar_one_or_none()
    return claimed is not None


@dataclass(frozen=True, slots=True)
class _Fresh:
    received_at: datetime
    batch_id: UUID
    late_after: timedelta


def _keep(
    session: Session,
    item: TransactionInput,
    index: int,
    *,
    fresh: _Fresh,
    resolved: _Resolved,
    counterparty: CounterpartyRef | None,
    digest: bytes,
) -> Transaction | UUID:
    late = fresh.received_at - item.occurred_at > fresh.late_after
    row = _row(
        session,
        item,
        batch_id=fresh.batch_id,
        resolved=resolved,
        counterparty=counterparty,
        digest=digest,
        received_at=fresh.received_at,
        status=TransactionStatus.BACKFILL if late else TransactionStatus.RECEIVED,
    )
    session.add(row)
    session.flush()
    if _claim_ref(session, row):
        return row
    # A concurrent batch committed this external_id first: the row goes again.
    _ = session.execute(
        delete(Transaction).where(
            Transaction.occurred_at == row.occurred_at, Transaction.id == row.id
        )
    )
    session.expunge(row)
    if counterparty is not None:
        uncount(session, counterparty.id)
    winner = session.get_one(
        TransactionRef, (row.tenant_id, item.external_id), populate_existing=True
    )
    return _known(index, winner, digest)


def ingest_batch(
    session: Session,
    items: Sequence[TransactionInput],
    *,
    source: BatchSource,
    submitted_by: str,
    queue: PostgresJobQueue,
    file_id: UUID | None = None,
    request_id: str | None = None,
) -> BatchReport:
    """Keep a validated batch's new transactions and enqueue their parties.

    A refused batch raises ``BatchInvalidError``, and a known ``external_id``
    re-sent with other content ``ExternalIdConflictError``; either way the caller's
    transaction must roll back. An unknown customer or an account bound elsewhere
    is refused before anything is written, but an account bound by a concurrent
    batch is only found by its upsert.

    Returns:
        The batch's id, its counters, the transaction id of every item (a
        duplicate's stored one) and the parties of its new rows.

    """
    resolved = _resolve_items(session, items)
    received_at = clock.now()
    digests = [content_hash(item) for item in items]
    stored = _stored_refs(session, items)
    known: dict[int, UUID] = {
        index: _known(index, stored[item.external_id], digests[index])
        for index, item in enumerate(items)
        if item.external_id in stored
    }
    fresh = [index for index in range(len(items)) if index not in known]
    counterparties = dict(
        zip(
            fresh,
            _counterparties(session, [items[index] for index in fresh]),
            strict=True,
        )
    )
    batch = TransactionBatch(
        tenant_id=bound_context(session).require_tenant_id(),
        source=source,
        file_id=file_id,
        submitted_by=submitted_by,
        item_count=len(items),
        accepted=0,
        duplicates=0,
        received_at=received_at,
        request_id=request_id,
    )
    session.add(batch)
    session.flush()
    fresh_rows = _Fresh(
        received_at=received_at,
        batch_id=batch.id,
        late_after=timedelta(days=get_settings().monitoring_late_days),
    )
    created: list[Transaction] = []
    # Refs are claimed in external_id order, so concurrent batches lock alike.
    for index in sorted(fresh, key=lambda fresh_index: items[fresh_index].external_id):
        kept = _keep(
            session,
            items[index],
            index,
            fresh=fresh_rows,
            resolved=resolved[index],
            counterparty=counterparties[index],
            digest=digests[index],
        )
        if isinstance(kept, Transaction):
            created.append(kept)
            known[index] = kept.id
        else:
            # Unreachable through the API today: validate_batch refuses a repeated
            # external_id, and two concurrent batches with equal content name one
            # account, whose upsert lock makes the later batch wait and then see
            # the ref in its pre-read. Kept for the race the locks do not cover.
            known[index] = kept
    received = [row for row in created if row.status is TransactionStatus.RECEIVED]
    batch.accepted = len(created)
    batch.duplicates = len(items) - len(created)
    session.flush()
    party_ids = frozenset(row.party_id for row in received)
    _ = enqueue_processing(session, queue, party_ids)
    _ = publish_monitoring_event(
        session,
        TRANSACTION_BATCH_ACCEPTED,
        entity="transaction_batch",
        entity_id=batch.id,
        key=str(batch.id),
        data={
            "batch_id": str(batch.id),
            "accepted": batch.accepted,
            "duplicates": batch.duplicates,
        },
    )
    return BatchReport(
        batch_id=batch.id,
        accepted=batch.accepted,
        duplicates=batch.duplicates,
        backfill=len(created) - len(received),
        transaction_ids=tuple(known[index] for index in range(len(items))),
        party_ids=party_ids,
    )
