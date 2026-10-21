"""The in-memory index holder: shared and private shards, refresh, warm-up (SP-03 §8.1).

Whenever a test expects an exception from inside a session, ``pytest.raises`` is the
outermost context manager, so the error rolls the session back.
"""

import gc
import logging
import threading
import weakref
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

import pytest
from sqlalchemy import Engine, event, select
from sqlalchemy.orm import Session

from tests.helpers import platform_ctx, staff_session
from tests.modules.screening.catalog.support import (
    load_private,
    load_shared,
    seed_sources,
)
from tests.modules.screening.engine.conftest import UN, FakeMonotonic, un_entry
from xolotl.app import wire_platform
from xolotl.modules.screening.catalog.models import ListEntryRow, PrivateListEntryRow
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


def test_the_shared_shard_streams_its_rows_without_loading_orm_objects(
    holder, admin_engine: Engine, un_entries, store
):
    # 40,000 rows loaded as ORM objects cost more than the index built from them
    # (tests/perf/test_screening_index_memory.py): the read takes three columns
    # through a server-side cursor, and no ListEntryRow ever reaches the session.
    seed_sources(admin_engine)
    _ = load_shared(UN, un_entries, store=store)
    loaded: list[object] = []
    streamed: list[bool] = []

    def on_load(target: object, _context: object) -> None:
        loaded.append(target)

    def on_execute(conn, _cursor, statement: str, _params, context, _many) -> None:
        if "FROM list_entry" in statement:
            streamed.append(bool(context.execution_options.get("stream_results")))

    event.listen(ListEntryRow, "load", on_load)
    event.listen(Engine, "before_cursor_execute", on_execute)
    try:
        with _platform() as s:
            shard = holder.shared(s)
    finally:
        event.remove(ListEntryRow, "load", on_load)
        event.remove(Engine, "before_cursor_execute", on_execute)
    assert len(shard.index) == 2
    assert loaded == []
    assert streamed == [True]


def test_the_shared_shard_keeps_one_copy_of_each_entry(
    holder, admin_engine: Engine, un_entries, store
):
    # The per-list entries for the scoring IDF are the very objects the index
    # holds, not a second copy of the lists.
    seed_sources(admin_engine)
    _ = load_shared(UN, un_entries, store=store)
    with _platform() as s:
        shard = holder.shared(s)
    for listed in shard.entries[UN]:
        query = NameQuery(
            entity_type=EntityType.PERSON,
            names=("nadie",),
            identifiers=tuple(
                QueryIdentifier(i.id_type, i.normalized) for i in listed.identifiers
            ),
        )
        found = shard.index.candidates(query, shards={UN}, top_k=5)
        assert [c.indexed.entry for c in found] == [listed]
        assert found[0].indexed.entry is listed


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


class _WatchedLock:
    """The holder's lock, saying when a named thread starts waiting for it."""

    def __init__(self) -> None:
        self.lock: threading.Lock = threading.Lock()
        self.waited_by: dict[str, threading.Event] = {}

    def acquire(self, *, blocking: bool = True) -> bool:
        name = threading.current_thread().name
        if blocking and (waits := self.waited_by.get(name)) is not None:
            waits.set()
        return self.lock.acquire(blocking=blocking)

    def release(self) -> None:
        self.lock.release()

    def __enter__(self) -> bool:
        return self.acquire()

    def __exit__(self, *_: object) -> None:
        self.release()


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


@dataclass(frozen=True, slots=True)
class _Gated:
    holder: ScreeningIndexHolder
    factory: _GatedFactory
    clock: FakeMonotonic
    lock: _WatchedLock


@pytest.fixture
def gated(engine: Engine, monkeypatch) -> _Gated:
    factory = _GatedFactory()
    clock = FakeMonotonic()
    held = ScreeningIndexHolder(
        factory, refresh_seconds=30, private_shards=4, monotonic=clock
    )
    lock = _WatchedLock()
    monkeypatch.setattr(held, "_lock", lock)
    return _Gated(held, factory, clock, lock)


def test_a_rebuild_frees_the_old_shard_first_and_screens_wait_for_the_new_one(
    gated: _Gated, admin_engine: Engine, un_entries, store
):
    holder, factory = gated.holder, gated.factory
    seed_sources(admin_engine)
    _ = load_shared(UN, un_entries, store=store)
    with _platform() as s:
        first = weakref.ref(holder.shared(s).index)
    _ = load_shared(UN, (*un_entries, un_entry("1004", "Nuevo Listado")), store=store)
    gated.clock.advance(31)
    factory.armed = True
    rebuild, rebuilt = _screen_in_thread(holder, "screen-a")
    waits = gated.lock.waited_by["screen-b"] = threading.Event()
    waiter = None
    try:
        assert factory.entered.wait(timeout=WAIT)
        # The rebuild is about to read the new shard: the old one is gone already,
        # so the process never holds both.
        _ = gc.collect()
        assert first() is None
        # A screen arriving meanwhile, due or not, waits for the new shard.
        gated.clock.advance(31)
        waiter, waited = _screen_in_thread(holder, "screen-b")
        assert waits.wait(timeout=WAIT)
    finally:
        factory.release.set()
        rebuild.join(timeout=WAIT)
        if waiter is not None:
            waiter.join(timeout=WAIT)
    assert waiter is not None
    assert not rebuild.is_alive()
    assert not waiter.is_alive()
    assert [shard.versions[UN].seq for shard in rebuilt] == [2]
    assert waited == rebuilt
    with _platform() as s:
        assert holder.shared(s) is rebuilt[0]


def test_a_screen_requiring_the_new_version_waits_for_the_rebuild(
    gated: _Gated, admin_engine: Engine, un_entries, store
):
    holder, factory = gated.holder, gated.factory
    seed_sources(admin_engine)
    _ = load_shared(UN, un_entries, store=store)
    with _platform() as s:
        _ = holder.shared(s)
    _ = load_shared(UN, (*un_entries, un_entry("1004", "Nuevo Listado")), store=store)
    gated.clock.advance(31)
    factory.armed = True
    rebuild, rebuilt = _screen_in_thread(holder, "screen-a")
    waits = gated.lock.waited_by["screen-b"] = threading.Event()
    waiter = None
    try:
        assert factory.entered.wait(timeout=WAIT)
        # The waiter waits for the rebuild under way rather than starting its own.
        waiter, waited = _screen_in_thread(holder, "screen-b", require={UN: 2})
        assert waits.wait(timeout=WAIT)
    finally:
        factory.release.set()
        rebuild.join(timeout=WAIT)
        if waiter is not None:
            waiter.join(timeout=WAIT)
    assert waiter is not None
    assert not rebuild.is_alive()
    assert not waiter.is_alive()
    assert len(rebuilt) == 1
    assert waited == rebuilt
    assert rebuilt[0].versions[UN].seq == 2


def test_a_due_screen_keeps_the_installed_shard_while_another_checks_for_versions(
    gated: _Gated, admin_engine: Engine, un_entries, store
):
    # A check that finds nothing new rebuilds nothing; a due screen arriving while
    # it holds the lock does not wait for it.
    holder = gated.holder
    seed_sources(admin_engine)
    _ = load_shared(UN, un_entries, store=store)
    with _platform() as s:
        first = holder.shared(s)
    gated.clock.advance(31)
    with gated.lock, _platform() as s:
        assert holder.shared(s) is first


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
