"""List parsers, the ``ListEntry`` shape and the version delta (SP-03 spec §4.1).

Pure: imports only the standard library, its parsing libraries and
``xolotl.platform.errors``/``xolotl.platform.enums``; never the matcher.
"""

from xolotl.modules.screening.lists.diff import ListDelta, diff
from xolotl.modules.screening.lists.errors import ListFormatError
from xolotl.modules.screening.lists.parse import parse
from xolotl.modules.screening.lists.types import (
    EntityType,
    EntryAddress,
    EntryIdentifier,
    EntryName,
    IdType,
    JsonValue,
    ListCode,
    ListEntry,
    NameQuality,
    ParsedList,
    PartialDate,
)

__all__ = [
    "EntityType",
    "EntryAddress",
    "EntryIdentifier",
    "EntryName",
    "IdType",
    "JsonValue",
    "ListCode",
    "ListDelta",
    "ListEntry",
    "ListFormatError",
    "NameQuality",
    "ParsedList",
    "PartialDate",
    "diff",
    "parse",
]
