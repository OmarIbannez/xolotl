"""Normalized names of list entries, cached because every query re-reads them."""

from functools import lru_cache

from xolotl.modules.screening.lists import EntityType, EntryName, ListEntry, NameQuality
from xolotl.modules.screening.matching.normalize import NormalizedName, normalize_name

_CACHE_SIZE = 1 << 18


@lru_cache(maxsize=_CACHE_SIZE)
def _normalized(name: EntryName, entity_type: EntityType) -> NormalizedName:
    return normalize_name(
        name.full, entity_type=entity_type, given=name.given, surname=name.surname
    )


def entry_names(entry: ListEntry) -> list[tuple[NormalizedName, NameQuality]]:
    """Normalize the primary name and every alias of an entry.

    Returns:
        ``(name, quality)`` pairs, primary name first, empty names dropped.

    """
    pairs = [
        (_normalized(name, entry.entity_type), name.quality)
        for name in (entry.primary_name, *entry.aliases)
    ]
    return [(name, quality) for name, quality in pairs if name.tokens]
