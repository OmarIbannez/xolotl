"""Errors of the screening module with stable codes (SP-03 spec §12)."""

from typing import ClassVar

from xolotl.platform.errors import DomainError


class ListMassRemovalError(DomainError):
    """A new list version would remove more entries than the guard allows.

    The detail is ``removed <r> of <n>``; ``xolotl lists load-file --accept-drop``
    is the only way past it.
    """

    default_code: ClassVar[str] = "LIST_MASS_REMOVAL"
    default_detail: ClassVar[str] = "the new version removes too many entries"


class ListMandatoryError(DomainError):
    """A mandatory list subscription cannot be disabled."""

    default_code: ClassVar[str] = "LIST_MANDATORY"
    default_detail: ClassVar[str] = "a mandatory list cannot be disabled"
