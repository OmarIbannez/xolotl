"""The in-memory index holder: shared and private shards, refresh, warm-up (SP-03 §8.1).

Whenever a test expects an exception from inside a session, ``pytest.raises`` is the
outermost context manager, so the error rolls the session back.
"""

import logging
import threading
from collections.abc import Iterable, Mapping
from typing import override

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from tests.helpers import platform_ctx, staff_session
from tests.modules.screening.catalog.support import (
    load_private,
    load_shared,
    seed_sources,
)
from tests.modules.screening.engine.conftest import UN, FakeMonotonic, un_entry
from xolotl.app import wire_platform
from xolotl.modules.screening.catalog.models import PrivateListEntryRow
from xolotl.modules.screening.engine.entries import (
    indexed_private_entry,
    indexed_shared_entry,
    shared_version_entries,
)
from xolotl.modules.screening.engine.holder import (
    PrivateShard,
    ScreeningIndexHolder,
    SharedShard,
    get_index_holder,
    set_index_holder,
)
from xolotl.modules.screening.errors import ScreeningIndexUnavailableError
from xolotl.modules.screening.events import STAFF_ONLY_EVENT_TYPES
from xolotl.modules.screening.lists import (
    EntityType,
    EntryIdentifier,
    EntryName,
    IdType,
    ListEntry,
    NameQuality,
)
from xolotl.modules.screening.matching import NameQuery, QueryIdentifier
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform.config import get_settings
from xolotl.platform.crypto.encrypted import Encrypted
from xolotl.platform.db import get_sessionmaker, tenant_session
from xolotl.platform.enums import Role
from xolotl.platform.events import is_staff_only
from xolotl.platform.storage.local import LocalFsObjectStore

LPB = "MX_LPB"


def _lpb_entry(key: str, name: str) -> ListEntry:
    return ListEntry(
        list_code=LPB,
        entry_key=key,
        entity_type=EntityType.PERSON,
        primary_name=EntryName(name, NameQuality.PRIMARY),
        identifiers=(EntryIdentifier(IdType.OTHER, f"X-{key}", f"X{key}"),),
        source_ref=f"clave {key}",
    )


def _mlro(tenant: CreatedTenant):
    return staff_session(tenant.tenant_id, tenant.admin_user_id, roles=(Role.MLRO,))


def _platform():
    return tenant_session(platform_ctx())


def _by_identifier(index_shard: PrivateShard, entry: ListEntry) -> list[ListEntry]:
    query = NameQuery(
        entity_type=EntityType.PERSON,
        names=("nadie",),
        identifiers=tuple(
            QueryIdentifier(i.id_type, i.normalized) for i in entry.identifiers
        ),
    )
    found = index_shard.index.candidates(query, shards={entry.list_code}, top_k=5)
    return [c.indexed.entry for c in found if c.via == "IDENTIFIER"]


def _private(
    holder: ScreeningIndexHolder, tenant: CreatedTenant
) -> PrivateShard | None:
    with _mlro(tenant) as s:
        return holder.private(s)


def _load_lpb(
    tenant: CreatedTenant, store: LocalFsObjectStore, entries: Iterable[ListEntry]
) -> None:
    with _mlro(tenant) as s:
        _ = load_private(s, store, LPB, entries)


def test_the_shared_shard_holds_the_active_versions_screenable_entries(
    holder, admin_engine: Engine, un_entries, store
):
    seed_sources(admin_engine)
    _ = load_shared(UN, un_entries, store=store)
    with _platform() as s:
        shard = holder.shared(s)
    held = shard.versions[UN]
    assert held.seq == 1
    assert held.list_code == UN
    assert held.list_kind == "SANCTION"
    assert set(shard.entries[UN]) == set(un_entries[:2])
    assert len(shard.index) == 2


def test_a_newer_version_is_picked_up_after_the_refresh_interval(
    holder, admin_engine: Engine, un_entries, store, fake_monotonic: FakeMonotonic
):
    seed_sources(admin_engine)
    _ = load_shared(UN, un_entries, store=store)
    with _platform() as s:
        first = holder.shared(s)
    _ = load_shared(UN, (*un_entries, un_entry("1004", "Nuevo Listado")), store=store)
    with _platform() as s:
        assert holder.shared(s) is first
    fake_monotonic.advance(31)
    with _platform() as s:
        second = holder.shared(s)
    assert second.versions[UN].seq == 2
    assert len(second.entries[UN]) == 3
    # Nothing changed since: the next check keeps the same shard.
    fake_monotonic.advance(31)
    with _platform() as s:
        assert holder.shared(s) is second


def test_a_required_version_forces_a_refresh_at_once(
    holder, admin_engine: Engine, un_entries, store
):
    seed_sources(admin_engine)
    _ = load_shared(UN, un_entries, store=store)
    with _platform() as s:
        _ = holder.shared(s)
    _ = load_shared(UN, (*un_entries, un_entry("1004", "Nuevo Listado")), store=store)
    with _platform() as s:
        shard = holder.shared(s, require={UN: 2})
        # An older version than the one held is satisfied by the newer shard.
        assert holder.shared(s, require={UN: 1}) is shard
    assert shard.versions[UN].seq == 2


def test_a_required_version_that_does_not_exist_is_unavailable(
    holder, admin_engine: Engine, un_entries, store
):
    seed_sources(admin_engine)
    _ = load_shared(UN, un_entries, store=store)
    with pytest.raises(ScreeningIndexUnavailableError) as raised, _platform() as s:
        _ = holder.shared(s, require={UN: 9})
    assert raised.value.status == 503
    assert raised.value.code == "SCREENING_INDEX_UNAVAILABLE"
    with pytest.raises(ScreeningIndexUnavailableError), _platform() as s:
        _ = holder.shared(s, require={"EU_FSF": 1})


WAIT = 30


class _GatedFactory:
    """A session factory that, once armed, holds its caller until released."""

    def __init__(self) -> None:
        self.armed: bool = False
        self.entered: threading.Event = threading.Event()
        self.release: threading.Event = threading.Event()

    def __call__(self) -> Session:
        if self.armed:
            self.entered.set()
            if not self.release.wait(timeout=WAIT):
                msg = "the test never released the factory"
                raise TimeoutError(msg)
        return get_sessionmaker()()


class _WatchedClock(FakeMonotonic):
    """A fake clock that says when a named thread first reads it."""

    def __init__(self) -> None:
        super().__init__()
        self.read_by: dict[str, threading.Event] = {}

    @override
    def __call__(self) -> float:
        if (read := self.read_by.get(threading.current_thread().name)) is not None:
            read.set()
        return super().__call__()


def _screen_in_thread(
    holder: ScreeningIndexHolder,
    name: str,
    *,
    require: Mapping[str, int] | None = None,
) -> tuple[threading.Thread, list[SharedShard]]:
    got: list[SharedShard] = []

    def screen() -> None:
        with _platform() as s:
            got.append(holder.shared(s, require=require))

    thread = threading.Thread(target=screen, name=name, daemon=True)
    thread.start()
    return thread, got


@pytest.fixture
def gated(engine: Engine) -> tuple[ScreeningIndexHolder, _GatedFactory, _WatchedClock]:
    factory = _GatedFactory()
    clock = _WatchedClock()
    held = ScreeningIndexHolder(
        factory, refresh_seconds=30, private_shards=4, monotonic=clock
    )
    return held, factory, clock


def test_a_screen_keeps_the_previous_shard_while_another_rebuilds(
    gated, admin_engine: Engine, un_entries, store
):
    holder, factory, clock = gated
    seed_sources(admin_engine)
    _ = load_shared(UN, un_entries, store=store)
    with _platform() as s:
        first = holder.shared(s)
    _ = load_shared(UN, (*un_entries, un_entry("1004", "Nuevo Listado")), store=store)
    clock.advance(31)
    factory.armed = True
    rebuild, rebuilt = _screen_in_thread(holder, "screen-a")
    try:
        assert factory.entered.wait(timeout=WAIT)
        # Due again while the rebuild is held: this screen does not wait for it.
        clock.advance(31)
        with _platform() as s:
            assert holder.shared(s) is first
    finally:
        factory.release.set()
        rebuild.join(timeout=WAIT)
    assert not rebuild.is_alive()
    assert [shard.versions[UN].seq for shard in rebuilt] == [2]
    with _platform() as s:
        assert holder.shared(s) is rebuilt[0]


def test_a_screen_requiring_the_new_version_waits_for_the_rebuild(
    gated, admin_engine: Engine, un_entries, store
):
    holder, factory, clock = gated
    seed_sources(admin_engine)
    _ = load_shared(UN, un_entries, store=store)
    with _platform() as s:
        _ = holder.shared(s)
    _ = load_shared(UN, (*un_entries, un_entry("1004", "Nuevo Listado")), store=store)
    clock.advance(31)
    factory.armed = True
    rebuild, rebuilt = _screen_in_thread(holder, "screen-a")
    waiter_read = clock.read_by["screen-b"] = threading.Event()
    try:
        assert factory.entered.wait(timeout=WAIT)
        # The waiter has taken the old shard and found it lacking before the swap,
        # so it waits for the rebuild rather than starting its own.
        waiter, waited = _screen_in_thread(holder, "screen-b", require={UN: 2})
        assert waiter_read.wait(timeout=WAIT)
    finally:
        factory.release.set()
        rebuild.join(timeout=WAIT)
    waiter.join(timeout=WAIT)
    assert not rebuild.is_alive()
    assert not waiter.is_alive()
    assert len(rebuilt) == 1
    assert waited == rebuilt
    assert rebuilt[0].versions[UN].seq == 2


def test_a_holder_still_warming_answers_unavailable(holder, monkeypatch):
    monkeypatch.setattr(holder, "warm", lambda: None)
    holder.start_warming().join()
    with pytest.raises(ScreeningIndexUnavailableError), _platform() as s:
        _ = holder.shared(s)


def test_warming_loads_the_shared_shard_in_a_daemon_thread(
    holder, admin_engine: Engine, un_entries, store
):
    seed_sources(admin_engine)
    _ = load_shared(UN, un_entries, store=store)
    thread = holder.start_warming()
    assert thread.daemon
    thread.join(timeout=30)
    with _platform() as s:
        assert holder.shared(s).versions[UN].seq == 1


def test_a_failed_warm_up_is_logged_and_loads_on_the_next_screen(
    engine: Engine, admin_engine: Engine, un_entries, store, caplog
):
    seed_sources(admin_engine)
    _ = load_shared(UN, un_entries, store=store)
    calls: list[int] = []

    def flaky() -> Session:
        calls.append(1)
        if len(calls) == 1:
            msg = "database down"
            raise RuntimeError(msg)
        return get_sessionmaker()()

    holder = ScreeningIndexHolder(
        flaky, refresh_seconds=30, private_shards=4, monotonic=FakeMonotonic()
    )
    with caplog.at_level(logging.ERROR):
        holder.start_warming().join(timeout=30)
    assert "screening index warm-up failed" in caplog.text
    with _platform() as s:
        assert holder.shared(s).versions[UN].seq == 1


def test_a_private_shard_is_built_per_tenant_and_invalidated_by_a_new_version(
    holder, tenant: CreatedTenant, other_tenant: CreatedTenant, store
):
    entries = (_lpb_entry("1", "Ximena Tlalocana"), _lpb_entry("2", "Zacarias Ocelotl"))
    _load_lpb(tenant, store, entries)
    first = _private(holder, tenant)
    assert first is not None
    assert first.tenant_id == tenant.tenant_id
    assert first.versions[LPB].seq == 1
    assert first.versions[LPB].list_kind == "BLOCKED"
    assert len(first.index) == 2
    assert _private(holder, tenant) is first
    assert _private(holder, other_tenant) is None
    _load_lpb(tenant, store, entries[:1])
    second = _private(holder, tenant)
    assert second is not None
    assert second is not first
    assert second.versions[LPB].seq == 2
    assert len(second.index) == 1


def test_private_shards_are_evicted_beyond_the_configured_number(
    engine: Engine, tenant: CreatedTenant, other_tenant: CreatedTenant, store
):
    holder = ScreeningIndexHolder(
        get_sessionmaker(),
        refresh_seconds=30,
        private_shards=1,
        monotonic=FakeMonotonic(),
    )
    _load_lpb(tenant, store, [_lpb_entry("1", "Ximena Tlalocana")])
    _load_lpb(other_tenant, store, [_lpb_entry("1", "Ximena Tlalocana")])
    first = _private(holder, tenant)
    assert _private(holder, tenant) is first
    other = _private(holder, other_tenant)
    assert other is not None
    assert other.tenant_id == other_tenant.tenant_id
    again = _private(holder, tenant)
    assert again is not None
    assert again is not first


def test_private_entries_are_decrypted_only_inside_their_tenants_session(
    holder, tenant: CreatedTenant, store
):
    entry = _lpb_entry("7", "Fantasmagorica Quetzalcoyotl")
    _load_lpb(tenant, store, [entry])
    with _mlro(tenant) as s:
        row = s.scalars(select(PrivateListEntryRow)).one()
        assert isinstance(row.entry, Encrypted)
        indexed = indexed_private_entry(s, row, LPB)
        shard = holder.private(s)
    assert indexed.entry == entry
    assert indexed.ref == row.id
    assert indexed.shard == LPB
    assert shard is not None
    assert _by_identifier(shard, entry) == [entry]


def test_scoring_idf_is_cached_per_list_set_key(
    holder, admin_engine: Engine, un_entries, store
):
    seed_sources(admin_engine)
    _ = load_shared(UN, un_entries, store=store)
    with _platform() as s:
        shard = holder.shared(s)
    first = holder.scoring_idf(shard, [UN], key=b"set-a")
    assert holder.scoring_idf(shard, [UN], key=b"set-a") is first
    # The same list set under another key is another table: the cache goes by key.
    other = holder.scoring_idf(shard, [UN], key=b"set-b")
    assert other is not first


def test_shared_entries_are_rebuilt_from_their_rows(
    holder, admin_engine: Engine, un_entries, store
):
    seed_sources(admin_engine)
    _ = load_shared(UN, un_entries, store=store)
    _ = load_shared(UN, (*un_entries, un_entry("1004", "Nuevo Listado")), store=store)
    with _platform() as s:
        v1 = shared_version_entries(s, UN, 1)
        v2 = shared_version_entries(s, UN, 2)
    assert [r.source_entry_key for r in v1] == ["1001", "1002"]
    assert sorted(r.source_entry_key for r in v2) == ["1001", "1002", "1004"]
    assert [r.id for r in v2] == sorted(r.id for r in v2)
    indexed = indexed_shared_entry(v1[0])
    assert indexed.ref == v1[0].id
    assert indexed.shard == UN
    assert indexed.entry == un_entries[0]


def test_get_index_holder_needs_register_screening():
    previous = get_index_holder() if _installed() else None
    set_index_holder(None)
    try:
        with pytest.raises(RuntimeError, match="register_screening"):
            _ = get_index_holder()
    finally:
        set_index_holder(previous)


def _installed() -> bool:
    try:
        _ = get_index_holder()
    except RuntimeError:
        return False
    return True


def test_register_screening_marks_every_staff_only_type(engine: Engine):
    _ = wire_platform(get_sessionmaker(), get_settings())
    assert all(is_staff_only(t) for t in STAFF_ONLY_EVENT_TYPES)
    assert isinstance(get_index_holder(), ScreeningIndexHolder)


def test_wire_platform_does_not_start_a_thread(engine: Engine):
    before = threading.active_count()
    _ = wire_platform(get_sessionmaker(), get_settings())
    assert threading.active_count() == before
