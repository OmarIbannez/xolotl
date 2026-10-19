"""Object store: the one place modules put documents and exports (spec §8.4).

Every object lives under ``<tenant_id>/<key>``. ``put`` returns an ``ObjectRef``
holding the sha256 of the stored bytes and ``get`` recomputes it, so a corrupted
or swapped object raises ``ObjectIntegrityError`` instead of being served. A ref
addresses the tenant it names, whoever holds it: service code must only hand out
refs it loaded under that tenant's row-level security.

``port`` defines the contract and ``local`` (a directory tree) is the only adapter
so far; S3 is a later adapter behind the same port. The composition root picks the
adapter from ``OBJECT_STORE``.
"""

from xolotl.platform.storage.port import (
    MAX_KEY_LENGTH,
    NO_METADATA,
    RECORD_SUFFIX,
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

__all__ = [
    "MAX_KEY_LENGTH",
    "NO_METADATA",
    "RECORD_SUFFIX",
    "InvalidObjectKeyError",
    "InvalidObjectMetadataError",
    "ObjectIntegrityError",
    "ObjectRef",
    "ObjectStore",
    "parse_digest",
    "validate_content_type",
    "validate_key",
    "validate_metadata",
    "validate_prefix",
    "verify_integrity",
]
