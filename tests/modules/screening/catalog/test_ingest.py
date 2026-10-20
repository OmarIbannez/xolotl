"""Shared list ingestion: versions, entry history, deltas, the guard, raw files."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import IntegrityError

from tests.helpers import platform_ctx
from tests.modules.screening.catalog.support import (
    UN_V1,
    UN_V2,
    ingest_fixture,
    load_shared,
    seed_sources,
)
from xolotl.modules.screening.catalog import (
    ListEntryDelta,
    ListEntryRow,
    ListSource,
    ListVersion,
)
from xolotl.modules.screening.catalog.ingest import (
    IngestReport,
    file_kind,
    ingest_file,
    ingest_parsed,
)
from xolotl.modules.screening.catalog.queries import (
    active_versions,
    current_hashes,
    entry_of,
    version_members,
)
from xolotl.modules.screening.enums import DeltaChange, VersionStatus
from xolotl.modules.screening.errors import ListMassRemovalError
from xolotl.modules.screening.lists import (
    EntityType,
    EntryName,
    ListEntry,
    ListFormatError,
    NameQuality,
    ParsedList,
    parse,
)
from xolotl.platform.config import get_settings
from xolotl.platform.db import tenant_session
from xolotl.platform.errors import NotFoundError
from xolotl.platform.ids import uuid7
from xolotl.platform.storage import SHARED_NAMESPACE
from xolotl.platform.storage.local import LocalFsObjectStore

UN = "UN_CONSOLIDATED"
FETCHED_AT = datetime(2026, 10, 19, 23, 30, tzinfo=UTC)


@pytest.fixture
def store(tmp_path: Path) -> LocalFsObjectStore:
    return LocalFsObjectStore(tmp_path / "objects")


@pytest.fixture
def seeded(admin_engine: Engine) -> Engine:
    seed_sources(admin_engine)
    return admin_engine


def _versions(code: str = UN) -> list[ListVersion]:
    with tenant_session(platform_ctx()) as s:
        return list(
            s.scalars(
                select(ListVersion)
                .where(ListVersion.list_code == code)
                .order_by(ListVersion.seq)
            )
        )


def _rows(code: str = UN) -> list[ListEntryRow]:
    with tenant_session(platform_ctx()) as s:
        return list(
            s.scalars(
                select(ListEntryRow)
                .where(ListEntryRow.list_code == code)
                .order_by(ListEntryRow.source_entry_key, ListEntryRow.from_seq)
            )
        )


def _deltas(version_id: UUID | None) -> dict[str, ListEntryDelta]:
    with tenant_session(platform_ctx()) as s:
        return {
            d.source_entry_key: d
            for d in s.scalars(
                select(ListEntryDelta).where(ListEntryDelta.version_id == version_id)
            )
        }


def _source(code: str = UN) -> ListSource:
    with tenant_session(platform_ctx()) as s:
        source = s.get(ListSource, code)
        assert source is not None
        return source


def _parsed(path: Path) -> ParsedList:
    return parse(UN, BytesIO(path.read_bytes()))


def _v1_keys() -> set[str]:
    return {e.entry_key for e in _parsed(UN_V1).entries}


def _objects(store: LocalFsObjectStore) -> list[str]:
    return [ref.key for ref in store.list(SHARED_NAMESPACE, "lists/")]


def _entry(key: str, name: str) -> ListEntry:
    return ListEntry(
        list_code=UN,
        entry_key=key,
        entity_type=EntityType.PERSON,
        primary_name=EntryName(full=name, quality=NameQuality.STRONG),
    )


def test_file_kind_sniffs_the_bytes():
    assert file_kind(b"<?xml version='1.0'?><a/>") == ("xml", "application/xml")
    assert file_kind(b"\xef\xbb\xbf  <root/>") == ("xml", "application/xml")
    assert file_kind(b"PK\x03\x04rest") == ("zip", "application/zip")
    assert file_kind(b"a,b\n1,2\n") == ("csv", "text/csv")


def test_the_first_version_is_all_added_and_active(seeded, store):
    report = ingest_fixture(UN, UN_V1, store=store)
    [version] = _versions()
    assert report == IngestReport(
        list_code=UN,
        version_id=version.id,
        seq=1,
        entry_count=30,
        added=30,
        changed=0,
        removed=0,
        unchanged=0,
        parser_version="un-xml/1",
        same_file=False,
    )
    assert version.status is VersionStatus.ACTIVE
    assert version.previous_version_id is None
    assert version.loaded_by == "cli:test"
    assert version.file_size == UN_V1.stat().st_size
    assert version.storage_key == f"lists/{UN}/{version.id}.xml"
    assert version.published_at == datetime(2026, 10, 19, 23, 0, 3, 500000, UTC)
    deltas = _deltas(version.id)
    assert set(deltas) == _v1_keys()
    assert {d.change for d in deltas.values()} == {DeltaChange.ADDED}
    assert _objects(store) == [version.storage_key]
    source = _source()
    assert source.last_success_at is not None
    assert source.last_change_at == source.last_success_at
    assert source.next_poll_at == source.last_success_at + source.poll_interval
    assert source.consecutive_failures == 0


def test_the_same_file_again_makes_no_version_and_refreshes_freshness(
    seeded, store, admin_engine
):
    _ = ingest_fixture(UN, UN_V1, store=store)
    with admin_engine.begin() as conn:
        _ = conn.execute(
            text(
                """
                UPDATE list_source SET last_error = 'HTTP_503',
                    consecutive_failures = 3, last_success_at = NULL
                WHERE code = 'UN_CONSOLIDATED'
                """
            )
        )
    first_change = _source().last_change_at
    report = ingest_fixture(UN, UN_V1, store=store)
    assert report.same_file
    assert report.version_id is None
    assert report.seq is None
    assert report.entry_count == 30
    assert report.unchanged == 30
    assert len(_versions()) == 1
    source = _source()
    assert source.last_error is None
    assert source.consecutive_failures == 0
    assert source.last_success_at is not None
    assert source.last_change_at == first_change
    assert len(_objects(store)) == 1


def test_a_second_version_supersedes_the_first_atomically(seeded, store):
    first = ingest_fixture(UN, UN_V1, store=store)
    second = ingest_fixture(UN, UN_V2, store=store)
    v1, v2 = _versions()
    assert (v1.seq, v1.status) == (1, VersionStatus.SUPERSEDED)
    assert (v2.seq, v2.status) == (2, VersionStatus.ACTIVE)
    assert v2.previous_version_id == first.version_id == v1.id
    assert (second.added, second.changed, second.removed, second.unchanged) == (
        1,
        1,
        1,
        28,
    )
    assert (v2.entry_count, v2.added, v2.changed, v2.removed) == (30, 1, 1, 1)
    with tenant_session(platform_ctx()) as s:
        assert {code: v.seq for code, v in active_versions(s).items()} == {UN: 2}
    assert len(_objects(store)) == 2


def test_a_changed_entry_closes_its_row_and_opens_a_new_one(seeded, store):
    _ = ingest_fixture(UN, UN_V1, store=store)
    report = ingest_fixture(UN, UN_V2, store=store)
    old, new = [row for row in _rows() if row.source_entry_key == "6908052"]
    assert (old.from_seq, old.to_seq) == (1, 2)
    assert (new.from_seq, new.to_seq) == (2, None)
    assert old.content_hash != new.content_hash
    delta = _deltas(report.version_id)["6908052"]
    assert delta.change is DeltaChange.CHANGED
    assert (delta.prev_entry_id, delta.new_entry_id) == (old.id, new.id)


def test_a_removed_entry_is_closed_and_an_unchanged_one_keeps_its_row(seeded, store):
    _ = ingest_fixture(UN, UN_V1, store=store)
    report = ingest_fixture(UN, UN_V2, store=store)
    rows = _rows()
    [removed] = [row for row in rows if row.source_entry_key == "6908048"]
    assert (removed.from_seq, removed.to_seq) == (1, 2)
    [kept] = [row for row in rows if row.source_entry_key == "6908021"]
    assert (kept.from_seq, kept.to_seq) == (1, None)
    [added] = [row for row in rows if row.source_entry_key == "9999001"]
    assert (added.from_seq, added.to_seq) == (2, None)
    deltas = _deltas(report.version_id)
    assert {key: d.change for key, d in deltas.items()} == {
        "6908048": DeltaChange.REMOVED,
        "6908052": DeltaChange.CHANGED,
        "9999001": DeltaChange.ADDED,
    }
    assert (deltas["6908048"].prev_entry_id, deltas["6908048"].new_entry_id) == (
        removed.id,
        None,
    )
    assert (deltas["9999001"].prev_entry_id, deltas["9999001"].new_entry_id) == (
        None,
        added.id,
    )
    assert len(rows) == 32


def test_version_members_are_the_entries_of_that_version(seeded, store):
    _ = ingest_fixture(UN, UN_V1, store=store)
    _ = ingest_fixture(UN, UN_V2, store=store)
    v2_keys = {e.entry_key for e in _parsed(UN_V2).entries}
    with tenant_session(platform_ctx()) as s:
        one = s.scalars(version_members(s, UN, 1)).all()
        two = s.scalars(version_members(s, UN, 2)).all()
        hashes = current_hashes(s, UN)
    assert {row.source_entry_key for row in one} == _v1_keys()
    assert len(one) == 30
    assert {row.source_entry_key for row in two} == v2_keys
    assert len(two) == 30
    assert hashes == {row.source_entry_key: row.content_hash for row in two}


def test_entries_round_trip_through_the_stored_json(seeded, store):
    _ = ingest_fixture(UN, UN_V1, store=store)
    parsed = {e.entry_key: e for e in _parsed(UN_V1).entries}
    rows = _rows()
    assert len(rows) == len(parsed)
    for row in rows:
        entry = parsed[row.source_entry_key]
        assert entry_of(row) == entry
        assert row.content_hash == entry.content_hash()
        assert row.entry == json.loads(entry.canonical_json())
        assert row.entity_type == entry.entity_type.value
        assert row.primary_name == entry.primary_name.full
        assert row.screenable == entry.screenable


def test_a_format_error_writes_nothing(seeded, store):
    data = UN_V1.read_bytes()
    with (
        pytest.raises(ListFormatError),
        tenant_session(platform_ctx()) as s,
    ):
        _ = ingest_file(
            s,
            UN,
            data[: len(data) // 2],
            store=store,
            loaded_by="cli:test",
            fetched_at=FETCHED_AT,
        )
    assert _versions() == []
    assert _rows() == []
    assert _objects(store) == []
    assert _source().last_success_at is None


def _ingest_entries(
    store: LocalFsObjectStore, entries: list[ListEntry], *, accept_drop: bool = False
) -> IngestReport:
    parsed = ParsedList(
        list_code=UN,
        parser_version="test/1",
        published_at=None,
        source_version=None,
        entries=tuple(entries),
    )
    data = b"\n".join(e.canonical_json() for e in entries)
    with tenant_session(platform_ctx()) as s:
        return ingest_parsed(
            s,
            parsed,
            data=data,
            store=store,
            loaded_by="test",
            fetched_at=FETCHED_AT,
            accept_drop=accept_drop,
        )


def test_the_guard_refuses_above_the_ratio_unless_accepted(seeded, store):
    assert get_settings().lists_max_removal_ratio == Decimal("0.20")
    entries = list(_parsed(UN_V1).entries)
    _ = ingest_fixture(UN, UN_V1, store=store)

    kept = entries[:21]  # 70 %: 9 of 30 removed
    with pytest.raises(ListMassRemovalError) as refused:
        _ = _ingest_entries(store, kept)
    assert refused.value.code == "LIST_MASS_REMOVAL"
    assert refused.value.status == 409
    assert refused.value.detail == "removed 9 of 30"
    assert len(_versions()) == 1
    assert len(_objects(store)) == 1

    accepted = _ingest_entries(store, kept, accept_drop=True)
    assert (accepted.seq, accepted.removed, accepted.entry_count) == (2, 9, 21)

    # Back to the 30 entries (nothing removed), then remove exactly 20 % of them.
    back = _ingest_entries(store, entries)
    assert back.seq == 3
    exactly = _ingest_entries(store, entries[:24])  # 6 of 30 removed: 20 %
    assert (exactly.seq, exactly.removed) == (4, 6)


def test_an_unknown_code_is_not_found(seeded, store, admin_engine):
    with pytest.raises(NotFoundError), tenant_session(platform_ctx()) as s:
        _ = ingest_file(
            s,
            "NOPE",
            UN_V1.read_bytes(),
            store=store,
            loaded_by="cli:test",
            fetched_at=FETCHED_AT,
        )
    with admin_engine.begin() as conn:
        _ = conn.execute(
            text("UPDATE list_source SET enabled = false WHERE code = :code"),
            {"code": UN},
        )
    with pytest.raises(NotFoundError), tenant_session(platform_ctx()) as s:
        _ = ingest_file(
            s,
            UN,
            UN_V1.read_bytes(),
            store=store,
            loaded_by="cli:test",
            fetched_at=FETCHED_AT,
        )
    assert _objects(store) == []


def test_a_failed_transaction_leaves_the_active_version(seeded, store, admin_engine):
    first = ingest_fixture(UN, UN_V1, store=store)
    # A row that collides with the new row v2 opens for its changed entry, so the
    # insert fails after the raw file is stored and the previous version superseded.
    with admin_engine.begin() as conn:
        _ = conn.execute(
            text(
                """
                INSERT INTO list_entry (id, list_code, source_entry_key, content_hash,
                    entity_type, primary_name, screenable, entry, from_seq, to_seq)
                VALUES (:id, 'UN_CONSOLIDATED', '6908052', :hash, 'INDIVIDUAL',
                    'x', true, '{}', 2, 2)
                """
            ),
            {"id": uuid7(), "hash": b"\x00" * 32},
        )
    with pytest.raises(IntegrityError):
        _ = ingest_fixture(UN, UN_V2, store=store)
    [v1] = _versions()
    assert (v1.id, v1.status) == (first.version_id, VersionStatus.ACTIVE)
    assert all(row.to_seq is None for row in _rows() if row.from_seq == 1)


def test_ingest_parsed_writes_hand_built_entries(seeded, store):
    first = load_shared(
        UN, [_entry("A", "ALPHA ONE"), _entry("B", "BRAVO TWO")], store=store
    )
    second = load_shared(
        UN,
        [_entry("A", "ALPHA ONE"), _entry("B", "BRAVO 2"), _entry("C", "CHARLIE")],
        store=store,
    )
    assert (first.seq, first.added, first.parser_version) == (1, 2, "test/1")
    assert (second.seq, second.added, second.changed, second.unchanged) == (2, 1, 1, 1)
    assert {k: d.change for k, d in _deltas(second.version_id).items()} == {
        "B": DeltaChange.CHANGED,
        "C": DeltaChange.ADDED,
    }
    v1, v2 = _versions()
    assert v1.loaded_by == v2.loaded_by == "test"
    assert v2.storage_key.endswith(".csv")
