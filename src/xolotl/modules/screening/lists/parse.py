"""Dispatch a list file to its parser by list code. Never fetches."""

from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import BinaryIO

from xolotl.modules.screening.lists.errors import ListFormatError
from xolotl.modules.screening.lists.types import ListCode, ParsedList
from xolotl.modules.screening.lists.un import parse_un

_PARSERS: Mapping[str, Callable[[BinaryIO], ParsedList]] = MappingProxyType({
    ListCode.UN_CONSOLIDATED: parse_un,
})


def parse(list_code: str, data: BinaryIO) -> ParsedList:
    """Parse a published list file.

    Returns:
        The parsed list; complete or not at all.

    Raises:
        ListFormatError: no parser handles ``list_code`` (code
            ``LIST_CODE_UNSUPPORTED``), or the file is malformed.

    """
    parser = _PARSERS.get(list_code)
    if parser is None:
        msg = f"no parser for list code {list_code!r}"
        raise ListFormatError(msg, code="LIST_CODE_UNSUPPORTED")
    return parser(data)
