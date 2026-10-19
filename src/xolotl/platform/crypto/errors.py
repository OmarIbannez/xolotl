"""Errors the crypto layer raises (spec §8.1), each with a stable problem code.

The messages are fixed text: they never carry key bytes, ciphertext or plaintext.
"""

from typing import ClassVar

from xolotl.platform.errors import XolotlError


class DecryptionError(XolotlError):
    status: ClassVar[int] = 500
    default_code: ClassVar[str] = "DECRYPTION_FAILED"
    default_detail: ClassVar[str] = "decryption failed"


class KekMismatchError(DecryptionError):
    """The tenant's keys were wrapped under another master key than this process holds.

    A kind of ``DecryptionError``, since nothing can be decrypted; the code tells an
    operator to look at ``XOLOTL_MASTER_KEY`` instead of at the data. The text names
    neither master key, nor a reference to one: both stay out of what an API shows.
    """

    default_code: ClassVar[str] = "KEK_MISMATCH"
    default_detail: ClassVar[str] = (
        "the keys of this tenant were wrapped under a different master key"
    )


class KeyDestroyedError(XolotlError):
    status: ClassVar[int] = 410
    default_code: ClassVar[str] = "KEY_DESTROYED"
    default_detail: ClassVar[str] = "the encryption keys of this tenant were destroyed"


class KeyNotFoundError(XolotlError):
    status: ClassVar[int] = 500
    default_code: ClassVar[str] = "KEY_NOT_FOUND"
    default_detail: ClassVar[str] = "no such encryption key for this tenant"


class TenantScopeError(XolotlError):
    status: ClassVar[int] = 500
    default_code: ClassVar[str] = "TENANT_SCOPE"
    default_detail: ClassVar[str] = (
        "the value is not scoped to the tenant of its row or session"
    )
