"""Local-filesystem object store, for development and single-node installs.

An object is the file ``<root>/<tenant_id>/<key>`` plus a record beside it,
``<key>.meta.json``, holding its sha256, size, content type and metadata.

``put`` checks everything it was given and serialises the record before it touches
the store. It then stages both files in ``<root>/.tmp``, flushes them to disk and
moves them into place, the bytes first and the record last. The record is what
``list`` walks, so bytes without a record (a ``put`` that failed midway) are
invisible, and retrying the ``put`` repairs them. When the move of the record fails
after the bytes landed, the object fails closed: the old ref no longer matches its
bytes and ``get`` raises ``ObjectIntegrityError`` until the ``put`` is retried. A
crash can leave files in ``<root>/.tmp``; they are safe to delete while the store is
idle. ``delete`` removes the record first and then the bytes, and prunes the
directories it empties, so a key can take the name of a directory that no longer
holds anything.

Containment is checked on the real filesystem, before any path is used. A tenant id
must be a ``UUID`` (a ``str`` could carry a path, and raises ``TypeError``). No
component under the root may be a symlink, and the resolved path must stay inside
the tenant's own directory; a link is refused as an invalid key, and ``list`` never
follows one. The root itself may be a link, which is how to put the store on another
volume.

What a filesystem adds to the port's rules: two keys cannot coexist when one is a
directory of the other (``a`` and ``a/b``), so ``put`` refuses the second; a name may
be at most 245 bytes; and on a case-insensitive filesystem (macOS by default) keys
that differ only in case are one object. Concurrent puts to one key are last writer
wins, file by file, so give each object its own key. Files are created readable by
the service user alone.

Reads hold the whole object in memory. One unreadable record fails the whole
``list`` with ``ObjectIntegrityError``, while an object deleted during a listing is
skipped. OS errors this adapter does not translate (a full disk, permissions)
propagate unchanged and their messages carry file paths: log them, do not show them.
"""

import hashlib
import os
import tempfile
from collections.abc import Generator, Iterator, Mapping
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import ClassVar, override
from uuid import UUID

from pydantic import BaseModel, ConfigDict, NonNegativeInt, ValidationError

from xolotl.platform.errors import NotFoundError
from xolotl.platform.storage import (
    NO_METADATA,
    RECORD_SUFFIX,
    InvalidObjectKeyError,
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

# Where writes are staged, beside the tenant directories on the same filesystem so
# the final move is atomic. Tenant directories are UUIDs, so the name cannot clash.
_STAGING_DIR = ".tmp"
# Filesystems cap a file name at 255 bytes; the record's name adds the suffix.
_MAX_SEGMENT_BYTES = 255 - len(RECORD_SUFFIX)
# Tries to move a file into a directory that a concurrent delete keeps pruning.
_INSTALL_ATTEMPTS = 3


class _Record(BaseModel):
    """The content of ``<key>.meta.json``: all ``list`` needs to rebuild a ref."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        strict=True, frozen=True, extra="forbid"
    )

    sha256: str
    size: NonNegativeInt
    content_type: str
    metadata: dict[str, str]


def _record_path(data_path: Path) -> Path:
    return data_path.with_name(data_path.name + RECORD_SUFFIX)


def _recorded_keys(tenant_dir: Path, base: Path, prefix: str) -> list[str]:
    # An object exists once its record does. A linked record is skipped, never
    # followed. Keys come back in code point order, which is also the byte order S3
    # lists in.
    keys = (
        record.relative_to(tenant_dir).as_posix().removesuffix(RECORD_SUFFIX)
        for record in base.rglob(f"*{RECORD_SUFFIX}")
        if not record.is_symlink() and record.is_file()
    )
    return sorted(key for key in keys if key.startswith(prefix))


def _load_record(path: Path) -> _Record | None:
    # None means the object was deleted since the listing began. Neither the parser's
    # error nor the OS's is chained: one echoes the record, the other its path.
    try:
        return _Record.model_validate_json(path.read_bytes())
    except FileNotFoundError:
        return None
    except (OSError, ValidationError):
        msg = "stored object record is missing or unreadable"
        raise ObjectIntegrityError(msg) from None


@contextmanager
def _refuse_colliding_keys() -> Generator[None]:
    """Turn a key that clashes with another key's path into a validation error.

    Yields:
        Nothing; wraps the filesystem calls of a ``put``.

    Raises:
        InvalidObjectKeyError: When the key is a file where a directory is needed,
            or a directory where a file is.

    """
    try:
        yield
    except (FileExistsError, NotADirectoryError, IsADirectoryError):
        msg = "object key conflicts with another key"
        raise InvalidObjectKeyError(msg) from None


@contextmanager
def _staged(root: Path, payload: bytes) -> Generator[Path]:
    # A complete, synced copy of ``payload`` in the staging directory. It is removed
    # on the way out: a file that was moved into place leaves nothing behind, and one
    # that was not leaves no litter.
    staging = root / _STAGING_DIR
    staging.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(dir=staging, prefix="put-")
    staged = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            _ = handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        yield staged
    finally:
        staged.unlink(missing_ok=True)


def _install(staged: Path, target: Path) -> None:
    # The directory is made as late as possible, and again if a concurrent delete
    # pruned it (it was empty) between the mkdir and the move.
    for _ in range(_INSTALL_ATTEMPTS - 1):
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            _ = staged.replace(target)
        except FileNotFoundError:
            continue
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    _ = staged.replace(target)


def _prune_empty_directories(directory: Path, tenant_dir: Path) -> None:
    # Directories made for a key outlive it otherwise, and would block a later key
    # with the same name. The first one that is not empty ends the climb.
    while directory != tenant_dir:
        try:
            directory.rmdir()
        except OSError:
            return
        directory = directory.parent


def _confined(tenant_dir: Path, path: Path) -> Path:
    # Real containment, not the lexical kind: nothing from the tenant's directory down
    # to ``path`` may be a link, and the resolved path must stay inside the tenant's
    # directory. Nothing is created or changed here.
    link = tenant_dir
    for part in ("", *path.relative_to(tenant_dir).parts):
        link /= part
        if link.is_symlink():
            raise InvalidObjectKeyError
    if not path.resolve().is_relative_to(tenant_dir.resolve()):
        raise InvalidObjectKeyError
    return path


def _data_path(tenant_dir: Path, key: str) -> Path:
    checked = validate_key(key)
    if any(len(s.encode()) > _MAX_SEGMENT_BYTES for s in checked.split("/")):
        raise InvalidObjectKeyError
    return _confined(tenant_dir, tenant_dir / checked)


def _refs(
    tenant_id: UUID, tenant_dir: Path, base: Path, prefix: str
) -> Iterator[ObjectRef]:
    for key in _recorded_keys(tenant_dir, base, prefix):
        record = _load_record(_record_path(tenant_dir / key))
        if record is None:
            continue
        yield ObjectRef(
            tenant_id=tenant_id,
            key=key,
            sha256=parse_digest(record.sha256),
            size=record.size,
            content_type=record.content_type,
        )


class LocalFsObjectStore(ObjectStore):
    """Objects as files under ``root``; see the module docstring for the layout."""

    def __init__(self, root: Path) -> None:
        super().__init__()
        self._root: Path = root

    def _tenant_dir(self, tenant_id: object) -> Path:
        if not isinstance(tenant_id, UUID):
            msg = "tenant_id must be a UUID"
            raise TypeError(msg)
        return self._root / str(tenant_id)

    @override
    def put(
        self,
        tenant_id: UUID,
        key: str,
        data: bytes,
        *,
        content_type: str,
        metadata: Mapping[str, str] = NO_METADATA,
    ) -> ObjectRef:
        tenant_dir = self._tenant_dir(tenant_id)
        path = _data_path(tenant_dir, key)
        record_path = _confined(tenant_dir, _record_path(path))
        digest = hashlib.sha256(data).digest()
        record = _Record(
            sha256=digest.hex(),
            size=len(data),
            content_type=validate_content_type(content_type),
            metadata=validate_metadata(metadata),
        )
        # Whatever can fail on the caller's input has failed by now: nothing has been
        # written, so a refused put leaves the previous version as it was.
        record_json = record.model_dump_json().encode()
        with _refuse_colliding_keys(), ExitStack() as staged:
            staged_data = staged.enter_context(_staged(self._root, data))
            staged_record = staged.enter_context(_staged(self._root, record_json))
            _install(staged_data, path)
            _install(staged_record, record_path)
        return ObjectRef(
            tenant_id=tenant_id,
            key=key,
            sha256=digest,
            size=len(data),
            content_type=content_type,
        )

    @override
    def get(self, ref: ObjectRef) -> bytes:
        path = _data_path(self._tenant_dir(ref.tenant_id), ref.key)
        try:
            data = path.read_bytes()
        except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
            msg = "object not found"
            raise NotFoundError(msg) from None
        return verify_integrity(ref, data)

    @override
    def delete(self, ref: ObjectRef) -> None:
        tenant_dir = self._tenant_dir(ref.tenant_id)
        path = _data_path(tenant_dir, ref.key)
        record_path = _confined(tenant_dir, _record_path(path))
        # The record goes first: it is what makes the object visible to ``list``.
        for doomed in (record_path, path):
            if doomed.is_file():
                doomed.unlink(missing_ok=True)
        _prune_empty_directories(path.parent, tenant_dir)

    @override
    def list(self, tenant_id: UUID, prefix: str) -> Iterator[ObjectRef]:
        tenant_dir = self._tenant_dir(tenant_id)
        # The walk starts in the prefix's own directory, or in the directory that
        # holds the name the prefix stops in.
        start = tenant_dir / validate_prefix(prefix).rpartition("/")[0]
        return _refs(tenant_id, tenant_dir, _confined(tenant_dir, start), prefix)
