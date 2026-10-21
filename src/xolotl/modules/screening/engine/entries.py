"""List entry rows as the matcher's indexed entries (SP-03 spec §8.1).

A shared row carries its entry as plain JSON; a private row carries it encrypted
under the tenant's DATA key and is decrypted in the tenant's own session, for
processing only (D6): no ``VIEW_PII`` is recorded, because no person sees it.
"""

from collections.abc import Collection, Iterator

from sqlalchemy import select
from sqlalchemy.orm import Session

from xolotl.modules.screening.catalog.models import ListEntryRow, PrivateListEntryRow
from xolotl.modules.screening.catalog.private import private_entry_of
from xolotl.modules.screening.catalog.queries import entry_of, version_members
from xolotl.modules.screening.lists import ListEntry
from xolotl.modules.screening.matching import IndexedEntry

# Rows a shard read fetches from its server-side cursor at a time.
SHARD_READ_CHUNK = 1_000


def indexed_shared_entry(row: ListEntryRow) -> IndexedEntry:
    """Wrap a shared entry row for the index, under its list code.

    Returns:
        The entry, with the row's id as its reference.

    """
    return IndexedEntry(entry=entry_of(row), ref=row.id, shard=row.list_code)


def current_shared_entries(
    s: Session, list_codes: Collection[str]
) -> Iterator[IndexedEntry]:
    """Stream the screenable current entries of shared lists, ordered by list and id.

    The current rows of a list with an active version are that version's members
    (3a's construction). Only the three columns an indexed entry needs are read,
    through a server-side cursor ``SHARD_READ_CHUNK`` rows at a time, so neither
    the driver's result nor the session's identity map ever holds the whole list.

    Yields:
        Each entry, with its row's id as the reference and its list code as shard.

    """
    rows = s.execute(
        select(ListEntryRow.id, ListEntryRow.list_code, ListEntryRow.entry)
        .where(
            ListEntryRow.list_code.in_(list_codes),
            ListEntryRow.to_seq.is_(None),
            ListEntryRow.screenable,
        )
        .order_by(ListEntryRow.list_code, ListEntryRow.id)
        .execution_options(yield_per=SHARD_READ_CHUNK)
    )
    for ref, list_code, entry in rows:
        yield IndexedEntry(entry=ListEntry.from_json(entry), ref=ref, shard=list_code)


def indexed_private_entry(
    s: Session, row: PrivateListEntryRow, list_code: str
) -> IndexedEntry:
    """Decrypt a private entry row in ``s`` and wrap it for the index.

    Returns:
        The entry, with the row's id as its reference and ``list_code`` as its shard.

    """
    return IndexedEntry(entry=private_entry_of(s, row), ref=row.id, shard=list_code)


def shared_version_entries(s: Session, list_code: str, seq: int) -> list[ListEntryRow]:
    """Read the screenable members of version ``seq`` of a shared list.

    Returns:
        The rows ``from_seq <= seq`` and ``to_seq`` null or above ``seq``, ordered
        by id.

    """
    query = (
        version_members(s, list_code, seq)
        .where(ListEntryRow.screenable)
        .order_by(None)
        .order_by(ListEntryRow.id)
    )
    return list(s.scalars(query))
