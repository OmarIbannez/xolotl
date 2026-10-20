"""The object store port, its refs and errors, and the input checks adapters share.

S3 is a later adapter behind this same port; nothing here depends on it. Keys,
prefixes, content types and metadata are validated here, once, so every adapter
accepts and refuses exactly the same ones, and does so before it writes anything.
Error details never carry keys, tenant ids or metadata values.
"""

import hashlib
import hmac
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import ClassVar, Protocol
from uuid import UUID

from xolotl.platform.errors import ValidationFailedError, XolotlError

MAX_KEY_LENGTH = 512
MAX_CONTENT_TYPE_LENGTH = 255
MAX_METADATA_ENTRIES = 32
MAX_METADATA_VALUE_LENGTH = 1024
MAX_METADATA_BYTES = 8 * 1024
# The local adapter keeps an object's record beside its bytes under this suffix.
# No key segment may end with it for any adapter, so keys stay portable.
RECORD_SUFFIX = ".meta.json"
NO_METADATA: Mapping[str, str] = MappingProxyType({})
# The namespace of objects no tenant owns, such as the raw files of the shared
# sanctions lists (SP-03 spec Decision 4). No tenant has the nil UUID as its id.
SHARED_NAMESPACE: UUID = UUID(int=0)

_DIGEST_HEX = re.compile(r"[0-9a-f]{64}")
_CONTENT_TYPE = re.compile(r"^[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+(;.*)?$")
_METADATA_NAME = re.compile(r"^[a-z0-9_-]{1,64}$")


class ObjectIntegrityError(XolotlError):
    status: ClassVar[int] = 500
    default_code: ClassVar[str] = "OBJECT_CORRUPT"
    default_detail: ClassVar[str] = "stored object failed its integrity check"


class InvalidObjectKeyError(ValidationFailedError):
    default_detail: ClassVar[str] = "invalid object key"


class InvalidObjectMetadataError(ValidationFailedError):
    default_detail: ClassVar[str] = "invalid object metadata"


@dataclass(frozen=True, slots=True)
class ObjectRef:
    """Points at one stored object, and carries what proves its bytes are intact.

    ``sha256`` is the raw 32-byte digest of the object's bytes, not hex text.
    """

    tenant_id: UUID
    key: str
    sha256: bytes
    size: int
    content_type: str


class ObjectStore(Protocol):
    """Tenant-scoped storage of byte objects, addressed by ``ObjectRef``.

    ``LocalFsObjectStore`` is the only adapter so far; an S3 one is a later adapter
    behind this same port. Tenant ids are ``UUID`` instances, and anything else is a
    programming error that an adapter may refuse with ``TypeError``.
    """

    def put(
        self,
        tenant_id: UUID,
        key: str,
        data: bytes,
        *,
        content_type: str,
        metadata: Mapping[str, str] = NO_METADATA,
    ) -> ObjectRef:
        """Store ``data`` under ``key``, replacing any object already there.

        The key, the content type and the metadata are checked (``validate_key``,
        ``validate_content_type``, ``validate_metadata``) before anything is
        written, so a refused ``put`` leaves the previous version in place.
        ``metadata`` is kept with the object as given.
        """
        ...

    def get(self, ref: ObjectRef) -> bytes:
        """Return the bytes ``ref`` points at once their sha256 matches the ref.

        A missing object raises ``NotFoundError``; bytes that do not hash to
        ``ref.sha256`` raise ``ObjectIntegrityError``.
        """
        ...

    def delete(self, ref: ObjectRef) -> None:
        """Remove the object ``ref`` points at; removing a missing object is fine."""
        ...

    def list(self, tenant_id: UUID, prefix: str) -> Iterator[ObjectRef]:
        """Yield a ref for every object of the tenant whose key starts with ``prefix``.

        The match is on the characters of the key, so ``docs/20`` finds
        ``docs/2026/a.pdf``; refs come in key order. An object whose stored
        record is unreadable raises ``ObjectIntegrityError``; one deleted while
        the listing runs is left out.
        """
        ...


def _bad_segment(segment: str) -> bool:
    return segment in {"", ".", ".."} or segment.lower().endswith(RECORD_SUFFIX)


def _encodable(text: str) -> bool:
    try:
        _ = text.encode()
    except UnicodeEncodeError:  # a lone surrogate, as undecodable file names yield
        return False
    return True


def _unsafe_text(text: str) -> bool:
    return (
        len(text) > MAX_KEY_LENGTH
        or "\\" in text
        or "\x00" in text
        or not _encodable(text)
    )


def validate_key(key: str) -> str:
    """Check that ``key`` cannot leave its tenant's namespace.

    A key is 1 to 512 characters of ``/``-separated segments. No segment is
    empty (so no leading, trailing or doubled ``/``), ``.`` or ``..``, none ends
    in ``.meta.json`` (reserved for the local adapter's records), and the key
    holds no backslash, no NUL and no lone surrogate (text UTF-8 cannot encode).

    Returns:
        The key, unchanged.

    Raises:
        InvalidObjectKeyError: When the key breaks one of those rules.

    """
    if _unsafe_text(key) or any(_bad_segment(s) for s in key.split("/")):
        raise InvalidObjectKeyError
    return key


def validate_prefix(prefix: str) -> str:
    """Check that a ``list`` prefix cannot leave its tenant's namespace.

    A prefix is a key that may stop mid-name or end in ``/``, and may be empty:
    every segment before the last must be valid, the last may be anything.

    Returns:
        The prefix, unchanged.

    Raises:
        InvalidObjectKeyError: When the prefix breaks one of those rules.

    """
    if _unsafe_text(prefix) or any(_bad_segment(s) for s in prefix.split("/")[:-1]):
        raise InvalidObjectKeyError
    return prefix


def validate_content_type(content_type: str) -> str:
    """Check that ``content_type`` is a media type that is safe to store and echo.

    ``type/subtype`` with the token characters of RFC 6838, optionally followed by
    ``;`` and parameters, in at most 255 printable characters: no line breaks,
    control characters or lone surrogates.

    Returns:
        The content type, unchanged.

    Raises:
        InvalidObjectMetadataError: When it breaks one of those rules.

    """
    if (
        len(content_type) > MAX_CONTENT_TYPE_LENGTH
        or not content_type.isprintable()
        or _CONTENT_TYPE.fullmatch(content_type) is None
    ):
        raise InvalidObjectMetadataError
    return content_type


def validate_metadata(metadata: Mapping[str, str]) -> dict[str, str]:
    """Check that ``metadata`` is small, plain text.

    At most 32 entries. Names are 1 to 64 characters from ``a-z``, ``0-9``, ``_``
    and ``-``. Values are printable text (``str.isprintable``: no control,
    separator other than space, or surrogate characters) of at most 1024
    characters. Names and values together take at most 8 KiB as UTF-8.

    Returns:
        A plain copy of ``metadata``.

    Raises:
        InvalidObjectMetadataError: When it breaks one of those rules.

    """
    size = 0
    for name, value in metadata.items():
        if (
            _METADATA_NAME.fullmatch(name) is None
            or len(value) > MAX_METADATA_VALUE_LENGTH
            or not value.isprintable()
        ):
            raise InvalidObjectMetadataError
        size += len(name) + len(value.encode())
    if len(metadata) > MAX_METADATA_ENTRIES or size > MAX_METADATA_BYTES:
        raise InvalidObjectMetadataError
    return dict(metadata)


def parse_digest(text: str | None) -> bytes:
    """Decode a stored sha256 hex digest.

    Returns:
        The 32 digest bytes.

    Raises:
        ObjectIntegrityError: When ``text`` is missing or not 64 lowercase hex digits.

    """
    if text is None or _DIGEST_HEX.fullmatch(text) is None:
        msg = "stored object record is missing or unreadable"
        raise ObjectIntegrityError(msg)
    return bytes.fromhex(text)


def verify_integrity(ref: ObjectRef, data: bytes) -> bytes:
    """Check that ``data`` hashes to the sha256 held by ``ref``.

    Returns:
        ``data``, unchanged.

    Raises:
        ObjectIntegrityError: When the digests differ.

    """
    if not hmac.compare_digest(hashlib.sha256(data).digest(), ref.sha256):
        raise ObjectIntegrityError
    return data
