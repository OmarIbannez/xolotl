"""Batch ingest: each ``external_id`` kept exactly once (SP-04 spec §6.1, D11).

``ingest_batch`` runs in the caller's transaction, in plan 4a's order (Deviation
4: spec step 3, decision linking, is plan 4c's):

1. Resolve every item's ``customer_ref`` (one query) and account. Any unknown or
   inactive customer, or an account bound to another relationship, refuses the
   whole batch with ``TRANSACTION_BATCH_INVALID``; nothing is written.
2. Classify each re-sent ``external_id`` against ``transaction_ref``: equal content
   is a duplicate, other content refuses the batch with ``EXTERNAL_ID_CONFLICT``.
   Resolve the counterparties of the new items, write the ``transaction_batch``
   row and, per new item, the ``transaction`` row and then its ``transaction_ref``
   with ``ON CONFLICT DO NOTHING``. The ref's foreign key to the partitioned
   primary key is not deferrable, so the row goes first; when a concurrent batch
   won the ref, the row is deleted again and the item is classified as above.
   (Plan 4c's C5 links decisions here, over ``created`` rows.)
4. Enqueue one ``monitoring.process`` job per party with a ``RECEIVED`` row.
5. Publish ``monitoring.transaction_batch_accepted.v1``.

A row whose ``occurred_at`` is more than ``MONITORING_LATE_DAYS`` before receipt is
``BACKFILL``: kept and counted as accepted, but never enqueued (spec §8.5).
No audit row is written: the batch row is the provenance record (spec §14).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
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
)
from xolotl.modules.party.enums import CustomerRelationshipStatus
from xolotl.modules.party.service import RelationshipRef, relationship_by_external_ref
from xolotl.modules.screening.counterparty.resolve import (
    CounterpartyRef,
    CounterpartyUnindexableError,
    dedup_hash_of,
    resolve,
)
from xolotl.modules.screening.counterparty.schema import CounterpartyInput
from xolotl.platform import clock
from xolotl.platform.config import get_settings
from xolotl.platform.crypto import encrypt_json
from xolotl.platform.crypto.blind_index import blind_index, normalize_identifier
from xolotl.platform.db import bound_context
from xolotl.platform.ids import uuid7
from xolotl.platform.jobs.queue import PostgresJobQueue


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


def _resolve_items(
    session: Session, items: Sequence[TransactionInput]
) -> list[_Resolved]:
    relationships = relationship_by_external_ref(
        session, {item.customer_ref for item in items}
    )
    errors: list[FieldError] = []
    resolved: list[_Resolved] = []
    for index, item in enumerate(items):
        relationship = relationships.get(item.customer_ref)
        if (
            relationship is None
            or relationship.status is not CustomerRelationshipStatus.ACTIVE
        ):
            errors.append(
                FieldError(
                    index=index,
                    field="customer_ref",
                    code=FieldErrorCode.CUSTOMER_REF_UNKNOWN,
                )
            )
            continue
        try:
            account = account_for(session, relationship, item.account_ref)
        except AccountRefConflictError:
            errors.append(
                FieldError(
                    index=index,
                    field="account_ref",
                    code=FieldErrorCode.ACCOUNT_REF_CONFLICT,
                )
            )
            continue
        resolved.append(_Resolved(relationship=relationship, account_id=account.id))
    if errors:
        raise BatchInvalidError(errors=field_error_items(errors))
    return resolved


def counterparty_of(item: TransactionInput) -> CounterpartyInput | None:
    """Return the counterparty an item names, the crypto address standing in.

    ``crypto.address`` is the counterparty's wallet when the counterparty names
    none (spec §4).

    Returns:
        The counterparty input, or None when the item names no one.

    """
    address = None if item.crypto is None else item.crypto.address
    network = None if item.crypto is None else item.crypto.network
    counterparty = item.counterparty
    if address is None:
        return counterparty
    if counterparty is None:
        return CounterpartyInput(wallet_address=address, wallet_network=network)
    if counterparty.wallet_address is not None:
        return counterparty
    return counterparty.model_copy(
        update={
            "wallet_address": address,
            "wallet_network": counterparty.wallet_network or network,
        }
    )


def _counterparties(
    session: Session, items: Sequence[TransactionInput]
) -> list[CounterpartyRef | None]:
    # One resolve per distinct counterparty; a later mention reuses the first ref.
    by_hash: dict[bytes, CounterpartyRef] = {}
    refs: list[CounterpartyRef | None] = []
    for item in items:
        counterparty = counterparty_of(item)
        if counterparty is None:
            refs.append(None)
            continue
        try:
            dedup_hash = dedup_hash_of(session, counterparty)
        except CounterpartyUnindexableError:
            # Nothing left to index (a name of punctuation only): the input stays
            # in raw and the row names no counterparty.
            refs.append(None)
            continue
        ref = by_hash.get(dedup_hash)
        if ref is None:
            ref = resolve(session, counterparty)
            by_hash[dedup_hash] = ref
        refs.append(ref)
    return refs


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
            None if item.counterparty is None else item.counterparty.country
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
    transaction must roll back.

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
    counterparties = _counterparties(session, [items[index] for index in fresh])
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
    for index, counterparty in zip(fresh, counterparties, strict=True):
        kept = _keep(
            session,
            items[index],
            index,
            fresh=fresh_rows,
            resolved=resolved[index],
            counterparty=counterparty,
            digest=digests[index],
        )
        if isinstance(kept, Transaction):
            created.append(kept)
            known[index] = kept.id
        else:
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
