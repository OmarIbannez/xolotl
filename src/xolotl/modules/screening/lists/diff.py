"""The delta between two versions of one list (D18, FR-SC-002).

The previous version is given as ``entry_key -> content_hash`` (read from Postgres,
never re-parsed). A change of any field changes the hash, so an entry that turns
non-screenable (69-B ``DESVIRTUADO``) is ``CHANGED``; the engine treats that as a
delisting.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from xolotl.modules.screening.lists.errors import ListFormatError
from xolotl.modules.screening.lists.types import ListEntry


@dataclass(frozen=True, slots=True)
class ListDelta:
    """Entry keys per change, each tuple sorted."""

    added: tuple[str, ...]
    changed: tuple[str, ...]
    removed: tuple[str, ...]
    unchanged: int


def diff(previous: Mapping[str, bytes], current: Iterable[ListEntry]) -> ListDelta:
    """Compare a new version against the previous one's content hashes.

    Returns:
        The added, changed and removed keys and the unchanged count.

    Raises:
        ListFormatError: ``current`` repeats an entry key.

    """
    added: list[str] = []
    changed: list[str] = []
    seen: set[str] = set()
    unchanged = 0
    for entry in current:
        key = entry.entry_key
        if key in seen:
            msg = f"{entry.list_code}: duplicate entry key {key!r} in the new version"
            raise ListFormatError(msg)
        seen.add(key)
        before = previous.get(key)
        if before is None:
            added.append(key)
        elif before != entry.content_hash():
            changed.append(key)
        else:
            unchanged += 1
    removed = [key for key in previous if key not in seen]
    return ListDelta(
        added=tuple(sorted(added)),
        changed=tuple(sorted(changed)),
        removed=tuple(sorted(removed)),
        unchanged=unchanged,
    )
