"""Errors of the screening module with stable codes (SP-03 spec §12)."""

from collections.abc import Sequence
from typing import ClassVar

from xolotl.modules.screening.engine.enums import BlockedReason
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


class DispositionNotAllowedError(DomainError):
    """The current user may not take this decision on this finding now.

    ``blocked_reasons`` tells why, in the order the action matrix checks them, and
    travels on the problem body (Deviation 10).
    """

    default_code: ClassVar[str] = "DISPOSITION_NOT_ALLOWED"
    default_detail: ClassVar[str] = "this decision is not allowed"

    def __init__(self, reasons: Sequence[BlockedReason] = ()) -> None:
        super().__init__()
        self.blocked_reasons: tuple[str, ...] = tuple(r.value for r in reasons)
