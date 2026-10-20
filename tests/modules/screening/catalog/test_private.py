"""Private lists: creation, encrypted versions with deltas, audit, event, isolation."""

import logging
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path
from uuid import UUID

import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, select

from tests.helpers import (
    audit_events,
    data_key_of,
    everything_stored,
    leaked,
    new_tenant,
    staff_session,
)
from tests.modules.screening.catalog.support import load_private
from xolotl.modules.screening.catalog import (
    PrivateList,
    PrivateListEntryDelta,
    PrivateListEntryRow,
    PrivateListVersion,
    active_private_versions,
    create_private_list,
    private_delta,
    private_entry_of,
    upload_private_version,
)
from xolotl.modules.screening.catalog.template import CSV_TYPE, XLSX_TYPE
from xolotl.modules.screening.enums import DeltaChange, ListKind, VersionStatus
from xolotl.modules.screening.errors import (
    PrivateListExistsError,
    PrivateListInvalidError,
)
from xolotl.modules.screening.events import (
    EVENT_SOURCE,
    PRIVATE_LIST_VERSION_ACTIVATED,
)
from xolotl.modules.screening.lists import (
    EntityType,
    EntryIdentifier,
    EntryName,
    IdType,
    ListDelta,
    ListEntry,
    NameQuality,
    PartialDate,
)
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform.config import get_settings
from xolotl.platform.crypto import aad_for, decrypt_value
from xolotl.platform.enums import KeyPurpose, Role
from xolotl.platform.errors import NotFoundError, ValidationFailedError
from xolotl.platform.events import is_staff_only
from xolotl.platform.events.event import event_from_row
from xolotl.platform.events.models import OutboxRow
from xolotl.platform.ids import uuid7
from xolotl.platform.storage.local import LocalFsObjectStore

PRIVATE = Path(__file__).parent / "fixtures" / "private"
V1 = (PRIVATE / "lpb_v1.csv").read_bytes()
V2 = (PRIVATE / "lpb_v2.csv").read_bytes()
LPB = "MX_LPB"
# Values of lpb_v1.csv that must never be stored or logged in the clear.
PLAINTEXT = (
    "Ximena",
    "Quetzalcoyotl",
    "Tlalocana",
    "QUTX800101AB2",
    "QUTX800101MDFLLM03",
    "Fantasmagorica",
    "FCO010203XY7",
    "Zacarias",
    "Ocelotlmetztli",
    "PAS-12345",
    "Observacion confidencial",
    "nota secreta",
)


@pytest.fixture
def store(tmp_path: Path) -> LocalFsObjectStore:
    return LocalFsObjectStore(tmp_path / "objects")


def _mlro(tenant: CreatedTenant):
    return staff_session(tenant.tenant_id, tenant.admin_user_id, roles=(Role.MLRO,))


def _lpb(tenant: CreatedTenant) -> UUID:
    with _mlro(tenant) as s:
        return create_private_list(
            s, list_code=LPB, title="Lista de personas bloqueadas", list_kind=None
        ).id


def _upload(
    tenant: CreatedTenant,
    store: LocalFsObjectStore,
    list_id: UUID,
    data: bytes,
    content_type: str = CSV_TYPE,
) -> PrivateListVersion:
    with _mlro(tenant) as s:
        return upload_private_version(
            s, store, list_id=list_id, data=data, content_type=content_type
        )


def _versions(tenant: CreatedTenant) -> list[PrivateListVersion]:
    with _mlro(tenant) as s:
        return list(
            s.scalars(select(PrivateListVersion).order_by(PrivateListVersion.seq))
        )


def _rows(tenant: CreatedTenant) -> list[PrivateListEntryRow]:
    with _mlro(tenant) as s:
        return list(
            s.scalars(
                select(PrivateListEntryRow).order_by(
                    PrivateListEntryRow.source_entry_key, PrivateListEntryRow.from_seq
                )
            )
        )


def _deltas(
    tenant: CreatedTenant, version_id: UUID
) -> dict[str, PrivateListEntryDelta]:
    with _mlro(tenant) as s:
        return {
            d.source_entry_key: d
            for d in s.scalars(
                select(PrivateListEntryDelta).where(
                    PrivateListEntryDelta.version_id == version_id
                )
            )
        }


def _objects(store_root: Path, *, records: bool = False) -> list[Path]:
    # The stored objects; with ``records`` also the ``.meta.json`` record of each.
    if not store_root.exists():
        return []
    return [
        p
        for p in store_root.rglob("*")
        if p.is_file() and (records or not p.name.endswith(".meta.json"))
    ]


def _actions(tenant: CreatedTenant) -> list[str]:
    return [e.action for e in audit_events(tenant.tenant_id)]


def _entry(key: str, name: str) -> ListEntry:
    return ListEntry(
        list_code=LPB,
        entry_key=key,
        entity_type=EntityType.PERSON,
        primary_name=EntryName(name, NameQuality.PRIMARY),
        birth_dates=(PartialDate(1980, 1),),
        identifiers=(EntryIdentifier(IdType.OTHER, "X-1", "X1"),),
        source_ref=f"clave {key}",
    )


def test_create_mx_lpb_once_and_internal_lists_by_slug(tenant):
    lpb_id = _lpb(tenant)
    with _mlro(tenant) as s:
        peps = create_private_list(
            s, list_code="INTERNAL_PEPS_2026", title="PEPs", list_kind=ListKind.PEP
        )
        assert peps.list_kind is ListKind.PEP
        assert peps.created_by == str(tenant.admin_user_id)
        internal = create_private_list(
            s, list_code="INTERNAL_X", title=" Propia ", list_kind=None
        )
        assert (internal.list_kind, internal.title) == (ListKind.INTERNAL, "Propia")
    with _mlro(tenant) as s:
        lpb = s.scalar(select(PrivateList).where(PrivateList.id == lpb_id))
        assert lpb is not None
        assert lpb.list_kind is ListKind.BLOCKED
    refused = [
        (LPB, None, "otra", PrivateListExistsError),
        ("INTERNAL_bad-slug", None, "x", ValidationFailedError),
        ("OFAC_SDN", None, "x", ValidationFailedError),
        ("MX_LPB", ListKind.SANCTION, "x", ValidationFailedError),
        ("INTERNAL_Y", ListKind.BLOCKED, "x", ValidationFailedError),
        ("INTERNAL_Z", None, "  ", ValidationFailedError),
    ]
    for code, kind, title, error in refused:
        with pytest.raises(error), _mlro(tenant) as s:
            _ = create_private_list(s, list_code=code, title=title, list_kind=kind)
    assert PrivateListExistsError().status == 409
    assert _actions(tenant).count("private_list.created") == 3
    created = [
        e.detail
        for e in audit_events(tenant.tenant_id)
        if e.action == "private_list.created"
    ]
    assert created[0] == {"list_code": LPB, "list_kind": "BLOCKED"}


def test_a_refused_duplicate_leaves_the_session_usable(tenant):
    _ = _lpb(tenant)
    with _mlro(tenant) as s:
        with pytest.raises(PrivateListExistsError):
            _ = create_private_list(s, list_code=LPB, title="otra", list_kind=None)
        later = create_private_list(
            s, list_code="INTERNAL_A", title="A", list_kind=None
        )
        assert later.list_kind is ListKind.INTERNAL
    with _mlro(tenant) as s:
        assert sorted(s.scalars(select(PrivateList.list_code))) == ["INTERNAL_A", LPB]


def test_concurrent_uploads_to_one_list_take_turns(tenant, store):
    list_id = _lpb(tenant)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with _mlro(tenant) as s:
            first = upload_private_version(
                s, store, list_id=list_id, data=V1, content_type=CSV_TYPE
            ).seq
            waiting = pool.submit(_upload, tenant, store, list_id, V2)
            done, _ = wait([waiting], timeout=0.5)
            assert not done  # it waits for the list lock this upload holds
        second = waiting.result(timeout=15).seq
    assert (first, second) == (1, 2)
    assert [(v.seq, v.status) for v in _versions(tenant)] == [
        (1, VersionStatus.SUPERSEDED),
        (2, VersionStatus.ACTIVE),
    ]


def test_an_upload_creates_an_active_version_with_all_added(tenant, store):
    list_id = _lpb(tenant)
    version = _upload(tenant, store, list_id, V1, "text/csv; charset=utf-8")
    assert (version.seq, version.status, version.entry_count) == (
        1,
        VersionStatus.ACTIVE,
        3,
    )
    assert (version.added, version.changed, version.removed) == (3, 0, 0)
    assert version.uploaded_by == str(tenant.admin_user_id)
    assert version.content_type == CSV_TYPE
    assert version.file_size == len(V1)
    assert version.storage_key == f"private-lists/{list_id}/{version.id}"
    rows = _rows(tenant)
    assert [(r.from_seq, r.to_seq) for r in rows] == [(1, None)] * 3
    keys = {r.source_entry_key for r in rows}
    assert {"LPB-001", "LPB-002"} < keys
    deltas = _deltas(tenant, version.id)
    assert {d.change for d in deltas.values()} == {DeltaChange.ADDED}
    assert set(deltas) == keys
    uploaded = [
        e
        for e in audit_events(tenant.tenant_id)
        if e.action == "private_list.version_uploaded"
    ]
    assert [e.detail for e in uploaded] == [
        {
            "list_id": str(list_id),
            "version_id": str(version.id),
            "seq": 1,
            "entry_count": 3,
            "added": 3,
            "changed": 0,
            "removed": 0,
        }
    ]


def test_an_xlsx_upload_gives_the_same_tags_as_the_csv(tenant, store):
    list_id = _lpb(tenant)
    _ = _upload(tenant, store, list_id, V1)
    second = _upload(
        tenant, store, list_id, (PRIVATE / "lpb_v1.xlsx").read_bytes(), XLSX_TYPE
    )
    # Same content, so nothing changes; the upload still makes a version.
    assert (second.seq, second.added, second.changed, second.removed) == (2, 0, 0, 0)
    assert second.content_type == XLSX_TYPE
    assert [(r.from_seq, r.to_seq) for r in _rows(tenant)] == [(1, None)] * 3


def test_a_new_private_version_supersedes_the_previous(tenant, store):
    list_id = _lpb(tenant)
    first = _upload(tenant, store, list_id, V1)
    keyed = next(
        r.source_entry_key
        for r in _rows(tenant)
        if not r.source_entry_key.startswith("LPB")
    )
    second = _upload(tenant, store, list_id, V2)
    assert [(v.seq, v.status) for v in _versions(tenant)] == [
        (1, VersionStatus.SUPERSEDED),
        (2, VersionStatus.ACTIVE),
    ]
    assert (second.entry_count, second.added, second.changed, second.removed) == (
        3,
        1,
        1,
        1,
    )
    rows = {(r.source_entry_key, r.from_seq): r for r in _rows(tenant)}
    assert sorted((key, f, r.to_seq) for (key, f), r in rows.items()) == sorted([
        ("LPB-001", 1, None),
        ("LPB-002", 1, 2),
        ("LPB-002", 2, None),
        ("LPB-004", 2, None),
        (keyed, 1, 2),
    ])
    deltas = _deltas(tenant, second.id)
    assert deltas["LPB-004"].change is DeltaChange.ADDED
    assert deltas["LPB-004"].new_entry_id == rows["LPB-004", 2].id
    assert deltas["LPB-002"].change is DeltaChange.CHANGED
    assert deltas["LPB-002"].prev_entry_id == rows["LPB-002", 1].id
    assert deltas["LPB-002"].new_entry_id == rows["LPB-002", 2].id
    assert deltas[keyed].change is DeltaChange.REMOVED
    assert deltas[keyed].new_entry_id is None
    assert "LPB-001" not in deltas
    assert len(_deltas(tenant, first.id)) == 3


def test_an_invalid_upload_creates_nothing(tenant, store, tmp_path, monkeypatch):
    list_id = _lpb(tenant)
    with pytest.raises(PrivateListInvalidError) as caught:
        _ = _upload(tenant, store, list_id, (PRIVATE / "bad_rows.csv").read_bytes())
    assert caught.value.status == 422
    assert len(caught.value.errors) == 10
    for data, content_type in [
        (b"", CSV_TYPE),
        (V1, "application/pdf"),
    ]:
        with pytest.raises(ValidationFailedError):
            _ = _upload(tenant, store, list_id, data, content_type)
    monkeypatch.setattr(get_settings(), "private_list_max_mb", 1)
    with pytest.raises(ValidationFailedError, match="too large"):
        _ = _upload(tenant, store, list_id, V1 + b" " * (1024 * 1024))
    with pytest.raises(NotFoundError):
        _ = _upload(tenant, store, uuid7(), V1)
    assert _versions(tenant) == []
    assert _rows(tenant) == []
    assert _objects(tmp_path / "objects") == []
    assert "private_list.version_uploaded" not in _actions(tenant)


def test_no_plaintext_of_an_upload_is_stored(
    tenant, store, admin_engine: Engine, tmp_path, caplog
):
    caplog.set_level(logging.DEBUG)
    list_id = _lpb(tenant)
    version = _upload(tenant, store, list_id, V1)
    stored = everything_stored(admin_engine)
    (sealed_path,) = _objects(tmp_path / "objects")
    sealed = sealed_path.read_bytes()
    files = b"".join(
        p.read_bytes() for p in _objects(tmp_path / "objects", records=True)
    )
    logged = "\n".join(r.getMessage() for r in caplog.records)
    for value in PLAINTEXT:
        assert value not in stored, value
        assert value.encode() not in files, value
        assert value not in logged, value
        assert leaked(stored, value.encode()) == []
    assert sealed_path.as_posix().endswith(f"private-lists/{list_id}/{version.id}")
    plain = decrypt_value(
        data_key_of(admin_engine, tenant.tenant_id),
        ciphertext=sealed,
        aad=aad_for(tenant.tenant_id, KeyPurpose.DATA),
    )
    assert plain == V1


def test_content_tags_differ_across_tenants_for_one_file(tenant, store):
    other = new_tenant("beta", "admin@beta.mx")
    for who in (tenant, other):
        _ = _upload(who, store, _lpb(who), V1)
    mine = {r.source_entry_key: r.content_tag for r in _rows(tenant)}
    theirs = {r.source_entry_key: r.content_tag for r in _rows(other)}
    assert mine["LPB-001"] != theirs["LPB-001"]
    # The keyed tag of the row without a clave is per tenant too.
    assert set(mine) != set(theirs)
    assert set(mine) & set(theirs) == {"LPB-001", "LPB-002"}


def test_the_activation_event_is_staff_only_and_carries_ids(
    tenant, store, app: FastAPI
):
    del app  # composing the application registers the staff-only type
    list_id = _lpb(tenant)
    version = _upload(tenant, store, list_id, V1)
    with _mlro(tenant) as s:
        rows = s.scalars(
            select(OutboxRow).where(OutboxRow.type == PRIVATE_LIST_VERSION_ACTIVATED)
        ).all()
        events = [event_from_row(row) for row in rows]
    (event,) = events
    assert event.source == EVENT_SOURCE
    assert event.key == str(list_id)
    assert event.subject == f"private_list_version/{version.id}"
    assert event.data == {
        "private_list_id": str(list_id),
        "version_id": str(version.id),
    }
    assert is_staff_only(PRIVATE_LIST_VERSION_ACTIVATED)


def test_private_entry_of_decrypts_what_was_uploaded(tenant, store):
    entries = [_entry("K-1", "Persona Sintetica Uno"), _entry("K-2", "Persona Dos")]
    with _mlro(tenant) as s:
        version = load_private(s, store, LPB, entries)
        again = load_private(s, store, LPB, entries[:1])
    assert (version.seq, again.seq, again.removed) == (1, 2, 1)
    with _mlro(tenant) as s:
        rows = s.scalars(
            select(PrivateListEntryRow).order_by(PrivateListEntryRow.source_entry_key)
        ).all()
        decrypted = [private_entry_of(s, row) for row in rows]
    assert decrypted == entries
    with pytest.raises(ValueError, match="share an entry key"), _mlro(tenant) as s:
        _ = load_private(s, store, LPB, [entries[0], entries[0]])


def test_another_tenant_sees_no_private_list_rows(tenant, store):
    _ = _upload(tenant, store, _lpb(tenant), V1)
    other = new_tenant("beta", "admin@beta.mx")
    with _mlro(tenant) as s:
        ((mine, active),) = active_private_versions(s)
        assert (mine.list_code, active.seq) == (LPB, 1)
    with _mlro(other) as s:
        for model in (
            PrivateList,
            PrivateListVersion,
            PrivateListEntryRow,
            PrivateListEntryDelta,
        ):
            assert s.scalars(select(model)).all() == []
        assert active_private_versions(s) == []


def test_private_delta_classifies_added_changed_removed():
    previous = {"a": b"1", "b": b"2", "c": b"3"}
    current = {"a": b"1", "b": b"9", "d": b"4"}
    assert private_delta(previous, current) == ListDelta(
        added=("d",), changed=("b",), removed=("c",), unchanged=1
    )
    assert private_delta({}, {}) == ListDelta((), (), (), 0)
