"""Whole files sealed under a tenant's DATA key (SP-04 spec §6.2).

A sealed blob is the value envelope of ``primitives``: ``version || nonce ||
ciphertext || tag``, authenticated against the tenant and the ``DATA`` purpose, so
it opens for that tenant only. Sealing uses the key's current version; opening reads
the version from the blob's header, so a blob sealed before a rotation still opens.
"""

from uuid import UUID

from sqlalchemy.orm import Session

from xolotl.platform.crypto.primitives import (
    aad_for,
    decrypt_value,
    encrypt_value,
    parse_header,
)
from xolotl.platform.crypto.provider import get_key_provider
from xolotl.platform.enums import KeyPurpose


def seal_blob(session: Session, tenant_id: UUID, data: bytes) -> bytes:
    """Encrypt ``data`` under the tenant's current DATA key.

    Returns:
        The sealed bytes.

    """
    version, dek = get_key_provider().data_key(
        tenant_id, KeyPurpose.DATA, session=session
    )
    return encrypt_value(
        dek, version=version, plaintext=data, aad=aad_for(tenant_id, KeyPurpose.DATA)
    )


def open_blob(session: Session, tenant_id: UUID, sealed: bytes) -> bytes:
    """Decrypt a blob sealed by :func:`seal_blob` for the same tenant.

    A blob of another tenant, or one altered, fails with ``DecryptionError``.

    Returns:
        The original bytes.

    """
    return decrypt_value(
        get_key_provider().data_key(
            tenant_id, KeyPurpose.DATA, parse_header(sealed), session=session
        )[1],
        ciphertext=sealed,
        aad=aad_for(tenant_id, KeyPurpose.DATA),
    )
