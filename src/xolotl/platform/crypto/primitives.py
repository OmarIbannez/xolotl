"""AES-256-GCM value envelope and data-key wrapping (spec §8.1).

An encrypted value is ``version || nonce || ciphertext || tag``: a 2-byte big-endian
key version, a fresh random 12-byte nonce, then the AES-256-GCM output with its
16-byte tag last. The authenticated data is ``tenant_id.bytes + purpose``, so a value
never decrypts for another tenant or purpose, even under the same key. The version
only selects the key and is not authenticated itself: pointing it at another version
selects another key, and the tag check fails.

A wrapped data key is ``nonce || ciphertext || tag`` under the key-encryption key,
authenticated against the same data plus the 2-byte version, so it cannot be moved
to another tenant, purpose or version.

Nonces are random 96-bit values, which NIST SP 800-38D (section 8.3) allows for up
to 2**32 encryptions under one key: rotate a data key well before that.

Nothing here logs, and no error message carries key bytes, ciphertext or plaintext.
Key bytes are also kept out of tracebacks that print local variables (Sentry, rich,
``pytest -l``): they are held as ``SecretBytes``, which prints as a mask, and every
function here rebinds its key arguments to one first thing, so no frame it can raise
from shows a key. Plaintext and ciphertext are not masked: their callers hold them in
frames of their own, so such a tracker has to be configured not to capture locals.
"""

import secrets
from typing import Self, final, override
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from xolotl.platform.crypto.errors import DecryptionError
from xolotl.platform.enums import KeyPurpose

HEADER_LEN = 2
NONCE_LEN = 12
KEY_LEN = 32
TAG_LEN = 16
MAX_VERSION = 2 ** (8 * HEADER_LEN) - 1

_MIN_VALUE_LEN = HEADER_LEN + NONCE_LEN + TAG_LEN
_WRAPPED_LEN = NONCE_LEN + KEY_LEN + TAG_LEN


@final
class SecretBytes(bytes):
    """Key bytes that print as a mask, wherever a repr or a str ends up.

    Everything else is ``bytes``: equality, hashing, slicing and the cipher accept it
    wherever they accept plain bytes, so a ``SecretBytes`` can be passed straight on.
    """

    __slots__ = ()

    @classmethod
    def of(cls, data: bytes) -> Self:
        """Wrap ``data``, or return it as it is when it already is a ``SecretBytes``.

        Returns:
            The same bytes, masked.

        """
        return data if isinstance(data, cls) else cls(data)

    @override
    def __repr__(self) -> str:
        return "SecretBytes(***)"

    @override
    def __str__(self) -> str:
        return "SecretBytes(***)"


def _new_nonce() -> bytes:
    return secrets.token_bytes(NONCE_LEN)


def _require_key(key: bytes) -> bytes:
    # AES accepts 16, 24 and 32 byte keys; a short one would silently pick AES-128.
    if len(key) != KEY_LEN:
        msg = f"key must be {KEY_LEN} bytes"
        raise ValueError(msg)
    return key


def _version_bytes(version: int) -> bytes:
    if not 0 <= version <= MAX_VERSION:
        msg = f"key version must fit in {HEADER_LEN} bytes (0 to {MAX_VERSION})"
        raise ValueError(msg)
    return version.to_bytes(HEADER_LEN, "big")


def _wrap_aad(tenant_id: UUID, purpose: KeyPurpose, version: int) -> bytes:
    return aad_for(tenant_id, purpose) + _version_bytes(version)


def aad_for(tenant_id: UUID, purpose: KeyPurpose) -> bytes:
    """Return the authenticated data that binds a value to its tenant and purpose.

    Returns:
        The 16 bytes of ``tenant_id`` followed by the purpose name in UTF-8.

    """
    return tenant_id.bytes + purpose.value.encode()


def new_dek() -> bytes:
    """Return a fresh random data-encryption key.

    Returns:
        32 bytes from the operating system's secure random source, masked.

    """
    return SecretBytes(secrets.token_bytes(KEY_LEN))


def encrypt_value(dek: bytes, *, version: int, plaintext: bytes, aad: bytes) -> bytes:
    """Encrypt ``plaintext`` under ``dek`` with a fresh nonce.

    A ``dek`` that is not 32 bytes, or a ``version`` that does not fit the 2-byte
    header, is a programming error and raises ``ValueError``.

    Returns:
        ``version || nonce || ciphertext || tag``.

    """
    dek = SecretBytes.of(dek)
    cipher = AESGCM(_require_key(dek))
    header = _version_bytes(version)
    nonce = _new_nonce()
    return header + nonce + cipher.encrypt(nonce, plaintext, aad)


def decrypt_value(dek: bytes, *, ciphertext: bytes, aad: bytes) -> bytes:
    """Decrypt and authenticate a value produced by :func:`encrypt_value`.

    Returns:
        The plaintext.

    Raises:
        DecryptionError: When the input is too short, or the key, the authenticated
            data or any byte after the header is not the one it was sealed with.

    """
    dek = SecretBytes.of(dek)
    cipher = AESGCM(_require_key(dek))
    if len(ciphertext) < _MIN_VALUE_LEN:
        raise DecryptionError
    nonce = ciphertext[HEADER_LEN : HEADER_LEN + NONCE_LEN]
    try:
        return cipher.decrypt(nonce, ciphertext[HEADER_LEN + NONCE_LEN :], aad)
    except InvalidTag as exc:
        raise DecryptionError from exc


def parse_header(ciphertext: bytes) -> int:
    """Read the key version from the first two bytes of an encrypted value.

    Returns:
        The version, as a big-endian integer.

    Raises:
        DecryptionError: When ``ciphertext`` is shorter than the header.

    """
    if len(ciphertext) < HEADER_LEN:
        raise DecryptionError
    return int.from_bytes(ciphertext[:HEADER_LEN], "big")


def wrap_dek(
    kek: bytes, dek: bytes, *, tenant_id: UUID, purpose: KeyPurpose, version: int
) -> bytes:
    """Encrypt a data key under the key-encryption key, bound to its identity.

    A ``kek`` or ``dek`` that is not 32 bytes, or a ``version`` that does not fit
    the 2-byte header, is a programming error and raises ``ValueError``.

    Returns:
        ``nonce || ciphertext || tag``, 60 bytes, masked.

    """
    kek = SecretBytes.of(kek)
    dek = SecretBytes.of(dek)
    cipher = AESGCM(_require_key(kek))
    aad = _wrap_aad(tenant_id, purpose, version)
    nonce = _new_nonce()
    return SecretBytes(nonce + cipher.encrypt(nonce, _require_key(dek), aad))


def unwrap_dek(
    kek: bytes, wrapped: bytes, *, tenant_id: UUID, purpose: KeyPurpose, version: int
) -> bytes:
    """Recover a data key wrapped by :func:`wrap_dek`.

    Returns:
        The 32-byte data key, masked.

    Raises:
        DecryptionError: When ``wrapped`` is not exactly what ``wrap_dek`` produced
            for this key-encryption key, tenant, purpose and version.

    """
    kek = SecretBytes.of(kek)
    wrapped = SecretBytes.of(wrapped)
    cipher = AESGCM(_require_key(kek))
    aad = _wrap_aad(tenant_id, purpose, version)
    if len(wrapped) != _WRAPPED_LEN:
        raise DecryptionError
    try:
        return SecretBytes(
            cipher.decrypt(wrapped[:NONCE_LEN], wrapped[NONCE_LEN:], aad)
        )
    except InvalidTag as exc:
        raise DecryptionError from exc
