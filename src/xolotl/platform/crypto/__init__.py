"""Per-tenant data keys, the AES-256-GCM primitives and the values built on them.

Blind indexes live in ``blind_index``, imported from there: the function shares its
module's name, so a package-level export would shadow the submodule.
"""

from xolotl.platform.crypto.encrypted import (
    Codec,
    Encrypted,
    EncryptedJson,
    EncryptedStr,
    JsonCodec,
    StrCodec,
    encrypt,
    encrypt_json,
    reveal_for_response,
)
from xolotl.platform.crypto.errors import (
    DecryptionError,
    KekMismatchError,
    KeyDestroyedError,
    KeyNotFoundError,
    TenantScopeError,
)
from xolotl.platform.crypto.models import TenantKey
from xolotl.platform.crypto.primitives import (
    HEADER_LEN,
    KEY_LEN,
    MAX_VERSION,
    NONCE_LEN,
    TAG_LEN,
    SecretBytes,
    aad_for,
    decrypt_value,
    encrypt_value,
    new_dek,
    parse_header,
    unwrap_dek,
    wrap_dek,
)
from xolotl.platform.crypto.provider import (
    KeyProvider,
    LocalKeyProvider,
    get_key_provider,
    set_key_provider,
)

__all__ = [
    "HEADER_LEN",
    "KEY_LEN",
    "MAX_VERSION",
    "NONCE_LEN",
    "TAG_LEN",
    "Codec",
    "DecryptionError",
    "Encrypted",
    "EncryptedJson",
    "EncryptedStr",
    "JsonCodec",
    "KekMismatchError",
    "KeyDestroyedError",
    "KeyNotFoundError",
    "KeyProvider",
    "LocalKeyProvider",
    "SecretBytes",
    "StrCodec",
    "TenantKey",
    "TenantScopeError",
    "aad_for",
    "decrypt_value",
    "encrypt",
    "encrypt_json",
    "encrypt_value",
    "get_key_provider",
    "new_dek",
    "parse_header",
    "reveal_for_response",
    "set_key_provider",
    "unwrap_dek",
    "wrap_dek",
]
