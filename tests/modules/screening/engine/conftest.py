"""Fixtures of the screening engine tests: hand-built entries and a fresh holder."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine

from tests.helpers import new_tenant
from xolotl.modules.screening.engine.holder import (
    ScreeningIndexHolder,
    set_index_holder,
)
from xolotl.modules.screening.lists import (
    EntityType,
    EntryIdentifier,
    EntryName,
    IdType,
    ListEntry,
    NameQuality,
)
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform.db import get_sessionmaker
from xolotl.platform.storage.local import LocalFsObjectStore

UN = "UN_CONSOLIDATED"


class FakeMonotonic:
    """A monotonic clock the test moves by hand."""

    def __init__(self) -> None:
        self.now: float = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def un_entry(key: str, name: str, *, screenable: bool = True) -> ListEntry:
    return ListEntry(
        list_code=UN,
        entry_key=key,
        entity_type=EntityType.PERSON,
        primary_name=EntryName(name, NameQuality.PRIMARY),
        identifiers=(EntryIdentifier(IdType.PASSPORT, f"P-{key}", f"P{key}"),),
        screenable=screenable,
        source_ref=f"un {key}",
    )


@pytest.fixture
def un_entries() -> tuple[ListEntry, ...]:
    return (
        un_entry("1001", "Abu Hamza Tlalocan"),
        un_entry("1002", "Ximena Quetzalli Ocotlan"),
        un_entry("1003", "Desvirtuado Ejemplo Nadie", screenable=False),
    )


@pytest.fixture
def store(tmp_path: Path) -> LocalFsObjectStore:
    return LocalFsObjectStore(tmp_path / "objects")


@pytest.fixture
def fake_monotonic() -> FakeMonotonic:
    return FakeMonotonic()


@pytest.fixture
def holder(
    engine: Engine, fake_monotonic: FakeMonotonic
) -> Iterator[ScreeningIndexHolder]:
    held = ScreeningIndexHolder(
        get_sessionmaker(),
        refresh_seconds=30,
        private_shards=64,
        monotonic=fake_monotonic,
    )
    set_index_holder(held)
    yield held
    set_index_holder(None)


@pytest.fixture
def other_tenant(engine: Engine) -> CreatedTenant:
    return new_tenant("globex", "admin@globex.mx")
