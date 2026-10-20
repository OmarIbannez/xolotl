"""Name normalization, phonetic keys and nicknames (SP-03 spec §4.2).

Pure: imports only the standard library, its string libraries, ``screening.lists``
and ``xolotl.platform.errors``/``xolotl.platform.enums``.
"""

from xolotl.modules.screening.matching.nicknames import are_nicknames
from xolotl.modules.screening.matching.normalize import (
    MATCHER_VERSION,
    NormalizedName,
    Token,
    TokenRole,
    normalize_name,
)
from xolotl.modules.screening.matching.phonetic import phonetic_key

__all__ = [
    "MATCHER_VERSION",
    "NormalizedName",
    "Token",
    "TokenRole",
    "are_nicknames",
    "normalize_name",
    "phonetic_key",
]
