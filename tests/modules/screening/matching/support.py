"""Builders for matcher tests."""

from decimal import Decimal
from uuid import UUID

from xolotl.modules.screening.lists import (
    EntityType,
    EntryIdentifier,
    EntryName,
    IdType,
    ListEntry,
    NameQuality,
    PartialDate,
)
from xolotl.modules.screening.matching import BandThresholds, IndexedEntry

THRESHOLDS = BandThresholds(alert=Decimal("0.70"), review=Decimal("0.55"))


def entry(
    key: str,
    name: str,
    *aliases: str,
    entity_type: EntityType = EntityType.PERSON,
    list_code: str = "UN_CONSOLIDATED",
    birth_dates: tuple[PartialDate, ...] = (),
    nationalities: tuple[str, ...] = (),
    gender: str | None = None,
    identifiers: tuple[tuple[IdType, str], ...] = (),
    weak: tuple[str, ...] = (),
    screenable: bool = True,
) -> ListEntry:
    return ListEntry(
        list_code=list_code,
        entry_key=key,
        entity_type=entity_type,
        primary_name=EntryName(name, NameQuality.PRIMARY),
        aliases=tuple(EntryName(a, NameQuality.STRONG) for a in aliases)
        + tuple(EntryName(a, NameQuality.WEAK) for a in weak),
        birth_dates=birth_dates,
        nationalities=nationalities,
        gender=gender,
        identifiers=tuple(
            EntryIdentifier(id_type=t, value=v, normalized=v) for t, v in identifiers
        ),
        screenable=screenable,
    )


def indexed(item: ListEntry, number: int) -> IndexedEntry:
    return IndexedEntry(entry=item, ref=UUID(int=number), shard=item.list_code)
