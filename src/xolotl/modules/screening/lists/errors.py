"""Errors of the list parsers."""

from typing import ClassVar

from xolotl.platform.errors import ValidationFailedError


class ListFormatError(ValidationFailedError):
    """A list file, or a stored entry, that cannot be read in full.

    The detail names the source, the parser version and the position; it never
    carries a whole record. A parser that raises it returns nothing, so a malformed
    file never yields a partial list.
    """

    default_code: ClassVar[str] = "LIST_FORMAT_INVALID"
    default_detail: ClassVar[str] = "list file is invalid"
