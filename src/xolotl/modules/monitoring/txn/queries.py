"""The transaction list filters and the lookups of the API (SP-04 spec §15, A10).

``from_`` and ``to`` are Mexico City days, both included, as the backfill range is;
the list is paginated on ``(occurred_at, id)`` by the caller. Every query runs under
the session's tenant: row-level security keeps another tenant's rows out.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from xolotl.modules.monitoring.enums import TransactionStatus
from xolotl.modules.monitoring.txn.models import (
    Transaction,
    TransactionBatch,
    TransactionFile,
)
from xolotl.modules.monitoring.txn.schema import Instrument, Rail
from xolotl.platform.calendar import MEXICO_CITY
from xolotl.platform.db import bound_context
from xolotl.platform.errors import NotFoundError

_ZONE = ZoneInfo(MEXICO_CITY)


@dataclass(frozen=True, slots=True)
class TransactionFilters:
    party_id: UUID | None = None
    relationship_id: UUID | None = None
    external_id: str | None = None
    from_: date | None = None
    to: date | None = None
    status: TransactionStatus | None = None
    instrument: Instrument | None = None
    rail: Rail | None = None
    late: bool | None = None


def _midnight(day: date) -> datetime:
    return datetime.combine(day, time(0), tzinfo=_ZONE)


def transactions_query(filters: TransactionFilters) -> Select[Transaction]:
    """Select the transactions that match every filter given.

    Returns:
        The statement, unordered.

    """
    stmt = select(Transaction)
    if filters.party_id is not None:
        stmt = stmt.where(Transaction.party_id == filters.party_id)
    if filters.relationship_id is not None:
        stmt = stmt.where(Transaction.relationship_id == filters.relationship_id)
    if filters.external_id is not None:
        stmt = stmt.where(Transaction.external_id == filters.external_id)
    if filters.from_ is not None:
        stmt = stmt.where(Transaction.occurred_at >= _midnight(filters.from_))
    if filters.to is not None:
        end = _midnight(filters.to + timedelta(days=1))
        stmt = stmt.where(Transaction.occurred_at < end)
    if filters.status is not None:
        stmt = stmt.where(Transaction.status == filters.status)
    if filters.instrument is not None:
        stmt = stmt.where(Transaction.instrument == filters.instrument)
    if filters.rail is not None:
        stmt = stmt.where(Transaction.rail == filters.rail)
    if filters.late is not None:
        stmt = stmt.where(Transaction.late.is_(filters.late))
    return stmt


def get_transaction(session: Session, transaction_id: UUID) -> Transaction:
    """Read one of the session's tenant's transactions by id.

    Returns:
        The row.

    Raises:
        NotFoundError: When the tenant has no such transaction.

    """
    row = session.scalar(select(Transaction).where(Transaction.id == transaction_id))
    if row is None:
        msg = "transaction not found"
        raise NotFoundError(msg)
    return row


def get_file(session: Session, file_id: UUID) -> TransactionFile:
    """Read one of the session's tenant's uploaded files.

    Returns:
        The row.

    Raises:
        NotFoundError: When the tenant has no such file.

    """
    tenant_id = bound_context(session).require_tenant_id()
    row = session.get(TransactionFile, (tenant_id, file_id))
    if row is None:
        msg = "transaction file not found"
        raise NotFoundError(msg)
    return row


def get_batch(session: Session, batch_id: UUID) -> TransactionBatch:
    """Read one of the session's tenant's batches.

    Returns:
        The row.

    Raises:
        NotFoundError: When the tenant has no such batch.

    """
    tenant_id = bound_context(session).require_tenant_id()
    row = session.get(TransactionBatch, (tenant_id, batch_id))
    if row is None:
        msg = "transaction batch not found"
        raise NotFoundError(msg)
    return row
