"""Object store (spec 8.4): the port, the local adapter on its suite, the wiring.

The adapter suite is written against the port and parametrised by backend, so an S3
adapter joins it by adding a backend. The last tests cover how the composition root
picks the adapter.
"""

import hashlib
import json
import os
import tempfile
from collections.abc import Callable
from dataclasses import FrozenInstanceError, dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, cast
from uuid import UUID

import pytest

from xolotl.app import build_app, build_object_store
from xolotl.platform.api.app import create_app
from xolotl.platform.config import Settings, get_settings
from xolotl.platform.db import get_sessionmaker
from xolotl.platform.errors import NotFoundError, ValidationFailedError, XolotlError
from xolotl.platform.storage import (
    NO_METADATA,
    InvalidObjectKeyError,
    InvalidObjectMetadataError,
    ObjectIntegrityError,
    ObjectRef,
    ObjectStore,
    parse_digest,
    validate_content_type,
    validate_key,
    validate_metadata,
    validate_prefix,
    verify_integrity,
)
from xolotl.platform.storage.local import LocalFsObjectStore

if TYPE_CHECKING:
    from collections.abc import MutableMapping

TENANT_A = UUID("0199c1b2-0000-7000-8000-00000000000a")
TENANT_B = UUID("0199c1b2-0000-7000-8000-00000000000b")
TENANT_C = UUID("0199c1b2-0000-7000-8000-00000000000c")
PAYLOAD = b"constancia de situacion fiscal"
SIZE = len(PAYLOAD)
DIGEST = hashlib.sha256(PAYLOAD).digest()


def make_ref(
    *,
    tenant_id=TENANT_A,
    key="docs/2026/10/file.pdf",
    sha256=DIGEST,
    size=SIZE,
    content_type="application/pdf",
) -> ObjectRef:
    return ObjectRef(
        tenant_id=tenant_id,
        key=key,
        sha256=sha256,
        size=size,
        content_type=content_type,
    )


# ---------------------------------------------------------------- errors and refs


def test_integrity_error_is_a_500_with_a_stable_code():
    exc = ObjectIntegrityError()
    assert isinstance(exc, XolotlError)
    assert (exc.status, exc.code) == (500, "OBJECT_CORRUPT")
    assert exc.detail


def test_invalid_key_error_is_a_validation_failure():
    exc = InvalidObjectKeyError()
    assert isinstance(exc, ValidationFailedError)
    assert (exc.status, exc.code) == (422, "VALIDATION_FAILED")
    assert exc.detail == "invalid object key"


def test_invalid_metadata_error_is_a_validation_failure():
    exc = InvalidObjectMetadataError()
    assert isinstance(exc, ValidationFailedError)
    assert (exc.status, exc.code) == (422, "VALIDATION_FAILED")
    assert exc.detail == "invalid object metadata"


def test_object_ref_is_frozen_and_hashable():
    ref = make_ref()
    attribute = "key"
    with pytest.raises(FrozenInstanceError):
        setattr(ref, attribute, "docs/other.pdf")
    assert {ref, make_ref()} == {ref}


# ------------------------------------------------------------------------- keys


@pytest.mark.parametrize(
    "key",
    [
        "docs/2026/10/file.pdf",
        "a",
        "report.pdf",
        "documentos/identificación/INE-ñandú.pdf",
        "a b/c d",
        "...",
        "..a",
        "a.meta.jsonx",
        "a.meta/b.txt",
        pytest.param("a" * 512, id="512-characters"),
    ],
)
def test_validate_key_accepts_and_returns_safe_keys(key):
    assert validate_key(key) == key


@pytest.mark.parametrize(
    "key",
    [
        "../x",
        "/abs",
        "a//b",
        "a/./b",
        "",
        "x\\y",
        pytest.param("a" * 513, id="513-characters"),
        "a/",
        "a\x00b",
        "x.meta.json",
        "docs/x.META.JSON",
        "x.meta.json/y",
        pytest.param("caf\ud800", id="lone-surrogate"),
    ],
)
def test_validate_key_rejects_unsafe_keys(key):
    with pytest.raises(InvalidObjectKeyError):
        _ = validate_key(key)


def test_a_rejected_key_is_never_echoed_in_the_error():
    with pytest.raises(InvalidObjectKeyError) as excinfo:
        _ = validate_key("../secret-tenant-folder")
    assert "secret-tenant-folder" not in str(excinfo.value)
    assert "secret-tenant-folder" not in excinfo.value.detail


# --------------------------------------------------------------------- prefixes


@pytest.mark.parametrize(
    "prefix",
    [
        "",
        "docs",
        "docs/",
        "docs/2026",
        "docs/2026/",
        "doc",
        "a b/",
        "ñ",
        "a/..",
        "x" * 512,
    ],
)
def test_validate_prefix_accepts_directory_and_partial_name_prefixes(prefix):
    assert validate_prefix(prefix) == prefix


@pytest.mark.parametrize(
    "prefix",
    [
        "../",
        "a/../b",
        "/abs",
        "a//b",
        "a/./b",
        "x.meta.json/",
        "x\\y",
        "a\x00b",
        pytest.param("x" * 513, id="513-characters"),
        pytest.param("caf\ud800", id="lone-surrogate"),
    ],
)
def test_validate_prefix_rejects_anything_that_could_leave_the_tenant(prefix):
    with pytest.raises(InvalidObjectKeyError):
        _ = validate_prefix(prefix)


# ----------------------------------------------------- content type and metadata


@pytest.mark.parametrize(
    "content_type",
    [
        "application/pdf",
        "text/plain; charset=utf-8",
        "image/svg+xml",
        "application/vnd.api+json",
        "a/b",
        "text/plain;name=ñandú",
        pytest.param("a/" + "b" * 253, id="255-characters"),
    ],
)
def test_validate_content_type_accepts_media_types(content_type):
    assert validate_content_type(content_type) == content_type


@pytest.mark.parametrize(
    "content_type",
    [
        "",
        "pdf",
        "text/",
        "/plain",
        "text//plain",
        "text /plain",
        "text/plain extra",
        "text/plain\n",
        "text/plain\r\nX-Injected: 1",
        "text/plain;\x00",
        pytest.param("text/plain;a=\ud800", id="lone-surrogate"),
        pytest.param("a/" + "b" * 254, id="256-characters"),
    ],
)
def test_validate_content_type_rejects_anything_else(content_type):
    with pytest.raises(InvalidObjectMetadataError):
        _ = validate_content_type(content_type)


def test_no_metadata_is_an_empty_immutable_mapping():
    assert len(NO_METADATA) == 0
    shared = cast("MutableMapping[str, str]", NO_METADATA)
    with pytest.raises(TypeError):
        shared["late"] = "edit"


def test_validate_metadata_returns_a_plain_copy():
    original = {"filename": "ine.pdf", "uploaded-by": "staff 7"}
    checked = validate_metadata(original)
    assert checked == original
    assert checked is not original
    assert validate_metadata(NO_METADATA) == {}


@pytest.mark.parametrize(
    "metadata",
    [
        {"filename": "identificación de Juan Pérez.pdf", "note": "🙂 ok", "empty": ""},
        {"a" * 64: "longest name"},
        {"k": "v" * 1024},
        {f"k{n}": "v" for n in range(32)},
        dict.fromkeys("abcdefgh", "x" * 1023),  # 8 * (1 + 1023) = 8192 bytes
    ],
    ids=["unicode", "64-character-name", "1024-character-value", "32-entries", "8-kib"],
)
def test_validate_metadata_accepts_text_within_the_bounds(metadata):
    assert validate_metadata(metadata) == metadata


@pytest.mark.parametrize(
    "metadata",
    [
        {"Filename": "x"},
        {"": "x"},
        {"a" * 65: "x"},
        {"file name": "x"},
        {"filéname": "x"},
        {"k": "line\nbreak"},
        {"k": "tab\there"},
        {"k": "nul\x00"},
        {"k": "lone \ud800"},
        {"k": "v" * 1025},
        {f"k{n}": "v" for n in range(33)},
        {**dict.fromkeys("abcdefg", "x" * 1023), "h": "x" * 1024},
        dict.fromkeys("abcd", "ñ" * 1024),
    ],
    ids=[
        "uppercase-name",
        "empty-name",
        "65-character-name",
        "space-in-name",
        "non-ascii-name",
        "newline-in-value",
        "tab-in-value",
        "nul-in-value",
        "lone-surrogate-in-value",
        "1025-character-value",
        "33-entries",
        "one-byte-over-8-kib",
        "counted-in-bytes-not-characters",
    ],
)
def test_validate_metadata_rejects_what_it_should_not_store(metadata):
    with pytest.raises(InvalidObjectMetadataError):
        _ = validate_metadata(metadata)


def test_a_rejected_metadata_value_is_never_echoed_in_the_error():
    with pytest.raises(InvalidObjectMetadataError) as excinfo:
        _ = validate_metadata({"filename": "identificación\nde-Juan-Pérez.pdf"})
    assert "Juan" not in str(excinfo.value)
    assert "filename" not in str(excinfo.value)


# ---------------------------------------------------------------------- digests


def test_parse_digest_reads_a_lowercase_hex_sha256():
    assert parse_digest(DIGEST.hex()) == DIGEST


@pytest.mark.parametrize(
    "text",
    [None, "", "zz" * 32, DIGEST.hex()[:-2], DIGEST.hex() + "00", DIGEST.hex().upper()],
    ids=["missing", "empty", "not-hex", "short", "long", "uppercase"],
)
def test_parse_digest_refuses_anything_else_as_corruption(text):
    with pytest.raises(ObjectIntegrityError):
        _ = parse_digest(text)


def test_verify_integrity_returns_the_bytes_when_the_digest_matches():
    assert verify_integrity(make_ref(), PAYLOAD) == PAYLOAD


@pytest.mark.parametrize(
    ("data", "digest"),
    [
        (PAYLOAD + b"!", DIGEST),
        (b"", DIGEST),
        (PAYLOAD, hashlib.sha256(b"other").digest()),
        (PAYLOAD, DIGEST[:-1]),
        (PAYLOAD, b""),
    ],
    ids=["altered-bytes", "emptied-bytes", "other-digest", "short-digest", "no-digest"],
)
def test_verify_integrity_refuses_any_mismatch(data, digest):
    with pytest.raises(ObjectIntegrityError) as excinfo:
        _ = verify_integrity(make_ref(sha256=digest), data)
    assert (excinfo.value.status, excinfo.value.code) == (500, "OBJECT_CORRUPT")


# ------------------------------------------------- every adapter, one suite


@dataclass(frozen=True)
class Backend:
    """An adapter, plus the two things a test must do behind its back."""

    store: ObjectStore
    # Replace the stored bytes without touching the adapter's own record of them.
    overwrite: Callable[[UUID, str, bytes], None]
    # The metadata the adapter persisted for an object.
    stored_metadata: Callable[[UUID, str], dict[str, str]]


def local_backend(root: Path) -> Backend:
    def overwrite(tenant_id: UUID, key: str, data: bytes) -> None:
        _ = (root / str(tenant_id) / key).write_bytes(data)

    def stored_metadata(tenant_id: UUID, key: str) -> dict[str, str]:
        record = json.loads(
            (root / str(tenant_id) / f"{key}.meta.json").read_text(encoding="utf-8")
        )
        return record["metadata"]

    return Backend(LocalFsObjectStore(root), overwrite, stored_metadata)


@pytest.fixture(params=["local"])
def backend(request, tmp_path) -> Backend:
    # Another adapter joins the suite by adding its name here and a backend for it.
    return local_backend(tmp_path / "objects")


def put_text(store, tenant_id, key, text, **kwargs) -> ObjectRef:
    return store.put(tenant_id, key, text.encode(), content_type="text/plain", **kwargs)


def test_put_then_get_returns_the_bytes_and_a_ref_that_describes_them(backend):
    ref = backend.store.put(
        TENANT_A, "docs/2026/10/file.pdf", PAYLOAD, content_type="application/pdf"
    )
    assert ref == make_ref()
    assert backend.store.get(ref) == PAYLOAD


@pytest.mark.parametrize(
    "data",
    [b"", bytes(range(256)) * 64, b"\x00\xff\r\n\x00"],
    ids=["empty", "every-byte-value", "control-bytes"],
)
def test_any_bytes_round_trip(backend, data):
    ref = backend.store.put(
        TENANT_A, "blob.bin", data, content_type="application/octet-stream"
    )
    assert (ref.size, ref.sha256) == (len(data), hashlib.sha256(data).digest())
    assert backend.store.get(ref) == data


def test_keys_may_be_unicode(backend):
    key = "documentos/identificación/INE-ñandú.pdf"
    ref = backend.store.put(TENANT_A, key, PAYLOAD, content_type="application/pdf")
    assert ref.key == key
    assert backend.store.get(ref) == PAYLOAD
    assert list(backend.store.list(TENANT_A, "documentos/")) == [ref]


def test_putting_under_an_existing_key_replaces_the_object(backend):
    store = backend.store
    old = put_text(store, TENANT_A, "doc.txt", "first")
    new = put_text(store, TENANT_A, "doc.txt", "second version")
    assert store.get(new) == b"second version"
    assert list(store.list(TENANT_A, "")) == [new]
    # A stale ref fails its checksum rather than being served the new bytes.
    with pytest.raises(ObjectIntegrityError):
        _ = store.get(old)


KEYS = [
    "root.txt",
    "docs/2026/10/b.pdf",
    "docs/2026/10/a.pdf",
    "docs/2026/9/z.pdf",
    "docs/2026-notes.txt",
    "documentos/ñandú.pdf",
]


@pytest.fixture
def populated(backend) -> dict[str, ObjectRef]:
    return {key: put_text(backend.store, TENANT_A, key, key) for key in KEYS}


def test_list_yields_every_object_of_the_tenant_in_key_order(backend, populated):
    listed = list(backend.store.list(TENANT_A, ""))
    assert listed == [populated[key] for key in sorted(KEYS)]
    assert [backend.store.get(ref) for ref in listed] == [
        key.encode() for key in sorted(KEYS)
    ]


@pytest.mark.parametrize(
    ("prefix", "expected"),
    [
        ("docs/", 4),
        ("docs/2026/", 3),
        ("docs/2026/10/", 2),
        ("docs/2026", 4),
        ("doc", 5),
        ("docs/2026/10/a", 1),
        ("root.txt", 1),
        ("root.txt/", 0),
        ("nothing", 0),
    ],
)
def test_list_matches_on_the_characters_of_the_key(
    backend, populated, prefix, expected
):
    listed = list(backend.store.list(TENANT_A, prefix))
    assert [ref.key for ref in listed] == sorted(
        key for key in KEYS if key.startswith(prefix)
    )
    assert len(listed) == expected


def test_listing_a_tenant_without_objects_is_empty(backend):
    assert list(backend.store.list(TENANT_B, "")) == []


def test_delete_removes_the_object_and_repeating_it_is_fine(backend):
    store = backend.store
    keep = put_text(store, TENANT_A, "keep.txt", "keep")
    gone = put_text(store, TENANT_A, "gone.txt", "gone")
    store.delete(gone)
    store.delete(gone)
    with pytest.raises(NotFoundError):
        _ = store.get(gone)
    assert list(store.list(TENANT_A, "")) == [keep]
    assert store.get(keep) == b"keep"


def test_deleting_an_object_that_never_existed_is_fine(backend):
    backend.store.delete(make_ref(key="never/existed.pdf"))


def test_getting_a_missing_object_is_not_found(backend):
    with pytest.raises(NotFoundError, match="object not found") as excinfo:
        _ = backend.store.get(make_ref())
    assert (excinfo.value.status, excinfo.value.code) == (404, "NOT_FOUND")


def test_a_corrupted_object_is_never_served(backend):
    ref = backend.store.put(
        TENANT_A, "docs/ine.pdf", PAYLOAD, content_type="application/pdf"
    )
    backend.overwrite(TENANT_A, "docs/ine.pdf", bytes(reversed(PAYLOAD)))
    with pytest.raises(ObjectIntegrityError) as excinfo:
        _ = backend.store.get(ref)
    assert (excinfo.value.status, excinfo.value.code) == (500, "OBJECT_CORRUPT")


def test_a_ref_carrying_the_wrong_digest_is_refused(backend):
    ref = put_text(backend.store, TENANT_A, "doc.txt", "right bytes")
    wrong = replace(ref, sha256=hashlib.sha256(b"other bytes").digest())
    with pytest.raises(ObjectIntegrityError):
        _ = backend.store.get(wrong)


def test_tenants_are_separate_namespaces(backend):
    store = backend.store
    ref_a = put_text(store, TENANT_A, "doc.txt", "alpha")
    ref_b = put_text(store, TENANT_B, "doc.txt", "bravo")
    assert list(store.list(TENANT_A, "")) == [ref_a]
    assert list(store.list(TENANT_B, "")) == [ref_b]
    assert store.get(ref_a) == b"alpha"
    assert store.get(ref_b) == b"bravo"
    # A ref names its tenant: A's ref pointed at B finds B's bytes, which fail
    # A's checksum, so nothing crosses over even for a forged ref.
    with pytest.raises(ObjectIntegrityError):
        _ = store.get(replace(ref_a, tenant_id=TENANT_B))
    store.delete(ref_a)
    assert store.get(ref_b) == b"bravo"
    assert list(store.list(TENANT_A, "")) == []


# The validator is tested on its own above; each operation needs one hostile input
# to show that it is wired in.
def test_an_unsafe_key_is_refused_and_nothing_is_stored(backend):
    with pytest.raises(InvalidObjectKeyError):
        _ = put_text(backend.store, TENANT_A, "../x", "x")
    assert list(backend.store.list(TENANT_A, "")) == []


def test_an_unsafe_ref_is_refused_by_get_and_delete(backend):
    ref = make_ref(key="../x")
    with pytest.raises(InvalidObjectKeyError):
        _ = backend.store.get(ref)
    with pytest.raises(InvalidObjectKeyError):
        backend.store.delete(ref)


def test_an_unsafe_prefix_is_refused_when_list_is_called(backend):
    with pytest.raises(InvalidObjectKeyError):
        # Not iterated: the refusal is eager, and it is the other tenant's folder.
        _ = backend.store.list(TENANT_B, f"../{TENANT_A}/")


def test_a_key_no_utf8_text_can_hold_is_refused_by_every_operation(backend):
    key = "caf\ud800"  # what an undecodable legacy file name looks like
    with pytest.raises(InvalidObjectKeyError):
        _ = put_text(backend.store, TENANT_A, key, "x")
    with pytest.raises(InvalidObjectKeyError):
        _ = backend.store.get(make_ref(key=key))
    with pytest.raises(InvalidObjectKeyError):
        backend.store.delete(make_ref(key=key))
    with pytest.raises(InvalidObjectKeyError):
        _ = backend.store.list(TENANT_A, key)


def test_metadata_is_kept_with_the_object_as_given(backend):
    metadata = {"filename": "identificación de Juan.pdf", "uploaded-by": "staff 7"}
    ref = backend.store.put(
        TENANT_A, "ine.pdf", PAYLOAD, content_type="application/pdf", metadata=metadata
    )
    assert backend.stored_metadata(TENANT_A, "ine.pdf") == metadata
    assert backend.store.get(ref) == PAYLOAD


@pytest.mark.parametrize(
    ("content_type", "metadata"),
    [
        ("text/plain\ud800", {}),
        ("pdf", {}),
        ("text/plain", {"filename": "x\udc80.pdf"}),
        ("text/plain", {"Not-Lowercase": "x"}),
    ],
    ids=[
        "surrogate-content-type",
        "not-a-media-type",
        "surrogate-metadata",
        "bad-metadata-name",
    ],
)
def test_a_refused_overwrite_leaves_the_previous_version_readable(
    backend, content_type, metadata
):
    old = put_text(backend.store, TENANT_A, "doc.txt", "previous version")
    with pytest.raises(InvalidObjectMetadataError):
        _ = backend.store.put(
            TENANT_A,
            "doc.txt",
            b"new bytes",
            content_type=content_type,
            metadata=metadata,
        )
    assert backend.store.get(old) == b"previous version"
    assert list(backend.store.list(TENANT_A, "")) == [old]


def test_metadata_beyond_the_bounds_stores_nothing(backend):
    too_many = {f"k{n}": "v" for n in range(33)}
    with pytest.raises(InvalidObjectMetadataError):
        _ = put_text(backend.store, TENANT_A, "doc.txt", "x", metadata=too_many)
    assert list(backend.store.list(TENANT_A, "")) == []


def test_an_object_put_without_metadata_has_none(backend):
    _ = backend.store.put(TENANT_A, "ine.pdf", PAYLOAD, content_type="application/pdf")
    assert backend.stored_metadata(TENANT_A, "ine.pdf") == {}


def messages_of(error: BaseException) -> str:
    # Every message the exception carries, along its cause or context chain.
    lines = []
    link: BaseException | None = error
    while link is not None:
        lines.append(f"{type(link).__name__}: {link}")
        if link.__cause__ is not None:
            link = link.__cause__
        else:
            link = None if link.__suppress_context__ else link.__context__
    return "\n".join(lines)


def test_errors_never_carry_keys_or_tenant_ids(backend: Backend):
    key = "private/passport-of-juan-perez.pdf"
    stored = backend.store.put(TENANT_A, key, PAYLOAD, content_type="application/pdf")
    backend.overwrite(TENANT_A, key, b"damaged")
    failures = []
    for action in (
        lambda: backend.store.get(stored),
        lambda: backend.store.get(make_ref(key="private/missing-juan-perez.pdf")),
        lambda: backend.store.get(make_ref(key="../passport-of-juan-perez.pdf")),
        lambda: backend.store.list(TENANT_A, "../passport-of-juan-perez/"),
        lambda: put_text(backend.store, TENANT_A, "../passport-of-juan-perez.pdf", "x"),
    ):
        with pytest.raises(XolotlError) as excinfo:
            _ = action()
        failures.append(messages_of(excinfo.value))
        # Nothing from the storage layer rides along as a cause or context.
        assert excinfo.value.__cause__ is None
        assert excinfo.value.__context__ is None or excinfo.value.__suppress_context__
    assert len(failures) == 5
    for text in failures:
        assert "juan-perez" not in text
        assert str(TENANT_A) not in text


# ------------------------------------------------------- local filesystem only


def tenant_dir(root: Path, tenant_id: UUID = TENANT_A) -> Path:
    return root / str(tenant_id)


def test_local_layout_is_the_tenant_directory_the_key_and_a_record_beside_it(
    tmp_path,
):
    store = LocalFsObjectStore(tmp_path)
    _ = store.put(
        TENANT_A,
        "docs/ine.pdf",
        PAYLOAD,
        content_type="application/pdf",
        metadata={"filename": "ine.pdf"},
    )
    assert (tenant_dir(tmp_path) / "docs" / "ine.pdf").read_bytes() == PAYLOAD
    record = (tenant_dir(tmp_path) / "docs" / "ine.pdf.meta.json").read_text()
    assert json.loads(record) == {
        "sha256": DIGEST.hex(),
        "size": SIZE,
        "content_type": "application/pdf",
        "metadata": {"filename": "ine.pdf"},
    }


def test_local_store_touches_nothing_until_the_first_put(tmp_path):
    root = tmp_path / "objects"
    store = LocalFsObjectStore(root)
    assert list(store.list(TENANT_A, "")) == []
    with pytest.raises(NotFoundError):
        _ = store.get(make_ref())
    store.delete(make_ref())
    assert not root.exists()
    _ = put_text(store, TENANT_A, "doc.txt", "x")
    assert tenant_dir(root).is_dir()


def test_local_files_are_private_to_the_service_user(tmp_path):
    store = LocalFsObjectStore(tmp_path)
    _ = put_text(store, TENANT_A, "docs/ine.txt", "x")
    for path in (
        tenant_dir(tmp_path) / "docs" / "ine.txt",
        tenant_dir(tmp_path) / "docs" / "ine.txt.meta.json",
    ):
        assert path.stat().st_mode & 0o077 == 0


def test_local_writes_use_a_temp_file_that_never_lingers(tmp_path):
    store = LocalFsObjectStore(tmp_path)
    for index in range(3):
        _ = put_text(store, TENANT_A, f"doc-{index}.txt", "x")
    assert list((tmp_path / ".tmp").iterdir()) == []


def test_local_bytes_reach_the_disk_before_they_are_moved_into_place(
    tmp_path, monkeypatch
):
    events = []
    real_fsync, real_replace = os.fsync, Path.replace

    def spy_fsync(descriptor: int) -> None:
        events.append(("fsync", os.fstat(descriptor).st_size))
        real_fsync(descriptor)

    def spy_replace(self: Path, target: Path) -> Path:
        events.append("replace")
        return real_replace(self, target)

    monkeypatch.setattr(os, "fsync", spy_fsync)
    monkeypatch.setattr(Path, "replace", spy_replace)
    _ = put_text(LocalFsObjectStore(tmp_path), TENANT_A, "doc.txt", "x")
    # Both files are written and synced, each complete at that point, before either
    # is moved into place; the bytes move first and the record last.
    data = tenant_dir(tmp_path) / "doc.txt"
    record = tenant_dir(tmp_path) / "doc.txt.meta.json"
    assert events == [
        ("fsync", data.stat().st_size),
        ("fsync", record.stat().st_size),
        "replace",
        "replace",
    ]


@pytest.fixture
def failing_replace(monkeypatch):
    # arm(n) makes the n-th ``Path.replace`` raise, as a full disk would.
    def arm(failing_call: int) -> None:
        calls = 0
        real = Path.replace

        def replace_or_fail(self: Path, target: Path) -> Path:
            nonlocal calls
            calls += 1
            if calls == failing_call:
                msg = "disk full"
                raise OSError(msg)
            return real(self, target)

        monkeypatch.setattr(Path, "replace", replace_or_fail)

    return arm


def test_local_failed_overwrite_leaves_the_previous_object_intact(
    tmp_path, monkeypatch, failing_replace
):
    store = LocalFsObjectStore(tmp_path)
    old = put_text(store, TENANT_A, "doc.txt", "old")
    failing_replace(1)
    with pytest.raises(OSError, match="disk full"):
        _ = put_text(store, TENANT_A, "doc.txt", "new")
    monkeypatch.undo()
    assert store.get(old) == b"old"
    assert list(store.list(TENANT_A, "")) == [old]
    assert list((tmp_path / ".tmp").iterdir()) == []


def test_local_put_that_cannot_stage_its_record_leaves_the_previous_version_intact(
    tmp_path, monkeypatch
):
    store = LocalFsObjectStore(tmp_path)
    old = put_text(store, TENANT_A, "doc.txt", "old")
    real_mkstemp = tempfile.mkstemp
    staged = 0

    def mkstemp_or_fail(*args, **kwargs):
        nonlocal staged
        staged += 1
        if staged == 2:  # the bytes are staged, the record cannot be
            msg = "disk full"
            raise OSError(msg)
        return real_mkstemp(*args, **kwargs)

    monkeypatch.setattr(tempfile, "mkstemp", mkstemp_or_fail)
    with pytest.raises(OSError, match="disk full"):
        _ = put_text(store, TENANT_A, "doc.txt", "new")
    monkeypatch.undo()
    assert store.get(old) == b"old"
    assert list(store.list(TENANT_A, "")) == [old]
    assert list((tmp_path / ".tmp").iterdir()) == []


def test_local_put_that_fails_before_its_record_is_repaired_by_retrying(
    tmp_path, monkeypatch, failing_replace
):
    store = LocalFsObjectStore(tmp_path)
    old = put_text(store, TENANT_A, "doc.txt", "old")
    failing_replace(2)  # the bytes land, the record does not
    with pytest.raises(OSError, match="disk full"):
        _ = put_text(store, TENANT_A, "doc.txt", "new")
    monkeypatch.undo()
    assert list((tmp_path / ".tmp").iterdir()) == []
    # Torn state fails closed: the record still describes the old bytes.
    with pytest.raises(ObjectIntegrityError):
        _ = store.get(old)
    retried = put_text(store, TENANT_A, "doc.txt", "new")
    assert store.get(retried) == b"new"
    assert list(store.list(TENANT_A, "")) == [retried]


def test_local_list_ignores_a_directory_that_merely_looks_like_a_record(tmp_path):
    store = LocalFsObjectStore(tmp_path)
    (tenant_dir(tmp_path) / "docs" / "odd.meta.json").mkdir(parents=True)
    kept = put_text(store, TENANT_A, "docs/kept.txt", "x")
    assert list(store.list(TENANT_A, "")) == [kept]


def test_local_bytes_without_a_record_are_not_listed(tmp_path):
    store = LocalFsObjectStore(tmp_path)
    (tenant_dir(tmp_path) / "docs").mkdir(parents=True)
    _ = (tenant_dir(tmp_path) / "docs" / "half-written.pdf").write_bytes(b"x")
    assert list(store.list(TENANT_A, "")) == []
    # Retrying the put completes it.
    ref = put_text(store, TENANT_A, "docs/half-written.pdf", "x")
    assert list(store.list(TENANT_A, "")) == [ref]


def test_local_keys_cannot_write_outside_the_tenant_directory(tmp_path):
    root = tmp_path / "objects"
    store = LocalFsObjectStore(root)
    with pytest.raises(InvalidObjectKeyError):
        _ = put_text(store, TENANT_A, f"../{TENANT_B}/escape.txt", "x")
    assert not root.exists()
    assert not any("escape" in path.name for path in tmp_path.rglob("*"))


def test_local_refused_puts_leave_no_trace(tmp_path):
    root = tmp_path / "objects"
    store = LocalFsObjectStore(root)
    with pytest.raises(InvalidObjectKeyError):
        _ = put_text(store, TENANT_A, "../doc.txt", "x")
    with pytest.raises(InvalidObjectKeyError):
        _ = put_text(store, TENANT_A, "a" * 246, "x")
    with pytest.raises(InvalidObjectMetadataError):
        _ = put_text(store, TENANT_A, "doc.txt", "x", metadata={"Not-Lowercase": "x"})
    with pytest.raises(InvalidObjectMetadataError):
        _ = store.put(TENANT_A, "doc.txt", b"x", content_type="not a media type")
    assert not root.exists()


def test_local_keys_that_collide_with_another_keys_directory_are_refused(tmp_path):
    store = LocalFsObjectStore(tmp_path)
    file_ref = put_text(store, TENANT_A, "docs", "a file")
    with pytest.raises(InvalidObjectKeyError, match="conflicts"):
        _ = put_text(store, TENANT_A, "docs/ine.pdf", "x")
    with pytest.raises(InvalidObjectKeyError, match="conflicts"):
        _ = put_text(store, TENANT_A, "docs/deeper/ine.pdf", "x")
    nested_ref = put_text(store, TENANT_A, "forms/ine.pdf", "nested")
    with pytest.raises(InvalidObjectKeyError, match="conflicts"):
        _ = put_text(store, TENANT_A, "forms", "x")
    assert store.get(file_ref) == b"a file"
    assert store.get(nested_ref) == b"nested"
    assert list((tmp_path / ".tmp").iterdir()) == []
    # A directory, or a path through a file, is not an object.
    with pytest.raises(NotFoundError):
        _ = store.get(make_ref(key="forms"))
    with pytest.raises(NotFoundError):
        _ = store.get(make_ref(key="docs/deeper/ine.pdf"))
    store.delete(make_ref(key="forms"))
    assert store.get(nested_ref) == b"nested"


@pytest.mark.parametrize(
    "key",
    ["a" * 245, "€" * 81, "dir/" + "a" * 245],
    ids=["ascii", "multi-byte", "nested"],
)
def test_local_names_at_the_filesystem_limit_are_stored(tmp_path, key):
    store = LocalFsObjectStore(tmp_path)
    assert store.get(put_text(store, TENANT_A, key, "x")) == b"x"


@pytest.mark.parametrize(
    "key",
    ["a" * 246, "€" * 82, "a" * 246 + "/file.txt"],
    ids=["ascii", "multi-byte", "directory"],
)
def test_local_names_longer_than_the_filesystem_allows_are_refused(tmp_path, key):
    store = LocalFsObjectStore(tmp_path)
    with pytest.raises(InvalidObjectKeyError):
        _ = put_text(store, TENANT_A, key, "x")
    with pytest.raises(InvalidObjectKeyError):
        _ = store.get(make_ref(key=key))
    assert list(tmp_path.iterdir()) == []


VALID_RECORD = {
    "sha256": "0" * 64,
    "size": 1,
    "content_type": "text/plain",
    "metadata": {},
}
UNREADABLE_RECORDS = {
    "not-json": b"not json",
    "missing-field": json.dumps({
        k: v for k, v in VALID_RECORD.items() if k != "metadata"
    }).encode(),
    "bad-digest": json.dumps({**VALID_RECORD, "sha256": "zz"}).encode(),
    "size-as-text": json.dumps({**VALID_RECORD, "size": "1"}).encode(),
    "negative-size": json.dumps({**VALID_RECORD, "size": -1}).encode(),
    "non-text-metadata": json.dumps({**VALID_RECORD, "metadata": {"a": 1}}).encode(),
    "unknown-field": json.dumps({**VALID_RECORD, "extra": 1}).encode(),
}


@pytest.mark.parametrize(
    "record", UNREADABLE_RECORDS.values(), ids=UNREADABLE_RECORDS.keys()
)
def test_local_list_refuses_an_unreadable_record(tmp_path, record):
    store = LocalFsObjectStore(tmp_path)
    _ = put_text(store, TENANT_A, "docs/doc.txt", "x")
    _ = (tenant_dir(tmp_path) / "docs" / "doc.txt.meta.json").write_bytes(record)
    with pytest.raises(ObjectIntegrityError, match="unreadable") as excinfo:
        _ = list(store.list(TENANT_A, ""))
    # The parser's own error would echo the record's content; it must not ride along.
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__context__ is None or excinfo.value.__suppress_context__


def test_local_list_accepts_a_well_formed_record_it_did_not_write(tmp_path):
    store = LocalFsObjectStore(tmp_path)
    _ = put_text(store, TENANT_A, "docs/doc.txt", "x")
    _ = (tenant_dir(tmp_path) / "docs" / "doc.txt.meta.json").write_bytes(
        json.dumps(VALID_RECORD).encode()
    )
    assert list(store.list(TENANT_A, "")) == [
        make_ref(
            key="docs/doc.txt",
            sha256=bytes(32),
            size=1,
            content_type="text/plain",
        )
    ]


def test_local_a_tenant_id_must_be_a_uuid(tmp_path):
    # A str could carry a path ("../elsewhere"); it is refused before any path is built.
    root = tmp_path / "objects"
    store = LocalFsObjectStore(root)
    smuggled = cast("UUID", cast("object", "../elsewhere"))
    with pytest.raises(TypeError, match="UUID"):
        _ = store.put(smuggled, "doc.txt", b"x", content_type="text/plain")
    with pytest.raises(TypeError, match="UUID"):
        _ = store.get(make_ref(tenant_id=smuggled))
    with pytest.raises(TypeError, match="UUID"):
        store.delete(make_ref(tenant_id=smuggled))
    with pytest.raises(TypeError, match="UUID"):
        _ = store.list(smuggled, "")
    assert list(tmp_path.iterdir()) == []


def tree(path: Path) -> dict[str, bytes]:
    # Every regular file under path, links not followed, by relative name.
    return {
        entry.relative_to(path).as_posix(): entry.read_bytes()
        for entry in sorted(path.rglob("*"))
        if entry.is_file() and not entry.is_symlink()
    }


@dataclass(frozen=True)
class Planted:
    """A store whose tenant A directory holds links someone else created."""

    store: LocalFsObjectStore
    root: Path
    outside: Path
    secret: ObjectRef  # tenant B's object
    mine: ObjectRef  # tenant A's own object
    inside: ObjectRef  # tenant A's, in a real directory that a link of A points to


@pytest.fixture
def planted(tmp_path) -> Planted:
    root = tmp_path / "objects"
    store = LocalFsObjectStore(root)
    secret = put_text(store, TENANT_B, "secret/passport.txt", "tenant B passport")
    mine = put_text(store, TENANT_A, "mine.txt", "tenant A own")
    inside = put_text(store, TENANT_A, "real/inside.txt", "tenant A inside")
    outside = tmp_path / "outside"
    outside.mkdir()
    _ = (outside / "loot.txt").write_text("outside the root")
    tenant_a, tenant_b = tenant_dir(root), tenant_dir(root, TENANT_B)
    (tenant_a / "to-b").symlink_to(tenant_b, target_is_directory=True)
    (tenant_a / "to-outside").symlink_to(outside, target_is_directory=True)
    (tenant_a / "to-loot").symlink_to(outside / "loot.txt")
    (tenant_a / "alias").symlink_to(tenant_a / "real", target_is_directory=True)
    (tenant_a / "ghost.meta.json").symlink_to(
        tenant_b / "secret" / "passport.txt.meta.json"
    )
    return Planted(store, root, outside, secret, mine, inside)


def test_local_get_refuses_to_follow_a_planted_link(planted):
    through_a_directory = replace(
        planted.secret, tenant_id=TENANT_A, key="to-b/secret/passport.txt"
    )
    loot = b"outside the root"
    onto_a_file = make_ref(
        key="to-loot", sha256=hashlib.sha256(loot).digest(), size=len(loot)
    )
    # Even a link that stays inside the tenant is refused: no link is followed.
    through_an_alias = replace(planted.inside, key="alias/inside.txt")
    for ref in (through_a_directory, onto_a_file, through_an_alias):
        with pytest.raises(InvalidObjectKeyError):
            _ = planted.store.get(ref)


@pytest.mark.parametrize(
    "key",
    [
        "to-b/planted.txt",
        "to-outside/planted.txt",
        "to-loot",
        "alias/planted.txt",
        "ghost",
    ],
    ids=[
        "into-another-tenant",
        "out-of-the-root",
        "over-a-linked-file",
        "inside-the-tenant",
        "over-a-linked-record",
    ],
)
def test_local_put_refuses_to_write_through_a_planted_link(planted, tmp_path, key):
    before = tree(tmp_path)
    with pytest.raises(InvalidObjectKeyError):
        _ = put_text(planted.store, TENANT_A, key, "planted")
    assert tree(tmp_path) == before


@pytest.mark.parametrize(
    "key",
    [
        "to-b/secret/passport.txt",
        "to-outside/loot.txt",
        "to-loot",
        "alias/inside.txt",
        "ghost",
    ],
    ids=[
        "into-another-tenant",
        "out-of-the-root",
        "a-linked-file",
        "inside-the-tenant",
        "a-linked-record",
    ],
)
def test_local_delete_refuses_to_delete_through_a_planted_link(planted, tmp_path, key):
    before = tree(tmp_path)
    with pytest.raises(InvalidObjectKeyError):
        planted.store.delete(make_ref(key=key))
    assert tree(tmp_path) == before
    assert planted.store.get(planted.secret) == b"tenant B passport"


def test_local_list_never_follows_a_planted_link(planted):
    # The linked record "ghost" and the linked directories stay out of the listing.
    assert list(planted.store.list(TENANT_A, "")) == [planted.mine, planted.inside]
    for prefix in ("to-b/", "to-outside/", "to-b/secret/", "alias/"):
        with pytest.raises(InvalidObjectKeyError):
            _ = planted.store.list(TENANT_A, prefix)


def test_local_a_tenant_directory_that_is_a_link_is_refused(planted):
    tenant_dir(planted.root, TENANT_C).symlink_to(
        tenant_dir(planted.root, TENANT_B), target_is_directory=True
    )
    stolen = replace(planted.secret, tenant_id=TENANT_C)
    with pytest.raises(InvalidObjectKeyError):
        _ = planted.store.get(stolen)
    with pytest.raises(InvalidObjectKeyError):
        _ = put_text(planted.store, TENANT_C, "planted.txt", "x")
    with pytest.raises(InvalidObjectKeyError):
        planted.store.delete(stolen)
    with pytest.raises(InvalidObjectKeyError):
        _ = planted.store.list(TENANT_C, "")
    assert planted.store.get(planted.secret) == b"tenant B passport"


def test_local_the_resolved_path_must_also_stay_inside_the_tenant_directory(
    planted, tmp_path, monkeypatch
):
    # Blind the link check: resolving the path still finds that it leaves the tenant.

    def not_a_link(_self: Path) -> bool:
        return False

    monkeypatch.setattr(Path, "is_symlink", not_a_link)
    before = tree(tmp_path)
    with pytest.raises(InvalidObjectKeyError):
        _ = put_text(planted.store, TENANT_A, "to-outside/planted.txt", "planted")
    assert tree(tmp_path) == before


def test_local_the_root_itself_may_be_a_link(tmp_path):
    volume = tmp_path / "volume"
    volume.mkdir()
    root = tmp_path / "objects"
    root.symlink_to(volume, target_is_directory=True)
    store = LocalFsObjectStore(root)
    ref = put_text(store, TENANT_A, "docs/ine.txt", "x")
    assert store.get(ref) == b"x"
    assert list(store.list(TENANT_A, "")) == [ref]
    assert (volume / str(TENANT_A) / "docs" / "ine.txt").read_bytes() == b"x"
    store.delete(ref)
    assert list(store.list(TENANT_A, "")) == []


def test_local_list_skips_an_object_deleted_while_it_is_being_listed(tmp_path):
    store = LocalFsObjectStore(tmp_path)
    refs = [put_text(store, TENANT_A, f"docs/{name}.txt", name) for name in "abc"]
    listing = store.list(TENANT_A, "")
    assert next(listing) == refs[0]
    store.delete(refs[1])
    assert list(listing) == [refs[2]]


def test_local_list_errors_never_carry_a_path(tmp_path, monkeypatch):
    store = LocalFsObjectStore(tmp_path)
    _ = put_text(store, TENANT_A, "docs/secret-name.txt", "x")
    real_read_bytes = Path.read_bytes

    def read_bytes_or_deny(self: Path) -> bytes:
        if self.name.endswith(".meta.json"):
            raise PermissionError(13, "Permission denied", str(self))
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", read_bytes_or_deny)
    with pytest.raises(ObjectIntegrityError, match="unreadable") as excinfo:
        _ = list(store.list(TENANT_A, ""))
    assert "secret-name" not in messages_of(excinfo.value)
    assert str(TENANT_A) not in messages_of(excinfo.value)
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__context__ is None or excinfo.value.__suppress_context__


def test_local_delete_removes_the_record_before_the_bytes(tmp_path, monkeypatch):
    store = LocalFsObjectStore(tmp_path)
    ref = put_text(store, TENANT_A, "doc.txt", "x")
    real_unlink = Path.unlink
    unlinked = 0

    def unlink_or_fail(self: Path, *, missing_ok: bool = False) -> None:
        nonlocal unlinked
        unlinked += 1
        if unlinked == 2:  # the record is gone, the bytes cannot be removed
            msg = "disk gone"
            raise OSError(msg)
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", unlink_or_fail)
    with pytest.raises(OSError, match="disk gone"):
        store.delete(ref)
    monkeypatch.undo()
    # A crash between the two removals leaves invisible bytes, not a ghost listing.
    assert (tenant_dir(tmp_path) / "doc.txt").exists()
    assert list(store.list(TENANT_A, "")) == []


def test_local_delete_prunes_the_directories_it_empties(tmp_path):
    store = LocalFsObjectStore(tmp_path)
    deep = put_text(store, TENANT_A, "a/b/c.txt", "deep")
    sibling = put_text(store, TENANT_A, "a/x.txt", "sibling")
    store.delete(deep)
    assert not (tenant_dir(tmp_path) / "a" / "b").exists()
    assert (tenant_dir(tmp_path) / "a").is_dir()  # still holds the sibling
    store.delete(sibling)
    assert not (tenant_dir(tmp_path) / "a").exists()
    assert tenant_dir(tmp_path).is_dir()  # the tenant directory itself stays
    # One delete climbs through every directory it empties.
    store.delete(put_text(store, TENANT_A, "p/q/r/s.txt", "chain"))
    assert not (tenant_dir(tmp_path) / "p").exists()


def test_local_a_key_can_take_the_name_of_a_directory_its_last_object_left(tmp_path):
    store = LocalFsObjectStore(tmp_path)
    gone = put_text(store, TENANT_A, "invoices/2026.pdf", "x")
    store.delete(gone)
    reused = put_text(store, TENANT_A, "invoices", "now a plain key")
    assert store.get(reused) == b"now a plain key"


def test_local_put_makes_the_directories_again_after_delete_pruned_them(tmp_path):
    store = LocalFsObjectStore(tmp_path)
    store.delete(put_text(store, TENANT_A, "p/q/r.txt", "one"))
    again = put_text(store, TENANT_A, "p/q/r.txt", "two")
    assert store.get(again) == b"two"


def test_local_put_survives_a_delete_pruning_its_directory_mid_way(
    tmp_path, monkeypatch
):
    store = LocalFsObjectStore(tmp_path)
    real_replace = Path.replace
    pruned = False

    def prune_then_replace(self: Path, target: Path) -> Path:
        nonlocal pruned
        if not pruned:
            pruned = True
            target.parent.rmdir()  # what a concurrent delete does to an empty directory
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", prune_then_replace)
    ref = put_text(store, TENANT_A, "docs/ine.txt", "x")
    monkeypatch.undo()
    assert store.get(ref) == b"x"
    assert list(store.list(TENANT_A, "")) == [ref]


def test_local_put_gives_up_when_the_directory_keeps_vanishing(tmp_path, monkeypatch):
    store = LocalFsObjectStore(tmp_path)

    def replace_into_nothing(self: Path, target: Path) -> Path:
        raise FileNotFoundError(2, "No such file or directory")

    monkeypatch.setattr(Path, "replace", replace_into_nothing)
    with pytest.raises(FileNotFoundError):
        _ = put_text(store, TENANT_A, "docs/ine.txt", "x")
    monkeypatch.undo()
    assert list((tmp_path / ".tmp").iterdir()) == []


@pytest.mark.parametrize(
    ("prefix", "walked"),
    [("", "."), ("docs/2026", "docs"), ("docs/2026/10/", "docs/2026/10")],
    ids=["whole-tenant", "partial-name", "directory"],
)
def test_local_list_walks_only_from_the_prefix_directory(
    tmp_path, monkeypatch, prefix, walked
):
    store = LocalFsObjectStore(tmp_path)
    starts = []
    real_rglob = Path.rglob

    def spy_rglob(self: Path, pattern: str, **kwargs):
        starts.append(self.relative_to(tenant_dir(tmp_path)).as_posix())
        return real_rglob(self, pattern, **kwargs)

    monkeypatch.setattr(Path, "rglob", spy_rglob)
    assert list(store.list(TENANT_A, prefix)) == []
    assert starts == [walked]


# ------------------------------------------------------------ composition root


@pytest.fixture
def object_store_settings(monkeypatch):
    # Start every selection test from a known configuration, whatever the shell holds.
    for name in (
        "XOLOTL_OBJECT_STORE",
        "XOLOTL_OBJECT_STORE_ROOT",
    ):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_the_local_store_is_the_default_and_uses_the_configured_root(tmp_path):
    settings = Settings.model_construct(object_store_root=tmp_path / "objects")
    assert settings.object_store == "local"
    store = build_object_store(settings)
    assert isinstance(store, LocalFsObjectStore)
    _ = put_text(store, TENANT_A, "doc.txt", "x")
    assert (tmp_path / "objects" / str(TENANT_A) / "doc.txt").read_bytes() == b"x"


def test_a_store_the_composition_root_cannot_build_is_refused():
    # Settings only lets "local" through; the check must still hold if it is bypassed.
    settings = Settings.model_construct(object_store="s3")
    with pytest.raises(RuntimeError, match="XOLOTL_OBJECT_STORE"):
        _ = build_object_store(settings)


def test_the_app_carries_the_store_the_composition_root_selected(
    engine, object_store_settings, tmp_path
):
    object_store_settings.setenv("XOLOTL_OBJECT_STORE_ROOT", str(tmp_path / "objects"))
    get_settings.cache_clear()
    try:
        app = build_app()
    finally:
        get_settings.cache_clear()
    store = app.state.xolotl.object_store
    assert isinstance(store, LocalFsObjectStore)
    ref = put_text(store, TENANT_A, "doc.txt", "x")
    assert (tmp_path / "objects" / str(TENANT_A) / "doc.txt").exists()
    assert store.get(ref) == b"x"


def test_create_app_hands_the_given_store_to_the_app_state(engine, tmp_path):
    store = LocalFsObjectStore(tmp_path)
    app = create_app(session_factory=get_sessionmaker(), object_store=store)
    assert app.state.xolotl.object_store is store


def test_create_app_without_a_store_leaves_it_unset(engine):
    app = create_app(session_factory=get_sessionmaker())
    assert app.state.xolotl.object_store is None
