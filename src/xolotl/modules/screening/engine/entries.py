"""List entry rows as the matcher's indexed entries (SP-03 spec §8.1).

A shared row carries its entry as plain JSON; a private row carries it encrypted
under the tenant's DATA key and is decrypted in the tenant's own session, for
processing only (D6): no ``VIEW_PII`` is recorded, because no person sees it.
"""

from sqlalchemy.orm import Session

from xolotl.modules.screening.catalog.models import ListEntryRow, PrivateListEntryRow
from xolotl.modules.screening.catalog.private import private_entry_of
from xolotl.modules.screening.catalog.queries import entry_of, version_members
from xolotl.modules.screening.matching import IndexedEntry


def indexed_shared_entry(row: ListEntryRow) -> IndexedEntry:
    """Wrap a shared entry row for the index, under its list code.

    Returns:
        The entry, with the row's id as its reference.

    """
    return IndexedEntry(entry=entry_of(row), ref=row.id, shard=row.list_code)


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
