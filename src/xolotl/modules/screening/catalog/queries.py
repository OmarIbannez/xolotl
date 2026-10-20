"""Reads of the shared list catalog: active versions, their members and entries.

The members of version ``n`` of a list are its ``list_entry`` rows with
``from_seq <= n`` and ``to_seq`` null or above ``n`` (SP-03 spec §5.1); the rows of
the active version are the ones with ``to_seq`` null.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session, aliased

from xolotl.modules.screening.catalog.models import (
    ListEntryDelta,
    ListEntryRow,
    ListVersion,
    TenantListSubscription,
)
from xolotl.modules.screening.enums import DeltaChange, VersionStatus
from xolotl.modules.screening.lists import ListEntry


@dataclass(frozen=True, slots=True)
class DeltaRowView:
    """One change of a version, with the primary names of the two entry rows."""

    source_entry_key: str
    change: DeltaChange
    prev_entry_id: UUID | None
    new_entry_id: UUID | None
    prev_name: str | None
    new_name: str | None


def active_versions(session: Session) -> dict[str, ListVersion]:
    """Read the active version of every shared list that has one.

    Returns:
        The active versions by list code.

    """
    rows = session.scalars(
        select(ListVersion).where(ListVersion.status == VersionStatus.ACTIVE)
    )
    return {row.list_code: row for row in rows}


def current_hashes(session: Session, list_code: str) -> dict[str, bytes]:
    """Read the content hash of every entry of the list's active version.

    Returns:
        The content hashes by entry key.

    """
    rows = session.execute(
        select(ListEntryRow.source_entry_key, ListEntryRow.content_hash).where(
            ListEntryRow.list_code == list_code, ListEntryRow.to_seq.is_(None)
        )
    )
    return dict(rows.all())


def version_members(session: Session, list_code: str, seq: int) -> Select[ListEntryRow]:
    """Build the query of the entry rows that make up version ``seq`` of a list.

    ``session`` is unused today; it keeps the signature of the other reads, so a
    caller can switch between them freely.

    Returns:
        The query, for the caller to run, page or stream.

    """
    _ = session
    return (
        select(ListEntryRow)
        .where(
            ListEntryRow.list_code == list_code,
            ListEntryRow.from_seq <= seq,
            or_(ListEntryRow.to_seq.is_(None), ListEntryRow.to_seq > seq),
        )
        .order_by(ListEntryRow.source_entry_key)
    )


def entry_of(row: ListEntryRow) -> ListEntry:
    """Rebuild the library entry a ``list_entry`` row stores.

    Returns:
        The entry, equal to the one that was ingested.

    """
    return ListEntry.from_json(row.entry)


def delta_page(
    session: Session, version_id: UUID, *, after_key: str | None, limit: int
) -> list[DeltaRowView]:
    """Read one page of a version's delta rows, in entry-key order.

    Returns:
        Up to ``limit`` rows whose key follows ``after_key`` (all when it is
        ``None``), each with the primary names of its previous and new entry rows.

    """
    prev = aliased(ListEntryRow)
    new = aliased(ListEntryRow)
    stmt = (
        select(
            ListEntryDelta.source_entry_key,
            ListEntryDelta.change,
            ListEntryDelta.prev_entry_id,
            ListEntryDelta.new_entry_id,
            prev.primary_name,
            new.primary_name,
        )
        .outerjoin(prev, prev.id == ListEntryDelta.prev_entry_id)
        .outerjoin(new, new.id == ListEntryDelta.new_entry_id)
        .where(ListEntryDelta.version_id == version_id)
        .order_by(ListEntryDelta.source_entry_key)
        .limit(limit)
    )
    if after_key is not None:
        stmt = stmt.where(ListEntryDelta.source_entry_key > after_key)
    return [
        DeltaRowView(
            source_entry_key=key,
            change=change,
            prev_entry_id=prev_id,
            new_entry_id=new_id,
            prev_name=prev_name,
            new_name=new_name,
        )
        for key, change, prev_id, new_id, prev_name, new_name in session.execute(stmt)
    ]


def enabled_subscriptions(session: Session) -> list[TenantListSubscription]:
    """Read the session tenant's enabled list subscriptions.

    Returns:
        The subscriptions, ordered by list code.

    """
    return list(
        session.scalars(
            select(TenantListSubscription)
            .where(TenantListSubscription.enabled)
            .order_by(TenantListSubscription.list_code)
        )
    )
