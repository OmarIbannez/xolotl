"""The screening module's problem codes (SP-03 spec §9 and §12)."""

from collections.abc import Sequence
from typing import ClassVar

from xolotl.modules.screening.engine.enums import BlockedReason
from xolotl.platform.errors import DomainError


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
